"""Tests for R-5, R-6 and R-12.

R-6  bounded, incremental capture of test-runner output
R-12 advisory lock serializing fix generation
R-5  explicit approval required before rollback overwrites working files
"""

import json
import os
import sys
import tracemalloc
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import load_config
from codepilot.core.fixer import FIX_LOCK_FILENAME, generate_preview_patch
from codepilot.core.locking import acquire_lock, is_pid_running, release_lock
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.tester import MAX_OUTPUT_BYTES, run_project_tests
from codepilot.exceptions import FixError, TestExecutionError

DEAD_PID = 0x7FFFFFFE


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _runnable_project(tmp_path: Path) -> None:
    _write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\nversion = '1'\n")


# ===========================================================================
# R-6: output is bounded as it arrives, not after it is all in memory
# ===========================================================================


def test_r6_huge_stdout_is_capped(tmp_path: Path) -> None:
    _runnable_project(tmp_path)
    flood = tmp_path / "flood.py"
    _write(
        flood,
        "import sys\n"
        "line = 'X' * 1000 + '\\n'\n"
        "for _ in range(30000):\n"
        "    sys.stdout.write(line)\n",
    )

    result = run_project_tests(
        tmp_path, load_config(tmp_path), custom_command=f'"{sys.executable}" {flood}'
    )

    # ~30 MB produced; retained output must stay at the cap plus the truncation notice.
    assert len(result.stdout.encode("utf-8")) <= MAX_OUTPUT_BYTES + 200
    assert result.truncated is True
    assert "truncated at 64 KB" in result.stdout


def test_r6_huge_stderr_is_capped(tmp_path: Path) -> None:
    _runnable_project(tmp_path)
    flood = tmp_path / "flood_err.py"
    _write(flood, "import sys\nsys.stderr.write('E' * 5_000_000)\n")

    result = run_project_tests(
        tmp_path, load_config(tmp_path), custom_command=f'"{sys.executable}" {flood}'
    )

    assert len(result.stderr.encode("utf-8")) <= MAX_OUTPUT_BYTES + 200
    assert result.truncated is True


def test_r6_memory_stays_bounded_under_a_flood(tmp_path: Path) -> None:
    """The point of the change: capture must not buffer the whole stream first."""
    _runnable_project(tmp_path)
    flood = tmp_path / "flood_big.py"
    _write(
        flood,
        "import sys\n"
        "line = 'X' * 1000 + '\\n'\n"
        "for _ in range(120000):\n"
        "    sys.stdout.write(line)\n",
    )

    tracemalloc.start()
    try:
        run_project_tests(
            tmp_path, load_config(tmp_path), custom_command=f'"{sys.executable}" {flood}'
        )
        peak_bytes = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    # ~120 MB of child output; retaining it all would show up plainly here.
    assert peak_bytes < 32 * 1024 * 1024, f"peak traced memory {peak_bytes / 1e6:.1f} MB"


def test_r6_small_output_is_passed_through_untouched(tmp_path: Path) -> None:
    _runnable_project(tmp_path)
    script = tmp_path / "small.py"
    _write(script, "print('hello from the suite')\n")

    result = run_project_tests(
        tmp_path, load_config(tmp_path), custom_command=f'"{sys.executable}" {script}'
    )

    assert "hello from the suite" in result.stdout
    assert result.truncated is False
    assert "truncated" not in result.stdout


def test_r6_exit_code_and_pass_flag_survive_streaming(tmp_path: Path) -> None:
    _runnable_project(tmp_path)
    script = tmp_path / "failing.py"
    _write(script, "import sys\nprint('some output')\nsys.exit(3)\n")

    result = run_project_tests(
        tmp_path, load_config(tmp_path), custom_command=f'"{sys.executable}" {script}'
    )

    assert result.exit_code == 3
    assert result.passed is False
    assert "some output" in result.stdout


def test_r6_output_before_a_timeout_is_retained(tmp_path: Path) -> None:
    """Partial output is usually the only clue to where a suite hung."""
    _runnable_project(tmp_path)
    script = tmp_path / "hang.py"
    _write(
        script,
        "import sys, time\n"
        "sys.stdout.write('reached phase one\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n",
    )

    result = run_project_tests(
        tmp_path,
        load_config(tmp_path),
        custom_command=f'"{sys.executable}" {script}',
        timeout_override=5.0,
    )

    assert result.timed_out is True
    assert "reached phase one" in result.stdout
    assert "timed out after" in result.stderr.lower()


# ===========================================================================
# R-12: concurrent fix runs cannot race on the pending patch
# ===========================================================================


def _fixable_project(tmp_path: Path) -> None:
    _write(tmp_path / "m.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")


def test_r12_fix_acquires_and_releases_its_lock(tmp_path: Path) -> None:
    _fixable_project(tmp_path)

    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))

    assert not (tmp_path / FIX_LOCK_FILENAME).exists(), "lock must not be left behind"


def test_r12_concurrent_fix_run_is_rejected(tmp_path: Path) -> None:
    """A live lock held by another process must stop a second run."""
    _fixable_project(tmp_path)
    lock_file = tmp_path / FIX_LOCK_FILENAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({"pid": os.getpid(), "cwd": str(tmp_path)}), encoding="utf-8")

    with pytest.raises(FixError, match="already running"):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))


