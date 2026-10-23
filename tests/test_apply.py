"""Automated tests for the 'codepilot apply --approved' command and patcher engine."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME


def test_apply_without_approved_flag_is_rejected(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'apply' without '--approved' is rejected and modifies nothing."""
    test_file = tmp_path / "app.py"
    original = "# AI: return true\ndef check():\n    pass\n"
    test_file.write_text(original, encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Generate patch
    fix_res = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert fix_res.exit_code == 0

    # Run apply WITHOUT --approved
    apply_res = cli_runner.invoke(app, ["apply", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 1
    assert "explicit approval is required" in apply_res.output

    # File must NOT be modified
    assert test_file.read_text(encoding="utf-8") == original


def test_apply_when_no_pending_patch_exists(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'apply --approved' fails cleanly if no patch was generated."""
    result = cli_runner.invoke(
        app, ["apply", "--approved", "--directory", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert "No pending patch found" in result.output


def test_apply_rejects_stale_patches(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that apply rejects patches if target file was modified after generation."""
    test_file = tmp_path / "service.py"
    test_file.write_text(
        "# AI: return true\ndef run():\n    pass\n", encoding="utf-8"
    )

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Generate preview patch
    cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    # Developer makes concurrent edits after patch generation!
    developer_edit = "# Developer edited this manually\ndef run():\n    return 42\n"
    test_file.write_text(developer_edit, encoding="utf-8")

    # Attempt to apply the now-stale patch
    apply_res = cli_runner.invoke(
        app, ["apply", "--approved", "--directory", str(tmp_path)]
    )
    assert apply_res.exit_code == 1
    assert "Patch is stale" in apply_res.output

    # Requirement 9: Developer's changes MUST be preserved
    assert test_file.read_text(encoding="utf-8") == developer_edit


def test_apply_valid_patch_successfully(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify clean application of validated patch when --approved is provided."""
    test_file = tmp_path / "logic.py"
    test_file.write_text(
        "def check():\n    # AI: return true\n    pass\n", encoding="utf-8"
    )

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Generate patch
    fix_res = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert fix_res.exit_code == 0

    # Apply with explicit approval
    apply_res = cli_runner.invoke(
        app, ["apply", "--approved", "--directory", str(tmp_path)]
    )
    assert apply_res.exit_code == 0
    assert "Successfully applied patch" in apply_res.output
    assert "logic.py" in apply_res.output

    # Verify changes were actually applied to file
    new_content = test_file.read_text(encoding="utf-8")
    assert "return True" in new_content

    # Second invocation should fail because patch is already applied
    second_res = cli_runner.invoke(
        app, ["apply", "--approved", "--directory", str(tmp_path)]
    )
    assert second_res.exit_code == 1


def test_apply_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'apply --approved --json' produces structured JSON output."""
    test_file = tmp_path / "module.py"
    test_file.write_text(
        "def check():\n    # AI: return false\n    pass\n", encoding="utf-8"
    )

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    result = cli_runner.invoke(
        app, ["apply", "--approved", "--directory", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "patch_id" in data
    assert data["total_modified"] == 1
    assert "module.py" in data["applied_files"]
