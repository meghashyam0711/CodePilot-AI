"""Regression tests for defects found during the full project audit.

Each test pins a specific bug that was reproduced against the shipped code, so that the
behaviour cannot silently regress. Test names reference the audit finding they cover.
"""

import json
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codepilot.cli import app, _looks_like_file_path
from codepilot.config import CodePilotConfig, load_config
from codepilot.core.fixer import (
    FilePatch,
    PatchBundle,
    compute_sha256,
    generate_preview_patch,
    load_pending_patch,
)
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.recovery import (
    create_patch_backup,
    detect_post_apply_divergence,
    resolve_backup_manifest,
    rollback_patch,
)
from codepilot.core.scanner import scan_repository
from codepilot.core.tester import acquire_test_lock, release_test_lock, validate_test_command
from codepilot.exceptions import FixError, PatchError, TestExecutionError


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# AUDIT-01: AI-DOCS invariance was applied to whole files, blocking mixed tasks
# ---------------------------------------------------------------------------

MIXED_SOURCE = (
    "def divide(a, b):\n"
    "    # AI-FIX: handle division by zero\n"
    "    return a / b\n"
    "\n"
    "\n"
    "def add(a, b):\n"
    "    # AI-DOCS: document the adder\n"
    "    return a + b\n"
)


def test_audit01_mixed_docs_and_fix_tasks_in_one_file_still_produce_a_patch(tmp_path: Path) -> None:
    """A file holding both an AI-DOCS and an AI-FIX task must still yield a patch.

    Previously the executable-AST-invariance rule was enforced whenever *any* task in the
    file was AI-DOCS, so the AI-FIX code change tripped it and the entire patch was
    rejected with 'violated executable AST invariance'.
    """
    _write(tmp_path / "calc.py", MIXED_SOURCE)
    config = load_config(tmp_path, model_provider="mock")

    bundle = generate_preview_patch(tmp_path, config)

    assert bundle.status == "success", f"expected a patch, got status={bundle.status}"
    assert [fp.file_path for fp in bundle.file_patches] == ["calc.py"]


def test_audit01_mixed_file_patch_records_non_docs_instruction_type(tmp_path: Path) -> None:
    """A mixed-task file must not be labelled AI-DOCS, or apply-time would reject it."""
    _write(tmp_path / "calc.py", MIXED_SOURCE)
    config = load_config(tmp_path, model_provider="mock")

    bundle = generate_preview_patch(tmp_path, config)

    assert bundle.file_patches[0].instruction_type != "AI-DOCS"


def test_audit01_mixed_file_patch_applies_successfully(tmp_path: Path) -> None:
    """The full preview -> apply cycle must complete for a mixed-task file."""
    target = tmp_path / "calc.py"
    _write(target, MIXED_SOURCE)
    config = load_config(tmp_path, model_provider="mock")

    generate_preview_patch(tmp_path, config)
    result = apply_approved_patch(tmp_path, approved=True, config=config)

    assert result.applied_files == ["calc.py"]
    assert target.read_text(encoding="utf-8") != MIXED_SOURCE


def test_audit01_docs_only_file_still_enforces_ast_invariance(tmp_path: Path) -> None:
    """The documentation-only guarantee must remain intact for pure AI-DOCS patches."""
    source = 'def compute(x):\n    # AI-DOCS: describe this\n    return x ** 2\n'
    target = tmp_path / "m.py"
    _write(target, source)

    unsafe = 'def compute(x):\n    """Describe this."""\n    return x ** 3\n'
    bundle = PatchBundle(
        patch_id="patch-unsafe-docs",
        created_at="2026-01-01T00:00:00Z",
        project_root=str(tmp_path),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path="m.py",
                original_hash=compute_sha256(source),
                original_content=source,
                proposed_content=unsafe,
                diff="",
                instruction_type="AI-DOCS",
            )
        ],
        full_diff="",
    )
    patches_dir = tmp_path / ".codepilot" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    (patches_dir / "pending_patch.json").write_text(json.dumps(bundle.to_dict()), encoding="utf-8")

    with pytest.raises(PatchError, match="executable AST invariance"):
        apply_approved_patch(tmp_path, approved=True, config=CodePilotConfig(project_root=tmp_path))

    assert target.read_text(encoding="utf-8") == source


