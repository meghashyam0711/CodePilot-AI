"""Tests for R-2: a failing task must not discard the work of the others.

Before this change, a single unusable model output aborted the whole run with FixError,
throwing away sibling tasks that had succeeded and the inference already paid for. This
was reproduced with a live 1.5B model: an AI-FIX task succeeded while an AI-DOCS task in
the same file failed, and the complete patch was lost.

Failures are now collected per task (and per file for whole-file validation), successful
work is staged, and the developer is told exactly what was skipped.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codepilot.cli import app, _render_skipped_tasks
from codepilot.config import load_config
from codepilot.core.fixer import PatchBundle, generate_preview_patch, load_pending_patch
from codepilot.core.models.base import ModelResponse
from codepilot.core.patcher import apply_approved_patch
from codepilot.exceptions import FixError

GUARD_CLAUSE = "```python\nif b == 0:\n    return 0\nreturn a / b\n```"

MIXED_SOURCE = (
    "def divide(a, b):\n"
    "    # AI-FIX: guard division by zero\n"
    "    return a / b\n"
    "\n\n"
    "def add(a, b):\n"
    "    # AI-DOCS: document this adder\n"
    "    return a + b\n"
)


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class _ScriptedClient:
    """Model stub whose reply depends on the task's instruction type or target file."""

    def __init__(self, by_type=None, by_file=None, default="no usable code here"):
        self._by_type = by_type or {}
        self._by_file = by_file or {}
        self._default = default
        self.calls = 0

    def health_check(self):
        return True, "stub runtime ready"

    def generate(self, request, model_name=None):
        self.calls += 1
        meta = request.metadata or {}
        content = self._by_file.get(meta.get("file_path"))
        if content is None:
            content = self._by_type.get(request.task_type, self._default)
        return ModelResponse(
            content=content,
            model_name="stub",
            latency_seconds=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            finish_reason="stop",
        )


@pytest.fixture()
def scripted(monkeypatch):
    def _install(**kwargs):
        import codepilot.core.models.factory as factory

        client = _ScriptedClient(**kwargs)
        monkeypatch.setattr(factory, "create_model_client", lambda **kw: client)
        return client

    return _install


# ---------------------------------------------------------------------------
# A failing task no longer discards its successful siblings
# ---------------------------------------------------------------------------


def test_successful_task_is_preserved_when_a_sibling_task_fails(
    tmp_path: Path, scripted
) -> None:
    """The original defect: the whole patch was lost because one task failed."""
    _write(tmp_path / "m.py", MIXED_SOURCE)
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert bundle.file_patches, "the successful task's work must still be staged"
    assert "if b == 0" in bundle.file_patches[0].proposed_content


def test_failed_task_is_recorded_with_a_reason(tmp_path: Path, scripted) -> None:
    _write(tmp_path / "m.py", MIXED_SOURCE)
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert len(bundle.task_failures) == 1
    failure = bundle.task_failures[0]
    assert failure["file_path"] == "m.py"
    assert failure["instruction_type"] == "AI-DOCS"
    assert "docstring" in failure["reason"]


def test_partial_run_is_reported_as_partial_success(tmp_path: Path, scripted) -> None:
    """Status must not claim plain success when work was skipped."""
    _write(tmp_path / "m.py", MIXED_SOURCE)
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert bundle.status == "partial_success"


def test_failed_task_leaves_no_partial_edit(tmp_path: Path, scripted) -> None:
    """A task that fails midway must roll back to the pre-task state."""
    _write(tmp_path / "m.py", MIXED_SOURCE)
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    proposed = bundle.file_patches[0].proposed_content
    assert "# AI-DOCS: document this adder" in proposed, "docs comment must be untouched"
    assert "return a + b" in proposed


def test_partial_patch_applies_cleanly(tmp_path: Path, scripted) -> None:
    target = tmp_path / "m.py"
    _write(target, MIXED_SOURCE)
    config = load_config(tmp_path, model_provider="ollama")
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    generate_preview_patch(tmp_path, config)
    result = apply_approved_patch(tmp_path, approved=True, config=config)

    assert result.applied_files == ["m.py"]
    assert "if b == 0" in target.read_text(encoding="utf-8")


