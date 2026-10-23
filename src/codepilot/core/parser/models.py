"""Data models for AI instruction comment parsing and structured task representations."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


class InstructionType(str, Enum):
    """Supported standard instruction types."""

    FIX = "AI-FIX"
    TASK = "AI-TASK"
    REFACTOR = "AI-REFACTOR"
    TEST = "AI-TEST"
    DOCS = "AI-DOCS"

    # Legacy / alias compatibility
    TODO = "AI-TODO"
    AI = "AI"
    CODEPILOT = "CODEPILOT"


# Canonical supported types for Phase 3
SUPPORTED_INSTRUCTION_TYPES: Set[str] = {
    InstructionType.FIX.value,
    InstructionType.TASK.value,
    InstructionType.REFACTOR.value,
    InstructionType.TEST.value,
    InstructionType.DOCS.value,
}

# Type aliases / normalization mapping
INSTRUCTION_TYPE_ALIASES: Dict[str, str] = {
    "FIX": InstructionType.FIX.value,
    "AI-FIX": InstructionType.FIX.value,
    "TASK": InstructionType.TASK.value,
    "AI-TASK": InstructionType.TASK.value,
    "REFACTOR": InstructionType.REFACTOR.value,
    "AI-REFACTOR": InstructionType.REFACTOR.value,
    "TEST": InstructionType.TEST.value,
    "AI-TEST": InstructionType.TEST.value,
    "DOCS": InstructionType.DOCS.value,
    "AI-DOCS": InstructionType.DOCS.value,
    "DOC": InstructionType.DOCS.value,
    "AI-DOC": InstructionType.DOCS.value,
    # Compatibility aliases
    "TODO": InstructionType.TASK.value,
    "AI-TODO": InstructionType.TASK.value,
    "AI": InstructionType.TASK.value,
    "CODEPILOT": InstructionType.TASK.value,
}


@dataclass
class RawInstructionMatch:
    """Internal representation of a detected raw instruction comment."""

    line_number: int
    raw_type: str
    description: str
    raw_comment: str
    continuation_lines: List[str] = field(default_factory=list)
    is_malformed: bool = False
    warning: Optional[str] = None


@dataclass
class InstructionTask:
    """Represents a discovered and structured CodePilot AI instruction task.

    Corresponds to:
        InstructionTask(
            instruction_type="AI-FIX",
            description="Handle empty lists safely.",
            file_path="src/math_utils.py",
            line_number=2,
            target_symbol="calculate_average"
        )
    """

    instruction_type: str
    description: str
    file_path: str
    line_number: int
    id: str = ""
    target_symbol: Optional[str] = None
    target_symbol_type: Optional[str] = None  # 'function', 'method', 'class', 'module'
    raw_comment: str = ""
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)
    multiline_lines: List[str] = field(default_factory=list)
    confidence: str = "high"  # 'high' (AST-resolved), 'medium', 'heuristic'
    is_malformed: bool = False
    warning: Optional[str] = None

    def __post_init__(self) -> None:
        """Ensure task has a deterministic ID if not provided."""
        if not self.id:
            import hashlib

            hash_src = f"{self.file_path}:{self.line_number}:{self.instruction_type}:{self.description}".encode(
                "utf-8"
            )
            self.id = f"task-{hashlib.sha256(hash_src).hexdigest()[:10]}"

    @property
    def prompt(self) -> str:
        """Alias for description for backwards compatibility with scanner/planner."""
        return self.description

    def to_dict(self) -> Dict[str, Any]:
        """Serialize instruction task to dictionary."""
        return {
            "id": self.id,
            "instruction_type": self.instruction_type,
            "description": self.description,
            "prompt": self.description,  # compatibility alias
            "file_path": self.file_path,
            "line_number": self.line_number,
            "target_symbol": self.target_symbol,
            "target_symbol_type": self.target_symbol_type,
            "raw_comment": self.raw_comment,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "multiline_lines": self.multiline_lines,
            "confidence": self.confidence,
            "is_malformed": self.is_malformed,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InstructionTask":
        """Deserialize from dictionary."""
        # Handle 'prompt' vs 'description'
        description = data.get("description") or data.get("prompt", "")
        return cls(
            id=data.get("id", ""),
            instruction_type=data.get("instruction_type", "AI-TASK"),
            description=description,
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 1),
            target_symbol=data.get("target_symbol"),
            target_symbol_type=data.get("target_symbol_type"),
            raw_comment=data.get("raw_comment", ""),
            context_before=data.get("context_before", []),
            context_after=data.get("context_after", []),
            multiline_lines=data.get("multiline_lines", []),
            confidence=data.get("confidence", "high"),
            is_malformed=data.get("is_malformed", False),
            warning=data.get("warning"),
        )


@dataclass
class ParseResult:
    """Aggregated outcome of AI instruction comment parsing across a repository."""

    project_root: str
    scanned_files_count: int
    tasks: List[InstructionTask] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    counts_by_type: Dict[str, int] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def instructions(self) -> List[InstructionTask]:
        """Backwards compatibility alias for tasks."""
        return self.tasks

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_root": self.project_root,
            "scanned_files_count": self.scanned_files_count,
            "total_tasks": self.total_tasks,
            "counts_by_type": self.counts_by_type,
            "warnings": self.warnings,
            "tasks": [t.to_dict() for t in self.tasks],
            "instructions": [t.to_dict() for t in self.tasks],  # compatibility alias
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParseResult":
        raw_tasks = data.get("tasks") or data.get("instructions", [])
        tasks = [InstructionTask.from_dict(t) for t in raw_tasks]
        return cls(
            project_root=data.get("project_root", ""),
            scanned_files_count=data.get("scanned_files_count", 0),
            tasks=tasks,
            warnings=data.get("warnings", []),
            counts_by_type=data.get("counts_by_type", {}),
            timestamp=data.get("timestamp", ""),
        )
