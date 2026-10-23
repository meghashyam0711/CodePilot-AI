"""Automated tests for CodePilot custom exceptions."""

from codepilot.exceptions import (
    CodePilotError,
    ConfigurationError,
    CoreAppError,
    EnvironmentDiagnosticError,
    ProjectInitError,
)


def test_base_codepilot_error() -> None:
    """Verify CodePilotError initialization, string representation, and details."""
    err = CodePilotError("Base error occurred", details={"key": "val"})
    assert str(err) == "Base error occurred"
    assert err.message == "Base error occurred"
    assert err.details == {"key": "val"}


def test_configuration_error() -> None:
    """Verify ConfigurationError inheritance from CodePilotError."""
    err = ConfigurationError("Config syntax invalid")
    assert isinstance(err, CodePilotError)
    assert str(err) == "Config syntax invalid"


def test_environment_diagnostic_error() -> None:
    """Verify EnvironmentDiagnosticError retains remediation and details."""
    err = EnvironmentDiagnosticError(
        "Python 3.8 is not supported",
        remediation="Upgrade to Python 3.11+",
        details={"version": "3.8"},
    )
    assert isinstance(err, CodePilotError)
    assert err.remediation == "Upgrade to Python 3.11+"
    assert err.details["version"] == "3.8"


def test_project_init_error() -> None:
    """Verify ProjectInitError inheritance."""
    err = ProjectInitError("Project already initialized")
    assert isinstance(err, CodePilotError)


def test_core_app_error() -> None:
    """Verify CoreAppError inheritance."""
    err = CoreAppError("App failed to start")
    assert isinstance(err, CodePilotError)


def test_analysis_error() -> None:
    """Verify AnalysisError inheritance."""
    from codepilot.exceptions import AnalysisError
    err = AnalysisError("Analysis failed")
    assert isinstance(err, CodePilotError)

