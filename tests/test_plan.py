"""Automated tests for the 'codepilot plan' command and planner engine."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.core.planner import (
    PlanTask,
    estimate_scope,
    evaluate_risk_level,
    generate_plan,
)
from codepilot.core.scanner import InstructionComment, ScanResult


def test_evaluate_risk_levels() -> None:
    """Verify risk evaluation based on keywords and instruction types."""
    auth_inst = InstructionComment(
        id="1",
        file_path="auth/jwt.py",
        line_number=10,
        instruction_type="AI",
        raw_comment="# AI: verify bearer token",
        prompt="verify bearer token",
    )
    assert evaluate_risk_level(auth_inst) == "HIGH"

    fix_inst = InstructionComment(
        id="2",
        file_path="math_utils.py",
        line_number=5,
        instruction_type="FIX",
        raw_comment="# AI-FIX: handle division by zero",
        prompt="handle division by zero",
    )
    assert evaluate_risk_level(fix_inst) == "MEDIUM"

    simple_inst = InstructionComment(
        id="3",
        file_path="greeting.py",
        line_number=1,
        instruction_type="TODO",
        raw_comment="# AI-TODO: print hello",
        prompt="print hello",
    )
    assert evaluate_risk_level(simple_inst) == "LOW"


def test_estimate_scope() -> None:
    """Verify scope estimation based on prompt keywords."""
    inst1 = InstructionComment(
        id="1", file_path="a.py", line_number=1, instruction_type="AI",
        raw_comment="", prompt="implement function calculate_tax",
    )
    assert estimate_scope(inst1) == "FUNCTION"

    inst2 = InstructionComment(
        id="2", file_path="b.py", line_number=1, instruction_type="AI",
        raw_comment="", prompt="refactor all module classes",
    )
    assert estimate_scope(inst2) == "MODULE"

    inst3 = InstructionComment(
        id="3", file_path="c.py", line_number=1, instruction_type="AI",
        raw_comment="", prompt="add error handling loop block",
    )
    assert estimate_scope(inst3) == "BLOCK"


def test_generate_plan_empty() -> None:
    """Verify plan generation on empty scan result."""
    empty_scan = ScanResult(
        project_root="/test",
        scanned_files_count=0,
        instructions=[],
        timestamp="2026-10-20T00:00:00Z",
    )
    plan = generate_plan(empty_scan)
    assert plan.total_tasks == 0
    assert plan.plan_id == "plan-empty"


def test_cli_plan_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'codepilot plan' generates tasks from scanned file."""
    code_file = tmp_path / "app.py"
    code_file.write_text("# AI: implement hello_world function\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["plan", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Implementation Plan" in result.output
    assert "TASK-001" in result.output
    assert "app.py:1" in result.output
    assert "Total Tasks: 1" in result.output


def test_cli_plan_custom_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'codepilot plan --output' writes plan JSON to custom destination."""
    code_file = tmp_path / "app.py"
    code_file.write_text("# AI: fix timeout error\n", encoding="utf-8")

    custom_out = tmp_path / "custom_plan.json"
    result = cli_runner.invoke(
        app,
        ["plan", "--directory", str(tmp_path), "--output", str(custom_out)],
    )
    assert result.exit_code == 0
    assert custom_out.exists()
    plan_data = json.loads(custom_out.read_text(encoding="utf-8"))
    assert plan_data["total_tasks"] == 1
    assert plan_data["tasks"][0]["task_id"] == "TASK-001"


def test_cli_plan_json(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify 'codepilot plan --json' prints valid JSON."""
    code_file = tmp_path / "calc.py"
    code_file.write_text("# AI-FIX: handle negative numbers\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["plan", "--directory", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "plan_id" in data
    assert data["total_tasks"] == 1
    assert "tasks" in data


def test_generate_plan_preserves_target_symbol_and_type() -> None:
    """TASK-AUD-07: Verify generate_plan preserves target_symbol and target_symbol_type."""
    inst = InstructionComment(
        id="inst-101",
        file_path="src/normalizer.py",
        line_number=25,
        instruction_type="AI-DOCS",
        raw_comment="# AI-DOCS: document method",
        prompt="document method",
        target_symbol="ScoreNormalizer.normalize_scores",
        target_symbol_type="method",
    )
    scan = ScanResult(
        project_root="/repo",
        scanned_files_count=1,
        instructions=[inst],
        timestamp="2026-10-23T00:00:00Z",
    )
    plan = generate_plan(scan)
    assert len(plan.tasks) == 1
    task = plan.tasks[0]
    assert task.target_symbol == "ScoreNormalizer.normalize_scores"
    assert task.target_symbol_type == "method"

    # Test serialization to dict
    data = task.to_dict()
    assert data["target_symbol"] == "ScoreNormalizer.normalize_scores"
    assert data["target_symbol_type"] == "method"


def test_plan_task_serialization_backward_compatibility() -> None:
    """TASK-AUD-07: Verify PlanTask.from_dict handles legacy dictionaries without target symbol fields."""
    legacy_data = {
        "task_id": "TASK-001",
        "instruction_id": "inst-1",
        "target_file": "app.py",
        "line_number": 10,
        "instruction_type": "AI-FIX",
        "description": "Fix bug",
        "risk_level": "LOW",
        "scope": "LINE",
        "steps": ["Step 1"],
    }
    task = PlanTask.from_dict(legacy_data)
    assert task.task_id == "TASK-001"
    assert task.target_symbol is None
    assert task.target_symbol_type is None

