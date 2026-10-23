"""Comprehensive tests for the 'codepilot evaluate' CLI command."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.core.dataset.schema import DatasetExample
from codepilot.core.evaluation.benchmark import get_default_benchmark_cases
from codepilot.core.models.base import ModelRequest, ModelResponse
from codepilot.core.models.mock_client import MockModelClient


def test_evaluate_command_registered_and_help(cli_runner: CliRunner) -> None:
    """Verify that 'codepilot evaluate --help' displays usage and all supported options."""
    result = cli_runner.invoke(app, ["evaluate", "--help"])
    assert result.exit_code == 0
    assert "Usage: codepilot evaluate" in result.output
    for opt in [
        "--directory",
        "-d",
        "--provider",
        "-p",
        "--model",
        "-m",
        "--dataset",
        "--output",
        "-o",
        "--json",
        "--strict",
    ]:
        assert opt in result.output


def test_benchmark_module_returns_eight_canonical_cases() -> None:
    """Verify that get_default_benchmark_cases() returns 8 distinct, well-formed cases."""
    cases = get_default_benchmark_cases()
    assert len(cases) == 8

    # Verify all 8 canonical categories are covered
    categories = {c.metadata.get("category") for c in cases}
    expected_categories = {
        "basic_function_implementation",
        "bug_fixing",
        "missing_return_statements",
        "boundary_conditions",
        "existing_function_body_modification",
        "malformed_or_empty_model_output",
        "unclear_or_conflicting_instructions",
        "security_sensitive_and_unauthorized_changes",
    }
    assert categories == expected_categories

    # Verify unique IDs
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))

    # Verify fields are non-empty
    for c in cases:
        assert c.prompt.strip()
        assert c.expected_output.strip()
        assert c.instruction_type in ("AI-TASK", "AI-FIX")

    # Category 7 should expect clarification
    cat7 = next(c for c in cases if c.metadata.get("category") == "unclear_or_conflicting_instructions")
    assert cat7.should_request_clarification is True
    assert cat7.validation_test is None


def test_evaluate_mock_provider_terminal(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot evaluate --provider mock' renders terminal scorecard."""
    result = cli_runner.invoke(app, ["evaluate", "--directory", str(tmp_path), "--provider", "mock"])
    assert result.exit_code == 0
    assert "Base Model Evaluation Scorecard" in result.output
    assert "mock-qwen2.5-coder:1.5b" in result.output
    assert "Benchmark Test Cases: 8" in result.output
    assert "Overall Benchmark Scorecard" in result.output
    assert "Instruction Following" in result.output
    assert "Syntax Validity" in result.output
    assert "Diff Hygiene Score" in result.output
    assert "Clarification Accuracy" in result.output
    assert "Validation Test Pass Rate" in result.output


def test_evaluate_unconfigured_provider_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that unconfigured provider exits with code 2 and helpful remediation."""
    # Default config has model_provider="none"
    result = cli_runner.invoke(app, ["evaluate", "--directory", str(tmp_path)])
    assert result.exit_code == 2
    assert "Model Provider Configuration Required" in result.output
    assert "--provider mock" in result.output


def test_evaluate_unconfigured_provider_json_error(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that unconfigured provider with --json produces valid JSON error output."""
    result = cli_runner.invoke(app, ["evaluate", "--directory", str(tmp_path), "--json"])
    assert result.exit_code == 2
    data = json.loads(result.output)
    assert "error" in data
    assert "provider is not configured" in data["error"]


