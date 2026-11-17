"""Security and repository boundary enforcement engine for CodePilot AI.

Enforces canonical path resolution, path-traversal protection, protected-file
isolation, secret scrubbing, and command execution verification.
"""

from enum import Enum
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, List, Optional, Set, Tuple, Union

from codepilot.config import CodePilotConfig
from codepilot.exceptions import SecurityError
from codepilot.logging_config import get_logger

logger = get_logger("core.security")


class FileOwnershipCategory(str, Enum):
    """Classification of files within and outside the repository."""

    PROJECT_MANAGED = "PROJECT_MANAGED"  # CodePilot internal files (.codepilot/*)
    USER_OWNED = "USER_OWNED"            # Application source code, tests, docs
    GENERATED = "GENERATED"              # Build outputs, test caches, coverage
    PROTECTED = "PROTECTED"              # Credentials, secrets, .git, .venv, pyproject.toml
    EXTERNAL = "EXTERNAL"                # Outside authorized project root


class FileOperation(str, Enum):
    """File operation types subject to permission enforcement."""

    READ = "READ"
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    RENAME = "RENAME"
    OVERWRITE = "OVERWRITE"


# ==============================================================================
# Secret redaction
#
# Single source of truth for the patterns used to redact credentials. Two distinct
# consumers with deliberately different aggressiveness:
#
#   sanitize_log_output()          -- diagnostics, logs, error text. Aggressive: a false
#                                     positive only makes a log line less readable.
#   redact_secrets_for_context()   -- source code about to be placed in an LLM prompt.
#                                     Conservative: over-redacting mangles the code the
#                                     model has to reason about, so only high-confidence
#                                     secrets are removed and line numbering is preserved.
#
# Note: codepilot.core.dataset.validator keeps its own pattern list. That serves a
# different purpose (rejecting training examples outright, with human-readable reasons)
# and uses stricter thresholds, so it is intentionally not merged here.
# ==============================================================================

REDACTION_PLACEHOLDER = "***REDACTED_SECRET***"
PRIVATE_KEY_PLACEHOLDER = "***REDACTED_PRIVATE_KEY***"

# Keys whose assigned value is treated as sensitive.
_SECRET_KEY_NAMES = (
    r"api[_-]?keys?|secret[_-]?key|client[_-]?secret|access[_-]?key[_-]?id|access[_-]?key"
    r"|secret[_-]?access[_-]?key|private[_-]?key|auth[_-]?token|access[_-]?token"
    r"|refresh[_-]?token|bearer|authorization|auth|credentials?|token|passwords?|passwd|pwd|secret"
)

# Vendor-specific token shapes. These are unambiguous enough to redact anywhere,
# including inside source code sent to a model.
VENDOR_SECRET_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,80}"),          # OpenAI-style secret key
    re.compile(r"ghp_[a-zA-Z0-9]{36,40}"),            # GitHub personal access token
    re.compile(r"gho_[a-zA-Z0-9]{36,40}"),            # GitHub OAuth token
    re.compile(r"github_pat_[a-zA-Z0-9_]{22,100}"),   # GitHub fine-grained PAT
    re.compile(r"AKIA[0-9A-Z]{16}"),                  # AWS access key ID
    re.compile(r"ASIA[0-9A-Z]{16}"),                  # AWS temporary access key ID
    re.compile(r"xox[baprs]-[a-zA-Z0-9\-]{10,250}"),  # Slack tokens
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),            # Google API key
    re.compile(r"glpat-[a-zA-Z0-9_\-]{20,}"),         # GitLab personal access token
    re.compile(r"sk_(?:live|test)_[a-zA-Z0-9]{16,}"), # Stripe secret key
    re.compile(r"eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}"),  # JWT
]

# A PEM private key block, matched so it can be replaced line-for-line.
PRIVATE_KEY_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Quote-aware key/value secret assignment. Handles bare, single-quoted and
# double-quoted keys, ':' or '=' delimiters, and optionally quoted values -- which
# covers the JSON form ("api_key": "...") that an unquoted-key-only pattern missed.
#
# The key may carry an identifier prefix: '_' is a word character, so requiring a word
# boundary before the name failed to match the very common 'db_password',
# 'openai_api_key' and 'MY_SECRET' forms. A trailing boundary is still required, which
# keeps 'token_count' and 'tokenizer' from being treated as credentials.
KEY_VALUE_SECRET_PATTERN = re.compile(
    r"""(?ix)
    (?P<key>["']?[A-Za-z0-9_]*(?:""" + _SECRET_KEY_NAMES + r""")\b["']?\s*[:=]\s*)
    (?P<open>["']?)
    (?P<value>[^\s"',;)\]}]+)
    (?P=open)
    """
)

