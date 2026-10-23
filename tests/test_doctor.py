"""Automated tests for CodePilot doctor diagnostics."""

from pathlib import Path
import sys
from unittest.mock import patch
import pytest

from codepilot.core.app import CodePilotApp, DoctorReport


def test_doctor_runs_all_checks(tmp_path: Path) -> None:
    """Verify that doctor inspects runtime, dependencies, tools, config, and filesystem."""
    app = CodePilotApp(project_root=tmp_path)
    report = app.run_doctor()

    assert isinstance(report, DoctorReport)
    assert report.total_count >= 5
    assert report.passed is True

    check_names = [check.name for check in report.checks]
    assert "Python Runtime" in check_names
    assert "Core Dependencies" in check_names
    assert "Git Command-Line Tool" in check_names
    assert "Configuration Validity" in check_names
    assert "Project Directory Access" in check_names


def test_doctor_fails_on_outdated_python(tmp_path: Path) -> None:
    """Verify that doctor flags Python < 3.11 with actionable remediation."""
    from collections import namedtuple

    app = CodePilotApp(project_root=tmp_path)
    VersionInfo = namedtuple(
        "version_info", ["major", "minor", "micro", "releaselevel", "serial"]
    )
    mock_version = VersionInfo(3, 9, 7, "final", 0)

    with patch.object(sys, "version_info", mock_version):
        report = app.run_doctor()
        py_check = next(c for c in report.checks if c.name == "Python Runtime")
        assert py_check.passed is False
        assert py_check.remediation is not None
        assert "3.11" in py_check.remediation
        assert report.passed is False


def test_doctor_fails_on_missing_git(tmp_path: Path) -> None:
    """Verify that doctor flags missing Git CLI with remediation."""
    app = CodePilotApp(project_root=tmp_path)

    with patch("shutil.which", return_value=None):
        report = app.run_doctor()
        git_check = next(c for c in report.checks if c.name == "Git Command-Line Tool")
        assert git_check.passed is False
        assert git_check.remediation is not None
        assert "git-scm.com" in git_check.remediation
        assert report.passed is False


def test_doctor_fails_on_missing_dependency(tmp_path: Path) -> None:
    """Verify that doctor detects missing required python dependencies."""
    app = CodePilotApp(project_root=tmp_path)

    original_import = __import__

    def mock_import(name, *args, **kwargs):
        if name == "pydantic_settings":
            raise ImportError("Simulated missing package")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with patch("importlib.import_module", side_effect=lambda name: (_ for _ in ()).throw(ImportError("missing")) if name == "pydantic_settings" else original_import(name)):
            report = app.run_doctor()
            dep_check = next(c for c in report.checks if c.name == "Core Dependencies")
            assert dep_check.passed is False
            assert "pydantic_settings" in dep_check.details
            assert dep_check.remediation is not None
            assert report.passed is False


def test_non_git_directory_is_non_critical_warning(tmp_path: Path) -> None:
    """Verify that an uninitialized git repo produces a warning without failing doctor."""
    app = CodePilotApp(project_root=tmp_path)
    report = app.run_doctor()

    git_repo_check = next(c for c in report.checks if c.name == "Git Repository State")
    assert git_repo_check.passed is False
    assert git_repo_check.is_critical is False
    # Overall passed should still be True if all other critical checks pass
    assert report.passed is True
