"""Automated tests for CodePilot configuration validation and management."""

from pathlib import Path
import pytest

from codepilot.config import (
    CONFIG_FILENAME,
    CodePilotConfig,
    DEFAULT_PROTECTED_DIRECTORIES,
    DEFAULT_PROTECTED_FILES,
    initialize_project_config,
    load_config,
)
from codepilot.exceptions import ConfigurationError, ProjectInitError


def test_default_config_values(tmp_path: Path) -> None:
    """Verify that CodePilotConfig sets secure and sensible defaults."""
    config = CodePilotConfig(project_root=tmp_path)
    assert config.project_root == tmp_path.resolve()
    assert config.log_level == "INFO"
    assert config.model_provider == "none"
    assert config.model_id is None
    assert config.request_timeout == 30.0
    assert config.max_input_size == 100_000
    assert config.require_approval is True
    assert set(config.protected_files) == set(DEFAULT_PROTECTED_FILES)
    assert set(config.protected_directories) == set(DEFAULT_PROTECTED_DIRECTORIES)


@pytest.mark.parametrize("invalid_level", ["VERBOSE", "TRACE", "ALL", "NONE", "123"])
def test_invalid_log_level_rejected(tmp_path: Path, invalid_level: str) -> None:
    """Verify that invalid log levels trigger ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config(project_root=tmp_path, log_level=invalid_level)


@pytest.mark.parametrize("invalid_provider", ["huggingface_custom", "superai", "unknown"])
def test_invalid_model_provider_rejected(tmp_path: Path, invalid_provider: str) -> None:
    """Verify that unsupported model providers trigger ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config(project_root=tmp_path, model_provider=invalid_provider)


@pytest.mark.parametrize("bad_timeout", [0.0, -1.0, -100.5])
def test_non_positive_timeout_rejected(tmp_path: Path, bad_timeout: float) -> None:
    """Verify that non-positive timeouts trigger ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config(project_root=tmp_path, request_timeout=bad_timeout)


@pytest.mark.parametrize("bad_size", [0, -1, -500])
def test_non_positive_input_size_rejected(tmp_path: Path, bad_size: int) -> None:
    """Verify that non-positive input sizes trigger ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config(project_root=tmp_path, max_input_size=bad_size)


def test_load_from_toml_file(tmp_path: Path) -> None:
    """Verify that configuration values are loaded accurately from a TOML file."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text(
        """
log_level = "DEBUG"
model_provider = "ollama"
model_id = "codellama"
request_timeout = 60.0
max_input_size = 250000
require_approval = false
protected_files = [".env", "secret.json"]
protected_directories = [".git", "custom_build"]
""",
        encoding="utf-8",
    )

    config = load_config(project_root=tmp_path)
    assert config.log_level == "DEBUG"
    assert config.model_provider == "ollama"
    assert config.model_id == "codellama"
    assert config.request_timeout == 60.0
    assert config.max_input_size == 250000
    assert config.require_approval is False
    assert config.protected_files == [".env", "secret.json"]
    assert config.protected_directories == [".git", "custom_build"]


def test_corrupt_toml_file_raises_error(tmp_path: Path) -> None:
    """Verify that invalid TOML syntax raises ConfigurationError."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text("this is not valid toml = [[[", encoding="utf-8")

    with pytest.raises(ConfigurationError) as exc_info:
        load_config(project_root=tmp_path)
    assert "Failed to read configuration file" in str(exc_info.value)


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that CODEPILOT_ environment variables override defaults."""
    monkeypatch.setenv("CODEPILOT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("CODEPILOT_REQUEST_TIMEOUT", "90.0")
    monkeypatch.setenv("CODEPILOT_REQUIRE_APPROVAL", "false")
    monkeypatch.setenv("CODEPILOT_PROTECTED_FILES", ".env,custom.key,id_rsa")

    config = load_config(project_root=tmp_path)
    assert config.log_level == "WARNING"
    assert config.request_timeout == 90.0
    assert config.require_approval is False
    assert config.protected_files == [".env", "custom.key", "id_rsa"]


def test_explicit_overrides_have_highest_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that explicit arguments to load_config override env vars and file."""
    monkeypatch.setenv("CODEPILOT_LOG_LEVEL", "WARNING")
    config = load_config(project_root=tmp_path, log_level="ERROR")
    assert config.log_level == "ERROR"


def test_initialize_project_config(tmp_path: Path) -> None:
    """Verify that initialize_project_config writes file and respects force."""
    created = initialize_project_config(tmp_path)
    assert created.exists()

    # Second invocation without force must fail
    with pytest.raises(ProjectInitError) as exc_info:
        initialize_project_config(tmp_path, force=False)
    assert "already exists" in str(exc_info.value)

    # Invocation with force=True should succeed
    overwritten = initialize_project_config(tmp_path, force=True)
    assert overwritten.exists()


def test_env_var_json_list_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that JSON array string in environment variable is parsed into a list."""
    monkeypatch.setenv("CODEPILOT_PROTECTED_FILES", '["custom1.key", "custom2.pem"]')
    config = load_config(project_root=tmp_path)
    assert config.protected_files == ["custom1.key", "custom2.pem"]


def test_invalid_project_root_type_raises_error() -> None:
    """Verify that a non-path project_root raises ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config(project_root=12345)  # type: ignore
