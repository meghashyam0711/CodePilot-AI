"""Automated tests for CodePilot core application lifecycle and status services."""

from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest

from codepilot.config import CodePilotConfig
from codepilot.core.app import CodePilotApp
from codepilot.exceptions import CoreAppError
from codepilot.version import __version__


def test_app_lifecycle(tmp_path: Path) -> None:
    """Verify clean initialization and shutdown of CodePilotApp."""
    app = CodePilotApp(project_root=tmp_path)
    assert not app.is_initialized

    app.initialize()
    assert app.is_initialized

    app.shutdown()
    assert not app.is_initialized


def test_app_context_manager(tmp_path: Path) -> None:
    """Verify that CodePilotApp can be used as a context manager."""
    with CodePilotApp(project_root=tmp_path) as app:
        assert app.is_initialized
    assert not app.is_initialized


def test_app_status_non_git(tmp_path: Path) -> None:
    """Verify status reporting for a directory that is not a git repository."""
    app = CodePilotApp(project_root=tmp_path)
    status = app.get_status()

    assert status.app_version == __version__
    assert status.project_root == tmp_path.resolve()
    assert status.config_file_found is False
    assert status.git_info.is_repo is False
    assert "Not a Git repository" in status.git_info.description
    assert status.ai_ready is False
    assert status.is_ready_for_phase1 is True


def test_app_status_in_actual_git_repo(tmp_path: Path) -> None:
    """Verify status reporting inside an initialized git repository."""
    # Initialize a temporary git repository
    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        pytest.skip("Git is not available in test environment")

    app = CodePilotApp(project_root=tmp_path)
    status = app.get_status()

    assert status.git_info.is_installed is True
    assert status.git_info.is_repo is True
    assert status.git_info.current_branch is not None


def test_app_initialization_error_wrapped(tmp_path: Path) -> None:
    """Verify that exceptions during initialize() are wrapped in CoreAppError."""
    app = CodePilotApp(project_root=tmp_path)
    with patch.object(CodePilotApp, "config", property(lambda self: (_ for _ in ()).throw(ValueError("boom")))):
        with pytest.raises(CoreAppError) as exc_info:
            app.initialize()
        assert "Application initialization failed" in str(exc_info.value)