# Values that are not real secrets: placeholders, empty strings, and indirections
# that read the value from somewhere else at runtime.
_NON_SECRET_VALUES = re.compile(
    r"""(?ix)^(?:
        none|null|nil|true|false|undefined|empty
        |os\.environ.*|os\.getenv.*|getenv.*|process\.env.*|env\..*
        |your[_-]?.*|my[_-]?.*|placeholder|example|sample|dummy|test|testing
        |changeme|change[_-]?me|todo|fixme|redacted|xxx+|\.\.\.|\*+
        |<.*>|\{\{.*\}\}|\$\{.*\}|%s|\{\}
    )$"""
)

# Minimum length before an opaque literal assigned to a secret-named key is treated as a
# real credential in source code. Short values are usually enum members or flags.
_MIN_CONTEXT_SECRET_LENGTH = 12

# Retained for backward compatibility with earlier releases that imported this name.
SECRET_PATTERNS: List["re.Pattern[str]"] = [
    KEY_VALUE_SECRET_PATTERN,
    *VENDOR_SECRET_PATTERNS,
    PRIVATE_KEY_BLOCK_PATTERN,
]


def is_binary_file(file_path: Path) -> bool:
    """Check whether a file is binary by inspecting its initial byte chunk."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(4096)
            return b"\x00" in chunk
    except OSError:
        return True


def should_skip_directory(dir_name: str, protected_dirs: List[str]) -> bool:
    """Check if directory name matches any protected directory rule."""
    dir_clean = dir_name.strip().lower()
    for protected in protected_dirs:
        p_clean = protected.strip().lower().strip("/\\")
        if dir_clean == p_clean or dir_clean.startswith(f"{p_clean}."):
            return True
    return False


def is_protected_file(file_path: Path, project_root: Path, protected_patterns: List[str]) -> bool:
    """Check if file matches any protected file glob pattern."""
    try:
        rel_path_str = str(file_path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        rel_path_str = file_path.name
    filename = file_path.name
    for pattern in protected_patterns:
        p = pattern.strip()
        if not p:
            continue
        if file_path.match(p) or Path(rel_path_str).match(p) or filename == p:
            return True
    return False


def validate_target_path(
    path: Union[str, Path],
    project_root: Path,
    config: CodePilotConfig,
    check_exists: bool = False,
    allow_binary: bool = False,
) -> Path:
    """Validate that a target path is strictly inside the project root and not protected.

    Args:
        path: Path string or Path object (relative or absolute).
        project_root: Repository root directory.
        config: CodePilotConfig with protected files and directories.
        check_exists: Whether to verify that the file exists on disk.
        allow_binary: Whether binary files are permitted.

    Returns:
        Path: Canonical resolved Path within the project root.

    Raises:
        SecurityError: If path traverses outside project root, is protected, or is binary.
    """
    root = project_root.resolve()
    target_str = str(path).strip()
    if not target_str:
        raise SecurityError("Target file path cannot be empty.")

    # Prevent basic path traversal strings
    if ".." in target_str.replace("\\", "/").split("/"):
        # Resolve to see if it escapes
        candidate = (root / target_str).resolve()
        if not candidate.is_relative_to(root):
            raise SecurityError(
                f"Path traversal detected: '{target_str}' resolves outside repository root '{root}'."
            )

    candidate = (root / target_str).resolve() if not Path(target_str).is_absolute() else Path(target_str).resolve()

    # Requirement: Canonical path validation against repository boundary
    if not candidate.is_relative_to(root):
        raise SecurityError(
            f"Security boundary violation: target path '{path}' is outside repository root '{root}'."
        )

    # Check protected directories in the path components
    try:
        rel_parts = candidate.relative_to(root).parts
        for part in rel_parts[:-1]:  # check parent directories
            if should_skip_directory(part, config.protected_directories):
                raise SecurityError(
                    f"Security boundary violation: directory '{part}' in path '{path}' is protected from modification."
                )
    except ValueError as e:
        raise SecurityError(f"Path '{path}' is outside repository root.") from e

    # Check protected files pattern
    if is_protected_file(candidate, root, config.protected_files):
        raise SecurityError(
            f"Security boundary violation: file '{path}' matches protected file pattern and cannot be modified."
        )

    if check_exists and not candidate.is_file():
        raise SecurityError(f"Target file '{path}' does not exist on disk.")

    if candidate.is_file() and not allow_binary and is_binary_file(candidate):
        raise SecurityError(
            f"Security boundary violation: '{path}' appears to be a binary file. CodePilot only operates on text source files."
        )

    return candidate


def sanitize_log_output(text: str) -> str:
    """Scrub secret values, authorization tokens, and credentials from string output.

    Intended for logs, diagnostics and error messages, where readability matters less
    than never printing a credential. Redaction is therefore deliberately aggressive:
    any value assigned to a secret-sounding key is masked regardless of how it looks.

    Handles bare, single-quoted and double-quoted keys with either ':' or '=' as the
    delimiter, so the JSON form '"api_key": "<secret>"' is redacted as well as the
    'api_key=<secret>' form.
    """
    if not text:
        return ""

    sanitized = PRIVATE_KEY_BLOCK_PATTERN.sub(PRIVATE_KEY_PLACEHOLDER, text)

    def _mask_key_value(match: "re.Match[str]") -> str:
        quote = match.group("open")
        return f"{match.group('key')}{quote}{REDACTION_PLACEHOLDER}{quote}"

    sanitized = KEY_VALUE_SECRET_PATTERN.sub(_mask_key_value, sanitized)

    for pattern in VENDOR_SECRET_PATTERNS:
        sanitized = pattern.sub(REDACTION_PLACEHOLDER, sanitized)

    return sanitized


def redact_secrets_for_context(text: str) -> Tuple[str, List[str]]:
    """Redact high-confidence secrets from source code bound for an LLM prompt.

    CodePilot excludes credential *files* from model context, but a hardcoded key inside
    an ordinary source file was previously forwarded verbatim. This closes that gap.

    Unlike sanitize_log_output(), this is deliberately conservative: the result is code
    the model must still be able to read and modify, so over-redaction is a correctness
    problem, not just a cosmetic one. Only the following are removed:

    1. Vendor-shaped tokens (OpenAI, GitHub, AWS, Slack, Google, GitLab, Stripe, JWT),
       which are unambiguous wherever they appear.
    2. PEM private key blocks, replaced line-for-line.
    3. Opaque literals assigned to a secret-named key, but only when the value is at
       least _MIN_CONTEXT_SECRET_LENGTH characters and is not a recognizable placeholder
       or a runtime indirection such as os.environ[...].

    Variable names, function signatures, control flow and line numbering are preserved,
    so a redacted snippet still maps onto the real file.

    Args:
        text: Source content destined for a model prompt.

    Returns:
        (redacted_text, descriptions) where descriptions names each kind of secret
        removed, suitable for surfacing to the developer.
    """
    if not text:
        return "", []

    findings: List[str] = []
    redacted = text

    # 1. PEM private keys: replace with the same number of lines so that line numbers
    #    reported for the snippet continue to match the real file.
    def _mask_private_key(match: "re.Match[str]") -> str:
        line_count = match.group(0).count("\n")
        return "\n".join([PRIVATE_KEY_PLACEHOLDER] * (line_count + 1))

    redacted, pem_count = PRIVATE_KEY_BLOCK_PATTERN.subn(_mask_private_key, redacted)
    if pem_count:
        findings.append(f"{pem_count} private key block(s)")

    # 2. Vendor-shaped tokens.
    vendor_hits = 0
    for pattern in VENDOR_SECRET_PATTERNS:
        redacted, count = pattern.subn(REDACTION_PLACEHOLDER, redacted)
        vendor_hits += count
    if vendor_hits:
        findings.append(f"{vendor_hits} provider API token(s)")

    # 3. Opaque literals assigned to secret-named keys.
    assignment_hits = 0

    def _mask_assignment(match: "re.Match[str]") -> str:
        nonlocal assignment_hits
        value = match.group("value")
        quote = match.group("open")

        if REDACTION_PLACEHOLDER in value or PRIVATE_KEY_PLACEHOLDER in value:
            return match.group(0)  # already redacted above
        if len(value) < _MIN_CONTEXT_SECRET_LENGTH:
            return match.group(0)  # too short to be a credential; likely a flag or enum
        if _NON_SECRET_VALUES.match(value):
            return match.group(0)  # placeholder or runtime indirection, not a secret
        if not quote:
            # Unquoted in source code this is an identifier or expression, not a literal.
            return match.group(0)

        assignment_hits += 1
        return f"{match.group('key')}{quote}{REDACTION_PLACEHOLDER}{quote}"

    redacted = KEY_VALUE_SECRET_PATTERN.sub(_mask_assignment, redacted)
    if assignment_hits:
        findings.append(f"{assignment_hits} hardcoded credential value(s)")

    if findings:
        logger.warning(
            "Redacted secrets from content before model submission: %s", ", ".join(findings)
        )

    return redacted, findings


def sanitize_filename(filename: str) -> str:
    """Safely extract the basename and remove path traversal sequences from a filename.

    Normalizes directory separators (both Unix '/' and Windows '\\'), strips null bytes
    and ASCII control characters, derives the basename component, and iteratively
    strips residual traversal tokens ('..').
    Handles empty, whitespace, nested, mixed-separator, and traversal-like inputs deterministically.

    Note: This is an in-memory string sanitization function for filenames and does
    not replace filesystem boundary verification performed by validate_target_path().

    Args:
        filename: Untrusted filename or path string.

    Returns:
        str: Sanitized basename, free of directory separators and traversal tokens.
    """
    if not filename:
        return ""

    # Strip null bytes and ASCII control characters (0x00-0x1f, 0x7f)
    s = re.sub(r"[\x00-\x1f\x7f]", "", filename).strip()
    if not s:
        return ""

    # Normalize Windows backslashes to forward slashes
    normalized = s.replace("\\", "/")

    # Extract basename (last non-empty path component)
    parts = [p for p in normalized.split("/") if p]
    if not parts:
        return ""

    basename = parts[-1]

    # Strip Windows drive prefix if attached directly (e.g. 'C:file.txt')
    if len(basename) >= 2 and basename[1] == ":" and basename[0].isalpha():
        basename = basename[2:]

    # Iteratively remove path traversal sequence '..' until none remain
    while ".." in basename:
        basename = basename.replace("..", "")

    return basename.strip()


def resolve_project_root(
    target_path: Optional[Union[str, Path]] = None,
    cwd: Optional[Path] = None,
    allow_home_or_root: bool = False,
) -> Path:
    """Resolve and validate the canonical project root directory.

    Enforces that:
    1. An explicitly provided path is validated and resolved.
    2. Git metadata ('git rev-parse --show-toplevel') is checked if inside a repository.
    3. Structural markers ('.git', 'pyproject.toml', '.codepilot.toml') are discovered upward.
    4. Refuses to treat the user home directory or filesystem root as project root without explicit authorization.
    5. Resolves symbolic links and validates boundaries.

    Raises:
        SecurityError: If root cannot be established or violates boundary rules.
    """
    home = Path.home().resolve()

    if target_path:
        target = Path(target_path).expanduser().resolve()
        if not target.exists():
            raise SecurityError(f"Specified project path '{target}' does not exist.")
        if not target.is_dir():
            raise SecurityError(f"Specified project path '{target}' is not a directory.")
        if not allow_home_or_root:
            if target == home:
                raise SecurityError(
                    f"Refusing to treat user home directory '{target}' as project root without explicit authorization."
                )
            if target == target.parent:
                raise SecurityError(
                    f"Refusing to treat filesystem root '{target}' as project root without explicit authorization."
                )
        return target

    # Determine base directory to start inspection from
    base = (cwd or Path.cwd()).resolve()

    # Step 1: Check Git repository root
    git_bin = shutil.which("git")
    if git_bin:
        try:
            res = subprocess.run(
                [git_bin, "rev-parse", "--show-toplevel"],
                cwd=str(base),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                git_root = Path(res.stdout.strip()).resolve()
                if git_root.is_dir():
                    if not allow_home_or_root and (git_root == home or git_root == git_root.parent):
                        raise SecurityError(
                            f"Git root '{git_root}' is user home or filesystem root. Explicit authorization required."
                        )
                    return git_root
        except (subprocess.SubprocessError, OSError):
            pass

    # Step 2: Search upward for project markers
    curr = base
    markers = [".git", "pyproject.toml", ".codepilot.toml"]
    while curr != curr.parent:
        if not allow_home_or_root and curr == home:
            break
        for m in markers:
            if (curr / m).exists():
                return curr
        curr = curr.parent

    # Step 3: Default fallback to base directory
    if not allow_home_or_root:
        if base == home:
            raise SecurityError(
                f"Refusing to treat user home directory '{base}' as project root without explicit authorization."
            )
        if base == base.parent:
            raise SecurityError(
                f"Refusing to treat filesystem root '{base}' as project root without explicit authorization."
            )

    return base


def classify_file_ownership(
    file_path: Union[str, Path],
    project_root: Path,
    config: CodePilotConfig,
) -> FileOwnershipCategory:
    """Classify a file into its ownership category.

    Categories:
    - EXTERNAL: Path resolves outside the project root.
    - PROTECTED: Matches protected files, secret extensions, credentials, or protected directories.
    - PROJECT_MANAGED: CodePilot internal state and metadata (.codepilot/*).
    - GENERATED: Build outputs, caches, coverage artifacts.
    - USER_OWNED: Application source code, tests, documentation, and existing configurations.
    """
    root = project_root.resolve()
    target_str = str(file_path).strip()
    if not target_str:
        return FileOwnershipCategory.EXTERNAL

    candidate = (root / target_str).resolve() if not Path(target_str).is_absolute() else Path(target_str).resolve()

    if not candidate.is_relative_to(root):
        return FileOwnershipCategory.EXTERNAL

    rel = candidate.relative_to(root)
    rel_parts = rel.parts

    # Check if project-managed (.codepilot/ directory)
    if rel_parts and rel_parts[0] == ".codepilot":
        return FileOwnershipCategory.PROJECT_MANAGED

    # Check if generated files / directories (build, dist, test caches, coverage artifacts)
    generated_markers = [".pytest_cache", ".coverage", "htmlcov", "dist", "build", "__pycache__", ".mypy_cache"]
    for part in rel_parts:
        if part in generated_markers or part.endswith(".egg-info") or part.endswith(".pyc"):
            return FileOwnershipCategory.GENERATED

    # Check protected files and directories
    if is_protected_file(candidate, root, config.protected_files):
        return FileOwnershipCategory.PROTECTED

    for part in rel_parts[:-1]:
        if should_skip_directory(part, config.protected_directories):
            return FileOwnershipCategory.PROTECTED

    return FileOwnershipCategory.USER_OWNED


def check_file_permission(
    file_path: Union[str, Path],
    operation: FileOperation,
    project_root: Path,
    config: CodePilotConfig,
    approved: bool = False,
) -> None:
    """Check if the requested operation is permitted on the specified file.

    Raises:
        SecurityError: If the operation is denied according to file ownership policies.
    """
    category = classify_file_ownership(file_path, project_root, config)

    if category == FileOwnershipCategory.EXTERNAL:
        raise SecurityError(
            f"Permission denied: operation '{operation.value}' on external path '{file_path}' "
            f"outside project root '{project_root}' is forbidden."
        )

    if category == FileOwnershipCategory.PROTECTED:
        if operation in (
            FileOperation.MODIFY,
            FileOperation.DELETE,
            FileOperation.OVERWRITE,
            FileOperation.RENAME,
            FileOperation.CREATE,
        ):
            raise SecurityError(
                f"Permission denied: file '{file_path}' is classified as PROTECTED and cannot be modified or deleted."
            )
        if operation == FileOperation.READ and is_protected_file(Path(file_path), project_root, config.protected_files):
            name = Path(file_path).name.lower()
            if any(name.startswith(p) for p in [".env", "credentials", "id_rsa", "secret"]) or any(
                name.endswith(e) for e in [".pem", ".key", ".cert", ".pfx"]
            ):
                raise SecurityError(
                    f"Permission denied: reading protected credential file '{file_path}' is strictly forbidden."
                )

    if category == FileOwnershipCategory.USER_OWNED:
        if operation in (
            FileOperation.MODIFY,
            FileOperation.DELETE,
            FileOperation.OVERWRITE,
            FileOperation.RENAME,
        ) and not approved:
            raise SecurityError(
                f"Permission denied: operation '{operation.value}' on user-owned file '{file_path}' "
                f"requires explicit user approval."
            )

