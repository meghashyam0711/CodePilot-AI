"""Quality assurance, security scanning, and data leakage detection for datasets."""

import ast
from collections import defaultdict
import hashlib
import re
from typing import Dict, List, Set, Tuple

from codepilot.core.dataset.schema import (
    DatasetExample,
    DatasetValidationResult,
)
from codepilot.core.parser.models import SUPPORTED_INSTRUCTION_TYPES

# Approved permissive open source licenses for dataset examples
PERMISSIVE_LICENSES: Set[str] = {
    "MIT",
    "APACHE-2.0",
    "APACHE 2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "CC0-1.0",
    "CC-BY-4.0",
    "UNLICENSE",
}

# Regex signatures for secret and credential leakage detection
SECRET_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{24,}", "OpenAI API Key signature"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "Private cryptographic key"),
    (r"(?:bearer|authorization):\s*['\"][a-zA-Z0-9_\-\.]{32,}['\"]", "Bearer / Auth Token"),
]


class DatasetValidator:
    """Comprehensive validator enforcing dataset quality, safety, and hygiene."""

    def __init__(self, check_syntax: bool = True, strict_licenses: bool = True) -> None:
        self.check_syntax = check_syntax
        self.strict_licenses = strict_licenses

    def validate_example(self, example: DatasetExample) -> Tuple[List[str], List[str]]:
        """Validate an individual example for schema, security, syntax, and prompt hygiene.
        
        Returns:
            Tuple of (errors, warnings)
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Required field checks
        if not example.id:
            errors.append("Example missing unique ID.")

        if example.instruction_type not in SUPPORTED_INSTRUCTION_TYPES:
            errors.append(
                f"Invalid instruction_type '{example.instruction_type}'. "
                f"Must be one of {sorted(list(SUPPORTED_INSTRUCTION_TYPES))}."
            )

        if not example.description.strip():
            errors.append("Empty instruction description.")

        if not example.source_code.strip():
            errors.append("Empty source_code snippet.")

        if not example.expected_output.strip():
            errors.append("Empty expected_output.")

        if example.split not in {"train", "validation", "test"}:
            errors.append(f"Invalid split '{example.split}'. Must be 'train', 'validation', or 'test'.")

        # 2. License verification
        if self.strict_licenses:
            norm_license = example.license.strip().upper()
            if norm_license not in PERMISSIVE_LICENSES:
                errors.append(
                    f"Non-permissive or unrecognized license '{example.license}'. "
                    f"Allowed permissive licenses: {sorted(list(PERMISSIVE_LICENSES))}."
                )

        # 3. Secret and credential scanning
        combined_text = f"{example.source_code}\n{example.expected_output}\n{example.prompt}\n{example.validation_test or ''}"
        for pattern, desc in SECRET_PATTERNS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                errors.append(f"Security check failed: Detected potential credential leak ({desc}).")

        # 4. Clarification consistency
        if example.should_request_clarification:
            if not example.clarification_prompt or not example.clarification_prompt.strip():
                errors.append("Example marked should_request_clarification=True but missing clarification_prompt.")

        # 5. Prompt format compliance
        if example.prompt:
            if "<untrusted_repository_context>" not in example.prompt or "</untrusted_repository_context>" not in example.prompt:
                errors.append("Prompt missing mandatory <untrusted_repository_context> boundary tags.")

        # 6. Syntax validation for Python
        if self.check_syntax and example.language.lower() == "python":
            # Check source_code
            try:
                ast.parse(example.source_code)
            except SyntaxError as e:
                # If it's a snippet with indentation, attempt normalized dedent
                try:
                    import textwrap
                    ast.parse(textwrap.dedent(example.source_code))
                except SyntaxError:
                    errors.append(f"Python syntax error in source_code: {e.msg} (line {e.lineno}).")

            # Check validation_test if provided
            if example.validation_test and example.validation_test.strip():
                try:
                    ast.parse(example.validation_test)
                except SyntaxError as e:
                    errors.append(f"Python syntax error in validation_test: {e.msg} (line {e.lineno}).")

            # Check expected_output if not a diff
            if example.expected_output and not example.expected_output.startswith("---") and not example.expected_output.startswith("diff"):
                try:
                    import textwrap
                    ast.parse(textwrap.dedent(example.expected_output))
                except SyntaxError:
                    warnings.append("expected_output is not valid standalone Python code (could be partial snippet or text).")

        return errors, warnings

    def validate_dataset(self, examples: List[DatasetExample]) -> DatasetValidationResult:
        """Validate an entire collection of examples for consistency, duplicates, and split leakage."""
        all_errors: List[str] = []
        all_warnings: List[str] = []
        counts_by_type: Dict[str, int] = defaultdict(int)
        counts_by_split: Dict[str, int] = defaultdict(int)

        seen_ids: Set[str] = set()
        seen_hashes: Dict[str, str] = {}  # hash -> id
        split_code_hashes: Dict[str, Set[str]] = defaultdict(set)
        duplicate_count = 0
        valid_count = 0

        for ex in examples:
            counts_by_type[ex.instruction_type] += 1
            counts_by_split[ex.split] += 1

            # Check duplicate IDs
            if ex.id in seen_ids:
                all_errors.append(f"Duplicate example ID: {ex.id}")
            seen_ids.add(ex.id)

            # Check per-example validity
            ex_errors, ex_warnings = self.validate_example(ex)
            if ex_errors:
                for err in ex_errors:
                    all_errors.append(f"[{ex.id}] {err}")
            else:
                valid_count += 1

            for warn in ex_warnings:
                all_warnings.append(f"[{ex.id}] {warn}")

            # Duplicate content check
            normalized_content = self._normalize_for_hashing(ex.source_code, ex.description)
            content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()

            if content_hash in seen_hashes:
                duplicate_count += 1
                all_warnings.append(
                    f"[{ex.id}] Duplicate or near-duplicate of example {seen_hashes[content_hash]}."
                )
            else:
                seen_hashes[content_hash] = ex.id

            # Track hash by split for leakage detection
            split_code_hashes[ex.split].add(content_hash)

        # Data leakage check between splits
        leakage_detected = False
        train_hashes = split_code_hashes.get("train", set())
        val_hashes = split_code_hashes.get("validation", set())
        test_hashes = split_code_hashes.get("test", set())

        train_val_overlap = train_hashes.intersection(val_hashes)
        if train_val_overlap:
            leakage_detected = True
            all_errors.append(
                f"Data leakage detected: {len(train_val_overlap)} examples overlap between 'train' and 'validation' splits."
            )

        train_test_overlap = train_hashes.intersection(test_hashes)
        if train_test_overlap:
            leakage_detected = True
            all_errors.append(
                f"Data leakage detected: {len(train_test_overlap)} examples overlap between 'train' and 'test' splits."
            )

        val_test_overlap = val_hashes.intersection(test_hashes)
        if val_test_overlap:
            leakage_detected = True
            all_errors.append(
                f"Data leakage detected: {len(val_test_overlap)} examples overlap between 'validation' and 'test' splits."
            )

        is_valid = len(all_errors) == 0 and not leakage_detected

        return DatasetValidationResult(
            is_valid=is_valid,
            total_examples=len(examples),
            valid_count=valid_count,
            errors=all_errors,
            warnings=all_warnings,
            counts_by_type=dict(counts_by_type),
            counts_by_split=dict(counts_by_split),
            leakage_detected=leakage_detected,
            duplicate_count=duplicate_count,
        )

    def _normalize_for_hashing(self, code: str, description: str) -> str:
        """Strip whitespace and comments for robust duplicate checking."""
        clean_code = "".join(code.split())
        clean_desc = "".join(description.lower().split())
        return f"{clean_desc}::{clean_code}"


def validate_dataset_examples(examples: List[DatasetExample]) -> DatasetValidationResult:
    """Convenience helper to validate a list of DatasetExample objects."""
    validator = DatasetValidator()
    return validator.validate_dataset(examples)
