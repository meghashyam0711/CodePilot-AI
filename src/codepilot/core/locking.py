"""Advisory PID-based locks for operations that must not run concurrently.

Several CodePilot operations write shared state under '.codepilot/' -- the pending patch
bundle, backup snapshots, test results -- and two simultaneous runs corrupt each other
(the classic symptom being "last writer wins" on 'pending_patch.json').

A lock records the owning process id. A lock whose owner is no longer alive is stale,
left behind by a run that was killed or crashed, and is reclaimed automatically; without
that, a single interrupted run blocked the repository until the file was deleted by hand.
Liveness checks fail *closed*: if it cannot be determined whether the owner is running,
the lock is respected.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional

from codepilot.logging_config import get_logger

logger = get_logger("core.locking")


def is_pid_running(pid: Any) -> bool:
    """Best-effort check whether a process id is still alive on this machine.

    Returns True when liveness cannot be determined, so an indeterminate result is
    treated as 'still running' and the lock is respected.
    """
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return True  # unknown owner: respect the lock

    if pid_int <= 0:
        return False

    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int)
            if not handle:
                return False
            try:
                still_active = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(still_active)):
                    return still_active.value == 259  # STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True

    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return True


def acquire_lock(
    project_root: Path,
    lock_filename: str,
    error_factory: Callable[[str], Exception],
    busy_message: Optional[Callable[[Any, Path], str]] = None,
    operation: str = "operation",
) -> Path:
    """Acquire an advisory lock file, reclaiming it if its owner process is gone.

    Args:
        project_root: Repository root.
        lock_filename: Lock path relative to the repository root.
        error_factory: Builds the exception raised when the lock is genuinely held.
        busy_message: Optional custom message builder, given the owning pid and lock path.
        operation: Human-readable operation name used in the default message.

    Returns:
        Path: The acquired lock file.

    Raises:
        Whatever 'error_factory' produces, if another live process holds the lock or the
        lock file cannot be read or written.
    """
    root = project_root.resolve()
    lock_file = root / lock_filename
    lock_file.parent.mkdir(parents=True, exist_ok=True)

    if lock_file.is_file():
        try:
            lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt lock fails closed: its owner cannot be identified, so it is not
            # safe to assume the lock is free.
            raise error_factory(
                f"Concurrency lock is currently held.\nLock file: '{lock_file}'"
            )

        pid = lock_data.get("pid")
        if is_pid_running(pid):
            if busy_message is not None:
                raise error_factory(busy_message(pid, lock_file))
            raise error_factory(
                f"Another {operation} (PID {pid}) is currently running in this repository.\n"
                f"Lock file: '{lock_file}'"
            )

        logger.warning(
            "Reclaiming stale lock '%s': owner process (PID %s) is no longer running.",
            lock_file,
            pid,
        )
        try:
            lock_file.unlink()
        except OSError as e:
            raise error_factory(
                f"Stale lock at '{lock_file}' could not be removed: {e}"
            ) from e

    payload = {
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(root),
    }
    try:
        lock_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return lock_file
    except OSError as e:
        raise error_factory(f"Failed to acquire lock: {e}") from e


def release_lock(project_root: Path, lock_filename: str) -> None:
    """Release an advisory lock file, ignoring an already-released lock."""
    lock_file = project_root.resolve() / lock_filename
    if lock_file.is_file():
        try:
            lock_file.unlink()
        except OSError:
            pass
