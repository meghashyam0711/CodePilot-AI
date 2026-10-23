"""Phase 10: End-to-End Workflow Integration and Release Readiness Test Suite.

Validates the complete 13-stage CodePilot AI lifecycle, security boundaries,
file ownership enforcement, stale patch guards, and reversible rollback in
isolated test fixtures.
"""

import json
from pathlib import Path
import subprocess
from typing import Generator
import pytest
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CodePilotConfig, load_config
from codepilot.core.security import (
    FileOperation,
    FileOwnershipCategory,
    check_file_permission,
    classify_file_ownership,
    resolve_project_root,
)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Fixture providing Typer CLI test runner."""
    return CliRunner()


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary initialized Git repository with basic structure."""
    repo = tmp_path / "test_repo"
    repo.mkdir(parents=True, exist_ok=True)

    # Initialize git repository
    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test Developer"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "dev@example.com"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
    except (subprocess.SubprocessError, OSError):
        pass  # Proceed even if Git binary is missing in some environments

    # Create dummy source and test files
    src_dir = repo / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)

    math_file = src_dir / "math_utils.py"
    math_file.write_text(
        "def calculate_average(items):\n"
        "    # AI-FIX: Handle empty list safely without ZeroDivisionError\n"
        "    return sum(items) / len(items)\n",
        encoding="utf-8",
    )

    test_file = tests_dir / "test_math.py"
    test_file.write_text(
        "def test_calculate_average():\n"
        "    from src.math_utils import calculate_average\n"
        "    assert calculate_average([10, 20]) == 15.0\n",
        encoding="utf-8",
    )

    yield repo


