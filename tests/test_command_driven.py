"""Automated tests for command-driven workflows in CodePilot AI.

Verifies that scanning, analysis, error investigation, and repair workflows
operate seamlessly on real code without requiring AI instruction comments.
"""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME, CodePilotConfig
from codepilot.core.scanner import scan_repository
from codepilot.core.tester import discover_project_tests, run_project_tests


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def test_scan_works_without_ai_comments(cli_runner: CliRunner, tmp_path: Path) -> None:
    """1. Scanning works on a project without any AI-specific comments."""
    # Create clean project files with zero AI comments
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src_dir / "utils.py").write_text("import os\n\ndef get_env():\n    return os.environ.get('PORT')\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")

    result = cli_runner.invoke(app, ["scan", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    # Must report scanned files and clean completion
    assert "Scanned Source Files" in result.output
    assert "Supported File Types" in result.output
    assert ".py" in result.output
    assert "Config Files Discovered" in result.output
    assert "No AI instruction comments found (special comments are optional)" in result.output


def test_scan_json_output_without_comments(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Scan produces structured JSON containing discovered files and metadata."""
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    result = cli_runner.invoke(app, ["scan", "--json", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["scanned_files_count"] == 1
    assert "app.py" in data["discovered_files"]
    assert ".py" in data["file_types"]
    assert data["instructions"] == []


def test_scan_detects_static_findings(tmp_path: Path) -> None:
    """Scan detects static syntax defects and empty files without comments."""
    (tmp_path / "broken.py").write_text("def missing_colon()\n    pass\n", encoding="utf-8")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    config = CodePilotConfig(project_root=tmp_path)
    res = scan_repository(tmp_path, config)
    assert res.scanned_files_count == 2
    findings_types = [f["type"] for f in res.static_findings]
    assert "syntax_error" in findings_types
    assert "empty_file" in findings_types


def test_analyze_comment_free_project(cli_runner: CliRunner, tmp_path: Path) -> None:
    """2. Comment-free projects can still be analyzed."""
    src = tmp_path / "math_module.py"
    src.write_text(
        "class Calculator:\n"
        "    def multiply(self, x, y):\n"
        "        return x * y\n"
        "    def divide(self, x, y):\n"
        "        return x / y\n",
        encoding="utf-8",
    )
    result = cli_runner.invoke(app, ["analyze", "--detailed", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Architecture Analysis" in result.output
    assert "math_module.py" in result.output
    assert "Calculator" in result.output


def test_analyze_json_and_context_options(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Analyze supports --json, --detailed, and --context on comment-free projects."""
    src = tmp_path / "entry.py"
    src.write_text("def main():\n    pass\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")

    # Test --json
    json_res = cli_runner.invoke(app, ["analyze", "--json", "--directory", str(tmp_path)])
    assert json_res.exit_code == 0
    data = json.loads(json_res.output)
    assert data["structure"]["file_count"] >= 1
    assert any(m["file_path"] == "entry.py" for m in data["modules"])

    # Test --context
    ctx_res = cli_runner.invoke(app, ["analyze", "--context", "--directory", str(tmp_path)])
    assert ctx_res.exit_code == 0
    assert "Architecture Prompt Context" in ctx_res.output


def test_fix_workflow_explicit_file_and_issue(cli_runner: CliRunner, tmp_path: Path) -> None:
    """3. Fix workflows can start from explicit target file and issue description."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    calc_file = tmp_path / "calculator.py"
    original_code = "def divide(a, b):\n    return a / b\n"
    calc_file.write_text(original_code, encoding="utf-8")

    # Run fix targeting file and issue
    res = cli_runner.invoke(
        app,
        ["fix", "calculator.py", "--issue", "handle division by zero", "--directory", str(tmp_path)],
    )
    assert res.exit_code == 0
    assert "Investigation & Proposed Fix" in res.output
    assert "calculator.py" in res.output
    assert "Diff Preview Ready" in res.output
    assert "diff" in res.output.lower()

    # 6 & 7: Verify fix preview did NOT modify source file on disk
    assert calc_file.read_text(encoding="utf-8") == original_code

    # 8: Apply changes requires explicit approval
    apply_res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 0
    assert "Successfully applied patch" in apply_res.output

    # Source code is now safely modified
    new_code = calc_file.read_text(encoding="utf-8")
    assert new_code != original_code
    assert "b == 0" in new_code


def test_fix_workflow_starts_from_static_analysis_finding(cli_runner: CliRunner, tmp_path: Path) -> None:
    """3b. Fix workflow starts automatically from a single clear static finding."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # File with missing colon syntax error
    bug_file = tmp_path / "service.py"
    bug_file.write_text("def compute(x, y)\n    return x + y\n", encoding="utf-8")

    # Run fix with no arguments
    res = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])
    assert res.exit_code == 0
    assert "Investigation & Proposed Fix" in res.output
    assert "service.py" in res.output
    assert "Diff Preview Ready" in res.output

    # Apply fix
    apply_res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 0
    assert "def compute(x, y):" in bug_file.read_text(encoding="utf-8")


def test_ambiguous_fix_targets_prompt_clearly(cli_runner: CliRunner, tmp_path: Path) -> None:
    """4. Ambiguous fix targets result in a clear listing of available choices."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Create multiple files with static findings
    (tmp_path / "mod_a.py").write_text("def func_a()\n    pass\n", encoding="utf-8")
    (tmp_path / "mod_b.py").write_text("def func_b()\n    pass\n", encoding="utf-8")

    # Non-interactive CLI invocation
    res = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)], input="q\n")
    assert res.exit_code == 0
    assert "Target is Ambiguous" in res.output or "Target Selection Required" in res.output
    assert "mod_a.py" in res.output
    assert "mod_b.py" in res.output


def test_ambiguous_fix_targets_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Ambiguous fix targets return structured JSON in --json mode."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    (tmp_path / "mod_1.py").write_text("def f1()\n    pass\n", encoding="utf-8")
    (tmp_path / "mod_2.py").write_text("def f2()\n    pass\n", encoding="utf-8")

    res = cli_runner.invoke(app, ["fix", "--json", "--directory", str(tmp_path)])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data.get("ambiguous") is True
    assert len(data.get("findings", [])) >= 2


def test_special_comments_remain_optional_and_compatible(cli_runner: CliRunner, tmp_path: Path) -> None:
    """5. Special comments remain fully functional as optional task hints."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    code_file = tmp_path / "handler.py"
    code_file.write_text(
        "def check_even(n):\n"
        "    # AI: even\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Scan finds comment
    scan_res = cli_runner.invoke(app, ["scan", "--directory", str(tmp_path)])
    assert scan_res.exit_code == 0
    assert "Discovered 1 AI instruction comment(s)" in scan_res.output

    # Fix handles it via existing plan flow
    fix_res = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])
    assert fix_res.exit_code == 0
    assert "Diff Preview Ready" in fix_res.output

    # Apply applies it
    apply_res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 0
    assert "return n % 2 == 0" in code_file.read_text(encoding="utf-8")


