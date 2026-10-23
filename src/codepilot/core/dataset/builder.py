"""Dataset loading, stratification, JSONL serialization, and summary statistics."""

from collections import defaultdict
import json
from pathlib import Path
import random
from typing import Any, Dict, List

from codepilot.core.dataset.schema import (
    DatasetExample,
    DatasetSplitSummary,
)


def load_jsonl_dataset(path: Path) -> List[DatasetExample]:
    """Read dataset examples from a JSONL file."""
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    examples: List[DatasetExample] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            try:
                data = json.loads(line_str)
                examples.append(DatasetExample.from_dict(data))
            except json.JSONDecodeError as e:
                raise ValueError(f"Malformed JSON on line {line_no} of {path}: {e}")

    return examples


def save_jsonl_dataset(examples: List[DatasetExample], path: Path) -> int:
    """Save dataset examples to a JSONL file. Parent directories are created if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for ex in examples:
            line = json.dumps(ex.to_dict(), ensure_ascii=False)
            f.write(line + "\n")
            count += 1
    return count


def build_dataset_from_raw(raw_items: List[Dict[str, Any]]) -> List[DatasetExample]:
    """Convert raw dictionary objects into strongly-typed DatasetExample instances."""
    examples: List[DatasetExample] = []
    for item in raw_items:
        examples.append(DatasetExample.from_dict(item))
    return examples


def split_dataset(
    examples: List[DatasetExample],
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    random_seed: int = 42,
) -> Dict[str, List[DatasetExample]]:
    """Stratify examples across train, validation, and test splits by instruction_type.
    
    Guarantees no example appears in more than one split (zero leakage).
    """
    if abs((train_ratio + val_ratio + test_ratio) - 1.0) > 1e-4:
        raise ValueError(
            f"Ratios must sum to 1.0. Given: {train_ratio} + {val_ratio} + {test_ratio} = {train_ratio + val_ratio + test_ratio}"
        )

    # Group examples by instruction_type for stratified distribution
    by_type: Dict[str, List[DatasetExample]] = defaultdict(list)
    for ex in examples:
        by_type[ex.instruction_type].append(ex)

    rng = random.Random(random_seed)
    train_list: List[DatasetExample] = []
    val_list: List[DatasetExample] = []
    test_list: List[DatasetExample] = []

    for itype, group in sorted(by_type.items()):
        # Copy and shuffle group deterministically
        group_shuffled = list(group)
        rng.shuffle(group_shuffled)

        n = len(group_shuffled)
        if n == 1:
            # Single example goes to train
            train_list.append(group_shuffled[0])
            continue
        elif n == 2:
            train_list.append(group_shuffled[0])
            val_list.append(group_shuffled[1])
            continue

        n_train = max(1, int(round(n * train_ratio)))
        n_val = max(1, int(round(n * val_ratio)))
        # Remainder goes to test
        n_test = n - (n_train + n_val)
        if n_test < 1 and n >= 3:
            # Ensure at least 1 in test if n >= 3
            if n_train > 1:
                n_train -= 1
            elif n_val > 1:
                n_val -= 1
            n_test = n - (n_train + n_val)

        train_part = group_shuffled[:n_train]
        val_part = group_shuffled[n_train : n_train + n_val]
        test_part = group_shuffled[n_train + n_val :]

        for ex in train_part:
            ex.split = "train"
            train_list.append(ex)
        for ex in val_part:
            ex.split = "validation"
            val_list.append(ex)
        for ex in test_part:
            ex.split = "test"
            test_list.append(ex)

    return {
        "train": train_list,
        "validation": val_list,
        "test": test_list,
    }


def compute_dataset_summary(examples: List[DatasetExample]) -> DatasetSplitSummary:
    """Compute statistical breakdown of a dataset collection."""
    summary = DatasetSplitSummary()
    summary.total_count = len(examples)
    types_count: Dict[str, int] = defaultdict(int)
    total_chars = 0

    for ex in examples:
        types_count[ex.instruction_type] += 1
        if ex.split == "train":
            summary.train_count += 1
        elif ex.split == "validation":
            summary.validation_count += 1
        elif ex.split == "test":
            summary.test_count += 1

        total_chars += len(ex.prompt) + len(ex.expected_output)

    summary.types_breakdown = dict(types_count)
    # Estimate ~4 characters per token
    summary.total_estimated_tokens = total_chars // 4

    return summary
