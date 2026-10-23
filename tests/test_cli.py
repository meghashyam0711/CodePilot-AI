"""Automated tests for CodePilot CLI commands."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME
from codepilot.version import APP_NAME, __version__


def test_cli_help(cli_runner: CliRunner) -> None:
    """Verify that 'codepilot --help' displays usage, options, and commands."""
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage: codepilot" in result.output
    for cmd in ["init", "status", "doctor", "scan", "plan", "fix", "apply", "test"]:
        assert cmd in result.output
    assert "--version" in result.output


def test_cli_version_flag(cli_runner: CliRunner) -> None:
    """Verify that 'codepilot --version' outputs application name and version."""
    result = cli_runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert APP_NAME in result.output
    assert __version__ in result.output


def test_cli_short_version_flag(cli_runner: CliRunner) -> None:
    """Verify that 'codepilot -v' outputs application name and version."""
    result = cli_runner.invoke(app, ["-v"])
    assert result.exit_code == 0
    assert APP_NAME in result.output
    assert __version__ in result.output


def test_subcommand_helps(cli_runner: CliRunner) -> None:
    """Verify help messages for each subcommand."""
    for cmd in ["init", "status", "doctor", "scan", "analyze", "plan", "fix", "apply", "test"]:
        result = cli_runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0
        assert f"Usage: codepilot {cmd}" in result.output



def test_init_in_clean_directory(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot init' generates .codepilot.toml in target directory."""
    result = cli_runner.invoke(app, ["init", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Successfully initialized CodePilot configuration!" in result.output

    config_path = tmp_path / CONFIG_FILENAME
    assert config_path.exists()
    assert config_path.is_file()
    content = config_path.read_text(encoding="utf-8")
    assert "project_root" in content
    assert "log_level" in content
    assert "require_approval" in content


def test_init_without_force_protects_existing_config(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'codepilot init' does not overwrite existing config without --force."""
    config_path = tmp_path / CONFIG_FILENAME
    original_content = "# original custom config content\nlog_level = 'DEBUG'\n"
    config_path.write_text(original_content, encoding="utf-8")

    result = cli_runner.invoke(app, ["init", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "Configuration already exists" in result.output
    # Ensure original file was NOT overwritten
    assert config_path.read_text(encoding="utf-8") == original_content


def test_init_with_force_overwrites(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot init --force' successfully overwrites existing config."""
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text("# old content\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["init", "--directory", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "Successfully initialized CodePilot configuration!" in result.output

    new_content = config_path.read_text(encoding="utf-8")
    assert "# old content" not in new_content
    assert "require_approval" in new_content


def test_status_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot status' reports accurate project and engine state."""
    result = cli_runner.invoke(app, ["status", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "CodePilot Engine" in result.output
    assert "AI Modification Engine" in result.output
    assert "Disabled" in result.output


def test_status_command_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot status --json' outputs valid JSON schema."""
    result = cli_runner.invoke(app, ["status", "--directory", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["app_name"] == APP_NAME
    assert data["version"] == __version__
    assert "git" in data
    assert "ai_engine" in data
    assert data["ai_engine"]["ready"] is False
    assert data["phase1_ready"] is True


def test_doctor_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot doctor' runs and displays diagnostic checks."""
    result = cli_runner.invoke(app, ["doctor", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Environment Doctor Diagnostics" in result.output
    assert "Python Runtime" in result.output
    assert "Core Dependencies" in result.output
    assert "Git Command-Line Tool" in result.output


def test_doctor_command_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify that 'codepilot doctor --json' produces valid diagnostic JSON."""
    result = cli_runner.invoke(app, ["doctor", "--directory", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "overall_passed" in data
    assert "checks" in data
    assert isinstance(data["checks"], list)
    assert len(data["checks"]) >= 5


def test_invalid_command_exit_code(cli_runner: CliRunner) -> None:
    """Verify that unknown commands fail with non-zero exit code."""
    result = cli_runner.invoke(app, ["invalid-subcommand-xyz"])
    assert result.exit_code != 0


def test_doctor_command_fails_exit_code_on_critical_failure(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'codepilot doctor' exits with code 1 if a critical check fails."""
    from unittest.mock import patch
    import shutil

    with patch("shutil.which", return_value=None):
        result = cli_runner.invoke(app, ["doctor", "--directory", str(tmp_path)])
        assert result.exit_code == 1
        assert "One or more critical diagnostic checks failed" in result.output


def test_doctor_command_json_fails_exit_code_on_critical_failure(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'codepilot doctor --json' exits with code 1 if a critical check fails."""
    from unittest.mock import patch

    with patch("shutil.which", return_value=None):
        result = cli_runner.invoke(app, ["doctor", "--directory", str(tmp_path), "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["overall_passed"] is False


def test_status_command_handles_corrupt_config(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'codepilot status' cleanly reports configuration errors without crashing."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text("invalid = [[toml", encoding="utf-8")

    result = cli_runner.invoke(app, ["status", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "Configuration Error" in result.output


def test_cli_main_entrypoint() -> None:
    """Verify that main() entrypoint executes app()."""
    from unittest.mock import patch
    from codepilot.cli import main

    with patch("codepilot.cli.app") as mock_app:
        main()
        assert mock_app.called