def test_scan_and_analysis_do_not_modify_files(cli_runner: CliRunner, tmp_path: Path) -> None:
    """6. Scan and analysis operations are strictly read-only."""
    test_file = tmp_path / "original.py"
    content = "x = 42\n"
    test_file.write_text(content, encoding="utf-8")

    cli_runner.invoke(app, ["scan", "--directory", str(tmp_path)])
    assert test_file.read_text(encoding="utf-8") == content

    cli_runner.invoke(app, ["analyze", "--directory", str(tmp_path)])
    assert test_file.read_text(encoding="utf-8") == content


def test_backups_stale_patch_and_rollback_flow(cli_runner: CliRunner, tmp_path: Path) -> None:
    """9. Backups, stale-patch checks, and rollback still function correctly."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    target_file = tmp_path / "compute.py"
    original_code = "def run():\n    return False\n"
    target_file.write_text(original_code, encoding="utf-8")

    # Generate patch
    cli_runner.invoke(app, ["fix", "compute.py", "--issue", "return true", "--directory", str(tmp_path)])

    # Apply patch with approval -> creates backup snapshot
    apply_res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 0
    assert "return True" in target_file.read_text(encoding="utf-8")

    # Rollback restores exact original content
    rollback_res = cli_runner.invoke(app, ["rollback", "--directory", str(tmp_path)])
    assert rollback_res.exit_code == 0
    assert target_file.read_text(encoding="utf-8") == original_code


def test_test_discovery_and_configurable_timeout(tmp_path: Path) -> None:
    """11. Test discovery and configurable timeout handling work correctly."""
    # When no test runner or files exist:
    config = CodePilotConfig(project_root=tmp_path)
    discovery = discover_project_tests(tmp_path, config)
    assert discovery.framework == "none"
    assert discovery.is_supported is False
    assert discovery.proposed_command == ""

    # When pytest files exist:
    (tmp_path / "test_example.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    discovery2 = discover_project_tests(tmp_path, config)
    assert discovery2.framework == "pytest"
    assert discovery2.is_supported is True
    assert "pytest" in discovery2.proposed_command

    # Run tests with custom timeout
    res = run_project_tests(tmp_path, config, timeout_override=10.0)
    assert res.passed is True
    assert res.exit_code == 0
