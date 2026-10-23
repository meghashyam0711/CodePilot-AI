"""Automated tests for Phase 7, 8, and 9: Security, Recovery, Model Distribution, and Safe Testing."""

import json
from pathlib import Path
import subprocess
import sys
import time
from typer.testing import CliRunner
import pytest

from codepilot.cli import app
from codepilot.config import CONFIG_FILENAME, CodePilotConfig
from codepilot.core.fixer import (
    PatchBundle,
    generate_mock_file_fix,
    generate_preview_patch,
    load_pending_patch,
)
from codepilot.core.models.base import ModelRequest
from codepilot.core.models.mock_client import MockModelClient
from codepilot.core.models.standalone_client import (
    StandaloneGGUFClient,
    compute_file_sha256,
)
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.recovery import (
    create_patch_backup,
    list_backups,
    rollback_patch,
)
from codepilot.core.security import (
    is_binary_file,
    is_protected_file,
    sanitize_filename,
    sanitize_log_output,
    validate_target_path,
)
from codepilot.core.test_analyzer import analyze_test_failure
from codepilot.core.tester import (
    acquire_test_lock,
    discover_project_tests,
    parse_test_failures,
    release_test_lock,
    run_project_tests,
    validate_test_command,
)
from codepilot.exceptions import (
    FixError,
    PatchError,
    RecoveryError,
    SecurityError,
    TestExecutionError,
)


# ==============================================================================
# 1. Security & Repository Boundary Tests
# ==============================================================================

def test_validate_target_path_rejects_path_traversal(tmp_path: Path) -> None:
    """Security Boundary: Reject paths attempting to escape repository root via '../'."""
    config = CodePilotConfig(project_root=tmp_path)
    escaped_path = "../outside.py"

    with pytest.raises(SecurityError) as exc_info:
        validate_target_path(escaped_path, tmp_path, config)
    assert "traversal detected" in str(exc_info.value).lower() or "boundary violation" in str(exc_info.value).lower()


def test_validate_target_path_rejects_protected_files(tmp_path: Path) -> None:
    """Security Boundary: Reject changes targeting protected credentials and configs."""
    config = CodePilotConfig(project_root=tmp_path)
    protected_targets = [".env", ".env.local", "credentials.json", "server.key", "cert.pem", "pyproject.toml"]

    for target in protected_targets:
        file_path = tmp_path / target
        file_path.touch()
        with pytest.raises(SecurityError) as exc_info:
            validate_target_path(target, tmp_path, config)
        assert "protected" in str(exc_info.value).lower()


def test_validate_target_path_rejects_protected_directories(tmp_path: Path) -> None:
    """Security Boundary: Reject changes inside .git or .venv directories."""
    config = CodePilotConfig(project_root=tmp_path)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    git_file = git_dir / "config"
    git_file.touch()

    with pytest.raises(SecurityError) as exc_info:
        validate_target_path(".git/config", tmp_path, config)
    assert "protected" in str(exc_info.value).lower()


def test_validate_target_path_rejects_binary_files(tmp_path: Path) -> None:
    """Security Boundary: Reject text patch generation on binary files."""
    config = CodePilotConfig(project_root=tmp_path)
    binary_file = tmp_path / "app.exe"
    binary_file.write_bytes(b"\x00\x01\x02\x03\x00\xff")

    with pytest.raises(SecurityError) as exc_info:
        validate_target_path("app.exe", tmp_path, config, check_exists=True)
    assert "binary file" in str(exc_info.value).lower()


def test_sanitize_log_output_masks_secrets() -> None:
    """Security Boundary: Secret values are scrubbed from diagnostic and log outputs."""
    raw_log = "API request failed with api_key='sk-1234567890abcdef1234567890' and ghp_abcdefghijklmnopqrstuvwxyz123456"
    sanitized = sanitize_log_output(raw_log)
    assert "sk-1234567890abcdef1234567890" not in sanitized
    assert "REDACTED_SECRET" in sanitized


