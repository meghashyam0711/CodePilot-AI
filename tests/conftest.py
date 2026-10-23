"""Pytest configuration and shared test fixtures for CodePilot AI."""

from pathlib import Path
from typing import Generator
import pytest
from typer.testing import CliRunner

from codepilot.config import CodePilotConfig


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provides a fresh Typer CLI runner for testing."""
    return CliRunner()


@pytest.fixture
def sample_config(tmp_path: Path) -> CodePilotConfig:
    """Provides a clean default configuration pointing to a temporary directory."""
    return CodePilotConfig(
        project_root=tmp_path,
        log_level="INFO",
        model_provider="none",
        request_timeout=30.0,
        max_input_size=100000,
        require_approval=True,
    )
