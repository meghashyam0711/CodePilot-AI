"""Standalone GGUF local model runtime client for CodePilot AI.

Provides Ollama-independent local inference using llama-cpp-python or standalone
GGUF binaries while maintaining model weights external to Git history.
"""

import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from codepilot.config import CodePilotConfig
from codepilot.core.models.base import (
    BaseModelClient,
    ModelInfo,
    ModelRequest,
    ModelResponse,
)
from codepilot.logging_config import get_logger

logger = get_logger("models.standalone")

DEFAULT_GGUF_FILENAME = "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
DEFAULT_MODELS_DIR = "models"
# Official HuggingFace Qwen2.5-Coder-1.5B-Instruct-GGUF reference metadata
RECOMMENDED_MODEL_NAME = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
RECOMMENDED_GGUF_FILE = "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"


def compute_file_sha256(file_path: Path, max_bytes: Optional[int] = None) -> str:
    """Compute SHA256 hex digest of a local file."""
    hasher = hashlib.sha256()
    bytes_read = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
            bytes_read += len(chunk)
            if max_bytes and bytes_read >= max_bytes:
                break
    return hasher.hexdigest()


class StandaloneGGUFClient(BaseModelClient):
    """Standalone local GGUF model client."""

    def __init__(
        self,
        config: Optional[CodePilotConfig] = None,
        model_path: Optional[Path] = None,
        expected_sha256: Optional[str] = None,
    ) -> None:
        self.config = config or CodePilotConfig()
        root = self.config.project_root.resolve()

        if model_path:
            p = Path(model_path)
            self.model_path = (
                p.resolve()
                if p.is_absolute()
                else (root / p).resolve()
            )
        elif self.config.model_id and Path(self.config.model_id).suffix.lower() == ".gguf":
            model_id_path = Path(self.config.model_id)
            self.model_path = (
                model_id_path.resolve()
                if model_id_path.is_absolute()
                else (root / model_id_path).resolve()
            )
        else:
            self.model_path = (root / DEFAULT_MODELS_DIR / DEFAULT_GGUF_FILENAME).resolve()

        self.expected_sha256 = expected_sha256
        self._llm: Any = None
        self._current_n_ctx: int = 0
        self._llm_lock = threading.Lock()

    @property
    def provider_name(self) -> str:
        return "standalone"

    @property
    def default_model_name(self) -> str:
        return self.model_path.name

    def health_check(self) -> Tuple[bool, str]:
        """Verify whether standalone inference engine and model file are available."""
        # Check llama-cpp-python optional dependency
        try:
            import llama_cpp  # type: ignore # noqa: F401
            has_llama_cpp = True
        except ImportError:
            has_llama_cpp = False

        if not has_llama_cpp:
            return (
                False,
                "Standalone GGUF runtime requires 'llama-cpp-python'.\n"
                "To install the optional standalone runtime: pip install 'codepilot-ai[standalone]'\n"
                "Alternatively, configure 'model_provider = \"ollama\"' to use local Ollama daemon.",
            )

        # Check model file existence
        if not self.model_path.is_file():
            return (
                False,
                f"Model file not found at '{self.model_path}'.\n"
                f"Place '{self.model_path.name}' in '{self.model_path.parent}' or set 'model_id' in '.codepilot.toml'.",
            )

        # Check model file size
        try:
            size_mb = round(self.model_path.stat().st_size / (1024 * 1024), 1)
            if size_mb < 50:
                return (
                    False,
                    f"Model file '{self.model_path}' appears incomplete or corrupted ({size_mb} MB).",
                )
        except OSError as e:
            return False, f"Could not access model file '{self.model_path}': {e}"

        # Verify checksum if expected hash is configured
        if self.expected_sha256:
            real_hash = compute_file_sha256(self.model_path)
            if real_hash.lower() != self.expected_sha256.lower():
                return (
                    False,
                    f"Checksum mismatch for '{self.model_path.name}'.\n"
                    f"Expected: {self.expected_sha256}\n"
                    f"Actual:   {real_hash}",
                )

        return True, f"Standalone GGUF runtime ready with '{self.model_path.name}' ({size_mb} MB)."

    def is_available(self) -> bool:
        """Check if standalone inference engine is ready."""
        healthy, _ = self.health_check()
        return healthy

    def get_model_info(self, model_name: Optional[str] = None) -> Optional[ModelInfo]:
        """Retrieve model metadata if model file exists."""
        size_gb = "unknown"
        if self.model_path.is_file():
            try:
                size_gb = f"{round(self.model_path.stat().st_size / (1024 * 1024 * 1024), 2)} GB"
            except OSError:
                pass
        return ModelInfo(
            name=model_name or self.model_path.name,
            parameter_size=size_gb,
            quantization="GGUF",
            context_length=self.config.max_context_tokens,
            is_available=self.is_available(),
            provider="standalone",
            family="qwen2",
        )

    def list_available_models(self) -> List[ModelInfo]:
        """Scan models directory for available GGUF files."""
        models: List[ModelInfo] = []
        models_dir = self.model_path.parent
        if models_dir.is_dir():
            for f in sorted(models_dir.glob("*.gguf")):
                try:
                    size_gb = round(f.stat().st_size / (1024 * 1024 * 1024), 2)
                    models.append(
                        ModelInfo(
                            name=f.name,
                            parameter_size=f"{size_gb} GB",
                            quantization="GGUF",
                            context_length=self.config.max_context_tokens,
                            is_available=True,
                            provider="standalone",
                            family="qwen2",
                        )
                    )
                except OSError:
                    pass
        return models

    def _get_or_load_model(self, n_ctx: Optional[int] = None) -> Any:
        """Lazily load and cache the Llama model instance with thread safety."""
        max_ctx = getattr(self.config, "max_context_tokens", 2048)
        target_ctx = n_ctx if n_ctx is not None else max_ctx
        target_ctx = max(target_ctx, max_ctx)

        if self._llm is not None and getattr(self, "_current_n_ctx", 0) >= target_ctx:
            return self._llm

        with self._llm_lock:
            if self._llm is not None and getattr(self, "_current_n_ctx", 0) >= target_ctx:
                return self._llm

            from llama_cpp import Llama  # type: ignore

            n_gpu_layers = getattr(self.config, "n_gpu_layers", 0)
            threads = os.cpu_count() or 4

            logger.info(
                "Loading standalone GGUF model from '%s' (n_ctx=%d, n_threads=%d, n_gpu_layers=%d)",
                self.model_path,
                target_ctx,
                threads,
                n_gpu_layers,
            )

            self._llm = Llama(
                model_path=str(self.model_path),
                n_ctx=target_ctx,
                n_threads=threads,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )
            self._current_n_ctx = target_ctx
            return self._llm

    def generate(
        self,
        request: ModelRequest,
        model_name: Optional[str] = None,
    ) -> ModelResponse:
        """Run standalone local inference."""
        healthy, msg = self.health_check()
        if not healthy:
            raise RuntimeError(f"Standalone runtime unavailable: {msg}")

        start_time = time.perf_counter()

        try:
            prompt_full = request.prompt
            if request.system_prompt:
                prompt_full = (
                    f"<|im_start|>system\n{request.system_prompt}<|im_end|>\n"
                    f"<|im_start|>user\n{request.prompt}<|im_end|>\n"
                    f"<|im_start|>assistant\n"
                )

            max_ctx = getattr(self.config, "max_context_tokens", 2048)
            estimated_prompt_tokens = max(1, len(prompt_full) // 4)

            # Check for context overflow
            if estimated_prompt_tokens >= max_ctx:
                raise RuntimeError(
                    f"Prompt length (~{estimated_prompt_tokens} tokens) exceeds maximum context window ({max_ctx} tokens)."
                )

            # Dynamically size context window requirement
            required_tokens = estimated_prompt_tokens + request.max_tokens
            effective_max_tokens = request.max_tokens

            if required_tokens > max_ctx:
                clamped_tokens = max(16, max_ctx - estimated_prompt_tokens)
                logger.warning(
                    "Requested max_tokens (%d) + prompt (~%d tokens) exceeds max_context_tokens (%d); "
                    "clamping generation max_tokens to %d.",
                    request.max_tokens,
                    estimated_prompt_tokens,
                    max_ctx,
                    clamped_tokens,
                )
                effective_max_tokens = clamped_tokens
                required_tokens = max_ctx

            llm = self._get_or_load_model(n_ctx=max(max_ctx, required_tokens))

            output = llm(
                prompt_full,
                max_tokens=effective_max_tokens,
                temperature=request.temperature,
                stop=["<|im_end|>", "```\n\n"],
            )

            completion = output["choices"][0]["text"].strip()
            latency = time.perf_counter() - start_time
            usage = output.get("usage", {})

            return ModelResponse(
                content=completion,
                model_name=self.model_path.name,
                prompt_tokens=usage.get("prompt_tokens", len(request.prompt) // 4),
                completion_tokens=usage.get("completion_tokens", len(completion) // 4),
                total_tokens=usage.get("total_tokens", (len(request.prompt) + len(completion)) // 4),
                latency_seconds=latency,
            )

        except Exception as e:
            raise RuntimeError(f"Standalone GGUF generation failed: {e}") from e

