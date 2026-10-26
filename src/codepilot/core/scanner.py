"""Scanner engine for discovering AI instruction comments in source repositories.

Inspects source code files, identifies supported AI instruction comments,
and extracts contextual metadata while honoring protected files and directories.
"""

import ast
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set

from codepilot.config import CodePilotConfig
from codepilot.core.parser.comment_parser import InstructionCommentParser
from codepilot.core.parser.engine import InstructionParserEngine
from codepilot.core.parser.models import InstructionTask
from codepilot.exceptions import ScanError
from codepilot.logging_config import get_logger

logger = get_logger("core.scanner")

KNOWN_CONFIG_FILENAMES: Set[str] = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    ".codepilot.toml",
    "pytest.ini",
    "tox.ini",
    "tsconfig.json",
}

TEST_DIR_NAMES: Set[str] = {
    "test",
    "tests",
    "testing",
    "spec",
    "specs",
}

# Supported AI comment prefixes
# Matches:
#   # AI: ...
#   # AI-FIX: ...
#   # AI-TODO: ...
#   # AI-REFACTOR: ...
#   # CODEPILOT: ...
#   // AI: ...
#   /* AI: ... */
#   <!-- AI: ... -->
AI_COMMENT_PATTERN = re.compile(
    r"(?:#|//|<!--|/\*)\s*(AI-FIX|AI-TODO|AI-REFACTOR|AI|CODEPILOT)\s*[:\-]\s*(.+?)(?:\*/|-->)?$",
    re.IGNORECASE,
)


# Supported programming and markup source extensions
SOURCE_EXTENSIONS: Set[str] = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".sql",
}



@dataclass
class InstructionComment:
    """Represents a discovered AI instruction comment."""

    id: str
    file_path: str
    line_number: int
    instruction_type: str
    raw_comment: str
    prompt: str
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)
    target_symbol: Optional[str] = None
    target_symbol_type: Optional[str] = None
    warning: Optional[str] = None
    is_malformed: bool = False

    @property
    def description(self) -> str:
        """Alias for prompt matching Phase 3 InstructionTask."""
        return self.prompt

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["description"] = self.prompt
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstructionComment":
        prompt = data.get("prompt") or data.get("description", "")
        return cls(
            id=data.get("id", ""),
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 1),
            instruction_type=data.get("instruction_type", "AI-TASK"),
            raw_comment=data.get("raw_comment", ""),
            prompt=prompt,
            context_before=data.get("context_before", []),
            context_after=data.get("context_after", []),
            target_symbol=data.get("target_symbol"),
            target_symbol_type=data.get("target_symbol_type"),
            warning=data.get("warning"),
            is_malformed=data.get("is_malformed", False),
        )


