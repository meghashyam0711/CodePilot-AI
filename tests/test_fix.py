"""Automated tests for the 'codepilot fix --preview' command and fixer engine."""

import json
from pathlib import Path
import pytest
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME


def test_fix_preview_fails_when_llm_not_configured(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'codepilot fix' fails cleanly when model_provider is 'none'."""
    test_file = tmp_path / "math.py"
    test_file.write_text("# AI: implement square(x)\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    # Requirement 11: Must report missing LLM configuration without pretending to fix
    assert "model provider is not configured" in result.output.lower()
    assert "CODEPILOT_MODEL_PROVIDER" in result.output


def test_fix_preview_generates_diff_without_modifying_files(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that 'fix --preview' displays Git diff and DOES NOT modify source files."""
    test_file = tmp_path / "math_service.py"
    original_content = (
        "def is_even(n):\n"
        "    # AI: return true if n is even\n"
        "    pass\n"
    )
    test_file.write_text(original_content, encoding="utf-8")

    # Configure mock provider in local .codepilot.toml
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Diff Preview Ready" in result.output
    assert "Diff for: math_service.py" in result.output
    assert "codepilot apply --approved" in result.output

    # Requirement 3: Source files on disk MUST NOT be modified!
    assert test_file.read_text(encoding="utf-8") == original_content

    # Pending patch file must be generated
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert pending_patch.exists()
    patch_data = json.loads(pending_patch.read_text(encoding="utf-8"))
    assert patch_data["is_approved"] is False
    assert patch_data["is_applied"] is False
    assert len(patch_data["file_patches"]) == 1


def test_fix_preview_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'fix --preview --json' outputs valid JSON patch bundle."""
    test_file = tmp_path / "calc.py"
    test_file.write_text("# AI: return true\ndef check():\n    pass\n", encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    result = cli_runner.invoke(
        app, ["fix", "--preview", "--directory", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "patch_id" in data
    assert "full_diff" in data
    assert len(data["file_patches"]) == 1


def test_fix_preview_missing_api_key_reports_error(
    cli_runner: CliRunner, tmp_path: Path, monkeypatch
) -> None:
    """Verify that external provider without API key raises actionable error."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "openai"\n', encoding="utf-8")

    code_file = tmp_path / "sample.py"
    code_file.write_text("# AI: add logging\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_fix_preview_ollama_unavailable_raises_truthful_error(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify Ollama runtime unavailability raises truthful FixError without silent mock fallback."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    from unittest.mock import patch

    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(False, "Connection refused: http://localhost:11434"),
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "model runtime 'ollama' is unavailable" in result.output.lower()
    # Confirm no pending patch was written
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert not pending_patch.exists()


def test_fix_preview_standalone_missing_model_raises_truthful_error(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify standalone runtime fails cleanly when GGUF model file is missing without falling back to mock."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text(
        'model_provider = "standalone"\nmodel_file = "non_existent.gguf"\n',
        encoding="utf-8",
    )

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert (
        "model runtime 'standalone' is unavailable" in result.output.lower()
        or "does not exist" in result.output.lower()
    )
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert not pending_patch.exists()


def test_fix_preview_inference_timeout_raises_truthful_error(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that inference timeout raises truthful FixError without fallback to mock."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    from unittest.mock import patch

    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(True, "OK"),
    ), patch(
        "codepilot.core.models.ollama_client.OllamaClient.generate",
        side_effect=RuntimeError("Request timed out after 30.0s"),
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "model inference failed" in result.output.lower()
    assert "timed out" in result.output.lower()
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert not pending_patch.exists()


def test_fix_preview_empty_model_response_raises_truthful_error(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that empty model completion returns truthful error without silent mock replacement."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    from unittest.mock import patch
    from codepilot.core.models.base import ModelResponse

    mock_resp = ModelResponse(
        content="",
        model_name="mock-model",
        prompt_tokens=10,
        completion_tokens=0,
    )
    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(True, "OK"),
    ), patch(
        "codepilot.core.models.ollama_client.OllamaClient.generate",
        return_value=mock_resp,
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "empty response" in result.output.lower()
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert not pending_patch.exists()


def test_fix_preview_unparseable_model_response_raises_truthful_error(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that unparseable model response produces truthful FixError."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    from unittest.mock import patch
    from codepilot.core.models.base import ModelResponse

    mock_resp = ModelResponse(
        content="    \n\n   ",
        model_name="mock-model",
        prompt_tokens=10,
        completion_tokens=5,
    )
    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(True, "OK"),
    ), patch(
        "codepilot.core.models.ollama_client.OllamaClient.generate",
        return_value=mock_resp,
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert (
        "could not extract usable code modification" in result.output.lower()
        or "empty response" in result.output.lower()
    )


def test_fix_preview_mock_mode_explicitly_labeled(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that explicit mock mode is labeled as a deterministic simulation in JSON and UI."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    # JSON output verification
    result_json = cli_runner.invoke(
        app, ["fix", "--preview", "--directory", str(tmp_path), "--json"]
    )
    assert result_json.exit_code == 0
    data = json.loads(result_json.output)
    assert data["is_mock"] is True
    assert data["output_source"] == "mock"
    assert data["status"] == "success"

    # UI output verification
    result_ui = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result_ui.exit_code == 0
    assert "Mock Simulation Mode" in result_ui.output
    assert "mock simulation" in result_ui.output.lower()


def test_fix_preview_syntax_validation_failure_rejected(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that a proposed patch introducing syntax errors is rejected with an error."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text(
        "def valid_fn():\n    # AI: fix logic\n    return 1\n", encoding="utf-8"
    )

    from unittest.mock import patch
    from codepilot.core.models.base import ModelResponse

    # Model generates broken syntax
    mock_resp = ModelResponse(
        content="```python\ndef broken(:\n```",
        model_name="mock-model",
        prompt_tokens=10,
        completion_tokens=5,
    )
    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(True, "OK"),
    ), patch(
        "codepilot.core.models.ollama_client.OllamaClient.generate",
        return_value=mock_resp,
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "syntax error" in result.output.lower()
    pending_patch = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    assert not pending_patch.exists()


def test_fix_preview_security_violation_in_context_halts(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Verify that security violations in context halt generation rather than falling back to mock."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "ollama"\n', encoding="utf-8")

    code_file = tmp_path / "service.py"
    code_file.write_text("# AI: return true\n", encoding="utf-8")

    from unittest.mock import MagicMock, patch

    mock_bundle = MagicMock()
    mock_bundle.is_safe = False
    mock_bundle.safety_warning = "Secret API token discovered in file"

    with patch(
        "codepilot.core.models.ollama_client.OllamaClient.health_check",
        return_value=(True, "OK"),
    ), patch(
        "codepilot.core.context.engine.ProjectContextEngine.build_context",
        return_value=mock_bundle,
    ):
        result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "security violation" in result.output.lower()
    assert "Secret API token" in result.output


def test_fix_multiline_indentation_preservation(tmp_path: Path) -> None:
    """Verify multi-line code generation preserves indentation and produces valid Python."""
    import ast
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch
    from codepilot.core.models.base import ModelResponse

    code_file = tmp_path / "math_ops.py"
    code_file.write_text(
        "class Calculator:\n"
        "    def compute(self):\n"
        "        # AI: compute sum\n"
        "        pass\n",
        encoding="utf-8",
    )

    multiline_code = (
        "```python\n"
        "total = 0\n"
        "for x in range(5):\n"
        "    total += x\n"
        "return total\n"
        "```"
    )
    mock_resp = ModelResponse(
        content=multiline_code,
        model_name="mock-model",
        prompt_tokens=20,
        completion_tokens=25,
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = mock_resp

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch(
        "codepilot.core.models.factory.create_model_client",
        return_value=mock_client,
    ):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert len(bundle.file_patches) == 1
    proposed = bundle.file_patches[0].proposed_content
    # Confirm every line inside compute() has at least 8 spaces indentation
    assert "        total = 0" in proposed
    assert "        for x in range(5):" in proposed
    assert "            total += x" in proposed
    assert "        return total" in proposed
    # Verify placeholder pass was replaced cleanly rather than left behind
    assert "pass" not in proposed
    # Must be valid syntax
    parsed = ast.parse(proposed)
    assert parsed is not None
    # Verify source file on disk was not modified
    assert code_file.read_text(encoding="utf-8") == (
        "class Calculator:\n"
        "    def compute(self):\n"
        "        # AI: compute sum\n"
        "        pass\n"
    )


def test_comment_generation_preserves_executable_logic_and_control_flow(tmp_path: Path) -> None:
    """Verify AI-DOCS comment generation preserves existing statements, signatures, and logic."""
    import ast
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch
    from codepilot.core.models.base import ModelResponse

    source_content = (
        "def calculate_metrics(values: list) -> dict:\n"
        "    # AI-DOCS: add docstring explaining calculate_metrics\n"
        "    total = sum(values)\n"
        "    count = len(values)\n"
        "    avg = total / count if count > 0 else 0.0\n"
        "    return {'total': total, 'count': count, 'average': avg}\n"
    )
    code_file = tmp_path / "metrics_service.py"
    code_file.write_text(source_content, encoding="utf-8")

    docstring_resp = (
        "```python\n"
        '"""Calculate summary metrics (total, count, average) for input numbers."""\n'
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=docstring_resp,
        model_name="mock-model",
        prompt_tokens=25,
        completion_tokens=15,
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch(
        "codepilot.core.models.factory.create_model_client",
        return_value=mock_client,
    ):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert len(bundle.file_patches) == 1
    proposed = bundle.file_patches[0].proposed_content

    # Docstring is properly indented inside function
    assert '    """Calculate summary metrics (total, count, average) for input numbers."""' in proposed
    # Executable logic is 100% preserved
    assert "    total = sum(values)" in proposed
    assert "    count = len(values)" in proposed
    assert "    avg = total / count if count > 0 else 0.0" in proposed
    assert "    return {'total': total, 'count': count, 'average': avg}" in proposed
    # AI-DOCS comment is cleanly replaced
    assert "# AI-DOCS" not in proposed
    # Source file on disk remains untouched
    assert code_file.read_text(encoding="utf-8") == source_content
    # Syntax is valid
    assert ast.parse(proposed) is not None


def test_comment_generation_inline_explanatory_comments(tmp_path: Path) -> None:
    """Verify inline comment insertion preserves subsequent code and valid syntax."""
    import ast
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch
    from codepilot.core.models.base import ModelResponse

    source_content = (
        "def process_order(price: float, qty: int) -> float:\n"
        "    # AI-DOCS: add explanatory note before discount logic\n"
        "    discount = 0.1 if qty >= 10 else 0.0\n"
        "    return price * qty * (1.0 - discount)\n"
    )
    code_file = tmp_path / "order_ops.py"
    code_file.write_text(source_content, encoding="utf-8")

    inline_comment = (
        "```python\n"
        "# Apply volume discount of 10% for bulk orders of 10 or more items\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=inline_comment,
        model_name="mock-model",
        prompt_tokens=20,
        completion_tokens=15,
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch(
        "codepilot.core.models.factory.create_model_client",
        return_value=mock_client,
    ):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert len(bundle.file_patches) == 1
    proposed = bundle.file_patches[0].proposed_content

    assert "    # Apply volume discount of 10% for bulk orders of 10 or more items" in proposed
    assert "    discount = 0.1 if qty >= 10 else 0.0" in proposed
    assert "    return price * qty * (1.0 - discount)" in proposed
    assert ast.parse(proposed) is not None


def test_comment_generation_patch_application_preserves_runtime_behavior(tmp_path: Path) -> None:
    """Verify applying a comment-generation patch preserves runtime function behavior."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch
    from codepilot.core.patcher import apply_approved_patch
    from codepilot.core.models.base import ModelResponse

    source_content = (
        "def add_numbers(a: int, b: int) -> int:\n"
        "    # AI-DOCS: document add_numbers\n"
        "    return a + b\n"
    )
    code_file = tmp_path / "add_mod.py"
    code_file.write_text(source_content, encoding="utf-8")

    docstring = (
        "```python\n"
        '"""Return the sum of two integers a and b."""\n'
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=docstring,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch(
        "codepilot.core.models.factory.create_model_client",
        return_value=mock_client,
    ):
        bundle = generate_preview_patch(tmp_path, cfg)

    # Apply the approved patch
    result = apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert result.applied_files == ["add_mod.py"]

    # Executable logic runs identically
    namespace = {}
    exec(code_file.read_text(encoding="utf-8"), namespace)
    assert namespace["add_numbers"](3, 4) == 7
    assert namespace["add_numbers"].__doc__ == "Return the sum of two integers a and b."


def test_ai_docs_complete_real_implementation(tmp_path: Path) -> None:
    """Verify AI-DOCS on a function with a complete real implementation preserves all statements without duplication."""
    import ast
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def evaluate_submission(score: float, max_score: float = 100.0) -> dict:\n"
        "    # AI-DOCS: document evaluate_submission\n"
        "    ratio = score / max_score if max_score > 0 else 0.0\n"
        "    passed = ratio >= 0.5\n"
        "    grade = 'A' if ratio >= 0.9 else ('B' if ratio >= 0.75 else 'C')\n"
        "    return {'score': score, 'ratio': ratio, 'passed': passed, 'grade': grade}\n"
    )
    test_file = tmp_path / "submission.py"
    test_file.write_text(source, encoding="utf-8")

    doc_response = (
        "```python\n"
        '"""Evaluate raw submission score against maximum score and determine grade."""\n'
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=doc_response,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert len(bundle.file_patches) == 1
    proposed = bundle.file_patches[0].proposed_content

    # Invariance verified
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, f"Invariance failed: {reason}"

    # Verify statement count in function body: 1 docstring Expr + 4 original statements = 5
    tree = ast.parse(proposed)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    assert len(func_def.body) == 5
    assert isinstance(func_def.body[0], ast.Expr)
    assert func_def.body[0].value.value == "Evaluate raw submission score against maximum score and determine grade."


def test_ai_docs_function_containing_pass(tmp_path: Path) -> None:
    """Verify AI-DOCS preserves pass statement in function body."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def placeholder_handler(event: dict) -> None:\n"
        "    # AI-DOCS: document placeholder handler\n"
        "    pass\n"
    )
    test_file = tmp_path / "handler.py"
    test_file.write_text(source, encoding="utf-8")

    doc_response = '"""Handle incoming event payload asynchronously."""'
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=doc_response, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    assert "pass" in proposed
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason


def test_ai_docs_function_containing_ellipsis(tmp_path: Path) -> None:
    """Verify AI-DOCS preserves ... (ellipsis) placeholder in function body."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def protocol_interface(data: bytes) -> bool:\n"
        "    # AI-DOCS: document protocol interface\n"
        "    ...\n"
    )
    test_file = tmp_path / "protocol.py"
    test_file.write_text(source, encoding="utf-8")

    doc_response = '"""Interface protocol definition for bytes validation."""'
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=doc_response, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    assert "..." in proposed
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason


def test_ai_docs_class_with_existing_methods(tmp_path: Path) -> None:
    """Verify AI-DOCS on a class preserves all methods, properties, and internal structure."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "class OrderManager:\n"
        "    # AI-DOCS: class docstring for OrderManager\n"
        "    def __init__(self, currency: str = 'USD'):\n"
        "        self.currency = currency\n"
        "        self.orders = []\n"
        "\n"
        "    def add_order(self, order_id: str, amount: float) -> None:\n"
        "        self.orders.append((order_id, amount))\n"
        "\n"
        "    def total(self) -> float:\n"
        "        return sum(amount for _, amount in self.orders)\n"
    )
    test_file = tmp_path / "orders.py"
    test_file.write_text(source, encoding="utf-8")

    doc_response = '"""Manages customer orders and computes totals across transactions."""'
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=doc_response, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    assert '    """Manages customer orders and computes totals across transactions."""' in proposed
    assert "def add_order" in proposed
    assert "def total" in proposed
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason


def test_ai_docs_model_response_docstring_plus_duplicated_body(tmp_path: Path) -> None:
    """Verify AI-DOCS strips redundant re-emitted function body from model response without duplication."""
    import ast
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def normalize_score(raw_score: float, scale_factor: float = 1.0) -> float:\n"
        "    # AI-DOCS: document normalize_score\n"
        "    if raw_score < 0.0:\n"
        "        return 0.0\n"
        "    scaled = raw_score * scale_factor\n"
        "    return min(100.0, scaled)\n"
    )
    test_file = tmp_path / "scoring.py"
    test_file.write_text(source, encoding="utf-8")

    # Model echoes docstring PLUS re-emitted function statements (the exact Stage R regression)
    duplicated_response = (
        "```python\n"
        '"""Normalize the raw score by scale factor and clamp to 0..100 range."""\n'
        "if raw_score < 0.0:\n"
        "    return 0.0\n"
        "scaled = raw_score * scale_factor\n"
        "return min(100.0, scaled)\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=duplicated_response,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content

    # Invariance holds: zero duplicate statements
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, f"Invariance violated: {reason}"

    # Verify statement counts: 1 docstring Expr + 3 executable statements = 4 statements
    tree = ast.parse(proposed)
    func_node = tree.body[0]
    assert len(func_node.body) == 4
    # Ensure "scaled = raw_score * scale_factor" appears exactly once
    assert proposed.count("scaled = raw_score * scale_factor") == 1
    assert proposed.count("return min(100.0, scaled)") == 1


def test_ai_docs_model_response_docstring_with_full_function_redeclaration(tmp_path: Path) -> None:
    """Verify AI-DOCS extracts docstring when model returns full def func(): with docstring and body."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def compute_hash(data: str) -> str:\n"
        "    # AI-DOCS: document compute_hash\n"
        "    import hashlib\n"
        "    return hashlib.sha256(data.encode('utf-8')).hexdigest()\n"
    )
    test_file = tmp_path / "hasher.py"
    test_file.write_text(source, encoding="utf-8")

    redeclaration = (
        "```python\n"
        "def compute_hash(data: str) -> str:\n"
        '    """Compute SHA-256 hexadecimal hash string for input data."""\n'
        "    import hashlib\n"
        "    return hashlib.sha256(data.encode('utf-8')).hexdigest()\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=redeclaration,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert proposed.count("import hashlib") == 1


def test_ai_docs_malformed_model_output_rejected(tmp_path: Path) -> None:
    """Verify malformed model output without docstring/comments is rejected with FixError and leaves files unchanged."""
    import pytest
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch
    from codepilot.core.models.base import ModelResponse
    from codepilot.exceptions import FixError

    source = (
        "def active_task() -> int:\n"
        "    # AI-DOCS: document active task\n"
        "    return 42\n"
    )
    test_file = tmp_path / "task.py"
    test_file.write_text(source, encoding="utf-8")

    # Response has invalid Python syntax and no docstring
    malformed_response = "```python\ndef broken(x: ,,, invalid syntax\n```"
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=malformed_response,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        with pytest.raises(FixError, match="does not contain a valid docstring or comments"):
            generate_preview_patch(tmp_path, cfg)

    # File on disk remains unchanged
    assert test_file.read_text(encoding="utf-8") == source


def test_ai_docs_replaces_existing_docstring_preserving_comments(tmp_path: Path) -> None:
    """Verify AI-DOCS replaces existing outdated docstrings while preserving inline comments and logic."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def convert_units(val: float, factor: float) -> float:\n"
        "    # AI-DOCS: update docstring\n"
        '    """Old outdated docstring."""\n'
        "    # Important conversion factor validation\n"
        "    if factor <= 0:\n"
        "        raise ValueError('Invalid factor')\n"
        "    return val * factor\n"
    )
    test_file = tmp_path / "converter.py"
    test_file.write_text(source, encoding="utf-8")

    new_docstring = '"""Convert value using positive scaling factor."""'
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=new_docstring,
        model_name="mock-model",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content

    assert '    """Convert value using positive scaling factor."""' in proposed
    assert "Old outdated docstring" not in proposed
    assert "    # Important conversion factor validation" in proposed
    assert "    if factor <= 0:" in proposed
    assert "    return val * factor" in proposed
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason


def test_ai_docs_patch_rejection_and_byte_for_byte_rollback(tmp_path: Path) -> None:
    """Verify pre-apply backup enables byte-for-byte rollback, and AST-violating patches are rejected by patcher."""
    import json
    import pytest
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, FilePatch, PatchBundle
    from codepilot.core.patcher import apply_approved_patch
    from codepilot.core.recovery import rollback_patch
    from codepilot.core.models.base import ModelResponse
    from codepilot.exceptions import PatchError

    source = (
        "def compute_total(items: list) -> float:\n"
        "    # AI-DOCS: document compute_total\n"
        "    return float(sum(items))\n"
    )
    test_file = tmp_path / "totals.py"
    test_file.write_text(source, encoding="utf-8")
    original_bytes = test_file.read_bytes()

    # Step 1: Valid AI-DOCS patch generation and application
    doc_response = '"""Compute the float sum of all items in the list."""'
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=doc_response, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    # Apply the valid patch
    res = apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert res.total_modified == 1
    assert test_file.read_bytes() != original_bytes

    # Step 2: Roll back and verify byte-for-byte equality
    rec = rollback_patch(tmp_path, patch_id=bundle.patch_id)
    assert rec.restored_files == ["totals.py"]
    assert test_file.read_bytes() == original_bytes

    # Step 3: Verify that if a corrupted/invariance-violating patch is fed to patcher, it rejects with PatchError
    corrupted_bundle = PatchBundle(
        patch_id="patch-corrupted-test",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path="totals.py",
                original_hash=bundle.file_patches[0].original_hash,
                original_content=source,
                proposed_content="def compute_total(items: list) -> float:\n    return 0.0\n",
                diff="",
            )
        ],
        full_diff="",
    )
    patch_dir = tmp_path / ".codepilot" / "patches"
    patch_dir.mkdir(parents=True, exist_ok=True)
    (patch_dir / "pending_patch.json").write_text(
        json.dumps(corrupted_bundle.to_dict()), encoding="utf-8"
    )

    with pytest.raises(PatchError, match="Structural validation failed"):
        apply_approved_patch(tmp_path, approved=True, config=cfg)

    # Disk file remains unmodified byte-for-byte
    assert test_file.read_bytes() == original_bytes


def test_verify_executable_ast_invariance_detection() -> None:
    """Verify that verify_executable_ast_invariance accurately catches code changes and allows doc changes."""
    from codepilot.core.fixer import verify_executable_ast_invariance

    code_original = (
        "def process(x: int, y: int = 10) -> int:\n"
        "    # Initial comment\n"
        "    result = x * y\n"
        "    if result > 100:\n"
        "        return 100\n"
        "    return result\n"
    )

    # 1. Valid: Added docstring
    code_doc = (
        "def process(x: int, y: int = 10) -> int:\n"
        '    """Process x and y, clamping result to 100."""\n'
        "    # Initial comment\n"
        "    result = x * y\n"
        "    if result > 100:\n"
        "        return 100\n"
        "    return result\n"
    )
    is_inv, _ = verify_executable_ast_invariance(code_original, code_doc)
    assert is_inv is True

    # 2. Invalid: Changed multiplier
    code_altered_math = (
        "def process(x: int, y: int = 10) -> int:\n"
        '    """Process x and y, clamping result to 100."""\n'
        "    result = x + y\n"  # changed * to +
        "    if result > 100:\n"
        "        return 100\n"
        "    return result\n"
    )
    is_inv, reason = verify_executable_ast_invariance(code_original, code_altered_math)
    assert is_inv is False
    assert "Executable AST differs" in reason

    # 3. Invalid: Removed condition
    code_removed_cond = (
        "def process(x: int, y: int = 10) -> int:\n"
        '    """Process x and y."""\n'
        "    result = x * y\n"
        "    return result\n"
    )
    is_inv, reason = verify_executable_ast_invariance(code_original, code_removed_cond)
    assert is_inv is False

    # 4. Invalid: Changed function signature default
    code_changed_sig = (
        "def process(x: int, y: int = 20) -> int:\n"  # changed default from 10 to 20
        '    """Process x and y."""\n'
        "    result = x * y\n"
        "    if result > 100:\n"
        "        return 100\n"
        "    return result\n"
    )
    is_inv, reason = verify_executable_ast_invariance(code_original, code_changed_sig)
    assert is_inv is False


def test_ai_docs_stage_v_standalone_function_preserved(tmp_path: Path) -> None:
    """Stage V: Preserve Stage T behavior for standalone functions with docstring and duplicated body."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "def normalize_grade(grade_str: str) -> str:\n"
        "    # AI-DOCS: docstring for normalize_grade\n"
        "    if not grade_str:\n"
        "        return ''\n"
        "    return grade_str.strip().lower()\n"
    )
    test_file = tmp_path / "normalizer.py"
    test_file.write_text(source, encoding="utf-8")

    mock_resp = (
        "```python\n"
        "def normalize_grade(grade_str: str) -> str:\n"
        '    """Normalize input grade string to canonical lowercase representation."""\n'
        "    if not grade_str:\n"
        "        return ''\n"
        "    return grade_str.strip().lower()\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=mock_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert "Normalize input grade string" in proposed
    assert proposed.count("return grade_str.strip().lower()") == 1


def test_ai_docs_stage_v_class_method_with_duplicated_body(tmp_path: Path) -> None:
    """Stage V: Extract docstring when model re-emits class definition and method body (Stage U fix)."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "class ScoreNormalizer:\n"
        "    def normalize_scores(self, results, score_key):\n"
        "        # AI-DOCS: docstring for normalize_scores\n"
        "        if not results:\n"
        "            return results\n"
        "        scores = [r.get(score_key, 0) for r in results]\n"
        "        max_s = max(scores)\n"
        "        min_s = min(scores)\n"
        "        return results\n"
    )
    test_file = tmp_path / "score_normalizer.py"
    test_file.write_text(source, encoding="utf-8")

    # Model re-emits class, method header, docstring, and re-emitted body
    mock_resp = (
        "```python\n"
        "class ScoreNormalizer:\n"
        "    def normalize_scores(self, results, score_key):\n"
        '        """Normalize dictionary scores in results using min-max scaling."""\n'
        "        if not results:\n"
        "            return results\n"
        "        scores = [r.get(score_key, 0) for r in results]\n"
        "        max_s = max(scores)\n"
        "        min_s = min(scores)\n"
        "        return results\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=mock_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert "Normalize dictionary scores in results" in proposed
    assert proposed.count("max_s = max(scores)") == 1
    assert proposed.count("min_s = min(scores)") == 1


def test_ai_docs_stage_v_class_multiple_methods_selection(tmp_path: Path) -> None:
    """Stage V: In a class with multiple methods, ensure the correct method docstring is extracted."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "class Calculator:\n"
        "    def add(self, a: int, b: int) -> int:\n"
        "        return a + b\n"
        "\n"
        "    def subtract(self, a: int, b: int) -> int:\n"
        "        # AI-DOCS: docstring for subtract\n"
        "        return a - b\n"
    )
    test_file = tmp_path / "calc.py"
    test_file.write_text(source, encoding="utf-8")

    # Model re-emits the entire Calculator class with docstrings on both methods
    mock_resp = (
        "```python\n"
        "class Calculator:\n"
        "    def add(self, a: int, b: int) -> int:\n"
        '        """Add two integers."""\n'
        "        return a + b\n"
        "\n"
        "    def subtract(self, a: int, b: int) -> int:\n"
        '        """Subtract integer b from integer a."""\n'
        "        return a - b\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=mock_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert "Subtract integer b from integer a." in proposed
    assert proposed.count("return a - b") == 1


def test_ai_docs_stage_v_nested_class_method(tmp_path: Path) -> None:
    """Stage V: Target method inside a nested class."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def compute(self, x: int) -> int:\n"
        "            # AI-DOCS: document inner compute\n"
        "            return x * 42\n"
    )
    test_file = tmp_path / "nested.py"
    test_file.write_text(source, encoding="utf-8")

    mock_resp = (
        "```python\n"
        "class Outer:\n"
        "    class Inner:\n"
        "        def compute(self, x: int) -> int:\n"
        '            """Compute inner value multiplied by 42."""\n'
        "            return x * 42\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=mock_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert "Compute inner value multiplied by 42." in proposed
    assert proposed.count("return x * 42") == 1


def test_ai_docs_stage_v_async_method(tmp_path: Path) -> None:
    """Stage V: Extract docstring from an asynchronous method."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, verify_executable_ast_invariance
    from codepilot.core.models.base import ModelResponse

    source = (
        "class AsyncHandler:\n"
        "    async def fetch_payload(self, endpoint: str) -> dict:\n"
        "        # AI-DOCS: document fetch_payload\n"
        "        return {'status': 'ok', 'url': endpoint}\n"
    )
    test_file = tmp_path / "async_handler.py"
    test_file.write_text(source, encoding="utf-8")

    mock_resp = (
        "```python\n"
        "class AsyncHandler:\n"
        "    async def fetch_payload(self, endpoint: str) -> dict:\n"
        '        """Asynchronously fetch payload from given endpoint."""\n'
        "        return {'status': 'ok', 'url': endpoint}\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=mock_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    proposed = bundle.file_patches[0].proposed_content
    is_inv, reason = verify_executable_ast_invariance(source, proposed)
    assert is_inv, reason
    assert "Asynchronously fetch payload" in proposed
    assert proposed.count("return {'status': 'ok', 'url': endpoint}") == 1


def test_ai_docs_stage_v_wrong_or_ambiguous_target_rejected(tmp_path: Path) -> None:
    """Stage V: Reject model responses with wrong or ambiguous target symbols."""
    import pytest
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, extract_documentation_content
    from codepilot.core.models.base import ModelResponse
    from codepilot.exceptions import FixError

    source = (
        "class DataProcessor:\n"
        "    def run_clean(self, data: str) -> str:\n"
        "        # AI-DOCS: document run_clean\n"
        "        return data.strip()\n"
    )
    test_file = tmp_path / "processor.py"
    test_file.write_text(source, encoding="utf-8")

    # 1. Model emits a completely different method name
    wrong_resp = (
        "```python\n"
        "class DataProcessor:\n"
        "    def export_csv(self, data: str) -> None:\n"
        '        """Export data as CSV."""\n'
        "        pass\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(content=wrong_resp, model_name="mock-model")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        with pytest.raises(FixError, match="does not contain a valid docstring"):
            generate_preview_patch(tmp_path, cfg)

    # 2. Ambiguous target symbol test on extract_documentation_content directly
    ambiguous_code = (
        "class A:\n"
        "    def execute(self):\n"
        '        """Execute A."""\n'
        "        pass\n"
        "class B:\n"
        "    def execute(self):\n"
        '        """Execute B."""\n'
        "        pass\n"
    )
    is_valid, _, _ = extract_documentation_content(ambiguous_code, target_symbol="execute")
    assert is_valid is False


def test_ai_docs_stage_v_malformed_and_empty_output_rejected() -> None:
    """Stage V: Direct unit tests verifying rejection of empty or malformed docstrings."""
    from codepilot.core.fixer import extract_documentation_content

    # Empty string docstring
    empty_doc = "def foo():\n    ''''''\n    return 1"
    is_valid, _, _ = extract_documentation_content(empty_doc, target_symbol="foo")
    assert is_valid is False

    # Whitespace only docstring
    ws_doc = 'def foo():\n    """   \n   """\n    return 1'
    is_valid, _, _ = extract_documentation_content(ws_doc, target_symbol="foo")
    assert is_valid is False

    # Executable code without any docstring
    no_doc = "def foo():\n    return 1"
    is_valid, _, _ = extract_documentation_content(no_doc, target_symbol="foo")
    assert is_valid is False

    # Syntax error code
    bad_syntax = "def foo(:\n    '''broken syntax'''"
    is_valid, _, _ = extract_documentation_content(bad_syntax, target_symbol="foo")
    assert is_valid is False


def test_ai_docs_stage_v_patch_rejection_and_rollback(tmp_path: Path) -> None:
    """Stage V: End-to-end patch application, rejection of mutated code, and byte-for-byte rollback."""
    import hashlib
    import json
    import pytest
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import FilePatch, PatchBundle, verify_executable_ast_invariance
    from codepilot.core.patcher import apply_approved_patch
    from codepilot.core.recovery import rollback_patch
    from codepilot.exceptions import PatchError

    source = (
        "class MathEngine:\n"
        "    def compute(self, x: int) -> int:\n"
        "        # AI-DOCS: document compute\n"
        "        return x * 10\n"
    )
    target_file = tmp_path / "engine.py"
    target_file.write_text(source, encoding="utf-8")
    original_bytes = target_file.read_bytes()
    original_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    # 1. Unsafe patch altering executable logic
    mutated_source = (
        "class MathEngine:\n"
        "    def compute(self, x: int) -> int:\n"
        '        """Compute value."""\n'
        "        return x * 20\n"  # altered 10 to 20
    )
    is_inv, reason = verify_executable_ast_invariance(source, mutated_source)
    assert not is_inv

    unsafe_bundle = PatchBundle(
        patch_id="patch-unsafe-stage-v",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path="engine.py",
                original_hash=original_hash,
                original_content=source,
                proposed_content=mutated_source,
                diff="",
            )
        ],
        full_diff="",
    )
    patches_dir = tmp_path / ".codepilot" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    (patches_dir / "pending_patch.json").write_text(json.dumps(unsafe_bundle.to_dict()), encoding="utf-8")

    cfg = CodePilotConfig(project_root=tmp_path)
    with pytest.raises(PatchError, match="patch violates executable AST invariance"):
        apply_approved_patch(tmp_path, approved=True, config=cfg)

    assert target_file.read_bytes() == original_bytes

    # 2. Safe patch application and rollback
    safe_source = (
        "class MathEngine:\n"
        "    def compute(self, x: int) -> int:\n"
        '        """Compute x multiplied by 10."""\n'
        "        return x * 10\n"
    )
    safe_bundle = PatchBundle(
        patch_id="patch-safe-stage-v",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path="engine.py",
                original_hash=original_hash,
                original_content=source,
                proposed_content=safe_source,
                diff="",
            )
        ],
        full_diff="",
    )
    (patches_dir / "pending_patch.json").write_text(json.dumps(safe_bundle.to_dict()), encoding="utf-8")
    apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert target_file.read_text(encoding="utf-8") == safe_source

    # Rollback
    rollback_res = rollback_patch(tmp_path, patch_id="patch-safe-stage-v")
    assert rollback_res.restored_files == ["engine.py"]
    assert target_file.read_bytes() == original_bytes


def test_generate_preview_patch_ambiguous_task_returns_clarification_without_error(
    tmp_path: Path,
) -> None:
    """Phase 14.3 Regression: Ambiguous task routing to CLARIFICATION returns a clarification

    question, avoids crashing with FixError, and stages zero patches on disk.
    """
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, load_pending_patch
    from codepilot.core.models.base import ModelResponse

    app_file = tmp_path / "auth.py"
    app_file.write_text(
        "def authenticate_user(token):\n"
        "    # AI-FIX: make this better\n"
        "    pass\n",
        encoding="utf-8",
    )

    clarification_msg = "Please clarify what specific authentication rules or checks should be added."
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=clarification_msg,
        model_name="mock-qwen",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert bundle.status == "clarification_requested"
    assert bundle.clarification_question is not None
    assert clarification_msg in bundle.clarification_question
    assert len(bundle.file_patches) == 0
    assert bundle.full_diff == ""

    # No patch should be staged on disk
    assert load_pending_patch(tmp_path) is None
    # Source file must remain completely untouched
    assert "make this better" in app_file.read_text(encoding="utf-8")


def test_generate_preview_patch_normal_code_generation_remains_unchanged(
    tmp_path: Path,
) -> None:
    """Phase 14.3 Regression: Normal, concrete coding instruction proceeds to code patch generation."""
    from unittest.mock import MagicMock, patch
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import generate_preview_patch, load_pending_patch
    from codepilot.core.models.base import ModelResponse

    math_file = tmp_path / "math_util.py"
    math_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    # AI-FIX: return sum of a and b\n"
        "    pass\n",
        encoding="utf-8",
    )

    code_completion = (
        "```python\n"
        "return a + b\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.health_check.return_value = (True, "OK")
    mock_client.generate.return_value = ModelResponse(
        content=code_completion,
        model_name="mock-qwen",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
        bundle = generate_preview_patch(tmp_path, cfg)

    assert bundle.status == "success"
    assert bundle.clarification_question is None
    assert len(bundle.file_patches) == 1
    assert "return a + b" in bundle.file_patches[0].proposed_content

    # Pending patch was saved to disk
    staged = load_pending_patch(tmp_path)
    assert staged is not None
    assert staged.patch_id == bundle.patch_id


@pytest.mark.parametrize("marker", ["# AI-DOCS:", "# ai-docs:", "# Ai-Docs:", "# ai-doc:"])
def test_ai_docs_marker_variants_trigger_ast_invariance_enforcement(
    tmp_path: Path, marker: str
) -> None:
    """Phase 14.4 Regression: All supported case and name variants of the AI-DOCS marker

    trigger executable AST invariance verification and reject unsafe logic mutations.
    """
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import FilePatch, PatchBundle, compute_sha256
    from codepilot.core.patcher import apply_approved_patch
    from codepilot.exceptions import PatchError

    source = (
        f"def compute(x: int) -> int:\n"
        f"    {marker} calculate square\n"
        f"    return x ** 2\n"
    )
    test_file = tmp_path / f"test_{marker.replace('#', '').replace(':', '').replace('-', '_').strip()}.py"
    test_file.write_text(source, encoding="utf-8")
    original_hash = compute_sha256(source)

    # Attempt an unsafe modification (changes executable logic: return x ** 2 -> return x ** 3)
    unsafe_proposed = (
        'def compute(x: int) -> int:\n'
        '    """Calculate square."""\n'
        '    return x ** 3\n'
    )
    patches_dir = tmp_path / ".codepilot" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    unsafe_bundle = PatchBundle(
        patch_id=f"patch-unsafe-{test_file.stem}",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path=test_file.name,
                original_hash=original_hash,
                original_content=source,
                proposed_content=unsafe_proposed,
                diff="",
            )
        ],
        full_diff="",
    )
    (patches_dir / "pending_patch.json").write_text(json.dumps(unsafe_bundle.to_dict()), encoding="utf-8")

    cfg = CodePilotConfig(project_root=tmp_path)
    # Must be strictly rejected because executable AST was mutated under a documentation marker!
    with pytest.raises(PatchError) as exc_info:
        apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert "executable AST invariance" in str(exc_info.value)

    # Now verify that a safe documentation addition succeeds
    safe_proposed = (
        'def compute(x: int) -> int:\n'
        '    """Calculate square."""\n'
        '    return x ** 2\n'
    )
    safe_bundle = PatchBundle(
        patch_id=f"patch-safe-{test_file.stem}",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path=test_file.name,
                original_hash=original_hash,
                original_content=source,
                proposed_content=safe_proposed,
                diff="",
            )
        ],
        full_diff="",
    )
    (patches_dir / "pending_patch.json").write_text(json.dumps(safe_bundle.to_dict()), encoding="utf-8")
    apply_res = apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert apply_res.total_modified == 1
    assert '"""Calculate square."""' in test_file.read_text(encoding="utf-8")


def test_unrelated_comments_do_not_trigger_ai_docs_ast_invariance(tmp_path: Path) -> None:
    """Phase 14.4 Regression: Unrelated comments mentioning 'ai-docs' in prose do not trigger

    strict AST invariance, allowing standard code changes.
    """
    from codepilot.config import CodePilotConfig
    from codepilot.core.fixer import FilePatch, PatchBundle, compute_sha256
    from codepilot.core.patcher import apply_approved_patch

    source = (
        "# Note: team reviewed ai-docs documentation guidelines yesterday\n"
        "def compute(x: int) -> int:\n"
        "    return x + 1\n"
    )
    test_file = tmp_path / "regular_task.py"
    test_file.write_text(source, encoding="utf-8")
    original_hash = compute_sha256(source)

    # Legitimate code change for a non-docs task
    proposed = (
        "# Note: team reviewed ai-docs documentation guidelines yesterday\n"
        "def compute(x: int) -> int:\n"
        "    return x + 2\n"
    )

    patches_dir = tmp_path / ".codepilot" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    bundle = PatchBundle(
        patch_id="patch-code-change",
        created_at="2026-10-23T12:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path=test_file.name,
                original_hash=original_hash,
                original_content=source,
                proposed_content=proposed,
                diff="",
            )
        ],
        full_diff="",
    )
    (patches_dir / "pending_patch.json").write_text(json.dumps(bundle.to_dict()), encoding="utf-8")

    cfg = CodePilotConfig(project_root=tmp_path)
    # Should succeed without raising PatchError because this is not an AI-DOCS marker task!
    apply_res = apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert apply_res.total_modified == 1
    assert "return x + 2" in test_file.read_text(encoding="utf-8")




