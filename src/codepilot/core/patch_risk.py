"""Risk screening for model-generated code modifications.

CodePilot stages candidate patches for human review rather than writing them directly,
but a reviewer skimming a diff can easily miss a single added line that executes a shell
command, evaluates a string, or opens a network connection. This module inspects what a
patch *introduces* and reports those constructs explicitly, so they are called out in the
diff preview instead of relying on the reader to spot them.

Design notes:

- Only newly introduced constructs are reported. A construct already present in the
  original file is not the patch's doing and would be noise on every subsequent patch.
- Detection is AST-based for Python, so occurrences inside strings and comments do not
  produce findings and `obj.eval(...)` is not confused with the builtin `eval(...)`.
- Non-Python files fall back to conservative textual matching, since no AST is available.
- Screening is advisory. It annotates the patch and never silently discards it: the
  developer remains the decision maker, which is the project's core safety model.
"""

import ast
from dataclasses import asdict, dataclass
from enum import Enum
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from codepilot.logging_config import get_logger

logger = get_logger("core.patch_risk")


class RiskSeverity(str, Enum):
    """Severity of an introduced construct."""

    HIGH = "HIGH"      # arbitrary code or command execution
    MEDIUM = "MEDIUM"  # network access, deserialization, dynamic import
    LOW = "LOW"        # filesystem deletion and other notable side effects


@dataclass
class RiskFinding:
    """A single risky construct newly introduced by a patch."""

    category: str
    severity: str
    line_number: int
    snippet: str
    message: str

    def __post_init__(self) -> None:
        # Normalize to the plain string value so the field serializes and renders as
        # "HIGH" rather than "RiskSeverity.HIGH" wherever it is displayed or stored.
        if isinstance(self.severity, RiskSeverity):
            self.severity = self.severity.value
        else:
            self.severity = str(self.severity)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Builtin callables that execute arbitrary code supplied at runtime.
_CODE_EXECUTION_BUILTINS: Dict[str, str] = {
    "exec": "executes arbitrary Python source at runtime",
    "eval": "evaluates an arbitrary Python expression at runtime",
    "compile": "compiles arbitrary Python source at runtime",
    "__import__": "imports a module chosen at runtime",
}

# Dotted calls grouped by category. Keys are matched against the trailing attribute path
# of a call, e.g. 'os.system' matches both `os.system(...)` and `subprocess.os.system(...)`.
_RISKY_CALLS: Dict[str, Tuple[str, str, str]] = {
    # attribute path: (category, severity, message)
    "os.system": ("command_execution", RiskSeverity.HIGH, "runs a shell command"),
    "os.popen": ("command_execution", RiskSeverity.HIGH, "runs a shell command"),
    "os.execv": ("command_execution", RiskSeverity.HIGH, "replaces the process image"),
    "os.execve": ("command_execution", RiskSeverity.HIGH, "replaces the process image"),
    "os.spawnl": ("command_execution", RiskSeverity.HIGH, "spawns a process"),
    "os.spawnv": ("command_execution", RiskSeverity.HIGH, "spawns a process"),
    "subprocess.run": ("command_execution", RiskSeverity.HIGH, "executes an external process"),
    "subprocess.call": ("command_execution", RiskSeverity.HIGH, "executes an external process"),
    "subprocess.check_call": ("command_execution", RiskSeverity.HIGH, "executes an external process"),
    "subprocess.check_output": ("command_execution", RiskSeverity.HIGH, "executes an external process"),
    "subprocess.Popen": ("command_execution", RiskSeverity.HIGH, "executes an external process"),
    "subprocess.getoutput": ("command_execution", RiskSeverity.HIGH, "runs a shell command"),
    "pty.spawn": ("command_execution", RiskSeverity.HIGH, "spawns an interactive process"),
    "pickle.load": ("deserialization", RiskSeverity.MEDIUM, "deserializes pickled data, which can execute code"),
    "pickle.loads": ("deserialization", RiskSeverity.MEDIUM, "deserializes pickled data, which can execute code"),
    "marshal.loads": ("deserialization", RiskSeverity.MEDIUM, "deserializes marshalled data"),
    "yaml.load": ("deserialization", RiskSeverity.MEDIUM, "YAML load can execute code unless SafeLoader is used"),
    "importlib.import_module": ("dynamic_import", RiskSeverity.MEDIUM, "imports a module chosen at runtime"),
    "urllib.request.urlopen": ("network_access", RiskSeverity.MEDIUM, "opens a network connection"),
    "request.urlopen": ("network_access", RiskSeverity.MEDIUM, "opens a network connection"),
    "requests.get": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "requests.post": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "requests.put": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "requests.delete": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "requests.request": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "httpx.get": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "httpx.post": ("network_access", RiskSeverity.MEDIUM, "performs an outbound HTTP request"),
    "socket.socket": ("network_access", RiskSeverity.MEDIUM, "opens a raw network socket"),
    "socket.create_connection": ("network_access", RiskSeverity.MEDIUM, "opens a network connection"),
    "shutil.rmtree": ("destructive_filesystem", RiskSeverity.LOW, "recursively deletes a directory tree"),
    "os.remove": ("destructive_filesystem", RiskSeverity.LOW, "deletes a file"),
    "os.unlink": ("destructive_filesystem", RiskSeverity.LOW, "deletes a file"),
    "os.rmdir": ("destructive_filesystem", RiskSeverity.LOW, "removes a directory"),
    "Path.unlink": ("destructive_filesystem", RiskSeverity.LOW, "deletes a file"),
}

