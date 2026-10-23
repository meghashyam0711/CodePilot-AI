"""Script to prepare, validate, and stratify CodePilot AI training datasets."""

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root and src are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from codepilot.core.dataset.builder import (
    build_dataset_from_raw,
    compute_dataset_summary,
    save_jsonl_dataset,
    split_dataset,
)
from codepilot.core.dataset.validator import DatasetValidator


def prepare_datasets(
    input_file: Path,
    output_dir: Path,
    train_ratio: float = 0.65,
    val_ratio: float = 0.18,
    test_ratio: float = 0.17,
    seed: int = 42,
) -> int:
    """Validate raw examples, stratify into splits, and export to JSONL."""
    print(f"[*] Loading raw dataset from: {input_file}")
    with open(input_file, "r", encoding="utf-8") as f:
        raw_items = json.load(f)

    examples = build_dataset_from_raw(raw_items)
    print(f"[*] Loaded {len(examples)} examples.")

    # 1. Run quality validation
    validator = DatasetValidator(check_syntax=True, strict_licenses=True)
    val_result = validator.validate_dataset(examples)

    if not val_result.is_valid:
        print("[!] Validation errors detected:")
        for err in val_result.errors:
            print(f"    - {err}")
        return 1

    print(f"[+] Quality checks passed: {val_result.valid_count}/{val_result.total_examples} valid.")
    if val_result.warnings:
        print(f"[*] {len(val_result.warnings)} warning(s) noted:")
        for w in val_result.warnings[:5]:
            print(f"    ~ {w}")

    # 2. Stratify into splits
    splits = split_dataset(
        examples,
        train_ratio=train_ratio,
        val_ratio=val_ratio,
        test_ratio=test_ratio,
        random_seed=seed,
    )

    # 3. Re-validate stratified splits for zero leakage
    split_examples = splits["train"] + splits["validation"] + splits["test"]
    leakage_check = validator.validate_dataset(split_examples)
    if leakage_check.leakage_detected:
        print("[!] Data leakage detected between splits:")
        for err in leakage_check.errors:
            print(f"    - {err}")
        return 1

    # 4. Save JSONL splits
    output_dir.mkdir(parents=True, exist_ok=True)
    train_path = output_dir / "train.jsonl"
    val_path = output_dir / "validation.jsonl"
    test_path = output_dir / "test.jsonl"

    n_train = save_jsonl_dataset(splits["train"], train_path)
    n_val = save_jsonl_dataset(splits["validation"], val_path)
    n_test = save_jsonl_dataset(splits["test"], test_path)

    summary = compute_dataset_summary(split_examples)

    print("\n=======================================================")
    print("CodePilot AI — Dataset Preparation Summary")
    print("=======================================================")
    print(f"  Train split:       {n_train:>3} examples -> {train_path.name}")
    print(f"  Validation split:  {n_val:>3} examples -> {val_path.name}")
    print(f"  Test split:        {n_test:>3} examples -> {test_path.name}")
    print(f"  Total examples:    {summary.total_count:>3}")
    print(f"  Estimated tokens:  {summary.total_estimated_tokens:,} tokens")
    print("-------------------------------------------------------")
    print("  Category Breakdown:")
    for itype, count in sorted(summary.types_breakdown.items()):
        print(f"    - {itype:<15}: {count}")
    print("=======================================================\n")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare CodePilot AI training dataset")
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "training" / "data" / "seed_examples.json",
        help="Path to raw seed examples JSON file",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "training" / "data",
        help="Directory to save train.jsonl, validation.jsonl, and test.jsonl",
    )
    args = parser.parse_args()

    ret = prepare_datasets(args.input, args.output_dir)
    sys.exit(ret)


if __name__ == "__main__":
    main()
