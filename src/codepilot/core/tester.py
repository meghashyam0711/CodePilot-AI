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
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from codepilot.config import CodePilotConfig
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


def acquire_test_lock(project_root: Path) -> Path:
    """Acquire concurrency lock to prevent simultaneous test runs.

    Raises:
        TestExecutionError: If another test execution is currently running.
    """
    root = project_root.resolve()
    lock_file = root / TEST_LOCK_FILENAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.is_file():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = lock_data.get("pid")
            raise TestExecutionError(
                f"Another test process (PID {pid}) is currently running in this repository.\n"
                f"Lock file: '{lock_file}'"
            )
        except json.JSONDecodeError:
            raise TestExecutionError(
                f"Test concurrency lock is currently held.\nLock file: '{lock_file}'"
            )


    lock_payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(root),
    }
    try:
        lock_file.write_text(json.dumps(lock_payload, indent=2), encoding="utf-8")
        return lock_file
    except OSError as e:
        raise TestExecutionError(f"Failed to acquire test lock: {e}") from e


def release_test_lock(project_root: Path) -> None:
    """Release concurrency lock."""
    root = project_root.resolve()
    lock_file = root / TEST_LOCK_FILENAME
    if lock_file.is_file():
        try:
            lock_file.unlink()
        except OSError:
            pass


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

    # Ensure command uses an approved test runner prefix
    normalized = trimmed.lower()
    exec_clean = sys.executable.lower().strip('"')
    is_approved = (
        any(normalized.startswith(runner.lower()) for runner in APPROVED_TEST_RUNNERS)
        or normalized.startswith(exec_clean)
        or normalized.startswith(f'"{exec_clean}"')
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
    timeout = timeout_override or config.request_timeout

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

        proc = subprocess.Popen(
            cmd_args,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        try:
            stdout_raw, stderr_raw = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            duration = round(time.time() - start_time, 2)
            return TestRunResult(
                command=command,
                passed=False,
                exit_code=-1,
                duration_seconds=duration,
                stdout="",
                stderr=f"Test command timed out after {timeout} seconds and was terminated.",
                summary=f"Timed out after {timeout}s",
                timed_out=True,
            )

        duration = round(time.time() - start_time, 2)
        passed = proc.returncode == 0

        # Bounded output truncation
        truncated = False
        stdout_clean = stdout_raw or ""
        stderr_clean = stderr_raw or ""

        if len(stdout_clean.encode("utf-8")) > MAX_OUTPUT_BYTES:
            stdout_clean = stdout_clean[: MAX_OUTPUT_BYTES // 2] + "\n\n[Output truncated at 64 KB by CodePilot safety guard]"
            truncated = True

        if len(stderr_clean.encode("utf-8")) > MAX_OUTPUT_BYTES:
            stderr_clean = stderr_clean[: MAX_OUTPUT_BYTES // 2] + "\n\n[Output truncated at 64 KB by CodePilot safety guard]"
            truncated = True

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
