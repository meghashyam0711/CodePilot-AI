"""Unit tests for Phase 4 Project Context Engine, Prompt construction, and CLI explain command."""

import json
from pathlib import Path
from typer.testing import CliRunner
import pytest

from codepilot.cli import app
from codepilot.core.context.engine import ProjectContextEngine
from codepilot.core.context.models import CodeSnippet, ProjectContextBundle, RelatedContext
from codepilot.core.context.prompt import (
    PROMPT_TEMPLATE_VERSION,
    build_model_request,
    build_system_prompt,
    build_user_prompt,
)
from codepilot.core.parser.models import InstructionTask


class TestCodeSnippet:
    """Tests for CodeSnippet formatting."""

    def test_format_line_numbered(self) -> None:
        snippet = CodeSnippet(
            file_path="src/calc.py",
            start_line=10,
            end_line=12,
            content="def add(a, b):\n    return a + b\n",
        )
        formatted = snippet.format_line_numbered()
        assert "Line    10: def add(a, b):" in formatted
        assert "Line    11:     return a + b" in formatted


class TestProjectContextEngine:
    """Tests for ProjectContextEngine context collection, safety guards, and budgeting."""

    def test_sensitive_path_detection(self) -> None:
        engine = ProjectContextEngine()
        assert engine.is_sensitive_path(".env") is True
        assert engine.is_sensitive_path(".env.local") is True
        assert engine.is_sensitive_path("config/credentials.json") is True
        assert engine.is_sensitive_path("certs/server.pem") is True
        assert engine.is_sensitive_path("certs/privkey.key") is True
        assert engine.is_sensitive_path("secrets.toml") is True

        # Normal source files must NOT be flagged
        assert engine.is_sensitive_path("src/codepilot/cli.py") is False
        assert engine.is_sensitive_path("tests/test_cli.py") is False
        assert engine.is_sensitive_path("utils.js") is False

    def test_build_context_blocks_sensitive_file(self, tmp_path: Path) -> None:
        engine = ProjectContextEngine(project_root=tmp_path)
        task = InstructionTask(
            id="task-secret",
            file_path=".env",
            line_number=1,
            instruction_type="AI-FIX",
            description="Fix API key",
        )
        bundle = engine.build_context(task)
        assert bundle.is_safe is False
        assert bundle.safety_warning is not None
        assert "sensitive credential" in bundle.safety_warning.lower()

    def test_build_context_missing_file(self, tmp_path: Path) -> None:
        engine = ProjectContextEngine(project_root=tmp_path)
        task = InstructionTask(
            id="task-001",
            file_path="nonexistent.py",
            line_number=5,
            instruction_type="AI-FIX",
            description="Fix bug",
        )
        bundle = engine.build_context(task)
        assert bundle.is_safe is True
        assert len(bundle.warnings) > 0
        assert "not found" in bundle.warnings[0].lower()

    def test_build_context_python_file(self, tmp_path: Path) -> None:
        # Create a sample python module with imports, functions, and a test file
        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True)
        py_file = src_dir / "math_ops.py"
        py_file.write_text(
            "import math\n"
            "from typing import List\n\n"
            "# AI-FIX: Handle empty numbers list\n"
            "def calculate_mean(numbers: List[float]) -> float:\n"
            "    return sum(numbers) / len(numbers)\n",
            encoding="utf-8",
        )

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir(parents=True)
        test_file = tests_dir / "test_math_ops.py"
        test_file.write_text(
            "def test_calculate_mean():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        engine = ProjectContextEngine(project_root=tmp_path)
        task = InstructionTask(
            id="task-mean",
            file_path="src/math_ops.py",
            line_number=4,
            instruction_type="AI-FIX",
            description="Handle empty numbers list",
            target_symbol="calculate_mean",
            target_symbol_type="function",
        )

        bundle = engine.build_context(task, token_budget=2048)
        assert bundle.is_safe is True
        assert bundle.target_snippet is not None
        assert "def calculate_mean" in bundle.target_snippet.content
        assert "import math" in bundle.imports
        assert len(bundle.related_contexts) == 1
        assert bundle.related_contexts[0].relationship == "test"
        assert bundle.estimated_tokens > 0

    def test_token_budget_truncation(self, tmp_path: Path) -> None:
        src_file = tmp_path / "big_module.py"
        lines = [f"x_{i} = {i}" for i in range(500)]
        src_file.write_text("\n".join(lines), encoding="utf-8")

        engine = ProjectContextEngine(project_root=tmp_path)
        task = InstructionTask(
            id="task-big",
            file_path="big_module.py",
            line_number=200,
            instruction_type="AI-REFACTOR",
            description="Refactor variables",
        )

        # Very tight budget to trigger truncation warning
        bundle = engine.build_context(task, token_budget=50)
        assert bundle.is_truncated is True
        assert any("token budget" in w.lower() for w in bundle.warnings)

    def test_find_ast_symbol_span_nested_three_levels(self) -> None:
        """TASK-AUD-08: Verify _find_ast_symbol_span resolves deeply nested symbols (Outer.Inner.method)."""
        import ast
        code = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def compute(self, x):\n"
            "            return x * 2\n"
        )
        tree = ast.parse(code)
        engine = ProjectContextEngine()

        # Three-level resolution
        span = engine._find_ast_symbol_span(tree, "Outer.Inner.compute")
        assert span is not None
        start, end = span
        assert start == 3
        assert end == 4

    def test_find_ast_symbol_span_nested_class_within_class(self) -> None:
        """TASK-AUD-08: Verify _find_ast_symbol_span resolves nested classes."""
        import ast
        code = (
            "class Outer:\n"
            "    class Inner:\n"
            "        pass\n"
        )
        tree = ast.parse(code)
        engine = ProjectContextEngine()

        span = engine._find_ast_symbol_span(tree, "Outer.Inner")
        assert span is not None
        start, end = span
        assert start == 2
        assert end == 3

    def test_estimate_tokens_includes_system_prompt_overhead(self) -> None:
        """TASK-AUD-09: Verify _estimate_tokens includes DEFAULT_SYSTEM_OVERHEAD_TOKENS."""
        from codepilot.core.context.engine import DEFAULT_SYSTEM_OVERHEAD_TOKENS
        engine = ProjectContextEngine()
        task = InstructionTask(
            id="t1",
            file_path="a.py",
            line_number=1,
            instruction_type="AI-FIX",
            description="Short desc",
        )
        # Content tokens only
        tokens_no_overhead = engine._estimate_tokens(
            task=task,
            snippet=None,
            imports=[],
            related=[],
            arch_summary=None,
            include_overhead=False,
        )
        # Content + overhead
        tokens_with_overhead = engine._estimate_tokens(
            task=task,
            snippet=None,
            imports=[],
            related=[],
            arch_summary=None,
            include_overhead=True,
        )
        assert tokens_with_overhead == tokens_no_overhead + DEFAULT_SYSTEM_OVERHEAD_TOKENS
        assert tokens_with_overhead >= 400


