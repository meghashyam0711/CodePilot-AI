"""Project lifecycle commands: init, status and doctor, plus the root callback.
"""

import json
from pathlib import Path
import sys
from typing import Optional
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
import typer
from codepilot.config import CONFIG_FILENAME, initialize_project_config
from codepilot.core.app import CodePilotApp
from codepilot.exceptions import ConfigurationError, ProjectInitError
from codepilot.version import APP_NAME, __version__
from codepilot.cli._context import app, console, err_console


def version_callback(value: bool) -> None:
    """Print the application version and exit."""
    if value:
        console.print(f"[bold cyan]{APP_NAME}[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit(code=0)

@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Display the installed application version.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """CodePilot AI CLI - Core Architecture & Foundation Engine."""

@app.command(
    name="init",
    help="Initialize CodePilot configuration in the target project directory.",
)
def init_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target directory to initialize CodePilot configuration in.",
        resolve_path=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing configuration file without interactive prompt.",
    ),
) -> None:
    """Initialize a new CodePilot project configuration file."""
    try:
        config_path = directory / CONFIG_FILENAME
        if config_path.exists() and not force:
            err_console.print(
                Panel(
                    f"[yellow]Configuration already exists at:[/yellow] {config_path}\n\n"
                    "CodePilot will [bold red]not overwrite[/bold red] existing configuration without approval.\n"
                    "Use [bold cyan]--force[/bold cyan] to overwrite.",
                    title="[bold yellow]Initialization Warning[/bold yellow]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=1)

        created_path = initialize_project_config(target_directory=directory, force=force)
        console.print(
            Panel(
                f"[bold green]Successfully initialized CodePilot configuration![/bold green]\n\n"
                f"[bold]Config File:[/bold] {created_path}\n"
                f"[bold]Project Root:[/bold] {directory.resolve()}\n\n"
                "Next steps:\n"
                "  • Check project status: [cyan]codepilot status[/cyan]\n"
                "  • Run system doctor:    [cyan]codepilot doctor[/cyan]",
                title="[bold cyan]CodePilot Initialized[/bold cyan]",
                border_style="green",
            )
        )
    except ProjectInitError as e:
        err_console.print(f"[bold red]Initialization Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during initialization:[/bold red] {e}")
        raise typer.Exit(code=1) from e

@app.command(
    name="status",
    help="Display tool version, configuration status, working directory, and readiness info.",
)
def status_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Project directory to inspect.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output status information as JSON.",
    ),
) -> None:
    """Inspect and report the current project and tool status."""
    try:
        core_app = CodePilotApp(project_root=directory)
        status = core_app.get_status()

        if json_output:
            data = {
                "app_name": APP_NAME,
                "version": status.app_version,
                "project_root": str(status.project_root),
                "config_file_found": status.config_file_found,
                "config_file_path": (
                    str(status.config_file_path) if status.config_file_path else None
                ),
                "git": {
                    "is_installed": status.git_info.is_installed,
                    "git_version": status.git_info.git_version,
                    "is_repo": status.git_info.is_repo,
                    "branch": status.git_info.current_branch,
                    "uncommitted_changes": status.git_info.has_uncommitted_changes,
                    "description": status.git_info.description,
                },
                "ai_engine": {
                    "provider": status.ai_provider,
                    "model_id": status.ai_model_id,
                    "ready": status.ai_ready,
                    "description": status.ai_status_description,
                },
                "phase1_ready": status.is_ready_for_phase1,
            }
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
            sys.stdout.flush()
            return

        # Render Rich visual status table
        table = Table(
            title=f"{APP_NAME} - Status Overview",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Component", style="bold cyan", width=22)
        table.add_column("Status", width=16)
        table.add_column("Details", style="dim")

        # Application version
        table.add_row("CodePilot Engine", f"[green]v{status.app_version}[/green]", "Active")

        # Project Root
        table.add_row("Project Root", "[blue]Active[/blue]", str(status.project_root))

        # Configuration file status
        if status.config_file_found:
            table.add_row(
                "Configuration",
                "[green]Found[/green]",
                f"{status.config_file_path} (log_level: {status.config.log_level})",
            )
        else:
            table.add_row(
                "Configuration",
                "[yellow]Defaults / Env[/yellow]",
                f"No {CONFIG_FILENAME} found. Run 'codepilot init'",
            )

        # Git status
        if not status.git_info.is_installed:
            table.add_row("Git Tool", "[red]Not Found[/red]", "Git CLI is not installed or not in PATH")
        elif not status.git_info.is_repo:
            table.add_row("Git Repository", "[yellow]Not Initialized[/yellow]", "Directory is not a Git repo (run 'git init')")
        else:
            git_details = f"Branch: {status.git_info.current_branch} | {'Dirty' if status.git_info.has_uncommitted_changes else 'Clean'}"
            table.add_row("Git Repository", "[green]Initialized[/green]", git_details)

        # AI Engine status (reflects active provider and health)
        ai_status_style = "[green]Ready[/green]" if status.ai_ready else "[yellow]Disabled / Offline[/yellow]"
        table.add_row(
            "AI Modification Engine",
            ai_status_style,
            status.ai_status_description,
        )

        # Architecture Analysis status
        arch_report_path = directory / ".codepilot" / "architecture.json"
        if arch_report_path.is_file():
            try:
                arch_data = json.loads(arch_report_path.read_text(encoding="utf-8"))
                ptype = arch_data.get("project_type", "unknown").capitalize()
                subtypes = arch_data.get("project_subtypes", [])
                if subtypes:
                    ptype += f" ({', '.join(subtypes)})"
                files_cnt = arch_data.get("structure", {}).get("file_count", 0)
                table.add_row(
                    "Architecture",
                    f"[green]{ptype}[/green]",
                    f"{files_cnt} files analyzed (run 'codepilot analyze' to refresh)",
                )
            except Exception:
                pass
        else:
            table.add_row(
                "Architecture",
                "[yellow]Not Analyzed[/yellow]",
                "Run 'codepilot analyze' to map repository architecture",
            )


        console.print(table)

        # Readiness summary footer
        console.print(
            Panel(
                f"[bold green]Ready for CodePilot operations.[/bold green]\n"
                f"[dim]Protected files configured: {len(status.config.protected_files)} | "
                f"Protected dirs: {len(status.config.protected_directories)} | "
                f"Approval required: {status.config.require_approval}[/dim]",
                border_style="green",
            )
        )

    except ConfigurationError as e:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Status Inspection Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e

@app.command(
    name="doctor",
    help="Check Python version, required dependencies, configuration validity, and Git availability.",
)
def doctor_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Project directory to diagnose.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output doctor diagnostics as JSON.",
    ),
) -> None:
    """Run diagnostic checks on the runtime environment and dependencies."""
    try:
        core_app = CodePilotApp(project_root=directory)
        report = core_app.run_doctor()

        if json_output:
            data = {
                "overall_passed": report.passed,
                "passed_count": report.passed_count,
                "total_count": report.total_count,
                "checks": [
                    {
                        "name": check.name,
                        "category": check.category,
                        "passed": check.passed,
                        "details": check.details,
                        "remediation": check.remediation,
                        "is_critical": check.is_critical,
                    }
                    for check in report.checks
                ],
            }
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
            sys.stdout.flush()
            if not report.passed:
                raise typer.Exit(code=1)
            return

        table = Table(
            title=f"{APP_NAME} - Environment Doctor Diagnostics",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Category", style="cyan", min_width=12)
        table.add_column("Diagnostic Check", style="bold", min_width=20)
        table.add_column("Status", min_width=10)
        table.add_column("Details & Remediation", min_width=40)

        for check in report.checks:
            if check.passed:
                status_str = "[bold green]PASS [OK][/bold green]"
                details_str = f"[green]{check.details}[/green]"
            elif check.is_critical:
                status_str = "[bold red]FAIL [X][/bold red]"
                details_str = f"[red]{check.details}[/red]"
                if check.remediation:
                    details_str += f"\n[yellow]Fix: {check.remediation}[/yellow]"
            else:
                status_str = "[bold yellow]WARN [!][/bold yellow]"
                details_str = f"[yellow]{check.details}[/yellow]"
                if check.remediation:
                    details_str += f"\n[dim]Suggestion: {check.remediation}[/dim]"

            table.add_row(check.category, check.name, status_str, details_str)

        console.print(table)

        if report.passed:
            # Non-critical checks do not fail the run, but claiming the environment "meets
            # all requirements" while a warning is on screen misreports the diagnosis.
            warnings = [c for c in report.checks if not c.passed and not c.is_critical]
            if warnings:
                console.print(
                    Panel(
                        f"[bold green]All critical diagnostic checks passed! ({report.passed_count}/{report.total_count})[/bold green]\n"
                        f"[yellow]{len(warnings)} non-critical check(s) need attention:[/yellow]\n"
                        + "\n".join(f"  • {escape(c.name)}" for c in warnings)
                        + "\n[dim]CodePilot will run, but the features above may be unavailable.[/dim]",
                        title="[bold yellow]Doctor Diagnosis: Healthy (with warnings)[/bold yellow]",
                        border_style="yellow",
                    )
                )
            else:
                console.print(
                    Panel(
                        f"[bold green]All critical diagnostic checks passed! ({report.passed_count}/{report.total_count})[/bold green]\n"
                        "Your environment meets all system requirements.",
                        title="[bold green]Doctor Diagnosis: Healthy[/bold green]",
                        border_style="green",
                    )
                )
        else:
            err_console.print(
                Panel(
                    f"[bold red]One or more critical diagnostic checks failed.[/bold red]\n"
                    "Please review the remediation instructions above to fix missing requirements.",
                    title="[bold red]Doctor Diagnosis: Issues Detected[/bold red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except Exception as e:
        err_console.print(f"[bold red]Diagnostic Execution Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