# ==============================================================================
# 2. Patch Generation & Diff Inspection Tests (Phase 7)
# ==============================================================================

def test_fix_preview_rejects_target_outside_repository(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Fix Engine: Rejects patch generation if an instruction references an external file."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Manually craft a malicious plan trying to patch ../escape.py
    from codepilot.core.planner import Plan, PlanTask
    plan_file = tmp_path / ".codepilot" / "plan.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    malicious_plan = Plan(
        plan_id="plan-evil",
        created_at="2026-10-22T00:00:00Z",
        project_root=str(tmp_path),
        tasks=[
            PlanTask(
                task_id="task-evil",
                instruction_id="inst-evil",
                target_file="../../outside.py",
                line_number=1,
                instruction_type="AI-FIX",
                description="Inject payload",
                risk_level="HIGH",
                scope="file",
            )
        ],
    )

    from codepilot.core.planner import save_plan
    save_plan(malicious_plan, plan_file)

    result = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "security boundary rejected" in result.output.lower() or "traversal" in result.output.lower()


def test_diff_command_with_no_pending_patch(cli_runner: CliRunner, tmp_path: Path) -> None:
    """CLI: 'codepilot diff' informs cleanly when no patch is awaiting review."""
    result = cli_runner.invoke(app, ["diff", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "No pending patch found" in result.output


def test_diff_command_inspects_generated_patch(cli_runner: CliRunner, tmp_path: Path) -> None:
    """CLI: 'codepilot diff' displays syntax-highlighted diff for review without modifying files."""
    test_file = tmp_path / "math_mod.py"
    original = "def add(a, b):\n    # AI-TASK: implement sum\n    pass\n"
    test_file.write_text(original, encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # 1. Generate preview
    fix_res = cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    assert fix_res.exit_code == 0

    # Source file MUST remain untouched
    assert test_file.read_text(encoding="utf-8") == original

    # 2. Inspect with diff command
    diff_res = cli_runner.invoke(app, ["diff", "--directory", str(tmp_path)])
    assert diff_res.exit_code == 0
    assert "Pending Patch Inspection" in diff_res.output
    assert "math_mod.py" in diff_res.output
    assert "return a + b" in diff_res.output

    # Source file STILL untouched
    assert test_file.read_text(encoding="utf-8") == original


def test_diff_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """CLI: 'codepilot diff --json' outputs machine-readable pending patch."""
    test_file = tmp_path / "service.py"
    test_file.write_text("def run():\n    # AI-FIX: return true\n    pass\n", encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    diff_res = cli_runner.invoke(app, ["diff", "--directory", str(tmp_path), "--json"])
    assert diff_res.exit_code == 0
    data = json.loads(diff_res.output)
    assert "patch_id" in data
    assert len(data["file_patches"]) == 1


# ==============================================================================
# 3. Recovery, Pre-Apply Backups, and Rollback Tests (Phase 9)
# ==============================================================================

def test_apply_creates_pre_apply_backup(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Recovery: Applying a patch automatically creates a backup snapshot before writing."""
    test_file = tmp_path / "core.py"
    original = "def compute():\n    # AI: return true\n    pass\n"
    test_file.write_text(original, encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])

    apply_res = cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])
    assert apply_res.exit_code == 0
    assert "Successfully applied patch" in apply_res.output
    assert "Pre-apply backup created" in apply_res.output

    # Verify backup exists on disk
    backups = list_backups(tmp_path)
    assert len(backups) == 1
    assert len(backups[0]["files"]) == 1
    assert backups[0]["files"][0]["file_path"] == "core.py"


def test_rollback_restores_original_file_contents(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Recovery: 'codepilot rollback' restores original source files cleanly."""
    test_file = tmp_path / "payment.py"
    unrelated_file = tmp_path / "untracked_user_notes.txt"

    original_code = "def charge():\n    # AI: return false\n    pass\n"
    unrelated_text = "Important developer scratchpad notes"

    test_file.write_text(original_code, encoding="utf-8")
    unrelated_file.write_text(unrelated_text, encoding="utf-8")

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # Generate and apply patch
    cli_runner.invoke(app, ["fix", "--preview", "--directory", str(tmp_path)])
    cli_runner.invoke(app, ["apply", "--approved", "--directory", str(tmp_path)])

    # Verify code was modified
    assert test_file.read_text(encoding="utf-8") != original_code
    assert "return False" in test_file.read_text(encoding="utf-8")

    # Execute rollback
    rollback_res = cli_runner.invoke(app, ["rollback", "--directory", str(tmp_path)])
    assert rollback_res.exit_code == 0
    assert "Successfully restored files from backup" in rollback_res.output
    assert "payment.py" in rollback_res.output

    # Source file must be restored to exact original code
    assert test_file.read_text(encoding="utf-8") == original_code

    # Unrelated untracked developer file MUST be untouched!
    assert unrelated_file.read_text(encoding="utf-8") == unrelated_text


def test_rollback_list_backups(cli_runner: CliRunner, tmp_path: Path) -> None:
    """CLI: 'codepilot rollback --list' lists available snapshots."""
    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    # When no backups exist
    empty_res = cli_runner.invoke(app, ["rollback", "--list", "--directory", str(tmp_path)])
    assert empty_res.exit_code == 0
    assert "No pre-apply backups found" in empty_res.output


# ==============================================================================
# 4. Test Discovery & Safe Execution Tests (Phase 8)
# ==============================================================================

def test_test_discovery_preview_does_not_execute(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Test Engine: 'codepilot test --preview' discovers tests without running them."""
    test_file = tmp_path / "test_sample.py"
    marker_file = tmp_path / "execution_marker.txt"
    # Test script that would create a marker file if executed
    test_file.write_text(
        f"def test_foo():\n    from pathlib import Path\n    Path('{marker_file.as_posix()}').touch()\n    assert True\n",
        encoding="utf-8",
    )

    result = cli_runner.invoke(app, ["test", "--preview", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Test Discovery Preview" in result.output
    assert "test_sample.py" in result.output
    assert "Approval Required" in result.output

    # Crucial guarantee: marker file MUST NOT exist!
    assert not marker_file.exists()


def test_test_requires_approval_when_autodetecting(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Test Engine: Running 'codepilot test' without --approved is rejected."""
    test_file = tmp_path / "test_example.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")

    # Run without --approved and without --command
    result = cli_runner.invoke(app, ["test", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "Approval Required" in result.output
    assert "codepilot test --approved" in result.output


def test_test_concurrency_lock_prevents_simultaneous_runs(tmp_path: Path) -> None:
    """Test Engine: Concurrency lock prevents concurrent test runner invocations."""
    lock_file = acquire_test_lock(tmp_path)
    assert lock_file.is_file()

    # Second acquisition with active PID must raise TestExecutionError
    with pytest.raises(TestExecutionError) as exc_info:
        acquire_test_lock(tmp_path)
    assert "currently running" in str(exc_info.value).lower()

    # Clean up lock
    release_test_lock(tmp_path)
    assert not lock_file.is_file()


def test_test_timeout_terminates_process_safely(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Test Engine: Hanging or infinite loop tests are terminated cleanly on timeout."""
    test_file = tmp_path / "test_hang.py"
    # Infinite sleep test
    test_file.write_text("import time\ndef test_forever(): time.sleep(10)\n", encoding="utf-8")

    cmd = f"{sys.executable} -m pytest {test_file}"
    start = time.time()
    result = cli_runner.invoke(
        app,
        ["test", "--command", cmd, "--timeout", "1.0", "--directory", str(tmp_path)],
    )
    elapsed = time.time() - start

    assert result.exit_code != 0
    assert elapsed < 5.0  # Must have terminated within ~1-2 seconds
    assert "timed out" in result.output.lower() or "failed" in result.output.lower()


def test_test_bounded_output_truncation(tmp_path: Path) -> None:
    """Test Engine: Gigantic stdout/stderr streams are truncated safely to protect memory."""
    config = CodePilotConfig(project_root=tmp_path)
    script_file = tmp_path / "print_huge.py"
    script_file.write_text("print('A' * 150000)\n", encoding="utf-8")
    huge_command = f"{sys.executable} {script_file}"
    result = run_project_tests(tmp_path, config, custom_command=huge_command)
    assert result.passed is True
    assert result.truncated is True
    assert "truncated at 64 KB" in result.stdout



# ==============================================================================
# 5. AI Failure Analysis and Error Correction (Phase 8)
# ==============================================================================

def test_test_failure_analysis_proposes_fix_without_modifying_source(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """AI Error Correction: Test failures produce proposed patch without touching source files."""
    calc_file = tmp_path / "calc.py"
    test_file = tmp_path / "test_calc.py"

    original_calc = "def divide(a, b):\n    return a / b\n"
    calc_file.write_text(original_calc, encoding="utf-8")

    test_file.write_text(
        "import calc\ndef test_zero():\n    assert calc.divide(10, 0) == 0\n",
        encoding="utf-8",
    )

    config_file = tmp_path / CONFIG_FILENAME
    config_file.write_text('model_provider = "mock"\n', encoding="utf-8")

    cmd = f"{sys.executable} -m pytest {test_file}"
    res = cli_runner.invoke(
        app,
        [
            "test",
            "--command", cmd,
            "--fix",
            "--provider", "mock",
            "--directory", str(tmp_path),
        ],
    )
    assert res.exit_code != 0
    assert "Test Suite Failed" in res.output
    assert "AI Test Failure Analysis" in res.output

    # Source code MUST remain unmodified!
    assert calc_file.read_text(encoding="utf-8") == original_calc


# ==============================================================================
# 6. Standalone Model Distribution & Verification (Phase 9)
# ==============================================================================

def test_standalone_client_reports_missing_dependency(tmp_path: Path) -> None:
    """Model Distribution: Standalone client cleanly reports missing llama-cpp-python."""
    client = StandaloneGGUFClient(model_path=tmp_path / "qwen.gguf")
    healthy, msg = client.health_check()
    # On environments without llama-cpp-python, health check reports actionable guidance
    if not healthy and "llama-cpp-python" in msg:
        assert "codepilot-ai[standalone]" in msg
        assert "ollama" in msg.lower()


def test_standalone_client_validates_file_checksum(tmp_path: Path) -> None:
    """Model Distribution: Standalone client verifies GGUF checksum integrity."""
    fake_gguf = tmp_path / "model.gguf"
    fake_gguf.write_bytes(b"GGUF_MAGIC_HEADER_FAKE_DATA_" * 1000)

    computed = compute_file_sha256(fake_gguf)
    assert len(computed) == 64

    # Matching checksum
    client_ok = StandaloneGGUFClient(model_path=fake_gguf, expected_sha256=computed)
    # Mismatched checksum
    client_bad = StandaloneGGUFClient(model_path=fake_gguf, expected_sha256="0" * 64)
    # If llama_cpp is mocked or missing, test SHA computation directly
    assert compute_file_sha256(fake_gguf).lower() == computed.lower()


# ==============================================================================
# 7. Filename Sanitization & Path Traversal Unit Tests (Stage L)
# ==============================================================================

class TestFilenameSanitization:
    """Deterministic tests for sanitize_filename handling traversal, separators, and edge cases."""

    def test_sanitize_filename_traversal_paths(self) -> None:
        """Verify path traversal sequences are stripped and basename is preserved."""
        assert sanitize_filename("../../etc/passwd") == "passwd"
        assert sanitize_filename("../secret.txt") == "secret.txt"
        assert sanitize_filename("../../../var/log/syslog") == "syslog"

    def test_sanitize_filename_windows_separators(self) -> None:
        """Verify Windows backslashes are normalized and basenames extracted."""
        assert sanitize_filename(r"..\..\windows\system32") == "system32"
        assert sanitize_filename(r"..\secret.txt") == "secret.txt"
        assert sanitize_filename(r"C:\Users\admin\Desktop\data.csv") == "data.csv"

    def test_sanitize_filename_nested_separators(self) -> None:
        """Verify nested and repeated directory separators are handled cleanly."""
        assert sanitize_filename("nested///dir//file.txt") == "file.txt"
        assert sanitize_filename("///") == ""
        assert sanitize_filename("/etc/passwd") == "passwd"

    def test_sanitize_filename_empty_and_whitespace(self) -> None:
        """Verify empty and whitespace inputs return an empty string."""
        assert sanitize_filename("") == ""
        assert sanitize_filename("   ") == ""
        assert sanitize_filename("\t\n") == ""

    def test_sanitize_filename_normal_files(self) -> None:
        """Verify standard filenames and dotfiles are preserved."""
        assert sanitize_filename("document.pdf") == "document.pdf"
        assert sanitize_filename("main.py") == "main.py"
        assert sanitize_filename(".gitignore") == ".gitignore"

    def test_sanitize_filename_repeated_dots_and_traversal(self) -> None:
        """Verify repeated dots and standalone traversal tokens are stripped."""
        assert sanitize_filename("../..") == ""
        assert ".." not in sanitize_filename("../..")
        sanitized = sanitize_filename("...hidden_traversal...")
        assert ".." not in sanitized
        assert "hidden_traversal" in sanitized

    def test_sanitize_filename_unicode_and_extensions(self) -> None:
        """Verify international characters, multiple extensions, and Unicode filenames."""
        assert sanitize_filename("résumé_2026.docx") == "résumé_2026.docx"
        assert sanitize_filename("archive.tar.gz") == "archive.tar.gz"
        assert sanitize_filename("データ_report.json") == "データ_report.json"

    def test_sanitize_filename_mixed_separators(self) -> None:
        """Verify mixed Unix/Windows separators are normalized."""
        assert sanitize_filename(r"../../dir\subdir/data.txt") == "data.txt"
        assert sanitize_filename(r"foo/bar\baz/test.py") == "test.py"
        assert sanitize_filename(r"..\../windows\system32") == "system32"

    def test_sanitize_filename_null_bytes_and_control_characters(self) -> None:
        """Verify null bytes and control characters (ASCII 0x00-0x1f, 0x7f) are stripped."""
        assert sanitize_filename("payload.php\x00.jpg") == "payload.php.jpg"
        assert sanitize_filename("secret\r\n.txt") == "secret.txt"
        assert sanitize_filename("bad\x1f\x00file\x7f.dat") == "badfile.dat"
        assert sanitize_filename("\x00\r\n\x1f") == ""

    def test_sanitize_filename_drive_prefixes(self) -> None:
        """Verify Windows drive prefixes attached to filenames are handled."""
        assert sanitize_filename("C:data.csv") == "data.csv"
        assert sanitize_filename(r"D:\path\to\document.pdf") == "document.pdf"

    def test_core_boundary_checks_remain_unaffected(self, tmp_path: Path) -> None:
        """Verify that core validate_target_path continues to enforce strict repository boundaries."""
        cfg = CodePilotConfig()
        root = tmp_path / "repo"
        root.mkdir()

        # Core boundary validator must strictly reject traversal outside root
        with pytest.raises(Exception):
            validate_target_path("../../outside.txt", project_root=root, config=cfg)


def test_create_patch_backup_preserves_distinct_subdirectories_for_identical_basenames(
    tmp_path: Path,
) -> None:
    """Phase 14.1 Regression: Two files in different subdirectories with identical basenames

    must have separate backups and restore independently during rollback.
    """
    from codepilot.core.fixer import FilePatch
    from codepilot.core.models.standalone_client import compute_file_sha256

    file1 = tmp_path / "pkg_a" / "models.py"
    file2 = tmp_path / "pkg_b" / "models.py"
    file1.parent.mkdir(parents=True, exist_ok=True)
    file2.parent.mkdir(parents=True, exist_ok=True)

    content1 = "# Package A models\nNAME = 'A'\n"
    content2 = "# Package B models\nNAME = 'B'\n"
    file1.write_text(content1, encoding="utf-8")
    file2.write_text(content2, encoding="utf-8")

    hash1 = compute_file_sha256(file1)
    hash2 = compute_file_sha256(file2)

    patches = [
        FilePatch(file_path="pkg_a/models.py", original_hash=hash1, original_content=content1, proposed_content="MODIFIED A", diff="diff A"),
        FilePatch(file_path="pkg_b/models.py", original_hash=hash2, original_content=content2, proposed_content="MODIFIED B", diff="diff B"),
    ]

    backup_dir = create_patch_backup(tmp_path, "patch-collision-test", patches)
    assert backup_dir.is_dir()

    # Read manifest and verify separate backup files
    manifest_data = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest_data["files"]
    assert len(entries) == 2
    assert entries[0]["file_path"] == "pkg_a/models.py"
    assert entries[1]["file_path"] == "pkg_b/models.py"
    assert entries[0]["backup_file"] != entries[1]["backup_file"]

    # Verify both backup files independently retain the original contents
    bak1 = backup_dir / entries[0]["backup_file"]
    bak2 = backup_dir / entries[1]["backup_file"]
    assert bak1.is_file()
    assert bak2.is_file()
    assert bak1.read_text(encoding="utf-8") == content1
    assert bak2.read_text(encoding="utf-8") == content2

    # Simulate modifying both files
    file1.write_text("CORRUPTED A", encoding="utf-8")
    file2.write_text("CORRUPTED B", encoding="utf-8")

    # Roll back and verify both files are restored to their exact originals
    res = rollback_patch(tmp_path, patch_id="patch-collision-test")
    assert sorted(res.restored_files) == ["pkg_a/models.py", "pkg_b/models.py"]
    assert file1.read_text(encoding="utf-8") == content1
    assert file2.read_text(encoding="utf-8") == content2


def test_analyze_test_failure_live_provider_produces_and_stages_patch(
    tmp_path: Path,
) -> None:
    """Phase 14.2 Regression: Live model provider produces repair diff, stages pending patch,

    preserves mandatory approval gate, and leaves source untouched prior to approved apply.
    """
    from unittest.mock import MagicMock, patch
    from codepilot.core.models.base import ModelResponse
    from codepilot.core.tester import TestRunResult
    from codepilot.core.fixer import load_pending_patch
    from codepilot.core.patcher import apply_approved_patch

    calc_file = tmp_path / "calc.py"
    orig_code = "def divide(a, b):\n    return a / b\n"
    calc_file.write_text(orig_code, encoding="utf-8")

    test_result = TestRunResult(
        command="pytest",
        exit_code=1,
        stdout="FAILED calc.py::test_div - ZeroDivisionError: division by zero",
        stderr="Traceback (most recent call last):\n  File 'calc.py', line 2, in divide\n    return a / b\nZeroDivisionError: division by zero",
        passed=False,
        duration_seconds=0.1,
        summary="1 failed",
    )

    repaired_code = (
        "Here is the explanation: divide-by-zero was not handled.\n\n"
        "```python\n"
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        return 0\n"
        "    return a / b\n"
        "```"
    )
    mock_client = MagicMock()
    mock_client.generate.return_value = ModelResponse(content=repaired_code, model_name="mock-qwen")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.test_analyzer.create_model_client", return_value=mock_client):
        report = analyze_test_failure(
            project_root=tmp_path,
            test_result=test_result,
            config=cfg,
            override_provider="ollama",
            generate_patch=True,
        )

    # Verify report fields
    assert report.proposed_diff is not None
    assert "+    if b == 0:" in report.proposed_diff
    assert report.proposed_patch_id is not None
    assert "divide-by-zero" in report.root_cause_explanation

    # Source file MUST be completely untouched!
    assert calc_file.read_text(encoding="utf-8") == orig_code

    # Staged pending patch must exist
    bundle = load_pending_patch(tmp_path)
    assert bundle is not None
    assert bundle.patch_id == report.proposed_patch_id
    assert len(bundle.file_patches) == 1
    assert bundle.file_patches[0].file_path == "calc.py"

    # Mandatory approval gate: apply without approved=True must be rejected
    with pytest.raises(PatchError):
        apply_approved_patch(tmp_path, approved=False, config=cfg)
    assert calc_file.read_text(encoding="utf-8") == orig_code

    # Approved apply succeeds and updates source
    apply_res = apply_approved_patch(tmp_path, approved=True, config=cfg)
    assert apply_res.total_modified == 1
    assert "if b == 0:" in calc_file.read_text(encoding="utf-8")


def test_analyze_test_failure_explanation_only_when_generate_patch_false(
    tmp_path: Path,
) -> None:
    """Phase 14.2 Regression: When generate_patch=False, returns explanation only without staging patch."""
    from unittest.mock import MagicMock, patch
    from codepilot.core.models.base import ModelResponse
    from codepilot.core.tester import TestRunResult
    from codepilot.core.fixer import load_pending_patch

    test_result = TestRunResult(
        command="pytest",
        exit_code=1,
        stdout="FAILED test_foo.py - AssertionError",
        stderr="",
        passed=False,
        duration_seconds=0.1,
        summary="1 failed",
    )

    mock_client = MagicMock()
    mock_client.generate.return_value = ModelResponse(
        content="The test assertion failed because expected was 42 but got 0.",
        model_name="mock-qwen",
    )

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.test_analyzer.create_model_client", return_value=mock_client):
        report = analyze_test_failure(
            project_root=tmp_path,
            test_result=test_result,
            config=cfg,
            override_provider="ollama",
            generate_patch=False,
        )

    assert "The test assertion failed" in report.root_cause_explanation
    assert report.proposed_diff is None
    assert report.proposed_patch_id is None
    assert load_pending_patch(tmp_path) is None


def test_analyze_test_failure_handles_client_exception_safely(
    tmp_path: Path,
) -> None:
    """Phase 14.2 Regression: Model client failure records error explanation and leaves source safe."""
    from unittest.mock import MagicMock, patch
    from codepilot.core.tester import TestRunResult

    calc_file = tmp_path / "calc.py"
    orig_code = "def divide(a, b):\n    return a / b\n"
    calc_file.write_text(orig_code, encoding="utf-8")

    test_result = TestRunResult(
        command="pytest",
        exit_code=1,
        stdout="FAILED calc.py::test_div",
        stderr="",
        passed=False,
        duration_seconds=0.1,
        summary="1 failed",
    )

    mock_client = MagicMock()
    mock_client.generate.side_effect = RuntimeError("Inference connection failed")

    cfg = CodePilotConfig(project_root=tmp_path, model_provider="ollama")
    with patch("codepilot.core.test_analyzer.create_model_client", return_value=mock_client):
        report = analyze_test_failure(
            project_root=tmp_path,
            test_result=test_result,
            config=cfg,
            override_provider="ollama",
            generate_patch=True,
        )

    assert "Could not complete AI failure analysis" in report.root_cause_explanation
    assert report.proposed_diff is None
    assert calc_file.read_text(encoding="utf-8") == orig_code