# ---------------------------------------------------------------------------
# AUDIT-02: rollback silently discarded work done after a patch was applied
# ---------------------------------------------------------------------------


def _staged_and_applied(tmp_path: Path) -> CodePilotConfig:
    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: handle division by zero\n    return a / b\n")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)
    apply_approved_patch(tmp_path, approved=True, config=config)
    return config


def test_audit02_backup_manifest_records_the_applied_content_hash(tmp_path: Path) -> None:
    """Without an applied-state hash, rollback cannot tell later edits from patch output."""
    _staged_and_applied(tmp_path)

    manifest = resolve_backup_manifest(tmp_path)

    assert manifest.files
    assert all(entry.applied_hash for entry in manifest.files)


def test_audit02_divergence_is_detected_after_a_later_hand_edit(tmp_path: Path) -> None:
    """Editing a patched file must be reported as divergence."""
    _staged_and_applied(tmp_path)
    target = tmp_path / "calc.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# later hand-written work\n", encoding="utf-8"
    )

    diverged = detect_post_apply_divergence(tmp_path, resolve_backup_manifest(tmp_path))

    assert diverged == ["calc.py"]


def test_audit02_no_divergence_when_the_patch_output_is_untouched(tmp_path: Path) -> None:
    """An unmodified patched file must not be flagged, so rollback stays frictionless."""
    _staged_and_applied(tmp_path)

    diverged = detect_post_apply_divergence(tmp_path, resolve_backup_manifest(tmp_path))

    assert diverged == []


def test_audit02_no_divergence_when_file_is_already_back_to_pre_patch_state(tmp_path: Path) -> None:
    """Restoring over a file already in its original state is a safe no-op."""
    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: handle division by zero\n    return a / b\n")
    original = (tmp_path / "calc.py").read_text(encoding="utf-8")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)
    apply_approved_patch(tmp_path, approved=True, config=config)
    (tmp_path / "calc.py").write_text(original, encoding="utf-8")

    diverged = detect_post_apply_divergence(tmp_path, resolve_backup_manifest(tmp_path))

    assert diverged == []


def test_audit02_legacy_snapshot_without_applied_hash_is_not_flagged(tmp_path: Path) -> None:
    """Snapshots written by older versions must keep working."""
    source = "VALUE = 1\n"
    _write(tmp_path / "m.py", source)
    patches = [
        FilePatch(
            file_path="m.py",
            original_hash=compute_sha256(source),
            original_content=source,
            proposed_content="VALUE = 2\n",
            diff="",
        )
    ]
    backup_dir = create_patch_backup(tmp_path, "patch-legacy", patches)
    manifest_file = backup_dir / "manifest.json"
    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    for entry in data["files"]:
        entry.pop("applied_hash", None)
    manifest_file.write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "m.py").write_text("VALUE = 999\n", encoding="utf-8")

    manifest = resolve_backup_manifest(tmp_path, patch_id="patch-legacy")

    assert detect_post_apply_divergence(tmp_path, manifest) == []