# Textual fallback for non-Python files, where no AST is available.
_TEXT_PATTERNS: List[Tuple[str, str, str, "re.Pattern[str]"]] = [
    ("command_execution", RiskSeverity.HIGH, "executes a shell command",
     re.compile(r"\b(?:child_process\.exec|execSync|spawnSync|system|shell_exec|popen)\s*\(")),
    ("code_execution", RiskSeverity.HIGH, "evaluates code at runtime",
     re.compile(r"\b(?:eval|new\s+Function)\s*\(")),
    ("network_access", RiskSeverity.MEDIUM, "performs an outbound network request",
     re.compile(r"\b(?:fetch|XMLHttpRequest|curl|wget)\s*\(")),
]


def _attribute_path(node: ast.AST) -> Optional[str]:
    """Return the dotted source path of an attribute/name expression, if static."""
    parts: List[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    elif parts:
        # Receiver is a call/subscript/literal; keep only the attribute chain.
        pass
    else:
        return None
    return ".".join(reversed(parts))


def _python_call_signatures(source: str) -> Optional[List[Tuple[str, int, str]]]:
    """Collect (match_key, line_number, snippet) for every call in Python source.

    Returns None when the source cannot be parsed, so the caller can decide whether to
    fall back to textual scanning.
    """
    try:
        tree = ast.parse(source.lstrip("﻿"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None

    lines = source.splitlines()
    found: List[Tuple[str, int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line_number = getattr(node.func, "lineno", getattr(node, "lineno", 0)) or 0
        snippet = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""

        func = node.func
        if isinstance(func, ast.Name):
            found.append((func.id, line_number, snippet))
        else:
            path = _attribute_path(func)
            if path:
                found.append((path, line_number, snippet))
                # Also record the trailing two segments so 'a.b.subprocess.run' matches.
                segments = path.split(".")
                if len(segments) > 2:
                    found.append((".".join(segments[-2:]), line_number, snippet))

    # Dynamic imports expressed as statements rather than calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("subprocess", "pty"):
                    line_number = getattr(node, "lineno", 0) or 0
                    snippet = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else ""
                    found.append((f"import:{alias.name}", line_number, snippet))

    return found


def _match_signature(key: str) -> Optional[Tuple[str, str, str]]:
    """Map a collected call key to its (category, severity, message), if risky."""
    if key in _CODE_EXECUTION_BUILTINS:
        return ("code_execution", RiskSeverity.HIGH, _CODE_EXECUTION_BUILTINS[key])
    if key in _RISKY_CALLS:
        return _RISKY_CALLS[key]
    if key.startswith("import:"):
        module = key.split(":", 1)[1]
        return (
            "command_execution",
            RiskSeverity.MEDIUM,
            f"imports '{module}', which is used to execute external processes",
        )
    return None


def screen_python_change(original: str, proposed: str) -> List[RiskFinding]:
    """Report risky constructs that 'proposed' introduces and 'original' did not."""
    proposed_calls = _python_call_signatures(proposed)
    if proposed_calls is None:
        # Proposed content is not parseable; the fix engine rejects it separately.
        return []

    original_calls = _python_call_signatures(original)
    baseline: Set[str] = {key for key, _, _ in (original_calls or [])}

    findings: List[RiskFinding] = []
    reported: Set[Tuple[str, int]] = set()

    for key, line_number, snippet in proposed_calls:
        if key in baseline:
            continue  # already present before the patch; not introduced by it
        matched = _match_signature(key)
        if not matched:
            continue
        category, severity, message = matched
        dedupe_key = (key, line_number)
        if dedupe_key in reported:
            continue
        reported.add(dedupe_key)
        display = key.split(":", 1)[1] if key.startswith("import:") else key
        findings.append(
            RiskFinding(
                category=category,
                severity=severity,
                line_number=line_number,
                snippet=snippet[:200],
                message=f"'{display}' {message}",
            )
        )

    return findings


def screen_text_change(original: str, proposed: str) -> List[RiskFinding]:
    """Conservative textual screening for files without an available AST."""
    original_lines = set(original.splitlines())
    findings: List[RiskFinding] = []

    for index, line in enumerate(proposed.splitlines(), start=1):
        if line in original_lines:
            continue  # unchanged line
        for category, severity, message, pattern in _TEXT_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    RiskFinding(
                        category=category,
                        severity=severity,
                        line_number=index,
                        snippet=line.strip()[:200],
                        message=f"'{match.group(0).rstrip('( ')}' {message}",
                    )
                )
                break

    return findings


def screen_file_change(file_path: str, original: str, proposed: str) -> List[RiskFinding]:
    """Screen a single file's proposed content for newly introduced risky constructs.

    Args:
        file_path: Repository-relative path, used to select the screening strategy.
        original: Current file content.
        proposed: Candidate content produced by the model.

    Returns:
        Findings ordered by severity (HIGH first) and then by line number.
    """
    if original == proposed:
        return []

    if file_path.endswith((".py", ".pyi")):
        findings = screen_python_change(original, proposed)
    else:
        findings = screen_text_change(original, proposed)

    order = {RiskSeverity.HIGH.value: 0, RiskSeverity.MEDIUM.value: 1, RiskSeverity.LOW.value: 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.line_number))

    if findings:
        logger.warning(
            "Patch for '%s' introduces %d risky construct(s): %s",
            file_path,
            len(findings),
            ", ".join(sorted({f.category for f in findings})),
        )

    return findings


def summarize_risk(findings: List[RiskFinding]) -> str:
    """Build a one-line human summary of a set of findings."""
    if not findings:
        return "No risky constructs introduced."
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    parts = [f"{counts[s]} {s}" for s in ("HIGH", "MEDIUM", "LOW") if s in counts]
    return f"{len(findings)} risky construct(s) introduced ({', '.join(parts)})."
