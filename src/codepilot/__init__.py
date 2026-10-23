"""CodePilot AI — Git-based AI Code Modification and Error Correction Engine."""

from codepilot.config import CodePilotConfig, load_config
from codepilot.core.app import CodePilotApp
from codepilot.exceptions import (
    CodePilotError,
    ConfigurationError,
    CoreAppError,
    EnvironmentDiagnosticError,
    ProjectInitError,
)
from codepilot.version import APP_NAME, __version__

__all__ = [
    "APP_NAME",
    "CodePilotApp",
    "CodePilotConfig",
    "CodePilotError",
    "ConfigurationError",
    "CoreAppError",
    "EnvironmentDiagnosticError",
    "ProjectInitError",
    "__version__",
    "load_config",
]
