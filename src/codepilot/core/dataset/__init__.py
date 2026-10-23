"""Dataset management, schema validation, and preparation for CodePilot AI training."""

from codepilot.core.dataset.builder import (
    build_dataset_from_raw,
    load_jsonl_dataset,
    save_jsonl_dataset,
    split_dataset,
)
from codepilot.core.dataset.schema import (
    DatasetExample,
    DatasetSplitSummary,
    DatasetValidationResult,
    TrainingContext,
)
from codepilot.core.dataset.validator import (
    DatasetValidator,
    validate_dataset_examples,
)

__all__ = [
    "DatasetExample",
    "DatasetSplitSummary",
    "DatasetValidationResult",
    "DatasetValidator",
    "TrainingContext",
    "build_dataset_from_raw",
    "load_jsonl_dataset",
    "save_jsonl_dataset",
    "split_dataset",
    "validate_dataset_examples",
]
