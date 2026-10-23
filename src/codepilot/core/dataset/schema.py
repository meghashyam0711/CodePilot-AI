"""Data models and schemas for CodePilot AI training datasets."""

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any, Dict, List, Optional

from codepilot.core.parser.models import SUPPORTED_INSTRUCTION_TYPES


@dataclass
class TrainingContext:
    """Project and file context accompanying an instruction example."""

    file_path: str
    line_number: int
    target_symbol: Optional[str] = None
    target_symbol_type: Optional[str] = None
    imports: List[str] = field(default_factory=list)
    related_tests: List[str] = field(default_factory=list)
    architecture_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "target_symbol": self.target_symbol,
            "target_symbol_type": self.target_symbol_type,
            "imports": self.imports,
            "related_tests": self.related_tests,
            "architecture_summary": self.architecture_summary,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrainingContext":
        return cls(
            file_path=data.get("file_path", ""),
            line_number=data.get("line_number", 1),
            target_symbol=data.get("target_symbol"),
            target_symbol_type=data.get("target_symbol_type"),
            imports=data.get("imports", []),
            related_tests=data.get("related_tests", []),
            architecture_summary=data.get("architecture_summary", ""),
        )


@dataclass
class DatasetExample:
    """A single training, validation, or test example for local model evaluation and fine-tuning."""

    instruction_type: str
    description: str
    source_code: str
    expected_output: str
    language: str = "python"
    id: str = ""
    file_path: str = "example.py"
    line_number: int = 1
    target_symbol: Optional[str] = None
    target_symbol_type: Optional[str] = None
    context: Optional[TrainingContext] = None
    prompt: str = ""
    expected_behavior: str = ""
    validation_test: Optional[str] = None
    should_request_clarification: bool = False
    clarification_prompt: Optional[str] = None
    split: str = "train"  # 'train', 'validation', 'test'
    source: str = "codepilot-curated"
    license: str = "MIT"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Generate deterministic ID and prompt if not provided."""
        if not self.id:
            hash_src = f"{self.instruction_type}:{self.file_path}:{self.line_number}:{self.description}:{self.source_code[:100]}".encode(
                "utf-8"
            )
            self.id = f"ds-{hashlib.sha256(hash_src).hexdigest()[:12]}"

        if not self.prompt:
            self.prompt = self._generate_default_prompt()

    def _generate_default_prompt(self) -> str:
        """Construct standard prompt adhering to prompt template v1.0.0."""
        from codepilot.core.context.prompt import PROMPT_TEMPLATE_VERSION

        lines = [
            f"### CodePilot AI Task Request (Template v{PROMPT_TEMPLATE_VERSION})",
            f"- Task ID: {self.id}",
            f"- Instruction Type: {self.instruction_type}",
            f"- Target File: {self.file_path} (Line {self.line_number})",
            f"- Target Symbol: {self.target_symbol or 'Module Scope'} ({self.target_symbol_type or 'module'})",
            f"- Developer Request: {self.description}",
            "",
            "### Project Architecture & Environment",
            f"- Project Summary: {self.context.architecture_summary if self.context else 'Standard repository'}",
        ]

        if self.context and self.context.imports:
            lines.append("- File Imports:")
            for imp in self.context.imports[:8]:
                lines.append(f"    {imp}")

        if self.context and self.context.related_tests:
            lines.append("- Related Contexts:")
            for test in self.context.related_tests:
                lines.append(f"    [TEST] {test}")

        lines.extend([
            "",
            "### Source Code to Modify",
            "<untrusted_repository_context>",
        ])

        # Number lines for realistic prompt simulation
        code_lines = self.source_code.splitlines()
        for i, code_line in enumerate(code_lines, start=self.line_number):
            lines.append(f"Line {i:3d}: {code_line}")

        lines.extend([
            "</untrusted_repository_context>",
            "",
            "### Required Output Format",
            "Please provide your response in the following clear sections:",
            "1. **Summary of Intent**: Briefly explain what needs to be changed and why.",
            "2. **Proposed Code Modification**: Provide the exact modified code snippet or diff, referencing line numbers.",
            "3. **Assumptions & Risks**: List any assumptions made and potential edge cases or risks.",
        ])

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize example to dictionary suitable for JSONL."""
        return {
            "id": self.id,
            "instruction_type": self.instruction_type,
            "description": self.description,
            "language": self.language,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "target_symbol": self.target_symbol,
            "target_symbol_type": self.target_symbol_type,
            "source_code": self.source_code,
            "context": self.context.to_dict() if self.context else None,
            "prompt": self.prompt,
            "expected_output": self.expected_output,
            "expected_behavior": self.expected_behavior,
            "validation_test": self.validation_test,
            "should_request_clarification": self.should_request_clarification,
            "clarification_prompt": self.clarification_prompt,
            "split": self.split,
            "source": self.source,
            "license": self.license,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DatasetExample":
        """Deserialize from dictionary."""
        context_data = data.get("context")
        context = TrainingContext.from_dict(context_data) if context_data else None

        return cls(
            id=data.get("id", ""),
            instruction_type=data.get("instruction_type", "AI-TASK"),
            description=data.get("description", ""),
            language=data.get("language", "python"),
            file_path=data.get("file_path", "example.py"),
            line_number=data.get("line_number", 1),
            target_symbol=data.get("target_symbol"),
            target_symbol_type=data.get("target_symbol_type"),
            source_code=data.get("source_code", ""),
            context=context,
            prompt=data.get("prompt", ""),
            expected_output=data.get("expected_output", ""),
            expected_behavior=data.get("expected_behavior", ""),
            validation_test=data.get("validation_test"),
            should_request_clarification=data.get("should_request_clarification", False),
            clarification_prompt=data.get("clarification_prompt"),
            split=data.get("split", "train"),
            source=data.get("source", "codepilot-curated"),
            license=data.get("license", "MIT"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class DatasetValidationResult:
    """Outcome of dataset quality and safety validation checks."""

    is_valid: bool
    total_examples: int
    valid_count: int
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    counts_by_type: Dict[str, int] = field(default_factory=dict)
    counts_by_split: Dict[str, int] = field(default_factory=dict)
    leakage_detected: bool = False
    duplicate_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "total_examples": self.total_examples,
            "valid_count": self.valid_count,
            "errors": self.errors,
            "warnings": self.warnings,
            "counts_by_type": self.counts_by_type,
            "counts_by_split": self.counts_by_split,
            "leakage_detected": self.leakage_detected,
            "duplicate_count": self.duplicate_count,
        }


@dataclass
class DatasetSplitSummary:
    """Statistical summary of dataset splits."""

    train_count: int = 0
    validation_count: int = 0
    test_count: int = 0
    total_count: int = 0
    types_breakdown: Dict[str, int] = field(default_factory=dict)
    total_estimated_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "train_count": self.train_count,
            "validation_count": self.validation_count,
            "test_count": self.test_count,
            "total_count": self.total_count,
            "types_breakdown": self.types_breakdown,
            "total_estimated_tokens": self.total_estimated_tokens,
        }
