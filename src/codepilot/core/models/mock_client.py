"""Deterministic mock model client for testing and offline development."""

from typing import Any, Callable, Dict, List, Optional, Tuple

from codepilot.core.models.base import (
    BaseModelClient,
    ModelInfo,
    ModelRequest,
    ModelResponse,
)
from codepilot.exceptions import CodePilotError


BENCHMARK_MOCK_RESPONSES: Dict[str, str] = {
    "eval-01-basic-function": (
        "### Analysis of Instruction\n"
        "Implement basic addition of two integers.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n"
        "```"
    ),
    "eval-02-bug-fixing": (
        "### Analysis of Instruction\n"
        "Fix off-by-one boundary error in list iteration.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def scan_prices(prices: list) -> int:\n"
        "    for i in range(len(prices)):\n"
        "        if prices[i] < 0:\n"
        "            return i\n"
        "    return -1\n"
        "```"
    ),
    "eval-03-missing-return": (
        "### Analysis of Instruction\n"
        "Add missing return statement for average calculation.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def compute_average(total: float, count: int) -> float:\n"
        "    if count == 0:\n"
        "        return 0.0\n"
        "    return total / count\n"
        "```"
    ),
    "eval-04-boundary-conditions": (
        "### Analysis of Instruction\n"
        "Safely handle empty list boundary condition.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def get_first_item(items: list):\n"
        "    if not items:\n"
        "        return None\n"
        "    return items[0]\n"
        "```"
    ),
    "eval-05-existing-body-modification": (
        "### Analysis of Instruction\n"
        "Double positive values and return 0 otherwise.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def process_number(val: int) -> int:\n"
        "    if val > 0:\n"
        "        return val * 2\n"
        "    return 0\n"
        "```"
    ),
    "eval-06-malformed-empty-output": (
        "### Analysis of Instruction\n"
        "Implement safe integer conversion with fallback.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def parse_integer_safe(val: str, default: int = 0) -> int:\n"
        "    try:\n"
        "        return int(val)\n"
        "    except (ValueError, TypeError):\n"
        "        return default\n"
        "```"
    ),
    "eval-07-clarification-request": (
        "Could you please clarify what authentication criteria or provider should be used?"
    ),
    "eval-08-security-sensitive": (
        "### Analysis of Instruction\n"
        "Sanitize path inputs to prevent directory traversal without external imports.\n\n"
        "### Proposed Implementation\n"
        "```python\n"
        "def sanitize_filename(filename: str) -> str:\n"
        "    clean = filename.replace('\\\\', '/').split('/')[-1]\n"
        "    return clean.replace('..', '')\n"
        "```"
    ),
}


class MockModelClient(BaseModelClient):
    """Deterministic in-memory model client for testing without GPU/network resources."""

    def __init__(
        self,
        default_response: Optional[str] = None,
        available: bool = True,
        model_name: str = "mock-qwen2.5-coder:1.5b",
        simulate_error: Optional[Exception] = None,
        response_handler: Optional[Callable[[ModelRequest], str]] = None,
    ) -> None:
        self._has_explicit_default = default_response is not None
        self._default_response = default_response or (
            "### Analysis of Instruction\n"
            "This task requests a safe modification to handle edge cases.\n\n"
            "### Proposed Implementation\n"
            "```python\n"
            "# Added input validation check\n"
            "if not numbers:\n"
            "    return 0.0\n"
            "```\n\n"
            "### Assumptions and Risk\n"
            "- Low risk: Preserves existing function signature.\n"
            "- Assumes numbers is an iterable."
        )
        self._available = available
        self._model_name = model_name
        self._simulate_error = simulate_error
        self._response_handler = response_handler
        self.call_count: int = 0
        self.last_request: Optional[ModelRequest] = None

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def default_model_name(self) -> str:
        return self._model_name

    def is_available(self) -> bool:
        return self._available

    def get_model_info(self, model_name: Optional[str] = None) -> Optional[ModelInfo]:
        return ModelInfo(
            name=model_name or self._model_name,
            parameter_size="1.5B",
            quantization="Q4_K_M",
            context_length=4096,
            is_available=self._available,
            provider="mock",
            family="qwen2",
            details={"mock": True},
        )

    def health_check(self) -> Tuple[bool, str]:
        if not self._available:
            return False, "Mock model runtime is configured as unavailable."
        return True, f"Mock model runtime is healthy (model: {self._model_name})."

    def generate(self, request: ModelRequest, model_name: Optional[str] = None) -> ModelResponse:
        self.call_count += 1
        self.last_request = request

        if self._simulate_error:
            raise self._simulate_error

        if not self._available:
            raise CodePilotError("Mock model client is currently marked unavailable.")

        if self._response_handler:
            content = self._response_handler(request)
        elif self._has_explicit_default:
            content = self._default_response
        else:
            case_id = request.metadata.get("case_id") if request.metadata else None
            route = request.metadata.get("route") if request.metadata else None
            if case_id and case_id in BENCHMARK_MOCK_RESPONSES:
                content = BENCHMARK_MOCK_RESPONSES[case_id]
            elif route == "clarification":
                content = "Could you please clarify the specific requirements and expected behavior for this task?"
            elif request.task_type == "AI-DOCS":
                content = (
                    "```python\n"
                    '"""Compute and return the result for the given input docstring."""\n'
                    "```"
                )
            elif request.task_type == "AI-TEST":
                content = (
                    "### Test Generation Proposal\n"
                    "Add unit test cases verifying empty list, None input, and valid data."
                )
            else:
                content = self._default_response

        # Rough token estimation (1 token ≈ 4 chars)
        prompt_tokens = max(1, len(request.prompt) // 4)
        completion_tokens = max(1, len(content) // 4)

        return ModelResponse(
            content=content,
            model_name=model_name or self._model_name,
            latency_seconds=0.01,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            finish_reason="stop",
            metadata={"mock": True},
        )

