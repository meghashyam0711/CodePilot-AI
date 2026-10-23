"""Configuration management for CodePilot AI.

Defines the configuration schema, safe defaults, environment variable loading,
and TOML configuration file serialization/deserialization.
"""

from enum import Enum
from pathlib import Path
import sys
from typing import Any, List, Optional, Union

# Use standard library tomllib for Python 3.11+
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from codepilot.exceptions import ConfigurationError, ProjectInitError


class LogLevel(str, Enum):
    """Supported logging verbosity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ModelProvider(str, Enum):
    """Supported AI model providers."""

    NONE = "none"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    STANDALONE = "standalone"
    GGUF = "gguf"
    MOCK = "mock"
    CUSTOM = "custom"



DEFAULT_PROTECTED_FILES = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials.json",
    "secrets.*",
    "pyproject.toml",
]

DEFAULT_PROTECTED_DIRECTORIES = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
]

CONFIG_FILENAME = ".codepilot.toml"


class CodePilotConfig(BaseSettings):
    """CodePilot configuration schema and validator."""

    model_config = SettingsConfigDict(
        env_prefix="CODEPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(
        default_factory=Path.cwd,
        description="Root directory of the target project.",
    )
    log_level: str = Field(
        default="INFO",
        description="Application logging verbosity.",
    )
    model_provider: str = Field(
        default="none",
        description="AI model provider ('none', 'mock', 'ollama', 'standalone').",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="Optional specific model identifier.",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Local Ollama daemon endpoint URL.",
    )
    n_gpu_layers: int = Field(
        default=0,
        ge=-1,
        description=(
            "Number of model layers to offload to GPU via llama.cpp "
            "(0 for CPU-only, -1 for all layers, or positive integer for specific layer count)."
        ),
    )
    max_context_tokens: int = Field(
        default=2048,
        gt=128,
        le=32768,
        description="Maximum token budget for prompt context construction.",
    )
    request_timeout: float = Field(
        default=30.0,
        gt=0,
        le=3600.0,
        description="Request timeout in seconds.",
    )
    max_input_size: int = Field(
        default=100_000,
        gt=0,
        le=100_000_000,
        description="Maximum allowed input size in bytes.",
    )
    require_approval: bool = Field(
        default=True,
        description="Require explicit user approval before applying modifications.",
    )
    protected_files: Union[List[str], str] = Field(
        default_factory=lambda: list(DEFAULT_PROTECTED_FILES),
        description="List of file patterns protected from modification.",
    )
    protected_directories: Union[List[str], str] = Field(
        default_factory=lambda: list(DEFAULT_PROTECTED_DIRECTORIES),
        description="List of directory patterns protected from modification.",
    )
    test_command: Optional[str] = Field(
        default=None,
        description="Safe command to execute project tests (e.g. 'pytest', 'npm test').",
    )

    @field_validator("project_root", mode="before")
    @classmethod
    def validate_project_root(cls, v: Any) -> Path:
        if isinstance(v, (str, Path)):
            path = Path(v).expanduser().resolve()
            return path
        raise ValueError("project_root must be a valid path string or Path object")

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: Any) -> str:
        if isinstance(v, LogLevel):
            return v.value
        val = str(v).strip().upper()
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if val not in valid_levels:
            raise ValueError(
                f"Invalid log_level '{v}'. Must be one of: {', '.join(sorted(valid_levels))}"
            )
        return val

    @field_validator("model_provider", mode="before")
    @classmethod
    def validate_model_provider(cls, v: Any) -> str:
        if isinstance(v, ModelProvider):
            return v.value
        val = str(v).strip().lower()
        valid_providers = {"none", "openai", "anthropic", "gemini", "ollama", "standalone", "gguf", "mock", "custom"}
        if val not in valid_providers:
            raise ValueError(
                f"Invalid model_provider '{v}'. Must be one of: {', '.join(sorted(valid_providers))}"
            )
        return val

    @field_validator("protected_files", "protected_directories")
    @classmethod
    def parse_string_list(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            v_stripped = v.strip()
            if v_stripped.startswith("[") and v_stripped.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v_stripped)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except Exception:
                    pass
            return [item.strip() for item in v_stripped.split(",") if item.strip()]
        if isinstance(v, (list, tuple, set)):
            return [str(item).strip() for item in v if str(item).strip()]
        return list(v)

    @field_validator("request_timeout")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("request_timeout must be strictly positive")
        return v

    @field_validator("max_input_size")
    @classmethod
    def validate_input_size(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_input_size must be strictly positive")
        return v


def generate_default_toml_content(project_root: Optional[Path] = None) -> str:
    """Generate nicely formatted default TOML configuration content."""
    root_str = str(project_root.resolve()).replace("\\", "/") if project_root else "."
    return f"""# CodePilot AI Configuration File