def test_evaluate_json_output_clean_and_valid(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot evaluate --json' outputs only parseable JSON to stdout."""
    result = cli_runner.invoke(app, ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)

    assert data["model_id"] == "mock-qwen2.5-coder:1.5b"
    assert data["total_cases"] == 8
    assert "instruction_following_rate" in data
    assert "syntax_validity_rate" in data
    assert "diff_hygiene_average" in data
    assert "clarification_accuracy" in data
    assert "test_pass_rate" in data
    assert "average_latency_seconds" in data
    assert "total_tokens_used" in data
    assert "tokens_per_second" in data
    assert "by_type_breakdown" in data
    assert "item_results" in data
    assert len(data["item_results"]) == 8


def test_evaluate_markdown_export(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that '--output scorecard.md' writes a valid Markdown scorecard report."""
    dest = tmp_path / "scorecard.md"
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", str(dest)],
    )
    assert result.exit_code == 0
    assert dest.is_file()

    content = dest.read_text(encoding="utf-8")
    assert "# CodePilot AI — Base Model Evaluation Report" in content
    assert "mock-qwen2.5-coder:1.5b" in content
    assert "Overall Scorecard" in content
    assert "Results by Instruction Category" in content
    assert "Case-by-Case Breakdown" in content


def test_evaluate_json_file_export(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that '--output report.json' writes a valid JSON scorecard report to file."""
    dest = tmp_path / "report.json"
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", str(dest)],
    )
    assert result.exit_code == 0
    assert dest.is_file()

    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["model_id"] == "mock-qwen2.5-coder:1.5b"
    assert data["total_cases"] == 8


def test_evaluate_output_overwrite_protection(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that existing output files are protected from accidental overwrite."""
    dest = tmp_path / "existing.md"
    dest.write_text("pre-existing content", encoding="utf-8")

    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", str(dest)],
    )
    assert result.exit_code == 1
    assert "already exists" in result.output
    # Original file must remain untouched
    assert dest.read_text(encoding="utf-8") == "pre-existing content"


def test_evaluate_output_nonexistent_directory(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that output to a non-existent parent directory is rejected without creating it."""
    dest = tmp_path / "missing_subdir" / "report.md"
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", str(dest)],
    )
    assert result.exit_code == 1
    assert "Destination directory" in result.output
    assert not (tmp_path / "missing_subdir").exists()


def test_evaluate_output_security_violations(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify path traversal and protected file targets are blocked with SecurityError (exit code 2)."""
    # 1. Path traversal outside project root
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", "../../escape.md"],
    )
    assert result.exit_code == 2
    assert "Path traversal detected" in result.output or "Security Policy Violation" in result.output

    # 2. Protected file (.env)
    env_file = tmp_path / ".env"
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--output", str(env_file)],
    )
    assert result.exit_code == 2
    assert "protected file" in result.output or "Security Policy Violation" in result.output


def test_evaluate_custom_dataset(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify loading and evaluation of a custom JSONL dataset file."""
    dataset_file = tmp_path / "custom_bench.jsonl"
    ex1 = DatasetExample(
        id="custom-01",
        instruction_type="AI-TASK",
        description="Custom task 1",
        source_code="def f(): pass",
        expected_output="def f(): return 1",
        validation_test="def test_f(): assert f() == 1",
    )
    ex2 = DatasetExample(
        id="custom-02",
        instruction_type="AI-FIX",
        description="Custom task 2",
        source_code="def g(): pass",
        expected_output="def g(): return 2",
    )

    with open(dataset_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(ex1.to_dict()) + "\n")
        f.write(json.dumps(ex2.to_dict()) + "\n")

    result = cli_runner.invoke(
        app,
        [
            "evaluate",
            "--directory",
            str(tmp_path),
            "--provider",
            "mock",
            "--dataset",
            str(dataset_file),
            "--json",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_cases"] == 2
    case_ids = [r["case_id"] for r in data["item_results"]]
    assert "custom-01" in case_ids
    assert "custom-02" in case_ids


def test_evaluate_invalid_dataset_errors(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that missing, malformed, or empty datasets fail cleanly with exit code 1."""
    # 1. Missing dataset file
    missing = tmp_path / "does_not_exist.jsonl"
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--dataset", str(missing)],
    )
    assert result.exit_code in (1, 2)

    # 2. Malformed JSONL file
    bad_file = tmp_path / "malformed.jsonl"
    bad_file.write_text("This is not JSON\n", encoding="utf-8")
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--dataset", str(bad_file)],
    )
    assert result.exit_code == 1
    assert "Dataset Error" in result.output

    # 3. Empty JSONL file
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("\n\n", encoding="utf-8")
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--dataset", str(empty_file)],
    )
    assert result.exit_code == 1
    assert "empty or contains no valid examples" in result.output


def test_evaluate_strict_mode_failure_on_unmet_targets(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that --strict fails with exit code 1 when target benchmark thresholds are not met."""
    failing_mock = MockModelClient(default_response="### Conversational text\nNo valid code here.")
    with patch("codepilot.cli.create_model_client", return_value=failing_mock):
        result = cli_runner.invoke(
            app,
            ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--strict"],
        )
        assert result.exit_code == 1
        assert "Strict Mode Validation Failed" in result.output


def test_evaluate_strict_mode_success_when_all_targets_met(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that --strict passes with exit code 0 when all benchmark metrics meet targets."""
    # Test with benchmark-aware default mock provider
    result = cli_runner.invoke(
        app,
        ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--strict"],
    )
    assert result.exit_code == 0
    assert "Strict Mode Validation Passed" in result.output

    # Also verify with custom response handler patch
    cases = get_default_benchmark_cases()
    expected_map = {c.id: c.expected_output for c in cases}

    def perfect_response_handler(request: ModelRequest) -> str:
        cid = request.metadata.get("case_id", "")
        exp = expected_map.get(cid, "return True")
        if cid == "eval-07-clarification-request":
            return "Could you please clarify what authentication criteria or provider should be used?"
        return f"```python\n{exp}\n```"

    perfect_mock = MockModelClient(response_handler=perfect_response_handler)
    with patch("codepilot.cli.create_model_client", return_value=perfect_mock):
        patch_result = cli_runner.invoke(
            app,
            ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--strict"],
        )
        assert patch_result.exit_code == 0
        assert "Strict Mode Validation Passed" in patch_result.output


def test_evaluate_clarification_failure_detected(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that guessing code on an ambiguous case without requesting clarification is flagged."""
    def guessing_response_handler(request: ModelRequest) -> str:
        return "```python\ndef authenticate_user(token: str) -> bool:\n    return True\n```"

    guessing_mock = MockModelClient(response_handler=guessing_response_handler)
    with patch("codepilot.cli.create_model_client", return_value=guessing_mock):
        result = cli_runner.invoke(
            app,
            ["evaluate", "--directory", str(tmp_path), "--provider", "mock", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        item7 = next(r for r in data["item_results"] if r["case_id"] == "eval-07-clarification-request")
        assert item7["clarification_correct"] is False


def test_existing_cli_commands_preserved(cli_runner: CliRunner) -> None:
    """Verify that existing CLI commands remain fully functional and registered."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["init", "status", "doctor", "scan", "analyze", "plan", "fix", "apply", "test", "evaluate"]:
        assert cmd in result.output

    ver_result = cli_runner.invoke(app, ["--version"])
    assert ver_result.exit_code == 0
    assert "CodePilot AI" in ver_result.output

