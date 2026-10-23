"""Scanner engine for discovering AI instruction comments in source repositories.

Inspects source code files, identifies supported AI instruction comments,
and extracts contextual metadata while honoring protected files and directories.
"""

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

            for filename in filenames:
                # Skip hidden and dotfiles
                if filename.startswith("."):
                    continue

                file_path = current_dir / filename

                # Check extension against supported source code extensions
                if file_path.suffix.lower() not in SOURCE_EXTENSIONS:
                    continue

                # Check protected files pattern
                if is_protected_file(file_path, root, config.protected_files):
                    continue

                # Check max input size
                try:
                    if file_path.stat().st_size > config.max_input_size:
                        continue
                except OSError:
                    continue

                # Check binary file
                if is_binary_file(file_path):
                    continue


                scanned_count += 1
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