@dataclass
class ScanResult:
    """Aggregated scan outcome for the repository."""

    project_root: str
    scanned_files_count: int
    instructions: List[InstructionComment]
    timestamp: str
    warnings: List[str] = field(default_factory=list)
    counts_by_type: Dict[str, int] = field(default_factory=dict)
    discovered_files: List[str] = field(default_factory=list)
    file_types: Dict[str, int] = field(default_factory=dict)
    skipped_files: List[str] = field(default_factory=list)
    static_findings: List[Dict[str, Any]] = field(default_factory=list)
    test_directories: List[str] = field(default_factory=list)
    config_files: List[str] = field(default_factory=list)

    @property
    def tasks(self) -> List[InstructionComment]:
        """Alias for instructions matching Phase 3 ParseResult."""
        return self.instructions

    @property
    def total_tasks(self) -> int:
        return len(self.instructions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_root": self.project_root,
            "scanned_files_count": self.scanned_files_count,
            "total_tasks": self.total_tasks,
            "counts_by_type": self.counts_by_type,
            "warnings": self.warnings,
            "instructions": [inst.to_dict() for inst in self.instructions],
            "tasks": [inst.to_dict() for inst in self.instructions],
            "discovered_files": self.discovered_files,
            "file_types": self.file_types,
            "skipped_files": self.skipped_files,
            "static_findings": self.static_findings,
            "test_directories": self.test_directories,
            "config_files": self.config_files,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScanResult":
        raw_list = data.get("instructions") or data.get("tasks", [])
        instructions = [InstructionComment.from_dict(inst) for inst in raw_list]
        return cls(
            project_root=data.get("project_root", ""),
            scanned_files_count=data.get("scanned_files_count", 0),
            instructions=instructions,
            timestamp=data.get("timestamp", ""),
            warnings=data.get("warnings", []),
            counts_by_type=data.get("counts_by_type", {}),
            discovered_files=data.get("discovered_files", []),
            file_types=data.get("file_types", {}),
            skipped_files=data.get("skipped_files", []),
            static_findings=data.get("static_findings", []),
            test_directories=data.get("test_directories", []),
            config_files=data.get("config_files", []),
        )


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
    rel_path_str = str(file_path.relative_to(project_root)).replace("\\", "/")
    filename = file_path.name
    for pattern in protected_patterns:
        p = pattern.strip()
        if not p:
            continue
        if file_path.match(p) or Path(rel_path_str).match(p) or filename == p:
            return True
    return False


def scan_file(file_path: Path, project_root: Path) -> List[InstructionComment]:
    """Scan a single source file for AI instruction comments using Phase 3 comment parser."""
    parser = InstructionCommentParser(project_root=project_root)
    tasks, _ = parser.parse_file(file_path=file_path)

    instructions: List[InstructionComment] = []
    for t in tasks:
        instructions.append(
            InstructionComment(
                id=t.id,
                file_path=t.file_path,
                line_number=t.line_number,
                instruction_type=t.instruction_type,
                raw_comment=t.raw_comment,
                prompt=t.description,
                context_before=t.context_before,
                context_after=t.context_after,
                target_symbol=t.target_symbol,
                target_symbol_type=t.target_symbol_type,
                warning=t.warning,
                is_malformed=t.is_malformed,
            )
        )
    return instructions


def scan_repository(
    project_root: Path,
    config: CodePilotConfig,
) -> ScanResult:
    """Inspect the repository and extract all supported AI instruction comments.

    Args:
        project_root: The root path of the project to scan.
        config: Current validated CodePilot configuration.

    Returns:
        ScanResult: Aggregated list of instructions and scan statistics.

    Raises:
        ScanError: If scanning fails unexpectedly.
    """
    root = project_root.resolve()
    if not root.is_dir():
        raise ScanError(f"Target directory does not exist or is not a directory: {root}")

    all_instructions: List[InstructionComment] = []
    scanned_count = 0
    discovered_files: List[str] = []
    file_types: Dict[str, int] = {}
    skipped_files: List[str] = []
    static_findings: List[Dict[str, Any]] = []
    test_directories: List[str] = []
    config_files: List[str] = []
    seen_test_dirs: Set[str] = set()

    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Prune protected directories in-place so os.walk does not recurse into them
            dirnames[:] = [
                d
                for d in dirnames
                if not should_skip_directory(d, config.protected_directories)
                and not d.startswith(".")
            ]

            current_dir = Path(dirpath)
            try:
                rel_dir = str(current_dir.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel_dir = ""

            if rel_dir and any(part.lower() in TEST_DIR_NAMES for part in current_dir.relative_to(root).parts):
                top_test_part = current_dir.relative_to(root).parts[0]
                if top_test_part not in seen_test_dirs:
                    seen_test_dirs.add(top_test_part)
                    test_directories.append(top_test_part)

            for filename in filenames:
                # Skip hidden and dotfiles
                if filename.startswith("."):
                    continue

                file_path = current_dir / filename
                try:
                    rel_path = str(file_path.relative_to(root)).replace("\\", "/")
                except ValueError:
                    rel_path = filename

                # Discover configuration files
                if filename.lower() in KNOWN_CONFIG_FILENAMES and rel_path not in config_files:
                    config_files.append(rel_path)

                # Check extension against supported source code extensions
                if file_path.suffix.lower() not in SOURCE_EXTENSIONS:
                    skipped_files.append(f"{rel_path} (unsupported extension)")
                    continue

                # Check protected files pattern
                if is_protected_file(file_path, root, config.protected_files):
                    skipped_files.append(f"{rel_path} (protected file)")
                    continue

                # Check max input size
                try:
                    if file_path.stat().st_size > config.max_input_size:
                        skipped_files.append(f"{rel_path} (exceeds max size {config.max_input_size} bytes)")
                        continue
                except OSError:
                    continue

                # Check binary file
                if is_binary_file(file_path):
                    skipped_files.append(f"{rel_path} (binary file)")
                    continue

                scanned_count += 1
                discovered_files.append(rel_path)
                ext = file_path.suffix.lower()
                file_types[ext] = file_types.get(ext, 0) + 1

                # Static analysis check 1: Empty file check
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    if not content.strip():
                        static_findings.append({
                            "file": rel_path,
                            "line": 1,
                            "type": "empty_file",
                            "severity": "warning",
                            "message": f"Source file '{rel_path}' is empty.",
                        })
                    elif ext in (".py", ".pyi"):
                        # Static analysis check 2: Python AST parse error detection
                        try:
                            ast.parse(content)
                        except SyntaxError as e:
                            static_findings.append({
                                "file": rel_path,
                                "line": getattr(e, "lineno", 1) or 1,
                                "column": getattr(e, "offset", 0) or 0,
                                "type": "syntax_error",
                                "severity": "error",
                                "message": f"Python syntax error: {e.msg if hasattr(e, 'msg') else str(e)}",
                            })
                except Exception as ex:
                    logger.debug("Failed static analysis inspection on %s: %s", rel_path, ex)

                # Optional comment-based task discovery
                file_instructions = scan_file(file_path, root)
                all_instructions.extend(file_instructions)

    except Exception as e:
        raise ScanError(f"Error during repository scan: {e}") from e

    from collections import Counter
    type_counts = dict(Counter(i.instruction_type for i in all_instructions))

    return ScanResult(
        project_root=str(root),
        scanned_files_count=scanned_count,
        instructions=all_instructions,
        timestamp=datetime.now(timezone.utc).isoformat(),
        counts_by_type=type_counts,
        discovered_files=sorted(discovered_files),
        file_types=dict(sorted(file_types.items())),
        skipped_files=skipped_files,
        static_findings=static_findings,
        test_directories=sorted(test_directories),
        config_files=sorted(config_files),
    )


def save_scan_result(result: ScanResult, target_file: Path) -> Path:
    """Persist scan results to a JSON file."""
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return target_file
    except OSError as e:
        raise ScanError(f"Failed to save scan results to {target_file}: {e}") from e


def load_scan_result(source_file: Path) -> Optional[ScanResult]:
    """Load cached scan results from a JSON file if present."""
    if not source_file.is_file():
        return None
    try:
        data = json.loads(source_file.read_text(encoding="utf-8"))
        return ScanResult.from_dict(data)
    except Exception as e:
        logger.warning("Could not read scan results from %s: %s", source_file, e)
        return None
