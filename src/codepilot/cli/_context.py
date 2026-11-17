"""Shared Typer application object and consoles for the CodePilot CLI.

Isolated in its own module so that every command module registers against the same
Typer app without importing each other, avoiding circular imports.
"""

from rich.console import Console
import typer

# Separate consoles for normal output and errors
console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="codepilot",
    help="CodePilot AI - Git-based AI Code Modification and Error Correction Engine.",
    no_args_is_help=True,
    add_completion=False,
)
