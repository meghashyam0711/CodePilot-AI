"""Data models for Project Context Engine."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from codepilot.core.parser.models import InstructionTask


@dataclass
class CodeSnippet:
    """Represents an extracted, line-numbered snippet of source code."""

    file_path: str
    start_line: int
    end_line: int
    content: str
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None

    def format_line_numbered(self) -> str:
        """Format snippet content with 1-indexed line numbers."""
        lines = self.content.splitlines()
        formatted: List[str] = []
        for i, line in enumerate(lines, start=self.start_line):
            formatted.append(f"Line {i:5d}: {line}")
        return "\n".join(formatted)


@dataclass
class RelatedContext:
    """Context from related files (imports, tests, callers)."""

    file_path: str
    relationship: str  # 'import', 'test', 'caller', 'dependency'
    summary: str
    snippet: Optional[str] = None


@dataclass
class ProjectContextBundle:
    """Structured context gathered for a specific instruction task."""

    task: InstructionTask
    target_snippet: Optional[CodeSnippet] = None
    imports: List[str] = field(default_factory=list)
    related_contexts: List[RelatedContext] = field(default_factory=list)
    architecture_summary: Optional[str] = None
    project_type: str = "Unknown"
    estimated_tokens: int = 0
    is_truncated: bool = False
    is_safe: bool = True
    safety_warning: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert bundle to serializable dictionary."""
        return {
            "task": self.task.to_dict(),
            "target_snippet": asdict(self.target_snippet) if self.target_snippet else None,
            "imports": self.imports,
            "related_contexts": [asdict(r) for r in self.related_contexts],
            "architecture_summary": self.architecture_summary,
            "project_type": self.project_type,
            "estimated_tokens": self.estimated_tokens,
            "is_truncated": self.is_truncated,
            "is_safe": self.is_safe,
            "safety_warning": self.safety_warning,
            "warnings": self.warnings,
        }
