"""Phase 11: End-to-End GGUF Patch Generation Integration Test Suite.

Validates:
1. Redundant function signature unwrapping and scope handling.
2. Symbol-grounded context and enclosing function detection.
3. Standalone GGUF patch preview generation without disk modification.
4. Syntax error rejection guarding against invalid generated code.
5. Path traversal defense rejecting external targets.
6. Approval gate enforcement requiring explicit --approved flag.
7. Atomic pre-apply snapshot backups and file modification.
8. Strict error handling preventing silent fallback to mock or Ollama.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from codepilot.config import CodePilotConfig
from codepilot.core.fixer import (
    FilePatch,
    PatchBundle,
    find_enclosing_function,
    generate_preview_patch,
    load_pending_patch,
    unwrap_nested_function_body,
)
from codepilot.core.models.base import ModelRequest, ModelResponse
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.planner import Plan, PlanTask
from codepilot.exceptions import FixError, PatchError


# ---------------------------------------------------------------------------
# Unit Tests: Redundant Signature Unwrapping & Symbol Enclosure
# ---------------------------------------------------------------------------

class TestRedundantSignatureHandling:
    """Tests for safe, narrowly scoped redundant function signature unwrapping."""

    def test_unwrap_nested_function_body_matching_signature(self) -> None:
        """When code has a single function matching target_symbol, unwraps the inner body."""
        code = (
            "def add_numbers(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        unwrapped = unwrap_nested_function_body(code, target_symbol="add_numbers")
        assert unwrapped == "return a + b"

    def test_unwrap_nested_function_body_with_docstring(self) -> None:
        """Preserves docstrings and multiple inner body statements when unwrapping."""
        code = (
            "def calculate_total(items: list) -> float:\n"
            '    """Calculate total price of all items."""\n'
            "    total = sum(item.price for item in items)\n"
            "    return total\n"
        )
        unwrapped = unwrap_nested_function_body(code, target_symbol="calculate_total")
        expected = (
            '"""Calculate total price of all items."""\n'
            "total = sum(item.price for item in items)\n"
            "return total"
        )
        assert unwrapped == expected

    def test_unwrap_nested_function_body_non_matching_signature(self) -> None:
        """Does NOT unwrap if the function name does not match target_symbol."""
        code = (
            "def other_function(x):\n"
            "    return x * 2\n"
        )
        unwrapped = unwrap_nested_function_body(code, target_symbol="add_numbers")
        # Retains full original code to avoid silent corruption
        assert unwrapped == code

    def test_unwrap_nested_function_body_multiple_statements(self) -> None:
        """Does NOT unwrap if code contains multiple top-level AST statements."""
        code = (
            "def add_numbers(a, b):\n"
            "    return a + b\n"
            "\n"
            "def helper():\n"
            "    pass\n"
        )
        unwrapped = unwrap_nested_function_body(code, target_symbol="add_numbers")
        assert unwrapped == code

    def test_unwrap_nested_function_body_syntax_error(self) -> None:
        """Does NOT modify or unwrap code with Python syntax errors."""
        code = "def add_numbers(a, b) return a + b"  # Missing colon
        unwrapped = unwrap_nested_function_body(code, target_symbol="add_numbers")
        assert unwrapped == code

    def test_unwrap_nested_function_body_no_target_symbol(self) -> None:
        """Returns code untouched if target_symbol is None or empty."""
        code = "def add_numbers(a, b):\n    return a + b"
        assert unwrap_nested_function_body(code, target_symbol=None) == code
        assert unwrap_nested_function_body(code, target_symbol="") == code
        assert unwrap_nested_function_body(code, target_symbol="   ") == code

    def test_find_enclosing_function(self) -> None:
        """Verifies finding the innermost enclosing function for various AST scopes."""
        source = (
            "# Line 1: Module comment\n"
            "GLOBAL_VAL = 42\n"
            "\n"
            "def outer_func():\n"
            "    # Line 5: Inside outer\n"
            "    def inner_func():\n"
            "        # Line 7: Inside inner\n"
            "        return 1\n"
            "    return inner_func()\n"
            "\n"
            "class MathService:\n"
            "    def compute(self, a, b):\n"
            "        # Line 13: Inside method\n"
            "        return a + b\n"
        )
        # Line 1 & 2 are at module level
        assert find_enclosing_function(source, 1) is None
        assert find_enclosing_function(source, 2) is None

        # Line 5 is inside outer_func
        assert find_enclosing_function(source, 5) == "outer_func"

        # Line 7 is inside inner_func (innermost function)
        assert find_enclosing_function(source, 7) == "inner_func"

        # Line 13 is inside compute method
        assert find_enclosing_function(source, 13) == "compute"

        # Invalid source returns None
        assert find_enclosing_function("def broken(:", 1) is None


# ---------------------------------------------------------------------------
# Integration Tests: Standalone GGUF Patch Generation & Safety Bounds
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_with_task(tmp_path: Path) -> Path:
    """Fixture providing an isolated project directory with a target Python file and AI task."""
    repo = tmp_path / "test_repo"
    repo.mkdir(parents=True, exist_ok=True)

    src_dir = repo / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    target_file = src_dir / "calculator.py"
    target_file.write_text(
        "def add(a: int, b: int) -> int:\n"
        "    # AI-TASK: implement addition\n"
        "    pass\n",
        encoding="utf-8",
    )
    return repo


class TestStandaloneGGUFPatchIntegration:
    """Deterministic integration tests for standalone GGUF backend in fix pipeline."""

    def test_standalone_patch_preview_with_redundant_signature_unwrapping(
        self, repo_with_task: Path
    ) -> None:
        """Validates standalone patch preview generation and unwrapping of redundant def signature."""
        cfg = CodePilotConfig(project_root=repo_with_task, model_provider="standalone")

        # Mock standalone client response returning redundant def add signature
        mock_response = ModelResponse(
            content="```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```",
            model_name="qwen2.5-coder:1.5b",
            metadata={"provider": "standalone"},
        )
        mock_client = MagicMock()
        mock_client.provider_name = "standalone"
        mock_client.health_check.return_value = (True, "Ready")
        mock_client.generate.return_value = mock_response

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            bundle = generate_preview_patch(
                project_root=repo_with_task,
                config=cfg,
                override_provider="standalone",
            )

        assert bundle.model_provider == "standalone"
        assert bundle.status == "success"
        assert bundle.is_applied is False
        assert bundle.is_approved is False
        assert len(bundle.file_patches) == 1

        fp = bundle.file_patches[0]
        assert fp.file_path.replace("\\", "/") == "src/calculator.py"
        # The redundant def add(...) was unwrapped to return a + b with correct indent
        assert "    return a + b\n" in fp.proposed_content
        assert "def add(a: int, b: int) -> int:\n    return a + b\n" == fp.proposed_content

        # Verify unified diff contains additions and deletions
        assert "+    return a + b" in fp.diff
        assert "-    # AI-TASK: implement addition" in fp.diff
        assert "-    pass" in fp.diff

        # Original source on disk is strictly NOT modified
        original = (repo_with_task / "src" / "calculator.py").read_text(encoding="utf-8")
        assert "pass" in original
        assert "# AI-TASK: implement addition" in original

    def test_standalone_syntax_validation_rejection(self, repo_with_task: Path) -> None:
        """Validates that a syntax error generated by the model is caught and rejected."""
        cfg = CodePilotConfig(project_root=repo_with_task, model_provider="standalone")

        # Model generates broken syntax
        mock_response = ModelResponse(
            content="```python\nreturn a + * b\n```",
            model_name="qwen2.5-coder:1.5b",
            metadata={"provider": "standalone"},
        )
        mock_client = MagicMock()
        mock_client.provider_name = "standalone"
        mock_client.health_check.return_value = (True, "Ready")
        mock_client.generate.return_value = mock_response

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            with pytest.raises(FixError, match="Python syntax error"):
                generate_preview_patch(
                    project_root=repo_with_task,
                    config=cfg,
                    override_provider="standalone",
                )

        # Confirm pending patch file was not created
        pending = load_pending_patch(repo_with_task)
        assert pending is None

    def test_standalone_path_traversal_rejection(self, repo_with_task: Path) -> None:
        """Validates that tasks targeting files outside project root are rejected by security boundary."""
        cfg = CodePilotConfig(project_root=repo_with_task, model_provider="standalone")

        # Create a malicious plan with traversal
        malicious_task = PlanTask(
            task_id="TASK-MALICIOUS",
            instruction_id="inst-001",
            target_file="../../etc/passwd",
            line_number=1,
            instruction_type="AI-FIX",
            description="inject credentials",
            risk_level="HIGH",
            scope="LINE",
        )
        plan = Plan(
            plan_id="plan-test",
            project_root=str(repo_with_task),
            created_at="2026-10-22T00:00:00Z",
            tasks=[malicious_task],
        )

        mock_client = MagicMock()
        mock_client.provider_name = "standalone"
        mock_client.health_check.return_value = (True, "Ready")

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            with pytest.raises(FixError, match="Security boundary rejected target file"):
                generate_preview_patch(
                    project_root=repo_with_task,
                    config=cfg,
                    plan=plan,
                    override_provider="standalone",
                )

    def test_approval_gate_and_atomic_backup_apply(self, repo_with_task: Path) -> None:
        """Validates that patch apply requires --approved, and creates pre-apply backup."""
        cfg = CodePilotConfig(project_root=repo_with_task, model_provider="standalone")

        mock_response = ModelResponse(
            content="```python\nreturn a + b\n```",
            model_name="qwen2.5-coder:1.5b",
            metadata={"provider": "standalone"},
        )
        mock_client = MagicMock()
        mock_client.provider_name = "standalone"
        mock_client.health_check.return_value = (True, "Ready")
        mock_client.generate.return_value = mock_response

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            bundle = generate_preview_patch(
                project_root=repo_with_task,
                config=cfg,
                override_provider="standalone",
            )

        assert bundle.status == "success"

        # 1. Reject without explicit approval flag
        with pytest.raises(PatchError, match="explicit approval is required"):
            apply_approved_patch(repo_with_task, approved=False, config=cfg)

        # File remains unmodified
        assert "# AI-TASK: implement addition" in (
            repo_with_task / "src" / "calculator.py"
        ).read_text(encoding="utf-8")

        # 2. Apply with explicit approval flag
        result = apply_approved_patch(repo_with_task, approved=True, config=cfg)
        assert result.total_modified == 1
        assert "src/calculator.py" in [p.replace("\\", "/") for p in result.applied_files]
        assert result.backup_dir is not None

        # Backup snapshot directory and manifest exist
        backup_path = Path(result.backup_dir)
        assert backup_path.is_dir()
        manifest_file = backup_path / "manifest.json"
        assert manifest_file.is_file()
        manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        assert manifest_data["patch_id"] == bundle.patch_id

        # Target file has been safely modified
        modified_content = (repo_with_task / "src" / "calculator.py").read_text(
            encoding="utf-8"
        )
        assert "return a + b" in modified_content
        assert "# AI-TASK: implement addition" not in modified_content

    def test_no_silent_fallback_on_runtime_failure(self, repo_with_task: Path) -> None:
        """Validates that runtime or inference failure raises FixError without silent fallback."""
        cfg = CodePilotConfig(project_root=repo_with_task, model_provider="standalone")

        # Standalone client health check fails
        mock_client = MagicMock()
        mock_client.provider_name = "standalone"
        mock_client.health_check.return_value = (False, "GGUF weights missing")

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            with pytest.raises(FixError, match="Model runtime 'standalone' is unavailable: GGUF weights missing"):
                generate_preview_patch(
                    project_root=repo_with_task,
                    config=cfg,
                    override_provider="standalone",
                )

        # Inference crash
        mock_client.health_check.return_value = (True, "Ready")
        mock_client.generate.side_effect = RuntimeError("Context window exceeded")

        with patch("codepilot.core.models.factory.create_model_client", return_value=mock_client):
            with pytest.raises(FixError, match="Model inference failed.*Context window exceeded"):
                generate_preview_patch(
                    project_root=repo_with_task,
                    config=cfg,
                    override_provider="standalone",
                )
