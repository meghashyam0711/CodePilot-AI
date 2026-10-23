"""Base abstractions and data models for local and remote LLM runtime clients."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ModelRequest:
    """Request payload for model inference."""

    prompt: str
    system_prompt: Optional[str] = None
    temperature: float = 0.1
    max_tokens: int = 1024
    stop_sequences: List[str] = field(default_factory=list)
    task_type: Optional[str] = None  # e.g. 'AI-FIX', 'AI-TASK', 'AI-REFACTOR', etc.
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    """Response returned from model inference."""

    content: str
    model_name: str
    latency_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: Optional[str] = "stop"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def completion(self) -> str:
        """Alias for content."""
        return self.content

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "model_name": self.model_name,
            "latency_seconds": self.latency_seconds,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "finish_reason": self.finish_reason,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class ModelInfo:
    """Metadata describing an available local or remote model."""

    name: str
    parameter_size: str = "unknown"
    quantization: str = "unknown"
    context_length: int = 4096
    is_available: bool = True
    provider: str = "ollama"
    family: str = "qwen2"
    details: Dict[str, Any] = field(default_factory=dict)


class BaseModelClient(ABC):
    """Abstract base class for model runtime clients."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Provider identifier (e.g. 'ollama', 'mock', 'gguf')."""
        pass

    @property
    @abstractmethod
    def default_model_name(self) -> str:
        """Default model name for this provider."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the model runtime/service is currently available and responsive."""
        pass

    @abstractmethod
    def get_model_info(self, model_name: Optional[str] = None) -> Optional[ModelInfo]:
        """Retrieve model metadata if available."""
        pass

    @abstractmethod
    def generate(self, request: ModelRequest, model_name: Optional[str] = None) -> ModelResponse:
        """Generate text completion from the model."""
        pass

    @abstractmethod
    def health_check(self) -> Tuple[bool, str]:
        """Perform health diagnostic check returning (healthy, diagnostic_message)."""
        pass
