"""Custom exceptions for CodePilot AI.

All custom exceptions inherit from CodePilotError, enabling consistent
error handling and user-friendly CLI error reporting.
"""

from typing import Any, Optional


class CodePilotError(Exception):
    """Base exception for all CodePilot errors."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return self.message


class ConfigurationError(CodePilotError):
    """Raised when configuration loading or validation fails."""


class EnvironmentDiagnosticError(CodePilotError):
    """Raised when an environmental diagnostic check fails."""

    def __init__(
        self,
        message: str,
        remediation: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, details)
        self.remediation = remediation


class ProjectInitError(CodePilotError):
    """Raised when project initialization encounters an error or conflicts with existing config."""


class CoreAppError(CodePilotError):
    """Raised when core application operations or lifecycle steps fail."""


class ScanError(CodePilotError):
    """Raised when scanning repository files fails."""


class PlanError(CodePilotError):
    """Raised when plan generation fails."""


class FixError(CodePilotError):
    """Raised when generating a proposed fix fails."""


class PatchError(CodePilotError):
    """Raised when validating or applying a patch fails."""


class TestExecutionError(CodePilotError):
    """Raised when running automated tests fails or encounters execution errors."""

    __test__ = False


class AnalysisError(CodePilotError):
    """Raised when repository architecture analysis encounters an error."""


class SecurityError(CodePilotError):
    """Raised when repository boundary, path traversal, or protected-file rules are violated."""


class RecoveryError(CodePilotError):
    """Raised when patch backup, rollback, or recovery operations fail."""