def test_r12_rejected_run_does_not_touch_the_existing_pending_patch(tmp_path: Path) -> None:
    """The whole point: the first run's staged patch must survive the second run."""
    _fixable_project(tmp_path)
    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))
    pending = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    original = pending.read_text(encoding="utf-8")

    lock_file = tmp_path / FIX_LOCK_FILENAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({"pid": os.getpid(), "cwd": str(tmp_path)}), encoding="utf-8")

    with pytest.raises(FixError):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))

    assert pending.read_text(encoding="utf-8") == original


def test_r12_stale_fix_lock_is_reclaimed(tmp_path: Path) -> None:
    """A crashed run must not block the repository permanently."""
    _fixable_project(tmp_path)
    lock_file = tmp_path / FIX_LOCK_FILENAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps({"pid": DEAD_PID, "cwd": str(tmp_path)}), encoding="utf-8")

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="mock"))

    assert bundle.file_patches
    assert not lock_file.exists()


def test_r12_lock_is_released_even_when_generation_fails(tmp_path: Path) -> None:
    _write(tmp_path / "m.py", "x = 1\n")  # no AI task -> nothing to do

    with pytest.raises(FixError):
        generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="none"))

    assert not (tmp_path / FIX_LOCK_FILENAME).exists()


# ===========================================================================
# Shared locking helper
# ===========================================================================


def test_locking_reclaims_dead_owner_and_respects_live_owner(tmp_path: Path) -> None:
    name = ".codepilot/probe.lock"
    lock_file = tmp_path / name

    acquire_lock(tmp_path, name, error_factory=RuntimeError)
    assert json.loads(lock_file.read_text(encoding="utf-8"))["pid"] == os.getpid()

    with pytest.raises(RuntimeError):
        acquire_lock(tmp_path, name, error_factory=RuntimeError)

    lock_file.write_text(json.dumps({"pid": DEAD_PID}), encoding="utf-8")
    acquire_lock(tmp_path, name, error_factory=RuntimeError)
    assert json.loads(lock_file.read_text(encoding="utf-8"))["pid"] == os.getpid()

    release_lock(tmp_path, name)
    assert not lock_file.exists()
    release_lock(tmp_path, name)  # releasing twice must not raise


def test_locking_corrupt_lock_fails_closed(tmp_path: Path) -> None:
    name = ".codepilot/probe.lock"
    lock_file = tmp_path / name
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("{ not json", encoding="utf-8")

    with pytest.raises(RuntimeError):
        acquire_lock(tmp_path, name, error_factory=RuntimeError)


@pytest.mark.parametrize("pid", [None, "", "abc", -1, 0])
def test_locking_liveness_is_conservative_for_unusable_pids(pid) -> None:
    """Unparsable owners are treated as alive; only a clearly invalid pid is dead."""
    expected_dead = pid in (-1, 0)
    assert is_pid_running(pid) is (not expected_dead)


def test_locking_current_process_is_detected_as_running() -> None:
    assert is_pid_running(os.getpid()) is True


def test_locking_dead_pid_is_detected() -> None:
    assert is_pid_running(DEAD_PID) is False


# ===========================================================================
# R-5: rollback requires explicit approval
# ===========================================================================


def _applied_patch(tmp_path: Path) -> None:
    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)
    apply_approved_patch(tmp_path, approved=True, config=config)


def test_r5_bare_rollback_is_refused(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Rollback was the only mutating command with no authorization gate."""
    _applied_patch(tmp_path)
    applied = (tmp_path / "calc.py").read_text(encoding="utf-8")

    result = cli_runner.invoke(app, ["rollback", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "Approval Required" in result.output
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == applied, "no write may occur"


def test_r5_approved_rollback_restores(cli_runner: CliRunner, tmp_path: Path) -> None:
    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    original = (tmp_path / "calc.py").read_text(encoding="utf-8")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)
    apply_approved_patch(tmp_path, approved=True, config=config)

    result = cli_runner.invoke(app, ["rollback", "--approved", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == original


def test_r5_listing_snapshots_needs_no_approval(cli_runner: CliRunner, tmp_path: Path) -> None:
    """--list is read-only, so it must stay frictionless."""
    _applied_patch(tmp_path)

    result = cli_runner.invoke(app, ["rollback", "--list", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Approval Required" not in result.output


def test_r5_force_alone_does_not_imply_approval(cli_runner: CliRunner, tmp_path: Path) -> None:
    """--force answers a different question and must not bypass the gate."""
    _applied_patch(tmp_path)
    applied = (tmp_path / "calc.py").read_text(encoding="utf-8")

    result = cli_runner.invoke(app, ["rollback", "--force", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "Approval Required" in result.output
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == applied


def test_r5_recovery_primitive_remains_callable_without_a_flag(tmp_path: Path) -> None:
    """The gate belongs at the CLI boundary; the library primitive is unchanged."""
    from codepilot.core.recovery import rollback_patch

    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    original = (tmp_path / "calc.py").read_text(encoding="utf-8")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)
    apply_approved_patch(tmp_path, approved=True, config=config)

    result = rollback_patch(tmp_path)

    assert result.restored_files == ["calc.py"]
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == original
