"""Safe test execution and discovery engine for CodePilot AI.

Discovers project tests, executes test suites using validated runners, enforces
concurrency locks, bounds output size, and kills processes on timeout.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from codepilot.config import DEFAULT_TEST_TIMEOUT_SECONDS, CodePilotConfig
from codepilot.core.locking import acquire_lock, is_pid_running, release_lock
from codepilot.core.security import validate_target_path
from codepilot.exceptions import SecurityError, TestExecutionError

from codepilot.logging_config import get_logger

logger = get_logger("core.tester")

# Whitelist of approved test runner command prefixes
APPROVED_TEST_RUNNERS = [
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "npm test",
    "npm run test",
    "yarn test",
    "cargo test",
    "go test",
]

# Disallowed dangerous shell operators in test commands
DANGEROUS_SHELL_OPERATORS = [";", "&&", "||", "|", "&", ">", "<", "`", "$("]

# Concurrency lock filename
TEST_LOCK_FILENAME = ".codepilot/test.lock"
MAX_OUTPUT_BYTES = 65536  # 64 KB bounded capture limit


@dataclass
class TestDiscoveryResult:
    """Outcome of static test framework discovery."""

    framework: str
    proposed_command: str
    working_directory: str
    discovered_files: List[str]
    total_test_files: int
    is_supported: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TestRunResult:
    """Outcome of automated test execution."""

    command: str
    passed: bool
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    summary: str
    truncated: bool = False
    timed_out: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "summary": self.summary,
            "truncated": self.truncated,
            "timed_out": self.timed_out,
        }


@dataclass
class FailureDetail:
    """Structured details of a specific test failure."""

    test_id: str
    failing_file: str
    failing_line: Optional[int]
    error_type: str
    error_message: str
    traceback: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class _BoundedStreamReader(threading.Thread):
    """Drain a child pipe on a worker thread, retaining at most 'limit' bytes.

    subprocess.communicate() buffers the child's entire output in memory before any
    truncation can be applied, so a runaway suite printing gigabytes exhausted memory
    before the 64 KB guard ever took effect. This reads in chunks and stops accumulating
    once the cap is reached, while still consuming the pipe so the child never blocks on
    a full buffer.
    """

    def __init__(self, stream: Any, limit: int = MAX_OUTPUT_BYTES) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._chunks: List[str] = []
        self._retained = 0
        self.total_bytes = 0
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    break
                self.total_bytes += len(chunk.encode("utf-8", errors="replace"))
                if self._retained < self._limit:
                    room = self._limit - self._retained
                    keep = chunk[:room]
                    self._chunks.append(keep)
                    self._retained += len(keep)
                    if len(keep) < len(chunk):
                        self.truncated = True
                else:
                    # Keep draining so the child is never blocked by a full pipe,
                    # but stop growing the retained buffer.
                    self.truncated = True
        except Exception as e:  # pragma: no cover - pipe teardown races
            logger.debug("Stream reader stopped: %s", e)
        finally:
            try:
                self._stream.close()
            except Exception:
                pass

    @property
    def text(self) -> str:
        body = "".join(self._chunks)
        if self.truncated:
            body += "\n\n[Output truncated at 64 KB by CodePilot safety guard]"
        return body


def _terminate_process_tree(proc: "subprocess.Popen[Any]") -> None:
    """Terminate a timed-out test runner together with every process it spawned.

    Killing only the direct child leaves grandchildren (test workers, servers started by
    the suite) running and holding the captured pipes open. Best effort: any failure falls
    back to killing the direct child so a timeout always returns.
    """
    try:
        if sys.platform == "win32":
            # taskkill walks the tree; the process was started in its own process group.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception as e:  # pragma: no cover - platform dependent
        logger.debug("Process-tree termination failed for PID %s: %s", proc.pid, e)

    try:
        proc.kill()
    except Exception:
        pass

    # Reap the child with wait(), not communicate(): the output pipes are drained by
    # dedicated reader threads which close them, and communicate() would then raise
    # "I/O operation on closed file" from its own internal reader threads.
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def acquire_test_lock(project_root: Path) -> Path:
    """Acquire concurrency lock to prevent simultaneous test runs.

    A lock whose recorded owner process is no longer alive is stale -- it is left behind
    when a test run is killed or crashes -- and is reclaimed automatically. Without this,
    a single interrupted run blocked 'codepilot test' in the repository permanently and
    could only be cleared by deleting the lock file by hand.

    Raises:
        TestExecutionError: If another live test execution is currently running.
    """
    # A lock held by a live process is respected, including one owned by the current
    # PID: that means a test run is already in progress in this process.
    return acquire_lock(
        project_root,
        TEST_LOCK_FILENAME,
        error_factory=TestExecutionError,
        busy_message=lambda pid, lock_file: (
            f"Another test process (PID {pid}) is currently running in this repository.\n"
            f"Lock file: '{lock_file}'"
        ),
        operation="test process",
    )


def release_test_lock(project_root: Path) -> None:
    """Release concurrency lock."""
    release_lock(project_root, TEST_LOCK_FILENAME)


def discover_project_tests(
    project_root: Path,
    config: CodePilotConfig,
) -> TestDiscoveryResult:
    """Discover available test frameworks and test files statically without executing code.

    Args:
        project_root: Root directory of the repository.
        config: CodePilot configuration.

    Returns:
        TestDiscoveryResult: Discovered files, framework, and proposed command.
    """
    root = project_root.resolve()
    discovered_files: List[str] = []
    framework = "none"
    proposed_cmd = ""

    def _is_valid_test_file(path: Path) -> bool:
        if any(part.startswith(".") for part in path.parts):
            return False
        if "test" in path.parent.parts or "tests" in path.parent.parts:
            return True
        if "src" in path.parts:
            try:
                txt = path.read_text(encoding="utf-8", errors="ignore")
                return "def test_" in txt or "class Test" in txt
            except Exception:
                return False
        return True

    # Python / pytest
    py_test_files: List[str] = []
    for p in root.rglob("test_*.py"):
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
            if _is_valid_test_file(p):
                py_test_files.append(rel)
        except ValueError:
            pass
    for p in root.rglob("*_test.py"):
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
            if _is_valid_test_file(p) and rel not in py_test_files:
                py_test_files.append(rel)
        except ValueError:
            pass

    if (
        (root / "pyproject.toml").is_file()
        or (root / "pytest.ini").is_file()
        or (root / "setup.cfg").is_file()
        or (root / "tests").is_dir()
        or bool(py_test_files)
    ):
        framework = "pytest"
        proposed_cmd = f"{sys.executable} -m pytest"
        discovered_files = py_test_files

    # Node / Jest / Mocha
    elif (root / "package.json").is_file():
        framework = "npm"
        proposed_cmd = "npm test"
        for p in root.rglob("*.test.js"):
            try:
                discovered_files.append(str(p.relative_to(root)).replace("\\", "/"))
            except ValueError:
                pass

    # Rust
    elif (root / "Cargo.toml").is_file():
        framework = "cargo"
        proposed_cmd = "cargo test"

    # Go
    elif (root / "go.mod").is_file():
        framework = "go"
        proposed_cmd = "go test ./..."


    # Fallback to configured command
    if config.test_command:
        proposed_cmd = config.test_command
        if framework == "none":
            framework = "custom"

    return TestDiscoveryResult(
        framework=framework,
        proposed_command=proposed_cmd,
        working_directory=str(root),
        discovered_files=sorted(discovered_files),
        total_test_files=len(discovered_files),
        is_supported=bool(proposed_cmd),
    )


def detect_safe_test_command(project_root: Path) -> Optional[str]:
    """Auto-detect standard test runner based on project workspace indicators."""
    root = project_root.resolve()

    py_test_files = list(root.rglob("test_*.py")) + list(root.rglob("*_test.py"))
    if (
        (root / "pyproject.toml").is_file()
        or (root / "pytest.ini").is_file()
        or (root / "setup.cfg").is_file()
        or (root / "tests").is_dir()
        or (root / "setup.py").is_file()
        or bool(py_test_files)
    ):
        executable = sys.executable
        if " " in executable and not (executable.startswith('"') and executable.endswith('"')):
            return f'"{executable}" -m pytest'
        return f"{executable} -m pytest"

    if (root / "package.json").is_file():
        return "npm test"

    if (root / "Cargo.toml").is_file():
        return "cargo test"

    if (root / "go.mod").is_file():
        return "go test ./..."

    return None


def validate_test_command(command: str) -> None:
    """Verify that the test command is safe and does not contain dangerous shell chaining."""
    trimmed = command.strip()
    if not trimmed:
        raise TestExecutionError("Test command cannot be empty.")

    # Disallow chained or arbitrary shell execution
    for operator in DANGEROUS_SHELL_OPERATORS:
        if operator in trimmed:
            raise TestExecutionError(
                f"Test command contains prohibited shell operator '{operator}'.\n"
                "To prevent command injection, chained commands are not permitted."
            )

    # Ensure command uses an approved test runner prefix.
    #
    # The prefix must end at a token boundary. A bare startswith() match also accepted
    # commands such as 'pytest_evil_binary' or 'go testsomething', letting an untrusted
    # '.codepilot.toml' name an arbitrary executable that merely begins with an
    # approved runner name.
    normalized = trimmed.lower()
    exec_clean = sys.executable.lower().strip('"')

    def _matches_prefix(text: str, prefix: str) -> bool:
        if not text.startswith(prefix):
            return False
        rest = text[len(prefix):]
        return rest == "" or rest[0].isspace()

    is_approved = (
        any(_matches_prefix(normalized, runner.lower()) for runner in APPROVED_TEST_RUNNERS)
        or _matches_prefix(normalized, exec_clean)
        or _matches_prefix(normalized, f'"{exec_clean}"')
    )

    if not is_approved:
        raise TestExecutionError(
            f"Command '{trimmed}' does not match any approved test runner.\n"
            f"Approved runners: {', '.join(APPROVED_TEST_RUNNERS)} or the current Python interpreter."
        )


def parse_test_failures(stdout: str, stderr: str) -> List[FailureDetail]:
    """Parse test runner failure output (e.g. pytest) into structured details."""
    combined = (stdout or "") + "\n" + (stderr or "")
    failures: List[FailureDetail] = []

    # Match pytest failure headers: e.g. FAILED tests/test_math.py::test_division - ZeroDivisionError
    matches = re.findall(
        r"FAILED\s+([^\s:]+)(?:::([^\s]+))?\s*(?:-\s*([^:\n]+)(?::\s*([^\n]+))?)?",
        combined,
    )
    for m in matches:
        file_path = m[0]
        test_fn = m[1] or ""
        err_type = m[2] or "AssertionError"
        err_msg = m[3] or ""

        # Attempt to locate line number in traceback
        line_num: Optional[int] = None
        line_match = re.search(re.escape(file_path) + r":(\d+):", combined)
        if line_match:
            try:
                line_num = int(line_match.group(1))
            except ValueError:
                pass

        failures.append(
            FailureDetail(
                test_id=f"{file_path}::{test_fn}" if test_fn else file_path,
                failing_file=file_path,
                failing_line=line_num,
                error_type=err_type.strip(),
                error_message=err_msg.strip(),
                traceback=combined[:2000],
            )
        )

    return failures


def run_project_tests(
    project_root: Path,
    config: CodePilotConfig,
    custom_command: Optional[str] = None,
    timeout_override: Optional[float] = None,
) -> TestRunResult:
    """Safely execute tests for the project and return bounded results.

    Enforces working directory boundaries, concurrency lock, process termination
    on timeout, and bounded output capture.

    Args:
        project_root: Root directory of the repository.
        config: Validated CodePilot configuration.
        custom_command: Optional explicit test command passed by developer.
        timeout_override: Optional timeout in seconds overriding config.

    Returns:
        TestRunResult: Result details including exit code, duration, and output.

    Raises:
        TestExecutionError: If command validation fails, concurrency lock fails, or execution crashes.
    """
    root = project_root.resolve()

    # Determine command
    command = custom_command or config.test_command
    if not command:
        command = detect_safe_test_command(root)

    if not command:
        raise TestExecutionError(
            "Could not detect test framework in project root.\n"
            "Configure 'test_command' in '.codepilot.toml' or supply a command via '--command'."
        )

    # Validate command safety
    validate_test_command(command)

    # Acquire concurrency lock
    acquire_test_lock(root)

    start_time = time.time()
    # Test execution has its own budget. 'request_timeout' bounds a single model inference
    # call (default 30s); using it here terminated any legitimate test suite that ran
    # longer than the LLM budget.
    timeout = (
        timeout_override
        or getattr(config, "test_timeout", None)
        or DEFAULT_TEST_TIMEOUT_SECONDS
    )

    try:
        raw_args = (
            shlex.split(command, posix=False)
            if sys.platform == "win32"
            else shlex.split(command)
        )
        cmd_args = [
            arg[1:-1] if len(arg) >= 2 and arg.startswith('"') and arg.endswith('"') else arg
            for arg in raw_args
        ]

        # Launch the runner in its own process group / job object so that a timeout can
        # terminate the whole tree. proc.kill() alone only reaps the direct child, leaving
        # orphaned grandchildren (pytest-xdist workers, dev servers started by tests)
        # running and still holding the captured pipes.
        popen_kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd_args,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )

        # Drain both pipes on worker threads with a hard retention cap, so output is
        # bounded as it arrives rather than after the whole of it is already in memory.
        stdout_reader: Optional[_BoundedStreamReader] = None
        stderr_reader: Optional[_BoundedStreamReader] = None
        if proc.stdout is not None:
            stdout_reader = _BoundedStreamReader(proc.stdout)
            stdout_reader.start()
        if proc.stderr is not None:
            stderr_reader = _BoundedStreamReader(proc.stderr)
            stderr_reader.start()

        def _collected(reader: Optional[_BoundedStreamReader]) -> Tuple[str, bool]:
            if reader is None:
                return "", False
            reader.join(timeout=5)
            return reader.text, reader.truncated

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(proc)
            duration = round(time.time() - start_time, 2)
            partial_stdout, _ = _collected(stdout_reader)
            timeout_note = (
                f"Test command timed out after {timeout} seconds and was terminated."
            )
            partial_stderr, _ = _collected(stderr_reader)
            if partial_stderr.strip():
                timeout_note = f"{timeout_note}\n\n{partial_stderr}"
            return TestRunResult(
                command=command,
                passed=False,
                exit_code=-1,
                duration_seconds=duration,
                # Output captured before the timeout is retained: it is usually the only
                # evidence of where the suite hung.
                stdout=partial_stdout,
                stderr=timeout_note,
                summary=f"Timed out after {timeout}s",
                timed_out=True,
            )

        duration = round(time.time() - start_time, 2)
        passed = proc.returncode == 0

        stdout_clean, stdout_truncated = _collected(stdout_reader)
        stderr_clean, stderr_truncated = _collected(stderr_reader)
        truncated = stdout_truncated or stderr_truncated

        summary = (
            f"Test execution succeeded in {duration}s"
            if passed
            else f"Test execution failed with exit code {proc.returncode} in {duration}s"
        )

        return TestRunResult(
            command=command,
            passed=passed,
            exit_code=proc.returncode,
            duration_seconds=duration,
            stdout=stdout_clean,
            stderr=stderr_clean,
            summary=summary,
            truncated=truncated,
            timed_out=False,
        )

    except FileNotFoundError as e:
        raise TestExecutionError(
            f"Test runner binary not found: {e}. Ensure the tool is installed and in PATH."
        ) from e
    except Exception as e:
        raise TestExecutionError(f"Unexpected error running tests: {e}") from e
    finally:
        release_test_lock(root)
