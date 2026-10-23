"""Factory for creating configured model runtime clients."""

from pathlib import Path
from typing import Optional

from codepilot.config import CodePilotConfig
from codepilot.core.models.base import BaseModelClient
from codepilot.core.models.mock_client import MockModelClient
from codepilot.core.models.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    OllamaClient,
)
from codepilot.exceptions import ConfigurationError


def create_model_client(
    config: Optional[CodePilotConfig] = None,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
) -> BaseModelClient:
    """Create a model client instance based on configuration.

    Args:
        config: Optional validated CodePilot configuration (defaults to CodePilotConfig()).
        override_provider: Optional provider override (e.g. 'ollama', 'mock').
        override_model: Optional specific model name override.

    Returns:
        BaseModelClient instance ready for health check and inference.

    Raises:
        ConfigurationError: If provider is 'none' or unsupported.
    """
    cfg = config or CodePilotConfig()
    provider = (override_provider or cfg.model_provider or "none").strip().lower()
    model_name = override_model or cfg.model_id

    if provider == "mock":
        return MockModelClient(model_name=model_name or "mock-qwen2.5-coder:1.5b")

    if provider == "ollama":
        base_url = getattr(cfg, "ollama_base_url", DEFAULT_OLLAMA_URL)
        timeout = getattr(cfg, "request_timeout", 30.0)
        return OllamaClient(
            base_url=base_url,
            model_name=model_name,
            timeout=timeout,
        )

    if provider in ("standalone", "gguf"):
        from codepilot.core.models.standalone_client import StandaloneGGUFClient
        model_path = Path(override_model) if override_model else None
        return StandaloneGGUFClient(config=cfg, model_path=model_path)

    if provider == "none":
        raise ConfigurationError(
            "AI model provider is not configured (current: 'none').\n"
            "To use a local coding LLM, set model_provider = 'ollama' or 'standalone' in '.codepilot.toml' or set\n"
            "the 'CODEPILOT_MODEL_PROVIDER=ollama' environment variable.\n"
            "For offline development/testing without Ollama, use: model_provider = 'mock'."
        )

    raise ConfigurationError(
        f"Unsupported model provider '{provider}'. Supported providers: 'ollama', 'standalone', 'mock', 'none'."
    )