def test_audit02_cli_rollback_refuses_to_discard_later_edits(cli_runner: CliRunner, tmp_path: Path) -> None:
    """An approved rollback must still stop rather than overwrite unsaved work.

    '--approved' is passed deliberately: without it the approval gate (R-5) would reject
    the command first and this test would pass for the wrong reason, proving nothing
    about divergence detection.
    """
    _staged_and_applied(tmp_path)
    target = tmp_path / "calc.py"
    kept = target.read_text(encoding="utf-8") + "\n# IMPORTANT later work\n"
    target.write_text(kept, encoding="utf-8")

    result = cli_runner.invoke(app, ["rollback", "--approved", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "Unsaved Changes Detected" in result.output
    assert "IMPORTANT later work" in target.read_text(encoding="utf-8")


def test_audit02_cli_rollback_force_overwrites_deliberately(cli_runner: CliRunner, tmp_path: Path) -> None:
    """--force remains available for an intentional overwrite."""
    _staged_and_applied(tmp_path)
    target = tmp_path / "calc.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# later work\n", encoding="utf-8")

    result = cli_runner.invoke(
        app, ["rollback", "--approved", "--force", "--directory", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "# later work" not in target.read_text(encoding="utf-8")


def test_audit02_rollback_primitive_still_recovers_corrupted_files(tmp_path: Path) -> None:
    """The recovery primitive must keep restoring damaged files unconditionally."""
    source = "NAME = 'original'\n"
    _write(tmp_path / "m.py", source)
    patches = [
        FilePatch(
            file_path="m.py",
            original_hash=compute_sha256(source),
            original_content=source,
            proposed_content="NAME = 'patched'\n",
            diff="",
        )
    ]
    create_patch_backup(tmp_path, "patch-recover", patches)
    (tmp_path / "m.py").write_text("CORRUPTED", encoding="utf-8")

    rollback_patch(tmp_path, patch_id="patch-recover")

    assert (tmp_path / "m.py").read_text(encoding="utf-8") == source


# ---------------------------------------------------------------------------
# AUDIT-03: 'doctor' reported a healthy runtime without probing it
# ---------------------------------------------------------------------------


def test_audit03_doctor_fails_the_runtime_check_when_ollama_is_unreachable(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A provider name alone must not count as a working runtime."""
    _write(
        tmp_path / ".codepilot.toml",
        'model_provider = "ollama"\nollama_base_url = "http://127.0.0.1:1"\nrequest_timeout = 5.0\n',
    )

    result = cli_runner.invoke(app, ["doctor", "--json", "--directory", str(tmp_path)])

    payload = json.loads(result.output[result.output.index("{"):])
    runtime = next(c for c in payload["checks"] if c["name"] == "Model Runtime Interface")
    assert runtime["passed"] is False
    assert "unreachable" in runtime["details"].lower() or "connect" in runtime["details"].lower()


def test_audit03_doctor_summary_reports_warnings_instead_of_claiming_full_health(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The summary must not claim all requirements are met while a warning is shown."""
    _write(
        tmp_path / ".codepilot.toml",
        'model_provider = "ollama"\nollama_base_url = "http://127.0.0.1:1"\nrequest_timeout = 5.0\n',
    )

    result = cli_runner.invoke(app, ["doctor", "--directory", str(tmp_path)])

    assert "with warnings" in result.output
    assert "meets all system requirements" not in result.output


def test_audit03_doctor_passes_the_runtime_check_for_mock_provider(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """The offline mock provider must not be probed over the network."""
    _write(tmp_path / ".codepilot.toml", 'model_provider = "mock"\n')

    result = cli_runner.invoke(app, ["doctor", "--json", "--directory", str(tmp_path)])

    payload = json.loads(result.output[result.output.index("{"):])
    runtime = next(c for c in payload["checks"] if c["name"] == "Model Runtime Interface")
    assert runtime["passed"] is True


# ---------------------------------------------------------------------------
# AUDIT-04: a stale test lock blocked 'codepilot test' permanently
# ---------------------------------------------------------------------------


def test_audit04_stale_lock_from_a_dead_process_is_reclaimed(tmp_path: Path) -> None:
    """A lock left behind by a crashed run must not block the repository forever."""
    lock_file = tmp_path / ".codepilot" / "test.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        json.dumps({"pid": 0x7FFFFFFE, "started_at": "2020-01-01T00:00:00+00:00", "cwd": str(tmp_path)}),
        encoding="utf-8",
    )

    acquire_test_lock(tmp_path)

    assert json.loads(lock_file.read_text(encoding="utf-8"))["pid"] == os.getpid()
    release_test_lock(tmp_path)


def test_audit04_live_lock_is_still_respected(tmp_path: Path) -> None:
    """Genuine concurrent runs must still be rejected."""
    acquire_test_lock(tmp_path)
    try:
        with pytest.raises(TestExecutionError, match="currently running"):
            acquire_test_lock(tmp_path)
    finally:
        release_test_lock(tmp_path)


def test_audit04_unparsable_lock_is_still_treated_as_held(tmp_path: Path) -> None:
    """A corrupt lock file must fail closed rather than be silently reclaimed."""
    lock_file = tmp_path / ".codepilot" / "test.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("{ not json", encoding="utf-8")

    with pytest.raises(TestExecutionError):
        acquire_test_lock(tmp_path)


# ---------------------------------------------------------------------------
# AUDIT-05: approved-runner matching had no token boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["pytest_evil_binary", "pytestmalware", "go testsomethingelse", "npm testrunner-evil"],
)
def test_audit05_runner_names_must_match_at_a_token_boundary(command: str) -> None:
    """'pytest...' must not whitelist an arbitrary executable that merely starts with it."""
    with pytest.raises(TestExecutionError, match="approved test runner"):
        validate_test_command(command)


@pytest.mark.parametrize("command", ["pytest", "pytest -v tests", "go test ./...", "npm test"])
def test_audit05_legitimate_runner_commands_remain_accepted(command: str) -> None:
    validate_test_command(command)


# ---------------------------------------------------------------------------
# AUDIT-06: a mistyped target path silently patched a different file
# ---------------------------------------------------------------------------


def test_audit06_missing_target_file_is_reported_not_retargeted(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """A nonexistent file target must raise, not fall back to an unrelated file."""
    _write(tmp_path / "calc.py", "def divide(a, b):\n    return a / b\n")
    _write(tmp_path / ".codepilot.toml", 'model_provider = "mock"\n')

    result = cli_runner.invoke(
        app,
        ["fix", "does_not_exist.py", "--issue", "x", "--directory", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert not (tmp_path / ".codepilot" / "patches" / "pending_patch.json").exists()


def test_audit06_issue_prose_is_still_accepted_as_a_target(
    cli_runner: CliRunner, tmp_path: Path
) -> None:
    """Free-form descriptions must keep working as positional targets."""
    _write(tmp_path / "calc.py", "def divide(a, b):\n    return a / b\n")
    _write(tmp_path / ".codepilot.toml", 'model_provider = "mock"\n')

    result = cli_runner.invoke(
        app, ["fix", "handle division by zero", "--directory", str(tmp_path)]
    )

    assert result.exit_code == 0


@pytest.mark.parametrize("value", ["utils.py", "src/utils.py", "src\\utils.py", "a/b/c.ts"])
def test_audit06_path_like_targets_are_recognised(value: str) -> None:
    assert _looks_like_file_path(value) is True


@pytest.mark.parametrize(
    "value", ["handle division by zero", "fix the bug in utils.py", "TASK-001", ""]
)
def test_audit06_prose_targets_are_not_mistaken_for_paths(value: str) -> None:
    assert _looks_like_file_path(value) is False


# ---------------------------------------------------------------------------
# AUDIT-07: UTF-8 BOM files were reported as syntax errors
# ---------------------------------------------------------------------------


def test_audit07_bom_encoded_python_file_is_not_a_syntax_error(tmp_path: Path) -> None:
    """Python accepts a BOM; the scanner must not report one as a defect."""
    (tmp_path / "bom.py").write_text("def ok():\n    return 1\n", encoding="utf-8-sig")
    config = CodePilotConfig(project_root=tmp_path)

    result = scan_repository(tmp_path, config)

    syntax_errors = [f for f in result.static_findings if f["type"] == "syntax_error"]
    assert syntax_errors == []


def test_audit07_genuine_syntax_errors_are_still_reported(tmp_path: Path) -> None:
    _write(tmp_path / "broken.py", "print('unclosed\n")
    config = CodePilotConfig(project_root=tmp_path)

    result = scan_repository(tmp_path, config)

    assert any(f["type"] == "syntax_error" for f in result.static_findings)


# ---------------------------------------------------------------------------
# AUDIT-08: damaged patch files were reported as "no pending patch"
# ---------------------------------------------------------------------------


def test_audit08_unknown_fields_in_a_patch_bundle_no_longer_break_loading(tmp_path: Path) -> None:
    """Forward compatibility: an extra key must not discard the whole patch."""
    _write(tmp_path / "calc.py", "def divide(a, b):\n    # AI-FIX: handle division by zero\n    return a / b\n")
    config = load_config(tmp_path, model_provider="mock")
    generate_preview_patch(tmp_path, config)

    pending = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    data = json.loads(pending.read_text(encoding="utf-8"))
    data["file_patches"][0]["field_from_a_future_version"] = "ignored"
    pending.write_text(json.dumps(data), encoding="utf-8")

    bundle = load_pending_patch(tmp_path)

    assert bundle is not None
    assert bundle.file_patches[0].file_path == "calc.py"


def test_audit08_corrupt_patch_file_raises_instead_of_claiming_none_exists(tmp_path: Path) -> None:
    """A damaged patch must be named as damaged, not silently hidden."""
    pending = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("{ this is not json", encoding="utf-8")

    with pytest.raises(FixError, match="damaged"):
        load_pending_patch(tmp_path)


def test_audit08_apply_reports_a_damaged_patch_as_a_patch_error(tmp_path: Path) -> None:
    pending = tmp_path / ".codepilot" / "patches" / "pending_patch.json"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("{ broken", encoding="utf-8")

    with pytest.raises(PatchError, match="damaged"):
        apply_approved_patch(tmp_path, approved=True, config=CodePilotConfig(project_root=tmp_path))


def test_audit08_absent_patch_file_still_returns_none(tmp_path: Path) -> None:
    assert load_pending_patch(tmp_path) is None


# ---------------------------------------------------------------------------
# AUDIT-09: a non-local model endpoint silently defeats the privacy guarantee
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://localhost:11434", "http://127.0.0.1:11434", "http://127.1.2.3:8080", "http://[::1]:11434"],
)
def test_audit09_loopback_endpoints_are_recognised_as_local(url: str) -> None:
    from codepilot.core.models.ollama_client import is_local_endpoint

    assert is_local_endpoint(url) is True


@pytest.mark.parametrize(
    "url", ["http://attacker.example.com:11434", "http://198.51.100.7:11434", "https://api.example.org"]
)
def test_audit09_remote_endpoints_are_recognised_as_non_local(url: str) -> None:
    from codepilot.core.models.ollama_client import is_local_endpoint

    assert is_local_endpoint(url) is False


def test_audit09_client_flags_a_remote_endpoint(tmp_path: Path) -> None:
    """The client must know it is remote so callers can warn the developer."""
    from codepilot.core.models.ollama_client import OllamaClient

    assert OllamaClient(base_url="http://attacker.example.com:11434").is_local is False
    assert OllamaClient(base_url="http://localhost:11434").is_local is True


# ---------------------------------------------------------------------------
# R-7: test execution was bounded by the model-inference timeout
# ---------------------------------------------------------------------------


def test_r7_test_execution_does_not_use_the_inference_timeout(tmp_path: Path) -> None:
    """'request_timeout' bounds a model call; it must not bound a test suite.

    With the old coupling, a 30s LLM default killed any longer-running real test suite.
    """
    import codepilot.core.tester as tester_module
    from codepilot.config import DEFAULT_TEST_TIMEOUT_SECONDS

    _write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\nversion = '1'\n")
    config = load_config(tmp_path, request_timeout=5.0)
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = os.getpid()
        stdout = None
        stderr = None

        def wait(self, timeout=None):
            captured["timeout"] = timeout
            return 0

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tester_module.subprocess, "Popen", lambda *a, **kw: _FakeProc())
    try:
        tester_module.run_project_tests(tmp_path, config, custom_command="pytest")
    finally:
        monkey.undo()

    assert captured["timeout"] != 5.0, "test run must not inherit request_timeout"
    assert captured["timeout"] == DEFAULT_TEST_TIMEOUT_SECONDS


def test_r7_configured_test_timeout_is_used(tmp_path: Path) -> None:
    import codepilot.core.tester as tester_module

    _write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\nversion = '1'\n")
    config = load_config(tmp_path, request_timeout=5.0, test_timeout=123.0)
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = os.getpid()
        stdout = None
        stderr = None

        def wait(self, timeout=None):
            captured["timeout"] = timeout
            return 0

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tester_module.subprocess, "Popen", lambda *a, **kw: _FakeProc())
    try:
        tester_module.run_project_tests(tmp_path, config, custom_command="pytest")
    finally:
        monkey.undo()

    assert captured["timeout"] == 123.0


def test_r7_explicit_override_wins_over_configuration(tmp_path: Path) -> None:
    import codepilot.core.tester as tester_module

    _write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\nversion = '1'\n")
    config = load_config(tmp_path, test_timeout=300.0)
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = os.getpid()
        stdout = None
        stderr = None

        def wait(self, timeout=None):
            captured["timeout"] = timeout
            return 0

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tester_module.subprocess, "Popen", lambda *a, **kw: _FakeProc())
    try:
        tester_module.run_project_tests(
            tmp_path, config, custom_command="pytest", timeout_override=7.0
        )
    finally:
        monkey.undo()

    assert captured["timeout"] == 7.0


def test_r7_test_timeout_is_validated(tmp_path: Path) -> None:
    from codepilot.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError):
        load_config(tmp_path, test_timeout=-1.0)


def test_r7_test_timeout_defaults_to_none_and_is_loaded_from_toml(tmp_path: Path) -> None:
    assert load_config(tmp_path).test_timeout is None

    _write(tmp_path / ".codepilot.toml", "test_timeout = 42.0\n")
    assert load_config(tmp_path).test_timeout == 42.0


def test_r7_generated_config_documents_the_separate_test_timeout(tmp_path: Path) -> None:
    """A developer hitting a timeout must be able to find the right knob."""
    from codepilot.config import generate_default_toml_content

    content = generate_default_toml_content(tmp_path)

    assert "test_timeout" in content
    assert "separate from 'request_timeout'" in content


def test_r7_runner_is_started_in_its_own_process_group(tmp_path: Path) -> None:
    """A timeout must be able to terminate spawned test workers, not just the child."""
    import codepilot.core.tester as tester_module

    _write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\nversion = '1'\n")
    config = load_config(tmp_path)
    captured = {}

    class _FakeProc:
        returncode = 0
        pid = os.getpid()
        stdout = None
        stderr = None

        def wait(self, timeout=None):
            return 0

    def _fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(tester_module.subprocess, "Popen", _fake_popen)
    try:
        tester_module.run_project_tests(tmp_path, config, custom_command="pytest")
    finally:
        monkey.undo()

    if sys.platform == "win32":
        assert captured.get("creationflags")
    else:
        assert captured.get("start_new_session") is True


# ---------------------------------------------------------------------------
# AUDIT-12: model output re-emitting the following statement duplicated it
# ---------------------------------------------------------------------------


class _EchoClient:
    """Minimal model client returning a fixed completion, for deterministic assembly tests."""

    def __init__(self, completion: str) -> None:
        self._completion = completion

    def health_check(self):
        return True, "stub runtime ready"

    def generate(self, request, model_name=None):
        from codepilot.core.models.base import ModelResponse

        return ModelResponse(
            content=self._completion,
            model_name="stub",
            latency_seconds=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            finish_reason="stop",
        )


@pytest.fixture()
def patch_model_factory(monkeypatch):
    """Install a stub model client so assembly logic is tested without a live model."""

    def _install(completion: str):
        import codepilot.core.models.factory as factory

        monkeypatch.setattr(
            factory, "create_model_client", lambda **kwargs: _EchoClient(completion)
        )

    return _install


def test_audit12_restated_trailing_statement_is_not_duplicated(
    tmp_path: Path, patch_model_factory
) -> None:
    """A model that re-emits the statement under the comment must not duplicate it.

    Replacing only the comment line left the original statement in place as well, so an
    AI-FIX produced e.g. a duplicated, unreachable 'return a / b' after the new guard.
    """
    source = "def divide(a, b):\n    # AI-FIX: raise ValueError when b is zero\n    return a / b\n"
    _write(tmp_path / "m.py", source)
    patch_model_factory(
        '```python\nif b == 0:\n    raise ValueError("Cannot divide by zero")\nreturn a / b\n```'
    )

    config = load_config(tmp_path, model_provider="ollama")
    bundle = generate_preview_patch(tmp_path, config)

    proposed = bundle.file_patches[0].proposed_content
    assert proposed.count("return a / b") == 1, proposed
    assert 'raise ValueError("Cannot divide by zero")' in proposed


def test_audit12_deduplicated_patch_remains_valid_python(
    tmp_path: Path, patch_model_factory
) -> None:
    import ast as _ast

    source = "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n"
    _write(tmp_path / "m.py", source)
    patch_model_factory("```python\nif b == 0:\n    return 0\nreturn a / b\n```")

    config = load_config(tmp_path, model_provider="ollama")
    bundle = generate_preview_patch(tmp_path, config)

    _ast.parse(bundle.file_patches[0].proposed_content)


def test_audit12_distinct_following_statement_is_preserved(
    tmp_path: Path, patch_model_factory
) -> None:
    """Only an exact duplicate may be removed; unrelated following code must survive."""
    source = (
        "def process(items):\n"
        "    # AI-FIX: skip empty input\n"
        "    total = sum(items)\n"
        "    return total\n"
    )
    _write(tmp_path / "m.py", source)
    patch_model_factory("```python\nif not items:\n    return 0\n```")

    config = load_config(tmp_path, model_provider="ollama")
    bundle = generate_preview_patch(tmp_path, config)

    proposed = bundle.file_patches[0].proposed_content
    assert "total = sum(items)" in proposed
    assert "return total" in proposed


def test_audit12_repeated_statement_at_different_indent_is_preserved(
    tmp_path: Path, patch_model_factory
) -> None:
    """Indentation must match before anything is removed."""
    source = (
        "def pick(flag):\n"
        "    # AI-FIX: handle the flag\n"
        "    if flag:\n"
        "        return 1\n"
        "    return 1\n"
    )
    _write(tmp_path / "m.py", source)
    patch_model_factory("```python\nvalidated = bool(flag)\n```")

    config = load_config(tmp_path, model_provider="ollama")
    bundle = generate_preview_patch(tmp_path, config)

    proposed = bundle.file_patches[0].proposed_content
    assert proposed.count("return 1") == 2, proposed


# ---------------------------------------------------------------------------
# AUDIT-11: the Python extractor re-walked the whole repository for every file
# ---------------------------------------------------------------------------


def test_audit11_local_module_names_are_resolved_once_per_project_root(tmp_path: Path) -> None:
    """Resolving local module names must not repeat the recursive walk per file.

    The walk is identical for every file in a repository. Running it inside extract()
    made 'codepilot analyze' quadratic in repository size (15s on a 101-module project).
    """
    from codepilot.core.analyzer.extractors.python import PythonLanguageExtractor

    for i in range(5):
        _write(tmp_path / f"mod_{i}.py", "import os\n\n\ndef f():\n    return 1\n")

    extractor = PythonLanguageExtractor()
    calls = {"n": 0}
    real_rglob = Path.rglob

    def counting_rglob(self, pattern, *a, **kw):
        if pattern == "*.py":
            calls["n"] += 1
        return real_rglob(self, pattern, *a, **kw)

    original = Path.rglob
    Path.rglob = counting_rglob  # type: ignore[method-assign]
    try:
        for i in range(5):
            target = tmp_path / f"mod_{i}.py"
            extractor.extract(target, target.read_text(encoding="utf-8"), tmp_path)
    finally:
        Path.rglob = original  # type: ignore[method-assign]

    assert calls["n"] == 1, f"expected a single repository walk, saw {calls['n']}"


def test_audit11_local_import_detection_is_unchanged_by_caching(tmp_path: Path) -> None:
    """The optimisation must not alter which imports are classified as local."""
    from codepilot.core.analyzer.extractors.python import PythonLanguageExtractor

    _write(tmp_path / "helpers.py", "VALUE = 1\n")
    target = tmp_path / "main.py"
    _write(target, "import os\nimport helpers\nfrom helpers import VALUE\n")

    extractor = PythonLanguageExtractor()
    info = extractor.extract(target, target.read_text(encoding="utf-8"), tmp_path)

    by_module = {i.module: i.is_local for i in info.imports}
    assert by_module["helpers"] is True
    assert by_module["os"] is False


def test_audit11_separate_project_roots_get_separate_caches(tmp_path: Path) -> None:
    """A cached walk must not leak local names between different repositories."""
    from codepilot.core.analyzer.extractors.python import PythonLanguageExtractor

    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    _write(proj_a / "alpha.py", "A = 1\n")
    _write(proj_b / "beta.py", "B = 1\n")
    main_b = proj_b / "main.py"
    _write(main_b, "import alpha\nimport beta\n")

    extractor = PythonLanguageExtractor()
    main_a = proj_a / "main.py"
    _write(main_a, "import alpha\n")
    extractor.extract(main_a, main_a.read_text(encoding="utf-8"), proj_a)
    info_b = extractor.extract(main_b, main_b.read_text(encoding="utf-8"), proj_b)

    by_module = {i.module: i.is_local for i in info_b.imports}
    assert by_module["beta"] is True
    assert by_module["alpha"] is False, "project A's modules must not count as local in B"


# ---------------------------------------------------------------------------
# AUDIT-10: patch application used an unvalidated path for the write
# ---------------------------------------------------------------------------


def test_audit10_traversal_path_in_a_patch_bundle_cannot_escape_the_repository(
    tmp_path: Path,
) -> None:
    """A hand-crafted bundle must not write outside the project root."""
    outside = tmp_path.parent / "escape_target.py"
    outside.write_text("ORIGINAL = True\n", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)

    original = outside.read_text(encoding="utf-8")
    bundle = PatchBundle(
        patch_id="patch-escape",
        created_at="2026-01-01T00:00:00Z",
        project_root=str(project),
        model_provider="mock",
        file_patches=[
            FilePatch(
                file_path="../escape_target.py",
                original_hash=compute_sha256(original),
                original_content=original,
                proposed_content="PWNED = True\n",
                diff="",
            )
        ],
        full_diff="",
    )
    patches_dir = project / ".codepilot" / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)
    (patches_dir / "pending_patch.json").write_text(json.dumps(bundle.to_dict()), encoding="utf-8")

    with pytest.raises(PatchError):
        apply_approved_patch(project, approved=True, config=CodePilotConfig(project_root=project))

    assert outside.read_text(encoding="utf-8") == original
