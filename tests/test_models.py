"""Unit tests for Phase 4 Model Runtime interface, Ollama client, and Mock client."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from codepilot.config import CodePilotConfig
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
    PREFERRED_CODER_MODEL,
    ModelNotFoundError,
    OllamaClient,
    OllamaRuntimeError,
)
from codepilot.core.models.standalone_client import StandaloneGGUFClient, compute_file_sha256
from codepilot.exceptions import CodePilotError, ConfigurationError


class TestMockModelClient:
    """Tests for MockModelClient deterministic behavior."""

    def test_mock_client_defaults(self) -> None:
        client = MockModelClient()
        assert client.provider_name == "mock"
        assert client.default_model_name == "mock-qwen2.5-coder:1.5b"
        assert client.is_available() is True

        healthy, msg = client.health_check()
        assert healthy is True
        assert "healthy" in msg.lower()

        info = client.get_model_info()
        assert info is not None
        assert info.name == "mock-qwen2.5-coder:1.5b"
        assert info.parameter_size == "1.5B"
        assert info.quantization == "Q4_K_M"

    def test_mock_client_generate_default(self) -> None:
        client = MockModelClient()
        req = ModelRequest(prompt="Write a function to add two numbers")
        resp = client.generate(req)

        assert isinstance(resp, ModelResponse)
        assert resp.model_name == "mock-qwen2.5-coder:1.5b"
        assert len(resp.content) > 0
        assert resp.prompt_tokens > 0
        assert resp.completion_tokens > 0
        assert resp.total_tokens == resp.prompt_tokens + resp.completion_tokens
        assert client.call_count == 1
        assert client.last_request == req

        resp_dict = resp.to_dict()
        assert resp_dict["content"] == resp.content
        assert resp_dict["model_name"] == resp.model_name

    def test_mock_client_task_types(self) -> None:
        client = MockModelClient()

        docs_req = ModelRequest(prompt="Document this", task_type="AI-DOCS")
        docs_resp = client.generate(docs_req)
        assert "docstring" in docs_resp.content.lower()

        test_req = ModelRequest(prompt="Test this", task_type="AI-TEST")
        test_resp = client.generate(test_req)
        assert "unit test" in test_resp.content.lower()

    def test_mock_client_custom_handler(self) -> None:
        client = MockModelClient(
            response_handler=lambda r: f"Custom response for {r.prompt[:10]}"
        )
        resp = client.generate(ModelRequest(prompt="Hello CodePilot World"))
        assert resp.content == "Custom response for Hello Code"

    def test_mock_client_unavailable(self) -> None:
        client = MockModelClient(available=False)
        assert client.is_available() is False

        healthy, msg = client.health_check()
        assert healthy is False
        assert "unavailable" in msg.lower()

        with pytest.raises(CodePilotError, match="unavailable"):
            client.generate(ModelRequest(prompt="Hello"))

    def test_mock_client_simulated_error(self) -> None:
        client = MockModelClient(simulate_error=RuntimeError("Simulated GPU OOM"))
        with pytest.raises(RuntimeError, match="Simulated GPU OOM"):
            client.generate(ModelRequest(prompt="Hello"))


class TestOllamaClient:
    """Tests for OllamaClient REST interaction using mocked HTTP responses."""

    def test_ollama_init_defaults(self) -> None:
        client = OllamaClient()
        assert client.provider_name == "ollama"
        assert client.default_model_name == DEFAULT_OLLAMA_MODEL
        assert client.base_url == "http://localhost:11434"

    def test_resolve_model_name_prefers_coder(self) -> None:
        client = OllamaClient()
        # Mock tags containing both coder and general model
        mock_tags = [
            {"name": "qwen2.5:1.5b-instruct-q4_K_M"},
            {"name": "qwen2.5-coder:1.5b"},
        ]
        with patch.object(client, "_get_tags", return_value=mock_tags):
            resolved = client.resolve_model_name()
            assert resolved == PREFERRED_CODER_MODEL

    def test_resolve_model_name_falls_back_to_instruct(self) -> None:
        client = OllamaClient()
        mock_tags = [{"name": "qwen2.5:1.5b-instruct-q4_K_M"}]
        with patch.object(client, "_get_tags", return_value=mock_tags):
            resolved = client.resolve_model_name()
            assert resolved == DEFAULT_OLLAMA_MODEL

    def test_resolve_model_name_explicit_override(self) -> None:
        client = OllamaClient(model_name="custom-model:latest")
        assert client.resolve_model_name("override:latest") == "override:latest"
        assert client.resolve_model_name() == "custom-model:latest"

    def test_is_available_success(self) -> None:
        client = OllamaClient()
        with patch.object(client, "_get_tags", return_value=[{"name": "test:1b"}]):
            assert client.is_available() is True

    def test_is_available_failure(self) -> None:
        client = OllamaClient()
        client._cached_tags = None
        with patch.object(client, "_get_tags", return_value=[]):
            # When unreachable and _cached_tags is empty/None
            client._cached_tags = None
            assert client.is_available() is False

    def test_health_check_healthy(self) -> None:
        client = OllamaClient(model_name="qwen2.5-coder:1.5b")
        mock_tags = [{"name": "qwen2.5-coder:1.5b"}]
        with patch.object(client, "_get_tags", return_value=mock_tags):
            healthy, msg = client.health_check()
            assert healthy is True
            assert "healthy" in msg.lower()
            assert "qwen2.5-coder:1.5b" in msg

    def test_health_check_model_missing(self) -> None:
        client = OllamaClient(model_name="missing-model:1.5b")
        mock_tags = [{"name": "qwen:latest"}]
        with patch.object(client, "_get_tags", return_value=mock_tags):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "not installed" in msg.lower()

    def test_health_check_unreachable(self) -> None:
        client = OllamaClient()
        client._cached_tags = None
        with patch.object(client, "_get_tags", side_effect=Exception("Connection refused")):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "cannot connect" in msg.lower()

    def test_generate_success(self) -> None:
        client = OllamaClient()
        mock_tags = [{"name": "qwen2.5-coder:1.5b"}]

        mock_api_resp = json.dumps({
            "model": "qwen2.5-coder:1.5b",
            "response": "def add(a: int, b: int) -> int:\n    return a + b",
            "done": True,
            "prompt_eval_count": 25,
            "eval_count": 18,
            "eval_duration": 180_000_000,
            "total_duration": 250_000_000,
        }).encode("utf-8")

        mock_resp_obj = MagicMock()
        mock_resp_obj.read.return_value = mock_api_resp
        mock_resp_obj.__enter__.return_value = mock_resp_obj

        with patch.object(client, "_get_tags", return_value=mock_tags), \
             patch("urllib.request.urlopen", return_value=mock_resp_obj):
            req = ModelRequest(
                prompt="Write add function",
                system_prompt="You are an expert coder",
                temperature=0.2,
                max_tokens=256,
            )
            resp = client.generate(req)

            assert isinstance(resp, ModelResponse)
            assert "def add" in resp.content
            assert resp.prompt_tokens == 25
            assert resp.completion_tokens == 18
            assert resp.total_tokens == 43
            assert resp.finish_reason == "stop"

    def test_generate_missing_model_raises_model_not_found(self) -> None:
        client = OllamaClient(model_name="nonexistent:model")
        mock_tags = [{"name": "qwen2.5:1.5b"}]

        with patch.object(client, "_get_tags", return_value=mock_tags):
            req = ModelRequest(prompt="Hello")
            with pytest.raises(ModelNotFoundError, match="not available"):
                client.generate(req)

    def test_generate_timeout_raises_ollama_runtime_error(self) -> None:
        client = OllamaClient(model_name="qwen2.5-coder:1.5b")
        mock_tags = [{"name": "qwen2.5-coder:1.5b"}]

        with patch.object(client, "_get_tags", return_value=mock_tags), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
            req = ModelRequest(prompt="Hello")
            with pytest.raises(OllamaRuntimeError, match="timed out"):
                client.generate(req)

    def test_get_model_info(self) -> None:
        client = OllamaClient()
        mock_tags = [{
            "name": "qwen2.5-coder:1.5b",
            "details": {
                "parameter_size": "1.5B",
                "quantization_level": "Q4_K_M",
                "family": "qwen2",
            },
        }]
        with patch.object(client, "_get_tags", return_value=mock_tags):
            info = client.get_model_info("qwen2.5-coder:1.5b")
            assert info is not None
            assert info.name == "qwen2.5-coder:1.5b"
            assert info.parameter_size == "1.5B"
            assert info.quantization == "Q4_K_M"
            assert info.family == "qwen2"


class TestModelFactory:
    """Tests for create_model_client factory function."""

    def test_create_mock_client(self, sample_config: CodePilotConfig) -> None:
        client = create_model_client(sample_config, override_provider="mock")
        assert isinstance(client, MockModelClient)
        assert client.provider_name == "mock"

    def test_create_ollama_client(self, sample_config: CodePilotConfig) -> None:
        client = create_model_client(sample_config, override_provider="ollama")
        assert isinstance(client, OllamaClient)
        assert client.provider_name == "ollama"

    def test_create_none_raises_configuration_error(self, sample_config: CodePilotConfig) -> None:
        with pytest.raises(ConfigurationError, match="not configured"):
            create_model_client(sample_config)

    def test_create_unsupported_provider(self, sample_config: CodePilotConfig) -> None:
        with pytest.raises(ConfigurationError, match="Unsupported model provider"):
            create_model_client(sample_config, override_provider="unknown_provider")

    def test_create_standalone_client(self, sample_config: CodePilotConfig) -> None:
        client = create_model_client(sample_config, override_provider="standalone")
        assert isinstance(client, StandaloneGGUFClient)
        assert client.provider_name == "standalone"
        expected_default = (sample_config.project_root / "models" / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf").resolve()
        assert client.model_path == expected_default

    def test_create_standalone_client_with_explicit_model_override(
        self, sample_config: CodePilotConfig, tmp_path: Path
    ) -> None:
        """Verify that override_model is forwarded to StandaloneGGUFClient as model_path."""
        custom_model = tmp_path / "custom_model.gguf"
        client = create_model_client(
            sample_config,
            override_provider="standalone",
            override_model=str(custom_model),
        )
        assert isinstance(client, StandaloneGGUFClient)
        assert client.model_path == custom_model.resolve()

    def test_create_standalone_client_with_relative_model_override(
        self, tmp_path: Path
    ) -> None:
        """Verify that relative model overrides resolve against project_root."""
        cfg = CodePilotConfig(project_root=tmp_path)
        client = create_model_client(
            cfg,
            override_provider="standalone",
            override_model="models/my_relative_model.gguf",
        )
        assert isinstance(client, StandaloneGGUFClient)
        assert client.model_path == (tmp_path / "models" / "my_relative_model.gguf").resolve()

    def test_create_standalone_client_config_gguf_model_id(self, tmp_path: Path) -> None:
        """Verify that config.model_id is respected if it has .gguf extension and no override is passed."""
        cfg = CodePilotConfig(project_root=tmp_path, model_id="configured_model.gguf")
        client = create_model_client(cfg, override_provider="standalone")
        assert isinstance(client, StandaloneGGUFClient)
        assert client.model_path == (tmp_path / "configured_model.gguf").resolve()

    def test_create_gguf_alias_client(self, sample_config: CodePilotConfig) -> None:
        client = create_model_client(sample_config, override_provider="gguf")
        assert isinstance(client, StandaloneGGUFClient)
        assert client.provider_name == "standalone"

    def test_create_gguf_alias_with_model_override(
        self, sample_config: CodePilotConfig, tmp_path: Path
    ) -> None:
        """Verify that the 'gguf' alias correctly forwards the model override."""
        custom = tmp_path / "alias_model.gguf"
        client = create_model_client(
            sample_config,
            override_provider="gguf",
            override_model=str(custom),
        )
        assert isinstance(client, StandaloneGGUFClient)
        assert client.model_path == custom.resolve()


class TestStandaloneGGUFClient:
    """Deterministic tests for StandaloneGGUFClient without requiring real weights or CUDA."""

    def test_standalone_defaults(self, tmp_path: Path) -> None:
        cfg = CodePilotConfig(project_root=tmp_path)
        client = StandaloneGGUFClient(config=cfg)
        assert client.provider_name == "standalone"
        assert client.default_model_name == "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
        assert client.model_path == tmp_path / "models" / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
        assert client._llm is None

    def test_standalone_relative_and_absolute_model_paths(self, tmp_path: Path) -> None:
        """Verify relative paths resolve against project_root and absolute paths stay absolute."""
        cfg = CodePilotConfig(project_root=tmp_path)

        # Relative path
        client_rel = StandaloneGGUFClient(config=cfg, model_path=Path("sub/model.gguf"))
        assert client_rel.model_path == (tmp_path / "sub" / "model.gguf").resolve()

        # Absolute path outside project root
        abs_target = (tmp_path / "other" / "abs.gguf").resolve()
        client_abs = StandaloneGGUFClient(config=cfg, model_path=abs_target)
        assert client_abs.model_path == abs_target

    def test_standalone_missing_dependency(self, tmp_path: Path) -> None:
        client = StandaloneGGUFClient(model_path=tmp_path / "model.gguf")
        with patch.dict("sys.modules", {"llama_cpp": None}):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "llama-cpp-python" in msg
            assert "codepilot-ai[standalone]" in msg

    def test_standalone_missing_model_file(self, tmp_path: Path) -> None:
        client = StandaloneGGUFClient(model_path=tmp_path / "missing.gguf")
        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "Model file not found" in msg

    def test_standalone_corrupt_or_small_file(self, tmp_path: Path) -> None:
        small_file = tmp_path / "small.gguf"
        small_file.write_bytes(b"small_header")
        client = StandaloneGGUFClient(model_path=small_file)
        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "incomplete or corrupted" in msg

    def test_standalone_checksum_mismatch_and_match(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "test.gguf"
        fake_file.write_bytes(b"x")
        client = StandaloneGGUFClient(model_path=fake_file, expected_sha256="expected_hash")

        mock_stat = MagicMock()
        mock_stat.st_size = 100 * 1024 * 1024

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "stat", return_value=mock_stat), \
             patch("codepilot.core.models.standalone_client.compute_file_sha256", return_value="different_hash"):
            healthy, msg = client.health_check()
            assert healthy is False
            assert "Checksum mismatch" in msg

        with patch.dict("sys.modules", {"llama_cpp": MagicMock()}), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "stat", return_value=mock_stat), \
             patch("codepilot.core.models.standalone_client.compute_file_sha256", return_value="expected_hash"):
            healthy, msg = client.health_check()
            assert healthy is True
            assert "ready with" in msg.lower()

    def test_standalone_generate_success(self, tmp_path: Path) -> None:
        mock_llama_inst = MagicMock()
        mock_llama_inst.return_value = {
            "choices": [{"text": "```python\ndef add(a, b):\n    return a + b\n```"}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 15, "total_tokens": 40},
        }
        mock_llama_class = MagicMock(return_value=mock_llama_inst)

        client = StandaloneGGUFClient(model_path=tmp_path / "qwen.gguf")
        with patch.object(client, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            req = ModelRequest(
                prompt="Write add function",
                system_prompt="You are a code assistant.",
                max_tokens=512,
                temperature=0.2,
            )
            resp = client.generate(req)

            assert isinstance(resp, ModelResponse)
            assert resp.content == "```python\ndef add(a, b):\n    return a + b\n```"
            assert resp.completion == resp.content
            assert resp.model_name == "qwen.gguf"
            assert resp.prompt_tokens == 25
            assert resp.completion_tokens == 15
            assert resp.total_tokens == 40
            assert resp.latency_seconds > 0.0

    def test_standalone_model_instance_reused(self, tmp_path: Path) -> None:
        mock_llama_inst = MagicMock()
        mock_llama_inst.return_value = {
            "choices": [{"text": "result"}],
            "usage": {},
        }
        mock_llama_class = MagicMock(return_value=mock_llama_inst)

        client = StandaloneGGUFClient(model_path=tmp_path / "qwen.gguf")
        with patch.object(client, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            client.generate(ModelRequest(prompt="call 1"))
            client.generate(ModelRequest(prompt="call 2"))

            assert mock_llama_class.call_count == 1
            assert mock_llama_inst.call_count == 2

    def test_standalone_gpu_layers_configuration(self, tmp_path: Path) -> None:
        mock_llama_class = MagicMock()

        # Custom n_gpu_layers = 33
        cfg_custom = CodePilotConfig(project_root=tmp_path, n_gpu_layers=33)
        client_custom = StandaloneGGUFClient(config=cfg_custom, model_path=tmp_path / "qwen.gguf")
        with patch.object(client_custom, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            client_custom.generate(ModelRequest(prompt="test"))
            mock_llama_class.assert_called_once()
            _, kwargs = mock_llama_class.call_args
            assert kwargs.get("n_gpu_layers") == 33

        # Default n_gpu_layers = 0 (CPU-only)
        mock_llama_class.reset_mock()
        cfg_default = CodePilotConfig(project_root=tmp_path)
        client_default = StandaloneGGUFClient(config=cfg_default, model_path=tmp_path / "qwen.gguf")
        with patch.object(client_default, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            client_default.generate(ModelRequest(prompt="test"))
            mock_llama_class.assert_called_once()
            _, kwargs_def = mock_llama_class.call_args
            assert kwargs_def.get("n_gpu_layers") == 0

    def test_standalone_list_available_models(self, tmp_path: Path) -> None:
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "alpha.gguf").write_bytes(b"alpha" * 100)
        (models_dir / "beta.gguf").write_bytes(b"beta" * 100)
        (models_dir / "notes.txt").write_bytes(b"notes")

        client = StandaloneGGUFClient(model_path=models_dir / "alpha.gguf")
        models = client.list_available_models()
        assert len(models) == 2
        names = [m.name for m in models]
        assert names == ["alpha.gguf", "beta.gguf"]
        assert models[0].provider == "standalone"
        assert models[0].quantization == "GGUF"
        assert models[0].is_available is True

    def test_standalone_error_handling_no_silent_fallback(self, tmp_path: Path) -> None:
        client = StandaloneGGUFClient(model_path=tmp_path / "qwen.gguf")

        # Health check failure raises RuntimeError
        with patch.object(client, "health_check", return_value=(False, "Missing weights")):
            with pytest.raises(RuntimeError, match="Standalone runtime unavailable: Missing weights"):
                client.generate(ModelRequest(prompt="test"))

        # Model inference crash raises RuntimeError without silent mock fallback
        failing_llama = MagicMock(side_effect=RuntimeError("CUDA out of memory"))
        with patch.object(client, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=MagicMock(return_value=failing_llama))}):
            with pytest.raises(RuntimeError, match="Standalone GGUF generation failed.*CUDA out of memory"):
                client.generate(ModelRequest(prompt="test"))

    def test_standalone_n_ctx_initialized_to_max_context_tokens(self, tmp_path: Path) -> None:
        """TASK-AUD-05: Verify n_ctx is sized to max_context_tokens rather than clamped to 2*max_tokens."""
        mock_llama_class = MagicMock()
        cfg = CodePilotConfig(project_root=tmp_path, max_context_tokens=4096)
        client = StandaloneGGUFClient(config=cfg, model_path=tmp_path / "qwen.gguf")

        with patch.object(client, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            client.generate(ModelRequest(prompt="hello", max_tokens=128))
            mock_llama_class.assert_called_once()
            _, kwargs = mock_llama_class.call_args
            assert kwargs.get("n_ctx") == 4096

    def test_standalone_prompt_exceeding_context_raises_clear_error(self, tmp_path: Path) -> None:
        """TASK-AUD-05: Verify prompt exceeding max_context_tokens raises clear error before inference."""
        cfg = CodePilotConfig(project_root=tmp_path, max_context_tokens=256)
        client = StandaloneGGUFClient(config=cfg, model_path=tmp_path / "qwen.gguf")

        # Create large prompt (~300 tokens > 256)
        huge_prompt = "word " * 300
        with patch.object(client, "health_check", return_value=(True, "Ready")):
            with pytest.raises(RuntimeError, match="exceeds maximum context window"):
                client.generate(ModelRequest(prompt=huge_prompt))

    def test_standalone_generation_clamps_max_tokens_when_near_context_limit(self, tmp_path: Path) -> None:
        """TASK-AUD-05: Verify max_tokens is clamped when prompt is near context window boundary."""
        mock_llama_inst = MagicMock(return_value={"choices": [{"text": "safe completion"}], "usage": {}})
        mock_llama_class = MagicMock(return_value=mock_llama_inst)

        cfg = CodePilotConfig(project_root=tmp_path, max_context_tokens=500)
        client = StandaloneGGUFClient(config=cfg, model_path=tmp_path / "qwen.gguf")

        # 400 chars ~ 100 tokens. Max tokens requested = 450 (100 + 450 = 550 > 500)
        prompt = "a" * 400
        with patch.object(client, "health_check", return_value=(True, "Ready")), \
             patch.dict("sys.modules", {"llama_cpp": MagicMock(Llama=mock_llama_class)}):
            resp = client.generate(ModelRequest(prompt=prompt, max_tokens=450))
            assert resp.content == "safe completion"
            # Clamped to max_ctx - prompt_tokens = 500 - 100 = 400
            _, call_kwargs = mock_llama_inst.call_args
            assert call_kwargs.get("max_tokens") <= 400

