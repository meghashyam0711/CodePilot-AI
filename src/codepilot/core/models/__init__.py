"""Modular local model runtime interface for CodePilot AI (Phase 4)."""

from codepilot.core.models.base import (
    BaseModelClient,
    ModelInfo,
    ModelRequest,
    ModelResponse,
)
from codepilot.core.models.factory import create_model_client
from codepilot.core.models.mock_client import MockModelClient
from codepilot.core.models.ollama_client import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    PREFERRED_CODER_MODEL,
    ModelNotFoundError,
    OllamaClient,
    OllamaRuntimeError,
)
from codepilot.core.models.standalone_client import StandaloneGGUFClient

__all__ = [
    "BaseModelClient",
    "ModelRequest",
    "ModelResponse",
    "ModelInfo",
    "OllamaClient",
    "MockModelClient",
    "StandaloneGGUFClient",
    "create_model_client",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_OLLAMA_MODEL",
    "PREFERRED_CODER_MODEL",
    "OllamaRuntimeError",
    "ModelNotFoundError",
]

