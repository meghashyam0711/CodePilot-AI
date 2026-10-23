"""Automated tests for the 'codepilot test' command and safe test execution engine."""

import json
from pathlib import Path
import pytest
import sys
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME
from codepilot.core.tester import detect_safe_test_command, validate_test_command
from codepilot.exceptions import TestExecutionError


def test_detect_safe_test_command(tmp_path: Path) -> None:
    """Verify automatic detection of safe test runners."""
    # Empty dir -> None
    assert detect_safe_test_command(tmp_path) is None

    # Python indicators
    (tmp_path / "pyproject.toml").touch()
    detected = detect_safe_test_command(tmp_path)
    assert detected is not None
    assert "pytest" in detected

    # Node indicator
    node_dir = tmp_path / "node_proj"
    node_dir.mkdir()
    (node_dir / "package.json").touch()
    assert detect_safe_test_command(node_dir) == "npm test"


def test_validate_test_command_rejects_dangerous_shell_injection() -> None:
    """Requirement 10: Reject chained or arbitrary commands."""
    dangerous_commands = [
        "pytest ; rm -rf /",
        "pytest && echo injected",
        "pytest || echo fallback",
        "pytest | grep pass",
        "pytest > output.txt",
        "pytest `whoami`",
        "pytest $(cat /etc/passwd)",
    ]
    for cmd in dangerous_commands:
        try:
            validate_test_command(cmd)
            assert False, f"Expected TestExecutionError for '{cmd}'"
        except TestExecutionError as e:
            assert "prohibited shell operator" in str(e)


def test_validate_test_command_rejects_unapproved_runners() -> None:
    """Verify that commands not starting with approved test runners are rejected."""
    unapproved = [
        "curl http://malicious.com",
        "cat secrets.txt",
        "bash script.sh",
        "eval 'something'",
    ]
    for cmd in unapproved:
        try:
            validate_test_command(cmd)
            assert False, f"Expected TestExecutionError for '{cmd}'"
        except TestExecutionError as e:
            assert "does not match any approved test runner" in str(e)


def test_cli_test_executes_passing_suite(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot test' executes actual test file and reports pass."""
    # Write a passing pytest file
    test_file = tmp_path / "test_simple.py"
    test_file.write_text("def test_ok(): assert 1 + 1 == 2\n", encoding="utf-8")

    # Configure explicit test command
    cmd = f"{sys.executable} -m pytest {test_file}"
    result = cli_runner.invoke(
        app, ["test", "--command", cmd, "--directory", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "Test Suite Passed" in result.output
    assert "All tests passed successfully" in result.output


def test_cli_test_executes_failing_suite(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot test' captures actual test failures and returns non-zero code."""
    # Write a failing pytest file
    test_file = tmp_path / "test_broken.py"
    test_file.write_text("def test_fail(): assert 2 + 2 == 5\n", encoding="utf-8")

    cmd = f"{sys.executable} -m pytest {test_file}"
    result = cli_runner.invoke(
        app, ["test", "--command", cmd, "--directory", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "Test Suite Failed" in result.output


def test_cli_test_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'codepilot test --json' produces structured test execution data."""
    test_file = tmp_path / "test_unit.py"
    test_file.write_text("def test_pass(): assert True\n", encoding="utf-8")

    cmd = f"{sys.executable} -m pytest {test_file}"
    result = cli_runner.invoke(
        app, ["test", "--command", cmd, "--directory", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["passed"] is True
    assert data["exit_code"] == 0
    assert "duration_seconds" in data
    assert "stdout" in data


def test_detect_safe_test_command_quotes_interpreter_with_spaces(tmp_path: Path) -> None:
    """Phase 14.5 Regression: Python interpreter path containing spaces is quoted properly."""
    from unittest.mock import patch

    (tmp_path / "pyproject.toml").touch()
    fake_exe = r"C:\Program Files\Python313\python.exe"
    with patch("sys.executable", fake_exe):
        detected = detect_safe_test_command(tmp_path)
        assert detected == f'"{fake_exe}" -m pytest'


def test_validate_test_command_handles_quoted_and_unquoted_interpreter_paths() -> None:
    """Phase 14.5 Regression: Both quoted and unquoted interpreter paths are accepted,

    while disallowed and injection commands are strictly rejected.
    """
    from unittest.mock import patch

    # 1. Standard unquoted interpreter path
    validate_test_command(f"{sys.executable} -m pytest")

    # 2. Quoted interpreter path without spaces
    validate_test_command(f'"{sys.executable}" -m pytest')

    # 3. Quoted interpreter path with spaces
    fake_exe_spaces = r"C:\Program Files\Python 3.13\python.exe"
    with patch("sys.executable", fake_exe_spaces):
        validate_test_command(f'"{fake_exe_spaces}" -m pytest tests/')
        validate_test_command(f"{fake_exe_spaces} -m pytest tests/")

    # 4. Dangerous injection operators must still be rejected even with quoted interpreter
    with pytest.raises(TestExecutionError) as exc_op:
        validate_test_command(f'"{sys.executable}" -m pytest ; echo pwned')
    assert "prohibited shell operator ';'" in str(exc_op.value)

    with pytest.raises(TestExecutionError) as exc_op2:
        validate_test_command(f'"{sys.executable}" -m pytest && pytest')
    assert "prohibited shell operator '&&'" in str(exc_op2.value)

    # 5. Non-approved arbitrary executables must still be rejected
    with pytest.raises(TestExecutionError) as exc_bad:
        validate_test_command(r'"C:\Program Files\Malicious\malware.exe" -m pytest')
    assert "does not match any approved test runner" in str(exc_bad.value)


def test_run_project_tests_executes_quoted_interpreter_successfully(tmp_path: Path) -> None:
    """Phase 14.5 Regression: run_project_tests successfully parses and executes quoted command."""
    from codepilot.config import CodePilotConfig
    from codepilot.core.tester import run_project_tests

    test_file = tmp_path / "test_quote_exec.py"
    test_file.write_text("def test_works(): assert True\n", encoding="utf-8")

    cfg = CodePilotConfig(project_root=tmp_path)
    cmd = f'"{sys.executable}" -m pytest {test_file}'
    result = run_project_tests(tmp_path, config=cfg, custom_command=cmd)
    assert result.passed is True
    assert result.exit_code == 0