class TestPromptConstruction:
    """Tests for prompt security boundaries and template construction."""

    def test_system_prompt_security_boundary(self) -> None:
        sys_prompt = build_system_prompt()
        assert "<untrusted_repository_context>" in sys_prompt
        assert "passive data" in sys_prompt.lower()
        assert "AI-FIX" in sys_prompt
        assert "AI-TASK" in sys_prompt
        assert "AI-REFACTOR" in sys_prompt
        assert "AI-TEST" in sys_prompt
        assert "AI-DOCS" in sys_prompt

    def test_system_prompt_diff_hygiene_and_clarification_rules(self) -> None:
        from codepilot.core.context.prompt import CLARIFICATION_SYSTEM_INSTRUCTION
        from codepilot.core.context.router import TaskRoute

        # Code generation prompt: minimal diffs, anti-defensive checks, security boundaries, NO clarification attractor
        sys_prompt = build_system_prompt(route=TaskRoute.CODE_GENERATION)
        assert "MINIMAL SCOPE & DIFF HYGIENE" in sys_prompt
        assert "isinstance" in sys_prompt
        assert "isolate the basename" in sys_prompt.lower()
        assert "prompt-only boundary delimiters" in sys_prompt.lower()
        assert "Please clarify..." not in sys_prompt
        assert "Could you please clarify..." not in sys_prompt

        # Clarification prompt: dedicated to asking targeted questions without code fences
        clarif_prompt = build_system_prompt(route=TaskRoute.CLARIFICATION)
        assert "CLARIFICATION REQUEST" in clarif_prompt
        assert "concise, targeted clarification question" in clarif_prompt
        assert "Do NOT emit markdown code fences" in clarif_prompt
        assert "prompt-only boundary delimiters" in clarif_prompt.lower()

    def test_user_prompt_encapsulates_untrusted_code(self) -> None:
        task = InstructionTask(
            id="task-safe-01",
            file_path="src/service.py",
            line_number=15,
            instruction_type="AI-FIX",
            description="Prevent SQL injection in query builder",
            target_symbol="build_query",
        )
        snippet = CodeSnippet(
            file_path="src/service.py",
            start_line=14,
            end_line=16,
            content="def build_query(user_input):\n    return f'SELECT * FROM users WHERE id={user_input}'\n",
        )
        bundle = ProjectContextBundle(
            task=task,
            target_snippet=snippet,
            imports=["import sqlite3"],
            architecture_summary="Python Service",
        )

        prompt = build_user_prompt(bundle)
        assert f"Template v{PROMPT_TEMPLATE_VERSION}" in prompt
        assert "<untrusted_repository_context>" in prompt
        assert "</untrusted_repository_context>" in prompt
        assert "Line    14: def build_query" in prompt

        # Verify snippet is strictly inside untrusted tags
        tag_open = prompt.index("<untrusted_repository_context>")
        tag_close = prompt.index("</untrusted_repository_context>")
        snippet_pos = prompt.index("def build_query")
        assert tag_open < snippet_pos < tag_close

    def test_build_model_request(self) -> None:
        task = InstructionTask(
            id="task-req",
            file_path="app.py",
            line_number=1,
            instruction_type="AI-DOCS",
            description="Add module docstring",
        )
        bundle = ProjectContextBundle(task=task)
        req = build_model_request(bundle, max_tokens=512, temperature=0.2)

        assert req.max_tokens == 512
        assert req.temperature == 0.2
        assert req.task_type == "AI-DOCS"
        assert req.metadata["task_id"] == "task-req"