def test_e2e_full_workflow_lifecycle(cli_runner: CliRunner, isolated_repo: Path) -> None:
    """Test the complete 13-step CodePilot workflow in end-to-end isolation."""
    repo = isolated_repo

    # Step 1: Project Root Detection & Init
    res = cli_runner.invoke(app, ["init", "--directory", str(repo)])
    assert res.exit_code == 0
    config_file = repo / ".codepilot.toml"
    assert config_file.is_file()

    # Configure mock provider for deterministic offline testing
    content = config_file.read_text(encoding="utf-8")
    content = content.replace('model_provider = "none"', 'model_provider = "mock"')
    config_file.write_text(content, encoding="utf-8")

    # Step 2: Non-destructive Status Check
    res = cli_runner.invoke(app, ["status", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    status_data = json.loads(res.output)
    assert status_data["config_file_found"] is True
    assert status_data["ai_engine"]["provider"] == "mock"
    assert status_data["ai_engine"]["ready"] is True

    # Step 3: Architecture Analysis
    arch_out = repo / ".codepilot" / "architecture.json"
    res = cli_runner.invoke(app, ["analyze", "--directory", str(repo), "--output", str(arch_out), "--json"])
    assert res.exit_code == 0
    arch_data = json.loads(res.output)
    assert arch_data["project_type"] == "python"
    assert arch_out.is_file()

    # Step 4: Instruction Scanning & Task Mapping
    res = cli_runner.invoke(app, ["scan", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    scan_data = json.loads(res.output)
    assert len(scan_data["instructions"]) >= 1

    res = cli_runner.invoke(app, ["tasks", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    tasks_data = json.loads(res.output)
    assert len(tasks_data["tasks"]) >= 1
    target_task = tasks_data["tasks"][0]
    assert target_task["instruction_type"] == "AI-FIX"

    # Step 5: Implementation Planning
    res = cli_runner.invoke(app, ["plan", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    plan_data = json.loads(res.output)
    assert len(plan_data["tasks"]) >= 1

    # Step 6: Task Context Explanation
    res = cli_runner.invoke(app, ["explain", target_task["id"], "--directory", str(repo)])
    assert res.exit_code == 0

    # Step 7: Fix Preview (Generates Patch without Disk Modification)
    original_math_content = (repo / "src" / "math_utils.py").read_text(encoding="utf-8")
    res = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(repo)])
    assert res.exit_code == 0
    # Source file MUST be completely untouched during preview
    assert (repo / "src" / "math_utils.py").read_text(encoding="utf-8") == original_math_content

    pending_patch = repo / ".codepilot" / "patches" / "pending_patch.json"
    assert pending_patch.is_file()

    # Step 8: Diff Inspection
    res = cli_runner.invoke(app, ["diff", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    diff_data = json.loads(res.output)
    assert diff_data["patch_id"].startswith("patch-")
    assert len(diff_data["file_patches"]) >= 1

    # Step 9: Rejection of Unapproved Patch Application
    res = cli_runner.invoke(app, ["apply", "--directory", str(repo)])
    assert res.exit_code != 0
    assert "explicit approval is required" in res.output or "rejected" in res.output
    # Source file still untouched
    assert (repo / "src" / "math_utils.py").read_text(encoding="utf-8") == original_math_content

    # Step 10: Approved Patch Application
    res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(repo)])
    assert res.exit_code == 0
    # Source file MUST now be safely modified
    modified_math_content = (repo / "src" / "math_utils.py").read_text(encoding="utf-8")
    assert modified_math_content != original_math_content

    # Pre-apply backup snapshot must exist
    backup_dir = repo / ".codepilot" / "backups"
    assert backup_dir.is_dir()
    patch_backups = list(backup_dir.glob("patch-*"))
    assert len(patch_backups) >= 1
    manifest_file = patch_backups[0] / "manifest.json"
    assert manifest_file.is_file()

    # Step 11: Rollback Inspection and Restoration
    res = cli_runner.invoke(app, ["rollback", "--list", "--directory", str(repo)])
    assert res.exit_code == 0

    patch_id = patch_backups[0].name
    res = cli_runner.invoke(app, ["rollback", "--patch-id", patch_id, "--directory", str(repo)])
    assert res.exit_code == 0
    # Source file MUST be exactly restored to original content
    restored_math_content = (repo / "src" / "math_utils.py").read_text(encoding="utf-8")
    assert restored_math_content == original_math_content

    # Step 12: Test Preview (Zero Process Execution)
    res = cli_runner.invoke(app, ["test", "--preview", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    preview_data = json.loads(res.output)
    assert preview_data["framework"] == "pytest"

    # Step 13: Doctor Diagnostics
    res = cli_runner.invoke(app, ["doctor", "--directory", str(repo), "--json"])
    assert res.exit_code == 0
    doctor_data = json.loads(res.output)
    assert doctor_data["overall_passed"] is True


def test_e2e_stale_patch_detection(cli_runner: CliRunner, isolated_repo: Path) -> None:
    """Verify that patch application fails safely if target file is modified after preview."""
    repo = isolated_repo

    # Initialize and configure mock provider
    cli_runner.invoke(app, ["init", "--directory", str(repo)])
    config_file = repo / ".codepilot.toml"
    content = config_file.read_text(encoding="utf-8").replace('model_provider = "none"', 'model_provider = "mock"')
    config_file.write_text(content, encoding="utf-8")

    # Generate preview patch
    res = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(repo)])
    assert res.exit_code == 0

    # Simulate developer editing the target file before running apply
    math_file = repo / "src" / "math_utils.py"
    math_file.write_text("# Developer manual edit\n" + math_file.read_text(encoding="utf-8"), encoding="utf-8")

    # Apply must be rejected due to stale hash mismatch
    res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(repo)])
    assert res.exit_code != 0
    assert "stale" in res.output.lower() or "modified" in res.output.lower()


def test_e2e_security_path_traversal_blocked(cli_runner: CliRunner, isolated_repo: Path) -> None:
    """Verify that path traversal attempts outside repository root are strictly blocked."""
    repo = isolated_repo
    cfg = CodePilotConfig()
    outside_dir = repo.parent / "external_forbidden"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret.py"
    outside_file.write_text("# AI-FIX: do evil\nx = 1\n", encoding="utf-8")

    # Direct ownership check
    cat = classify_file_ownership(outside_file, project_root=repo, config=cfg)
    assert cat == FileOwnershipCategory.EXTERNAL

    from codepilot.exceptions import SecurityError
    with pytest.raises(SecurityError) as exc_info:
        check_file_permission(outside_file, FileOperation.MODIFY, project_root=repo, config=cfg, approved=True)
    assert "outside" in str(exc_info.value).lower()


def test_e2e_security_protected_files_blocked(isolated_repo: Path) -> None:
    """Verify that sensitive credentials and version control files reject modifications."""
    repo = isolated_repo
    cfg = CodePilotConfig()

    env_file = repo / ".env"
    env_file.write_text("SECRET_KEY=12345", encoding="utf-8")

    cat = classify_file_ownership(env_file, project_root=repo, config=cfg)
    assert cat == FileOwnershipCategory.PROTECTED

    from codepilot.exceptions import SecurityError
    with pytest.raises(SecurityError) as exc_info:
        check_file_permission(env_file, FileOperation.MODIFY, project_root=repo, config=cfg, approved=True)
    assert "protected" in str(exc_info.value).lower()


def test_e2e_test_concurrency_lock_defense(cli_runner: CliRunner, isolated_repo: Path) -> None:
    """Verify that active test lock file blocks overlapping test executions."""
    repo = isolated_repo

    # Create artificial lock file
    lock_file = repo / ".codepilot" / "test.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("pid=99999\nstarted=2026-10-22T00:00:00Z\n", encoding="utf-8")

    res = cli_runner.invoke(app, ["test", "--approved", "--directory", str(repo)])
    assert res.exit_code != 0
    assert "lock" in res.output.lower()