# Git-based AI Code Modification and Error Correction Engine

# Project root directory
project_root = "{root_str}"

# Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
log_level = "INFO"

# Model provider selection: 'none', 'mock', 'ollama', 'standalone'
model_provider = "none"

# Optional specific model identifier
model_id = ""

# Request timeout in seconds
request_timeout = 30.0

# Maximum input size in bytes
max_input_size = 100000

# Approval requirement for modifications
require_approval = true

# Protected file patterns (will not be modified by CodePilot)
protected_files = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials.json",
    "secrets.*",
    "pyproject.toml"
]

# Protected directory names
protected_directories = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build"
]

# Safe test execution command (optional, defaults to automatic test runner detection)
# test_command = "pytest tests -v"
"""


def load_config(
    project_root: Optional[Union[str, Path]] = None,
    config_file: Optional[Union[str, Path]] = None,
    **overrides: Any,
) -> CodePilotConfig:
    """Load and validate CodePilot configuration.

    Resolution order:
    1. Default values
    2. TOML configuration file (.codepilot.toml in project root or custom config_file)
    3. Environment variables (CODEPILOT_*) and .env file
    4. Explicit overrides passed to this function

    Args:
        project_root: Optional path to project root. Defaults to current directory.
        config_file: Optional explicit path to configuration TOML file.
        **overrides: Key-value pairs to override loaded values.

    Returns:
        CodePilotConfig: Validated configuration object.

    Raises:
        ConfigurationError: If configuration values fail validation or file reading errors occur.
    """
    try:
        resolved_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path.cwd().resolve()
        )
    except Exception as e:
        raise ConfigurationError(
            f"Invalid project_root '{project_root}': {e}",
            details={"error": str(e)},
        ) from e

    toml_data: dict[str, Any] = {}
    resolved_config_path: Optional[Path] = None

    if config_file:
        resolved_config_path = Path(config_file).expanduser().resolve()
    else:
        candidate = resolved_root / CONFIG_FILENAME
        if candidate.is_file():
            resolved_config_path = candidate

    if resolved_config_path and resolved_config_path.is_file():
        try:
            with open(resolved_config_path, "rb") as f:
                toml_data = tomllib.load(f)
        except Exception as e:
            raise ConfigurationError(
                f"Failed to read configuration file at '{resolved_config_path}': {e}",
                details={"path": str(resolved_config_path), "error": str(e)},
            ) from e

    # Filter out empty model_id strings in toml
    if toml_data.get("model_id") == "":
        toml_data["model_id"] = None

    # Merge TOML data with overrides
    merged_data: dict[str, Any] = {
        "project_root": resolved_root,
        **toml_data,
        **{k: v for k, v in overrides.items() if v is not None},
    }

    try:
        return CodePilotConfig(**merged_data)
    except Exception as e:
        raise ConfigurationError(
            f"Configuration validation failed: {e}",
            details={"error": str(e)},
        ) from e


def initialize_project_config(
    target_directory: Union[str, Path],
    force: bool = False,
) -> Path:
    """Initialize CodePilot configuration in the specified directory.

    Args:
        target_directory: Path to the project directory.
        force: Whether to overwrite an existing configuration file.

    Returns:
        Path: Path to the created configuration file.

    Raises:
        ProjectInitError: If configuration already exists and force is False.
    """
    target_path = Path(target_directory).expanduser().resolve()
    if not target_path.exists():
        target_path.mkdir(parents=True, exist_ok=True)

    config_file_path = target_path / CONFIG_FILENAME

    if config_file_path.exists() and not force:
        raise ProjectInitError(
            f"Configuration file already exists at '{config_file_path}'. Use --force to overwrite.",
            details={"path": str(config_file_path)},
        )

    content = generate_default_toml_content(project_root=target_path)
    try:
        config_file_path.write_text(content, encoding="utf-8")
    except OSError as e:
        raise ProjectInitError(
            f"Failed to write configuration file at '{config_file_path}': {e}",
            details={"path": str(config_file_path), "error": str(e)},
        ) from e

    return config_file_path
