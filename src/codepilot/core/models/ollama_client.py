"""Ollama local model runtime client for CodePilot AI.

Connects to local Ollama daemon (default http://localhost:11434) using standard library
HTTP facilities, supporting Qwen 2.5 1.5B Instruct and Qwen 2.5 Coder 1.5B without
installing external dependencies.
"""

import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request

from codepilot.core.models.base import (
    BaseModelClient,
    ModelInfo,
    ModelRequest,
    ModelResponse,
)
from codepilot.exceptions import CodePilotError
from codepilot.logging_config import get_logger

logger = get_logger("core.models.ollama")

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"
PREFERRED_CODER_MODEL = "qwen2.5-coder:1.5b"


class OllamaRuntimeError(CodePilotError):
    """Raised when Ollama service encounters a runtime or communication error."""

    pass


class ModelNotFoundError(OllamaRuntimeError):
    """Raised when the requested model is not installed in the local Ollama instance."""

    pass


class OllamaClient(BaseModelClient):
    """Local model runtime client communicating with Ollama REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        model_name: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._configured_model = model_name
        self.timeout = timeout
        self._cached_tags: Optional[List[Dict[str, Any]]] = None
        self._last_tags_check: float = 0.0

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def default_model_name(self) -> str:
        return self._configured_model or DEFAULT_OLLAMA_MODEL

    def _get_tags(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Fetch list of locally installed models from Ollama."""
        now = time.time()
        if not force_refresh and self._cached_tags is not None and (now - self._last_tags_check) < 10.0:
            return self._cached_tags

        url = f"{self.base_url}/api/tags"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=min(self.timeout, 5.0)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                self._cached_tags = models
                self._last_tags_check = now
                return models
        except Exception as e:
            logger.debug("Failed to connect to Ollama /api/tags: %s", e)
            self._cached_tags = None
            return []

    def is_available(self) -> bool:
        """Check if Ollama service is reachable on base_url."""
        try:
            models = self._get_tags(force_refresh=True)
            if self._cached_tags is not None:
                return True
            return bool(models)
        except Exception:
            return False

    def list_installed_models(self) -> List[str]:
        """Return list of model names currently installed in local Ollama."""
        tags = self._get_tags(force_refresh=True)
        return [m.get("name", "") for m in tags if m.get("name")]

    def resolve_model_name(self, requested_model: Optional[str] = None) -> str:
        """Determine the model name to use, choosing preferred coding models when available."""
        if requested_model:
            return requested_model
        if self._configured_model:
            return self._configured_model

        installed = self.list_installed_models()
        # Prefer coding-specialized model if installed
        if PREFERRED_CODER_MODEL in installed:
            return PREFERRED_CODER_MODEL
        for m in installed:
            if m.startswith("qwen2.5-coder"):
                return m

        # Fallback to default instruction model if installed
        if DEFAULT_OLLAMA_MODEL in installed:
            return DEFAULT_OLLAMA_MODEL
        for m in installed:
            if m.startswith("qwen2.5:1.5b"):
                return m

        # Return configured default
        return DEFAULT_OLLAMA_MODEL

    def get_model_info(self, model_name: Optional[str] = None) -> Optional[ModelInfo]:
        """Retrieve metadata for a model from local Ollama tags."""
        target = self.resolve_model_name(model_name)
        tags = self._get_tags()
        for m in tags:
            name = m.get("name", "")
            if name == target or name.startswith(target) or target.startswith(name):
                details = m.get("details", {})
                return ModelInfo(
                    name=name,
                    parameter_size=details.get("parameter_size", "unknown"),
                    quantization=details.get("quantization_level", "unknown"),
                    context_length=details.get("context_length", 4096),
                    is_available=True,
                    provider="ollama",
                    family=details.get("family", "qwen2"),
                    details=details,
                )
        return None

    def health_check(self) -> Tuple[bool, str]:
        """Verify Ollama connectivity and model presence."""
        try:
            models = self._get_tags(force_refresh=True)
        except Exception as e:
            return (
                False,
                f"Cannot connect to Ollama at {self.base_url}. Ensure Ollama is running ('ollama serve'). Error: {e}",
            )

        if not models and self._cached_tags is None:
            return False, f"Ollama is unreachable at {self.base_url}."

        installed_names = [m.get("name", "") for m in models]
        target_model = self.resolve_model_name()

        if target_model not in installed_names and not any(m.startswith(target_model.split(":")[0]) for m in installed_names):
            return (
                False,
                f"Ollama is running at {self.base_url}, but target model '{target_model}' is not installed.\n"
                f"Installed models: {installed_names or 'none'}.\n"
                f"Run 'ollama run {target_model}' to pull it before use.",
            )

        return True, f"Ollama is healthy at {self.base_url}. Active model: '{target_model}'."

    def generate(self, request: ModelRequest, model_name: Optional[str] = None) -> ModelResponse:
        """Send inference request to local Ollama /api/generate endpoint.

        Args:
            request: ModelRequest containing prompt, system prompt, and sampling options.
            model_name: Optional model override.

        Returns:
            ModelResponse with generated completion text and token metrics.

        Raises:
            OllamaRuntimeError: If service is unreachable, returns an error, or times out.
            ModelNotFoundError: If requested model is not installed.
        """
        target_model = self.resolve_model_name(model_name)

        # Pre-check model availability
        installed = self.list_installed_models()
        if installed and target_model not in installed:
            matched = False
            for m in installed:
                if m.startswith(target_model.split(":")[0]) or target_model.startswith(m.split(":")[0]):
                    target_model = m
                    matched = True
                    break
            if not matched:
                raise ModelNotFoundError(
                    f"Model '{target_model}' is not available in local Ollama.\n"
                    f"Installed models: {installed}.\n"
                    f"Note: CodePilot AI does not download models automatically. Pull it manually with: ollama pull {target_model}"
                )

        payload: Dict[str, Any] = {
            "model": target_model,
            "prompt": request.prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }

        if request.system_prompt:
            payload["system"] = request.system_prompt

        if request.stop_sequences:
            payload["options"]["stop"] = request.stop_sequences

        body_bytes = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/api/generate"
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start_time = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw_data = resp.read().decode("utf-8")
                res_json = json.loads(raw_data)
        except urllib.error.URLError as e:
            latency = time.time() - start_time
            if "timed out" in str(e).lower():
                raise OllamaRuntimeError(
                    f"Inference request timed out after {self.timeout:.1f}s. "
                    f"Consider shortening prompt context or increasing request_timeout."
                ) from e
            raise OllamaRuntimeError(
                f"Failed to communicate with Ollama at {self.base_url}: {e}.\n"
                f"Ensure Ollama daemon is active with: ollama serve"
            ) from e
        except Exception as e:
            raise OllamaRuntimeError(f"Unexpected error during Ollama inference: {e}") from e

        latency = time.time() - start_time
        response_text = res_json.get("response", "")
        prompt_tokens = res_json.get("prompt_eval_count", 0)
        completion_tokens = res_json.get("eval_count", 0)

        return ModelResponse(
            content=response_text,
            model_name=target_model,
            latency_seconds=round(latency, 3),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            finish_reason="stop" if res_json.get("done") else "length",
            metadata={
                "eval_duration_ms": res_json.get("eval_duration", 0) // 1_000_000,
                "total_duration_ms": res_json.get("total_duration", 0) // 1_000_000,
            },
        )