class TestCLIExplainAndPlanLLM:
    """CLI tests for 'codepilot explain' and 'codepilot plan --llm'."""

    def test_explain_no_tasks(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        result = cli_runner.invoke(app, ["explain", "--directory", str(tmp_path)])
        assert result.exit_code == 0
        assert "No AI instruction comments found" in result.output or "No Tasks Found" in result.output

    def test_explain_context_only(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "app.py"
        code_file.write_text(
            "# AI-FIX: Handle None input safely\n"
            "def process(val):\n"
            "    return val.upper()\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["explain", "--directory", str(tmp_path), "--context-only"],
        )
        assert result.exit_code == 0
        assert "Context Inspector" in result.output
        assert "Source Snippet" in result.output
        assert "Context preview complete" in result.output

    def test_explain_context_only_json(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "app.py"
        code_file.write_text(
            "# AI-TASK: Implement greeting\n"
            "def greet():\n"
            "    pass\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["explain", "--directory", str(tmp_path), "--context-only", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "task" in data
        assert "target_snippet" in data
        assert data["is_safe"] is True

    def test_explain_with_mock_provider(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "worker.py"
        code_file.write_text(
            "# AI-FIX: Guard against division by zero\n"
            "def ratio(a, b):\n"
            "    return a / b\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["explain", "--directory", str(tmp_path), "--provider", "mock"],
        )
        assert result.exit_code == 0
        assert "Task Explanation" in result.output
        assert "Model Analysis & Proposed Approach" in result.output
        assert "Inference Metrics" in result.output
        assert "Source files were not" in result.output

    def test_explain_with_mock_provider_json(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "worker.py"
        code_file.write_text(
            "# AI-DOCS: Add docstring to function\n"
            "def ratio(a, b):\n"
            "    return a / b\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["explain", "--directory", str(tmp_path), "--provider", "mock", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "task" in data
        assert "explanation" in data
        assert "context" in data
        assert data["explanation"]["model_name"] == "mock-qwen2.5-coder:1.5b"

    def test_explain_invalid_task_id(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "worker.py"
        code_file.write_text(
            "# AI-FIX: Fix error\n"
            "def run(): pass\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["explain", "nonexistent-id", "--directory", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "Task ID 'nonexistent-id' not found" in result.output

    def test_explain_blocked_sensitive_file(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        from unittest.mock import patch
        from codepilot.core.parser.models import ParseResult

        sensitive_task = InstructionTask(
            id="task-secret-99",
            file_path=".env",
            line_number=1,
            instruction_type="AI-FIX",
            description="Expose database password",
        )
        fake_result = ParseResult(
            project_root=str(tmp_path),
            scanned_files_count=1,
            tasks=[sensitive_task],
            warnings=[],
            counts_by_type={"AI-FIX": 1},
        )

        with patch("codepilot.cli.InstructionParserEngine.parse", return_value=fake_result):
            result = cli_runner.invoke(
                app,
                ["explain", "--directory", str(tmp_path)],
            )
            assert result.exit_code == 1
            assert "Security Guard Blocked" in result.output or "Security Violation" in result.output

    def test_plan_with_llm_flag(self, cli_runner: CliRunner, tmp_path: Path) -> None:
        code_file = tmp_path / "calc.py"
        code_file.write_text(
            "# AI-FIX: Fix precision loss\n"
            "def compute(): pass\n",
            encoding="utf-8",
        )

        result = cli_runner.invoke(
            app,
            ["plan", "--directory", str(tmp_path), "--llm"],
        )
        assert result.exit_code == 0
        assert "Implementation Plan" in result.output
        # Since default config provider is 'none', it outputs the model provider note
        assert "Model Provider Note" in result.output or "LLM Planning Assistant" in result.output
