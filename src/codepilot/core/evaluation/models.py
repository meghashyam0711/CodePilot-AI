"""Data models for base model evaluation fixtures and structured reporting."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from codepilot.core.dataset.schema import DatasetExample


@dataclass
class EvaluationCase:
    """A single evaluation benchmark fixture."""

    id: str
    instruction_type: str
    description: str
    prompt: str
    expected_output: str
    source_code: str = ""
    language: str = "python"
    validation_test: Optional[str] = None
    should_request_clarification: bool = False
    clarification_prompt: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dataset_example(cls, example: DatasetExample) -> "EvaluationCase":
        return cls(
            id=example.id,
            instruction_type=example.instruction_type,
            description=example.description,
            prompt=example.prompt,
            expected_output=example.expected_output,
            source_code=example.source_code,
            language=example.language,
            validation_test=example.validation_test,
            should_request_clarification=example.should_request_clarification,
            clarification_prompt=example.clarification_prompt,
            metadata=example.metadata,
        )


@dataclass
class EvaluationItemResult:
    """Outcome of evaluating a single test case against a model."""

    case_id: str
    instruction_type: str
    raw_completion: str
    extracted_code: str
    instruction_followed: bool
    syntax_valid: bool
    diff_hygiene_score: float  # 0.0 to 1.0 (1.0 = perfect minimal focused change)
    clarification_correct: bool
    test_passed: Optional[bool] = None
    latency_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationReport:
    """Aggregated report evaluating a coding model against a test suite."""

    model_id: str
    total_cases: int
    instruction_following_rate: float
    syntax_validity_rate: float
    diff_hygiene_average: float
    clarification_accuracy: float
    test_pass_rate: float
    average_latency_seconds: float
    total_tokens_used: int
    tokens_per_second: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    item_results: List[EvaluationItemResult] = field(default_factory=list)
    by_type_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "instruction_following_rate": round(self.instruction_following_rate, 4),
            "syntax_validity_rate": round(self.syntax_validity_rate, 4),
            "diff_hygiene_average": round(self.diff_hygiene_average, 4),
            "clarification_accuracy": round(self.clarification_accuracy, 4),
            "test_pass_rate": round(self.test_pass_rate, 4),
            "average_latency_seconds": round(self.average_latency_seconds, 3),
            "total_tokens_used": self.total_tokens_used,
            "tokens_per_second": round(self.tokens_per_second, 2),
            "by_type_breakdown": self.by_type_breakdown,
            "item_results": [r.to_dict() for r in self.item_results],
        }

    def to_markdown(self) -> str:
        """Render report as a markdown document for reviews and baselines."""
        md_lines = [
            f"# CodePilot AI — Base Model Evaluation Report",
            f"",
            f"- **Evaluated Model:** `{self.model_id}`",
            f"- **Timestamp:** {self.timestamp}",
            f"- **Total Benchmark Cases:** {self.total_cases}",
            f"",
            f"## 1. Overall Scorecard",
            f"",
            f"| Metric | Result | Target Benchmark | Status |",
            f"|---|---|---|---|",
            f"| **Instruction Following** | {self.instruction_following_rate * 100:.1f}% | $\\ge 85.0\\%$ | {'PASS' if self.instruction_following_rate >= 0.85 else 'NEEDS IMPROVEMENT'} |",
            f"| **Syntax Validity** | {self.syntax_validity_rate * 100:.1f}% | $\\ge 95.0\\%$ | {'PASS' if self.syntax_validity_rate >= 0.95 else 'NEEDS IMPROVEMENT'} |",
            f"| **Diff Hygiene Score** | {self.diff_hygiene_average * 100:.1f}% | $\\ge 75.0\\%$ | {'PASS' if self.diff_hygiene_average >= 0.75 else 'NEEDS IMPROVEMENT'} |",
            f"| **Clarification Accuracy** | {self.clarification_accuracy * 100:.1f}% | $\\ge 80.0\\%$ | {'PASS' if self.clarification_accuracy >= 0.80 else 'NEEDS IMPROVEMENT'} |",
            f"| **Validation Test Pass Rate** | {self.test_pass_rate * 100:.1f}% | $\\ge 70.0\\%$ | {'PASS' if self.test_pass_rate >= 0.70 else 'NEEDS IMPROVEMENT'} |",
            f"",
            f"## 2. Resource & Latency Performance",
            f"",
            f"- **Average Response Latency:** {self.average_latency_seconds:.2f} s",
            f"- **Inference Speed:** {self.tokens_per_second:.1f} tokens/s",
            f"- **Total Tokens Consumed:** {self.total_tokens_used:,} tokens",
            f"",
            f"## 3. Results by Instruction Category",
            f"",
            f"| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |",
            f"|---|---|---|---|---|---|",
        ]

        for cat, stats in sorted(self.by_type_breakdown.items()):
            md_lines.append(
                f"| `{cat}` | {stats.get('count', 0)} | "
                f"{stats.get('instruction_followed', 0) * 100:.1f}% | "
                f"{stats.get('syntax_valid', 0) * 100:.1f}% | "
                f"{stats.get('diff_hygiene', 0) * 100:.1f}% | "
                f"{stats.get('test_passed', 0) * 100:.1f}% |"
            )

        md_lines.extend([
            f"",
            f"## 4. Case-by-Case Breakdown",
            f"",
            f"| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |",
            f"|---|---|---|---|---|---|---|---|",
        ])

        for item in self.item_results:
            test_str = "N/A" if item.test_passed is None else ("PASS" if item.test_passed else "FAIL")
            md_lines.append(
                f"| `{item.case_id}` | `{item.instruction_type}` | "
                f"{'OK' if item.instruction_followed else 'NO'} | "
                f"{'OK' if item.syntax_valid else 'FAIL'} | "
                f"{item.diff_hygiene_score * 100:.0f}% | "
                f"{'OK' if item.clarification_correct else 'FAIL'} | "
                f"{test_str} | "
                f"{item.latency_seconds:.2f}s |"
            )

        return "\n".join(md_lines)
