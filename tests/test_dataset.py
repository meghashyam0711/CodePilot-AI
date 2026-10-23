"""Comprehensive tests for dataset schemas, quality validation, and stratification."""

import json
from pathlib import Path
import pytest

from codepilot.core.dataset.builder import (
    build_dataset_from_raw,
    compute_dataset_summary,
    load_jsonl_dataset,
    save_jsonl_dataset,
    split_dataset,
)
from codepilot.core.dataset.schema import (
    DatasetExample,
    TrainingContext,
)
from codepilot.core.dataset.validator import (
    DatasetValidator,
    validate_dataset_examples,
)


def test_dataset_example_initialization() -> None:
    """Verify DatasetExample default ID generation, prompt construction, and serialization."""
    example = DatasetExample(
        instruction_type="AI-FIX",
        description="Handle division by zero safely.",
        source_code="def div(a, b):\n    return a / b",
        expected_output="def div(a, b):\n    if b == 0:\n        return 0.0\n    return a / b",
        context=TrainingContext(
            file_path="math.py",
            line_number=1,
            target_symbol="div",
            imports=["import sys"],
        ),
    )

    assert example.id.startswith("ds-")
    assert "<untrusted_repository_context>" in example.prompt
    assert "Line   1: def div(a, b):" in example.prompt
    assert "</untrusted_repository_context>" in example.prompt

    d = example.to_dict()
    assert d["instruction_type"] == "AI-FIX"
    assert d["context"]["file_path"] == "math.py"

    restored = DatasetExample.from_dict(d)
    assert restored.id == example.id
    assert restored.description == example.description
    assert restored.context.target_symbol == "div"


def test_validator_detects_secrets() -> None:
    """Verify validator flags API keys, tokens, and private keys."""
    validator = DatasetValidator()

    # Clean example
    clean = DatasetExample(
        instruction_type="AI-TASK",
        description="Clean task",
        source_code="def hello(): return 'world'",
        expected_output="def hello(): return 'hello world'",
    )
    errors, _ = validator.validate_example(clean)
    assert len(errors) == 0

    # Injected OpenAI key
    leaky_openai = DatasetExample(
        instruction_type="AI-TASK",
        description="Leaky task",
        source_code="api_key = 'sk-1234567890abcdef1234567890abcdef'",
        expected_output="def run(): pass",
    )
    errors, _ = validator.validate_example(leaky_openai)
    assert any("OpenAI" in err or "credential" in err for err in errors)

    # Injected private key
    leaky_key = DatasetExample(
        instruction_type="AI-TASK",
        description="Key task",
        source_code="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...",
        expected_output="pass",
    )
    errors, _ = validator.validate_example(leaky_key)
    assert any("Private cryptographic key" in err for err in errors)


def test_validator_detects_syntax_errors() -> None:
    """Verify validator catches syntax errors in Python snippets."""
    validator = DatasetValidator(check_syntax=True)

    bad_syntax = DatasetExample(
        instruction_type="AI-FIX",
        description="Bad syntax",
        source_code="def broken_func(:\n    return 1",
        expected_output="def fixed(): return 1",
    )
    errors, _ = validator.validate_example(bad_syntax)
    assert any("syntax error in source_code" in err.lower() for err in errors)


def test_validator_detects_data_leakage() -> None:
    """Verify validator catches code or task leakage between train and test splits."""
    validator = DatasetValidator()

    code = "def compute(x):\n    return x * 2"
    ex_train = DatasetExample(
        id="ex-1",
        instruction_type="AI-TASK",
        description="Double the number",
        source_code=code,
        expected_output="def compute(x): return x * 2",
        split="train",
    )
    ex_test = DatasetExample(
        id="ex-2",
        instruction_type="AI-TASK",
        description="Double the number",
        source_code=code,
        expected_output="def compute(x): return x * 2",
        split="test",
    )

    res = validator.validate_dataset([ex_train, ex_test])
    assert res.leakage_detected is True
    assert any("Data leakage detected" in err for err in res.errors)


def test_stratified_dataset_splitting() -> None:
    """Verify stratified splitting proportions and zero split overlap."""
    examples = []
    types = ["AI-FIX", "AI-TASK", "AI-REFACTOR", "AI-TEST", "AI-DOCS"]

    for i in range(20):
        itype = types[i % len(types)]
        examples.append(
            DatasetExample(
                id=f"ex-{i}",
                instruction_type=itype,
                description=f"Task {i}",
                source_code=f"def func_{i}():\n    return {i}",
                expected_output=f"def func_{i}():\n    return {i} + 1",
            )
        )

    splits = split_dataset(examples, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2, random_seed=42)

    train_ids = {ex.id for ex in splits["train"]}
    val_ids = {ex.id for ex in splits["validation"]}
    test_ids = {ex.id for ex in splits["test"]}

    # Ensure zero overlap
    assert len(train_ids.intersection(val_ids)) == 0
    assert len(train_ids.intersection(test_ids)) == 0
    assert len(val_ids.intersection(test_ids)) == 0
    assert len(train_ids) + len(val_ids) + len(test_ids) == 20

    # Ensure every category is in train
    train_types = {ex.instruction_type for ex in splits["train"]}
    assert len(train_types) == 5


def test_curated_seed_dataset_file(tmp_path: Path) -> None:
    """Load the actual seed_examples.json and verify all quality checks pass."""
    seed_file = Path("training/data/seed_examples.json")
    assert seed_file.exists(), "seed_examples.json must exist"

    with open(seed_file, "r", encoding="utf-8") as f:
        raw_items = json.load(f)

    assert len(raw_items) >= 15, "Curated dataset must contain at least 15 examples"
    examples = build_dataset_from_raw(raw_items)

    validator = DatasetValidator(check_syntax=True, strict_licenses=True)
    res = validator.validate_dataset(examples)

    assert res.is_valid is True, f"Seed dataset validation errors: {res.errors}"
    assert res.total_examples == len(examples)
    assert res.leakage_detected is False

    # Test saving and loading JSONL
    jsonl_path = tmp_path / "test_out.jsonl"
    count = save_jsonl_dataset(examples, jsonl_path)
    assert count == len(examples)

    loaded = load_jsonl_dataset(jsonl_path)
    assert len(loaded) == len(examples)
    assert loaded[0].id == examples[0].id
