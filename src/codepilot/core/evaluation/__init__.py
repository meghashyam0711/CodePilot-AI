"""Base model evaluation framework and metrics for CodePilot AI."""

from codepilot.core.evaluation.benchmark import get_default_benchmark_cases
from codepilot.core.evaluation.models import (
    EvaluationCase,
    EvaluationItemResult,
    EvaluationReport,
)
from codepilot.core.evaluation.runner import (
    EvaluationRunner,
    evaluate_dataset,
)

__all__ = [
    "EvaluationCase",
    "EvaluationItemResult",
    "EvaluationReport",
    "EvaluationRunner",
    "evaluate_dataset",
    "get_default_benchmark_cases",
]

