"""Command-line interface for CodePilot AI.

Provides the CLI entry points for:
- codepilot --help
- codepilot --version
- codepilot init
- codepilot status
- codepilot doctor
- codepilot scan
- codepilot plan
- codepilot fix --preview
- codepilot apply --approved
- codepilot test
- codepilot evaluate

The implementation is split by command group:
- project      init, status, doctor and the root callback
- inspection   scan, tasks, explain, analyze, plan (all read-only)
- modify       fix, diff, apply, rollback (the commands that can write)
- verify       test, evaluate
- _render      shared terminal rendering helpers
- _context     the shared Typer app and consoles

Importing this package registers every command on the shared app.
"""

import sys

from codepilot.cli._context import app, console, err_console
from codepilot.exceptions import CodePilotError
from codepilot.cli._render import (
    _looks_like_file_path,
    _render_evaluation_terminal_scorecard,
    _render_patch_risk_warning,
    _render_skipped_tasks,
)

# Imported for their registration side effects: each module attaches its commands to
# the shared Typer app above. Keep these imports after _context to avoid cycles.
from codepilot.cli import project as project  # noqa: E402,F401
from codepilot.cli import inspection as inspection  # noqa: E402,F401
from codepilot.cli import modify as modify  # noqa: E402,F401
from codepilot.cli import verify as verify  # noqa: E402,F401


def main() -> None:
    """CLI execution entrypoint."""
    try:
        app()
    except CodePilotError as e:
        err_console.print(f"[bold red]CodePilot Error:[/bold red] {e}")
        sys.exit(1)


__all__ = [
    "app",
    "console",
    "err_console",
    "main",
]