def test_failures_persist_to_the_pending_patch_file(tmp_path: Path, scripted) -> None:
    """'diff' in a later invocation must still be able to report what was skipped."""
    _write(tmp_path / "m.py", MIXED_SOURCE)
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    saved = json.loads(
        (tmp_path / ".codepilot" / "patches" / "pending_patch.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "partial_success"
    assert saved["task_failures"][0]["instruction_type"] == "AI-DOCS"

    reloaded = load_pending_patch(tmp_path)
    assert reloaded is not None
    assert len(reloaded.task_failures) == 1


# ---------------------------------------------------------------------------
# Multi-file runs
# ---------------------------------------------------------------------------


def test_one_bad_file_does_not_discard_a_good_file(tmp_path: Path, scripted) -> None:
    _write(tmp_path / "good.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / "bad.py", "def g(a, b):\n    # AI-FIX: guard zero\n    return a - b\n")
    scripted(by_file={"good.py": GUARD_CLAUSE, "bad.py": "no code at all"})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert [fp.file_path for fp in bundle.file_patches] == ["good.py"]
    assert [f["file_path"] for f in bundle.task_failures] == ["bad.py"]
    assert bundle.status == "partial_success"


def test_file_level_validation_failure_drops_only_that_file(tmp_path: Path, scripted) -> None:
    """A syntax-breaking result for one file must not abort the whole run."""
    _write(tmp_path / "good.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / "bad.py", "def g(a, b):\n    # AI-FIX: guard zero\n    return a - b\n")
    scripted(
        by_file={
            "good.py": GUARD_CLAUSE,
            # Syntactically invalid replacement: breaks the file, so it must be dropped
            "bad.py": "```python\nif b == (((:\n```",
        }
    )

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert [fp.file_path for fp in bundle.file_patches] == ["good.py"]
    assert any(f["file_path"] == "bad.py" for f in bundle.task_failures)


def test_dropped_file_is_not_modified_on_disk(tmp_path: Path, scripted) -> None:
    source = "def g(a, b):\n    # AI-FIX: guard zero\n    return a - b\n"
    _write(tmp_path / "good.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / "bad.py", source)
    scripted(by_file={"good.py": GUARD_CLAUSE, "bad.py": "no code at all"})

    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert (tmp_path / "bad.py").read_text(encoding="utf-8") == source


# ---------------------------------------------------------------------------
# Behaviour that must NOT change
# ---------------------------------------------------------------------------


def test_fully_successful_run_still_reports_plain_success(tmp_path: Path, scripted) -> None:
    _write(tmp_path / "m.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    scripted(by_type={"AI-FIX": GUARD_CLAUSE})

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert bundle.status == "success"
    assert bundle.task_failures == []


def test_single_failing_task_still_raises(tmp_path: Path, scripted) -> None:
    """With nothing staged there is no partial result, so an error is still correct."""
    _write(tmp_path / "m.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    scripted(by_type={"AI-FIX": "I cannot help with that."})

    with pytest.raises(FixError, match="Could not extract usable code modification"):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))


def test_all_tasks_failing_raises_with_every_reason(tmp_path: Path, scripted) -> None:
    _write(tmp_path / "a.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / "b.py", "def g(a, b):\n    # AI-FIX: guard zero\n    return a - b\n")
    scripted(default="nothing usable")

    with pytest.raises(FixError) as exc:
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    message = str(exc.value)
    assert "All 2 task(s) failed" in message
    assert "a.py" in message and "b.py" in message


def test_nothing_is_staged_when_every_task_fails(tmp_path: Path, scripted) -> None:
    _write(tmp_path / "m.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    scripted(by_type={"AI-FIX": "I cannot help with that."})

    with pytest.raises(FixError):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert not (tmp_path / ".codepilot" / "patches" / "pending_patch.json").exists()


def test_security_violation_remains_fatal(tmp_path: Path, scripted, monkeypatch) -> None:
    """A boundary violation must stop the run, never be downgraded to a skipped task."""
    import codepilot.core.context.engine as engine_module

    _write(tmp_path / "m.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    scripted(by_type={"AI-FIX": GUARD_CLAUSE})

    real_build = engine_module.ProjectContextEngine.build_context

    def _unsafe(self, task, token_budget=2048):
        bundle = real_build(self, task, token_budget=token_budget)
        bundle.is_safe = False
        bundle.safety_warning = "simulated boundary violation"
        return bundle

    monkeypatch.setattr(engine_module.ProjectContextEngine, "build_context", _unsafe)

    with pytest.raises(FixError, match="Security violation"):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))


def test_empty_project_still_reports_no_modifications(tmp_path: Path) -> None:
    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))

    assert bundle.status == "no_modifications"
    assert bundle.task_failures == []


# ---------------------------------------------------------------------------
# The developer must be told
# ---------------------------------------------------------------------------


def test_cli_fix_shows_the_skipped_task_table(cli_runner: CliRunner, tmp_path: Path, scripted) -> None:
    _write(tmp_path / "m.py", MIXED_SOURCE)
    _write(tmp_path / ".codepilot.toml", 'model_provider = "ollama"\n')
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})

    result = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Skipped Tasks" in result.output
    assert "Partially Completed" in result.output


def test_cli_fix_stays_quiet_on_a_fully_successful_run(
    cli_runner: CliRunner, tmp_path: Path, scripted
) -> None:
    _write(tmp_path / "m.py", "def f(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / ".codepilot.toml", 'model_provider = "ollama"\n')
    scripted(by_type={"AI-FIX": GUARD_CLAUSE})

    result = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Skipped Tasks" not in result.output


def test_cli_diff_resurfaces_skipped_tasks(cli_runner: CliRunner, tmp_path: Path, scripted) -> None:
    _write(tmp_path / "m.py", MIXED_SOURCE)
    _write(tmp_path / ".codepilot.toml", 'model_provider = "ollama"\n')
    scripted(by_type={"AI-FIX": GUARD_CLAUSE, "AI-DOCS": "this is not a docstring"})
    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    result = cli_runner.invoke(app, ["diff", "--directory", str(tmp_path)])

    assert "Skipped Tasks" in result.output


def test_render_helper_counts_failures_and_handles_empty() -> None:
    bundle = PatchBundle(
        patch_id="p1",
        created_at="2026-01-01T00:00:00Z",
        project_root="/tmp",
        model_provider="mock",
        file_patches=[],
        full_diff="",
        task_failures=[
            {"task_id": "TASK-002", "file_path": "m.py", "instruction_type": "AI-DOCS",
             "reason": "no docstring produced"}
        ],
    )
    empty = PatchBundle(
        patch_id="p2",
        created_at="2026-01-01T00:00:00Z",
        project_root="/tmp",
        model_provider="mock",
        file_patches=[],
        full_diff="",
    )

    assert _render_skipped_tasks(bundle) == 1
    assert _render_skipped_tasks(empty) == 0
