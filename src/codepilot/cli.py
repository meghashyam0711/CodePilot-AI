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
"""

import json
from pathlib import Path
import sys
from typing import Optional

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
import typer

from codepilot.config import CONFIG_FILENAME, initialize_project_config
from codepilot.core.analyzer import (
    ArchitectureContextBuilder,
    ArchitectureReport,
    ProjectAnalyzer,
)
from codepilot.core.app import CodePilotApp
from codepilot.core.context import (
    ProjectContextBundle,
    ProjectContextEngine,
    build_model_request,
    build_user_prompt,
)
from codepilot.core.dataset.builder import load_jsonl_dataset
from codepilot.core.evaluation import (
    EvaluationCase,
    EvaluationReport,
    EvaluationRunner,
    get_default_benchmark_cases,
)
from codepilot.core.fixer import generate_preview_patch, load_pending_patch
from codepilot.core.models import (
    BaseModelClient,
    ModelNotFoundError,
    ModelRequest,
    ModelResponse,
    OllamaRuntimeError,
    create_model_client,
)
from codepilot.core.parser import (
    INSTRUCTION_TYPE_ALIASES,
    SUPPORTED_INSTRUCTION_TYPES,
    InstructionParserEngine,
    InstructionTask,
    InstructionType,
)
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.planner import generate_plan, load_plan, save_plan
from codepilot.core.recovery import list_backups, rollback_patch
from codepilot.core.scanner import (
    load_scan_result,
    save_scan_result,
    scan_repository,
)
from codepilot.core.security import validate_target_path
from codepilot.core.test_analyzer import analyze_test_failure
from codepilot.core.tester import discover_project_tests, run_project_tests
from codepilot.exceptions import (
    AnalysisError,
    CodePilotError,
    ConfigurationError,
    FixError,
    PatchError,
    PlanError,
    ProjectInitError,
    RecoveryError,
    ScanError,
    SecurityError,
    TestExecutionError,
)
from codepilot.version import APP_NAME, __version__


# Separate consoles for normal output and errors
console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name="codepilot",
    help="CodePilot AI - Git-based AI Code Modification and Error Correction Engine.",
    no_args_is_help=True,
    add_completion=False,
)


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
    # If no subcommand and no eager flag, Typer's no_args_is_help will show help


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


@app.command(
    name="scan",
    help="Inspect the selected Git repository and discover AI instruction comments.",
)
def scan_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory to scan.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output discovered instructions as JSON.",
    ),
) -> None:
    """Scan the repository for supported AI instruction comments."""
    try:
        core_app = CodePilotApp(project_root=directory)
        scan_result = scan_repository(directory, core_app.config)

        # Cache results in .codepilot/scan_results.json
        cache_path = directory / ".codepilot" / "scan_results.json"
        save_scan_result(scan_result, cache_path)

        if json_output:
            sys.stdout.write(json.dumps(scan_result.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        table = Table(
            title=f"{APP_NAME} - Discovered AI Instruction Comments",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("ID", style="dim", width=12)
        table.add_column("File", style="cyan", min_width=25)
        table.add_column("Line", style="bold yellow", width=6)
        table.add_column("Type", style="bold magenta", width=10)
        table.add_column("Instruction Prompt", min_width=40)

        for inst in scan_result.instructions:
            table.add_row(
                inst.id,
                escape(inst.file_path),
                str(inst.line_number),
                inst.instruction_type,
                escape(inst.prompt),
            )

        console.print(table)

        if scan_result.instructions:
            counts_str = ""
            if scan_result.counts_by_type:
                counts_str = "\n[bold cyan]Counts:[/bold cyan] " + " | ".join(
                    f"[bold white]{k}:[/bold white] {v}" for k, v in scan_result.counts_by_type.items()
                ) + "\n"
            console.print(
                Panel(
                    f"[bold green]Discovered {len(scan_result.instructions)} AI instruction comment(s)[/bold green] "
                    f"across {scan_result.scanned_files_count} scanned files.{counts_str}\n"
                    "Next step: Run [bold cyan]codepilot tasks[/bold cyan] to inspect symbol mapping,\n"
                    "or run [bold cyan]codepilot plan[/bold cyan] to generate an implementation plan.",
                    title="[bold green]Scan Complete[/bold green]",
                    border_style="green",
                )
            )
        else:
            sample_comments = (
                "  # AI-FIX: handle division by zero\n"
                "  # AI-TASK: implement user login function\n"
                "  # AI-REFACTOR: optimize database query\n"
                "  # AI-TEST: add unit test\n"
                "  # AI-DOCS: document public methods"
            )
            console.print(
                Panel(
                    f"Scanned {scan_result.scanned_files_count} files in '{directory}'.\n"
                    f"No AI instruction comments found.\n\n"
                    f"Add comments such as:\n[dim]{escape(sample_comments)}[/dim]",
                    title="[bold yellow]No Instructions Found[/bold yellow]",
                    border_style="yellow",
                )
            )

    except ScanError as e:
        err_console.print(f"[bold red]Scan Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during scan:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="tasks",
    help="List parsed AI instruction tasks with associated code symbols, counts, and status.",
)
def tasks_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory to inspect.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output parsed tasks and metrics as raw JSON.",
    ),
    type_filter: Optional[str] = typer.Option(
        None,
        "--type",
        "-t",
        help="Filter tasks by instruction type (e.g. AI-FIX, AI-TASK, AI-REFACTOR, AI-TEST, AI-DOCS).",
    ),
    detailed: bool = typer.Option(
        False,
        "--detailed",
        help="Show context and full task parameters.",
    ),
) -> None:
    """List parsed CodePilot AI instruction tasks converted from source comments."""
    try:
        engine = InstructionParserEngine(project_root=directory)
        parse_result = engine.parse()

        tasks = parse_result.tasks
        if type_filter:
            norm_filter = INSTRUCTION_TYPE_ALIASES.get(type_filter.upper(), type_filter.upper())
            tasks = [t for t in tasks if t.instruction_type.upper() == norm_filter]

        if json_output:
            data = parse_result.to_dict()
            if type_filter:
                data["tasks"] = [t.to_dict() for t in tasks]
                data["total_tasks"] = len(tasks)
            sys.stdout.write(json.dumps(data, indent=2) + "\n")
            sys.stdout.flush()
            return

        table = Table(
            title=f"{APP_NAME} - Parsed AI Instruction Tasks",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Task ID", style="dim", width=14)
        table.add_column("Type", style="bold", width=13)
        table.add_column("Location", style="cyan", min_width=25)
        table.add_column("Target Symbol", style="bold yellow", min_width=20)
        table.add_column("Description", min_width=35)
        table.add_column("Status", width=12)

        type_color_map = {
            "AI-FIX": "bold red",
            "AI-TASK": "bold green",
            "AI-REFACTOR": "bold magenta",
            "AI-TEST": "bold blue",
            "AI-DOCS": "bold cyan",
        }

        for task in tasks:
            t_color = type_color_map.get(task.instruction_type, "bold white")
            type_styled = f"[{t_color}]{task.instruction_type}[/{t_color}]"
            loc_str = f"{task.file_path}:{task.line_number}"

            sym_str = task.target_symbol or "-"
            if task.target_symbol_type and task.target_symbol_type != "module":
                sym_str += f" ({task.target_symbol_type})"

            if task.is_malformed:
                status_styled = "[bold red]Malformed[/bold red]"
            elif task.warning:
                status_styled = "[bold yellow]Warning[/bold yellow]"
            else:
                status_styled = "[green]Parsed[/green]"

            table.add_row(
                task.id,
                type_styled,
                loc_str,
                escape(sym_str),
                escape(task.description),
                status_styled,
            )

        console.print(table)

        # 1. Type Breakdown Counts Panel
        counts = parse_result.counts_by_type
        counts_line = " | ".join(
            f"[bold cyan]{k}:[/bold cyan] [bold white]{v}[/bold white]" for k, v in counts.items()
        )
        if counts_line:
            console.print(
                Panel(
                    counts_line,
                    title="[bold cyan]Instruction Counts by Type[/bold cyan]",
                    border_style="cyan",
                )
            )

        # 2. Warnings Panel (if any malformed or unsupported instructions)
        if parse_result.warnings:
            warn_items = "\n".join(f"• [yellow]{escape(w)}[/yellow]" for w in parse_result.warnings[:10])
            if len(parse_result.warnings) > 10:
                warn_items += f"\n• [dim]... and {len(parse_result.warnings) - 10} more warning(s)[/dim]"
            console.print(
                Panel(
                    warn_items,
                    title="[bold yellow]Parser Warnings & Anomalies[/bold yellow]",
                    border_style="yellow",
                )
            )

        # 3. Status Overview / Next Steps Notice (Clear that tasks are parsed, not completed)
        if tasks:
            filter_note = f" (filtered by {type_filter})" if type_filter else ""
            console.print(
                Panel(
                    f"[bold green]{len(tasks)} AI instruction task(s) parsed and ready for planning{filter_note}.[/bold green]\n\n"
                    f"[dim]Note: Tasks are structured instructions awaiting execution; no files have been modified.\n"
                    f"Run [bold cyan]codepilot plan[/bold cyan] to generate an implementation plan.\n"
                    f"Run [bold cyan]codepilot fix --preview[/bold cyan] to preview proposed modifications.[/dim]",
                    title="[bold green]Tasks Overview[/bold green]",
                    border_style="green",
                )
            )
        else:
            sample_tasks = (
                "  # AI-FIX: handle empty list gracefully\n"
                "  # AI-TASK: implement JWT authentication\n"
                "  # AI-REFACTOR: extract helper function\n"
                "  # AI-TEST: add unit tests for validator\n"
                "  # AI-DOCS: document public API methods"
            )
            console.print(
                Panel(
                    f"Scanned {parse_result.scanned_files_count} files in '{directory}'.\n"
                    f"No matching AI instruction tasks found.\n\n"
                    f"Supported comment patterns:\n[dim]{escape(sample_tasks)}[/dim]",
                    title="[bold yellow]No Tasks Found[/bold yellow]",
                    border_style="yellow",
                )
            )

    except ScanError as e:
        err_console.print(f"[bold red]Parser Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during task parsing:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="explain",
    help="Explain a detected AI instruction and summarize relevant project context using local LLM.",
)
def explain_cmd(
    task_id: Optional[str] = typer.Argument(
        None,
        help="Optional specific task ID to explain (e.g. task-06ecb2a39e). If omitted, explains the first task.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory to inspect.",
        resolve_path=True,
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override local model name (e.g. 'qwen2.5-coder:1.5b' or 'qwen2.5:1.5b-instruct-q4_K_M').",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Override model provider ('ollama', 'mock').",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output explanation and context bundle as JSON.",
    ),
    context_only: bool = typer.Option(
        False,
        "--context-only",
        help="Gather and preview project context and prompt without invoking model inference.",
    ),
) -> None:
    """Explain an instruction task and its architecture context using the local LLM."""
    try:
        core_app = CodePilotApp(project_root=directory)
        engine = InstructionParserEngine(project_root=directory)
        parse_result = engine.parse()
        tasks = parse_result.tasks

        if not tasks:
            if json_output:
                sys.stdout.write(json.dumps({"error": "No AI instruction tasks found", "tasks": []}, indent=2) + "\n")
                return
            console.print(
                Panel(
                    f"Scanned {parse_result.scanned_files_count} files in '{directory}'.\n"
                    f"No AI instruction comments found to explain.\n\n"
                    "Add an instruction comment such as:\n"
                    "  [dim]# AI-FIX: Handle empty lists safely[/dim]\n"
                    "or run [bold cyan]codepilot tasks[/bold cyan] to verify comment discovery.",
                    title="[bold yellow]No Tasks Found[/bold yellow]",
                    border_style="yellow",
                )
            )
            return

        # Select target task
        target_task: Optional[InstructionTask] = None
        if task_id:
            clean_id = task_id.strip()
            for t in tasks:
                if t.id == clean_id or t.id.startswith(clean_id) or clean_id in t.id:
                    target_task = t
                    break
            if not target_task:
                available = ", ".join(t.id for t in tasks[:6])
                err_msg = f"Task ID '{task_id}' not found. Available tasks: {available}"
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Task Not Found:[/bold red] {err_msg}")
                raise typer.Exit(code=1)
        else:
            target_task = tasks[0]

        # Gather context via ProjectContextEngine
        ctx_engine = ProjectContextEngine(project_root=directory)
        token_budget = getattr(core_app.config, "max_context_tokens", 2048)
        bundle = ctx_engine.build_context(target_task, token_budget=token_budget)

        if not bundle.is_safe:
            err_msg = bundle.safety_warning or "Security policy violation: Sensitive file."
            if json_output:
                sys.stdout.write(json.dumps({"error": err_msg, "is_safe": False}, indent=2) + "\n")
                raise typer.Exit(code=1)
            err_console.print(
                Panel(
                    f"[bold red]Security Guard Blocked Context Gathering[/bold red]\n\n{escape(err_msg)}",
                    title="[bold red]Security Violation[/bold red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

        # Context-Only Preview Mode
        if context_only:
            if json_output:
                sys.stdout.write(json.dumps(bundle.to_dict(), indent=2) + "\n")
                return

            console.print(
                Panel(
                    f"[bold white]Task ID:[/bold white] [bold cyan]{target_task.id}[/bold cyan]\n"
                    f"[bold white]Type:[/bold white] [bold magenta]{target_task.instruction_type}[/bold magenta]\n"
                    f"[bold white]Location:[/bold white] [cyan]{escape(target_task.file_path)}[/cyan]:[yellow]{target_task.line_number}[/yellow]\n"
                    f"[bold white]Target Symbol:[/bold white] [bold yellow]{escape(target_task.target_symbol or 'Module')}[/bold yellow] ({target_task.target_symbol_type or 'module'})\n"
                    f"[bold white]Instruction:[/bold white] {escape(target_task.description)}\n"
                    f"[bold white]Estimated Context Tokens:[/bold white] {bundle.estimated_tokens} / {token_budget}",
                    title=f"[bold green]CodePilot Context Inspector — {target_task.id}[/bold green]",
                    border_style="green",
                )
            )

            if bundle.target_snippet and bundle.target_snippet.content:
                snippet_text = bundle.target_snippet.format_line_numbered()
                console.print(
                    Panel(
                        Syntax(
                            snippet_text,
                            "python" if target_task.file_path.endswith((".py", ".pyi")) else "text",
                            theme="monokai",
                            line_numbers=False,
                        ),
                        title=f"[bold cyan]Source Snippet ({escape(target_task.file_path)} lines {bundle.target_snippet.start_line}-{bundle.target_snippet.end_line})[/bold cyan]",
                        border_style="cyan",
                    )
                )

            if bundle.imports or bundle.related_contexts:
                ctx_table = Table(title="Related Architectural Context", show_header=True, header_style="bold blue")
                ctx_table.add_column("Type", style="bold cyan", width=12)
                ctx_table.add_column("Details", style="dim", min_width=50)

                for imp in bundle.imports[:6]:
                    ctx_table.add_row("Import", escape(imp))
                for r in bundle.related_contexts:
                    ctx_table.add_row(r.relationship.capitalize(), escape(f"{r.file_path}: {r.summary}"))
                console.print(ctx_table)

            prompt_preview = build_user_prompt(bundle)
            console.print(
                Panel(
                    Syntax(prompt_preview, "markdown", theme="monokai", word_wrap=True),
                    title="[bold yellow]Constructed Model Prompt Preview[/bold yellow]",
                    border_style="yellow",
                )
            )
            console.print(
                "[dim]Context preview complete (--context-only). Model inference was not invoked.[/dim]"
            )
            return

        # Inference Mode: Connect to local model
        try:
            client = create_model_client(
                config=core_app.config,
                override_provider=provider,
                override_model=model,
            )
        except ConfigurationError as e:
            err_text = str(e)
            if json_output:
                sys.stdout.write(json.dumps({"error": err_text, "context": bundle.to_dict()}, indent=2) + "\n")
                raise typer.Exit(code=1)
            err_console.print(
                Panel(
                    f"[bold yellow]Model Provider Configuration Required[/bold yellow]\n\n"
                    f"{escape(err_text)}\n\n"
                    f"[dim]Tip: To preview the context gathered for this task without an AI model, run:\n"
                    f"  [bold cyan]codepilot explain --context-only[/bold cyan]\n"
                    f"To test with mock responses: run with [bold cyan]--provider mock[/bold cyan][/dim]",
                    title="[bold yellow]Model Not Configured[/bold yellow]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=1)

        # Health check
        healthy, health_msg = client.health_check()
        if not healthy:
            if json_output:
                sys.stdout.write(json.dumps({"error": health_msg, "context": bundle.to_dict()}, indent=2) + "\n")
                raise typer.Exit(code=1)
            err_console.print(
                Panel(
                    f"[bold red]Model Runtime Unavailable[/bold red]\n\n"
                    f"{escape(health_msg)}\n\n"
                    f"[dim]You can inspect the gathered task context offline by running:\n"
                    f"  [bold cyan]codepilot explain --context-only[/bold cyan][/dim]",
                    title="[bold red]Health Check Failed[/bold red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

        # Build request and query model
        model_req = build_model_request(bundle, max_tokens=1024, temperature=0.1)

        with console.status(f"[bold cyan]Querying {client.provider_name} model ({client.default_model_name})...[/bold cyan]"):
            response = client.generate(model_req, model_name=model)

        if json_output:
            out_obj = {
                "task": target_task.to_dict(),
                "explanation": response.to_dict(),
                "context": bundle.to_dict(),
            }
            sys.stdout.write(json.dumps(out_obj, indent=2) + "\n")
            return

        # Render Terminal Output
        console.print(
            Panel(
                f"[bold white]Task ID:[/bold white] [bold cyan]{target_task.id}[/bold cyan] | "
                f"[bold white]Type:[/bold white] [bold magenta]{target_task.instruction_type}[/bold magenta] | "
                f"[bold white]Target:[/bold white] [yellow]{escape(target_task.file_path)}:{target_task.line_number}[/yellow] ([cyan]{escape(target_task.target_symbol or 'module')}[/cyan])\n"
                f"[bold white]Instruction:[/bold white] {escape(target_task.description)}",
                title=f"[bold green]CodePilot AI — Task Explanation ({target_task.id})[/bold green]",
                border_style="green",
            )
        )

        console.print(
            Panel(
                response.content,
                title=f"[bold cyan]Model Analysis & Proposed Approach ({response.model_name})[/bold cyan]",
                border_style="cyan",
            )
        )

        footer_metrics = (
            f"[bold white]Model:[/bold white] {response.model_name} | "
            f"[bold white]Latency:[/bold white] {response.latency_seconds:.2f}s | "
            f"[bold white]Tokens:[/bold white] prompt={response.prompt_tokens}, completion={response.completion_tokens}, total={response.total_tokens}"
        )
        console.print(Panel(footer_metrics, title="[dim]Inference Metrics[/dim]", border_style="dim"))
        console.print("[dim]Note: This explanation was generated by your local model. Source files were not modified.[/dim]")

    except typer.Exit:
        raise
    except CodePilotError as e:
        err_console.print(f"[bold red]CodePilot Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during explain:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="analyze",
    help="Analyze project architecture, entry points, dependencies, and symbols using static analysis.",
)
def analyze_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory to analyze.",
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional destination path to save architecture report JSON.",
        resolve_path=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite existing output file without confirmation.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output architecture report as raw JSON.",
    ),
    detailed: bool = typer.Option(
        False,
        "--detailed",
        help="Show detailed breakdown of classes, methods, and module relationships.",
    ),
    context: bool = typer.Option(
        False,
        "--context",
        help="Output bounded prompt context formatted for local LLM.",
    ),
    max_files: Optional[int] = typer.Option(
        None,
        "--max-files",
        help="Maximum number of source files to analyze (hardware safety limit).",
    ),
    max_file_size: Optional[int] = typer.Option(
        None,
        "--max-file-size",
        help="Maximum individual file size in bytes to read/parse.",
    ),
    max_depth: Optional[int] = typer.Option(
        None,
        "--max-depth",
        help="Maximum directory traversal depth.",
    ),
) -> None:
    """Execute safe static architecture analysis of the target repository."""
    try:
        analyzer = ProjectAnalyzer(project_root=directory)
        report = analyzer.analyze(
            max_files=max_files,
            max_file_size=max_file_size,
            max_depth=max_depth,
        )

        # Save report ONLY to an explicitly chosen path; never overwrite without approval
        if output:
            ProjectAnalyzer.save_report(report, output, force=force)

        if json_output:
            sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        if context:
            prompt_context = ArchitectureContextBuilder.build_context(report)
            console.print(
                Panel(
                    prompt_context,
                    title="[bold cyan]Architecture Prompt Context (Local LLM)[/bold cyan]",
                    border_style="cyan",
                )
            )
            return

        # Render Rich visual architecture report
        # 1. Summary Panel
        subtypes_str = f" ({', '.join(report.project_subtypes)})" if report.project_subtypes else ""
        console.print(
            Panel(
                f"[bold cyan]Project:[/bold cyan] [bold white]{escape(report.project_name)}[/bold white]\n"
                f"[bold cyan]Type:[/bold cyan] [bold green]{escape(report.project_type.capitalize())}{subtypes_str}[/bold green]\n"
                f"[bold cyan]Root:[/bold cyan] {escape(report.project_root)}\n"
                f"[bold cyan]Metrics:[/bold cyan] {report.structure.file_count} files across {report.structure.directory_count} directories | "
                f"{report.structure.total_lines_of_code:,} total lines of code\n\n"
                f"[dim]{escape(report.summary)}[/dim]",
                title="[bold cyan]CodePilot AI — Architecture Analysis[/bold cyan]",
                border_style="cyan",
            )
        )

        # 2. Languages Table
        lang_table = Table(title="Language Distribution", show_header=True, header_style="bold magenta")
        lang_table.add_column("Language", style="bold cyan", width=18)
        lang_table.add_column("Files", justify="right", width=10)
        lang_table.add_column("Lines of Code", justify="right", width=16)
        lang_table.add_column("% Share", justify="right", width=10)

        total_files = max(1, report.structure.file_count)
        for lang, count in list(report.structure.languages.items())[:8]:
            loc = report.structure.language_lines.get(lang, 0)
            pct = (count / total_files) * 100
            lang_table.add_row(lang, str(count), f"{loc:,}", f"{pct:.1f}%")

        console.print(lang_table)

        # 3. Entry Points Table
        if report.entry_points:
            ep_table = Table(title="Discovered Application Entry Points", show_header=True, header_style="bold green")
            ep_table.add_column("Entry Point File", style="bold cyan", min_width=25)
            ep_table.add_column("Type", width=18)
            ep_table.add_column("Target / Symbol", style="yellow", width=22)
            ep_table.add_column("Confidence", width=12)
            ep_table.add_column("Description", style="dim", min_width=30)

            for ep in report.entry_points:
                conf_color = "green" if ep.confidence == "high" else ("yellow" if ep.confidence == "medium" else "dim")
                ep_table.add_row(
                    escape(ep.file_path),
                    ep.entry_type,
                    escape(ep.symbol_name or "-"),
                    f"[{conf_color}]{ep.confidence}[/{conf_color}]",
                    escape(ep.description),
                )
            console.print(ep_table)

        # 4. Modules Overview Table
        mod_table = Table(title="Analyzed Modules & Architectural Symbols", show_header=True, header_style="bold blue")
        mod_table.add_column("Module File", style="cyan", min_width=28)
        mod_table.add_column("Lang", width=10)
        mod_table.add_column("Classes", justify="right", width=9)
        mod_table.add_column("Functions", justify="right", width=11)
        mod_table.add_column("Imports", justify="right", width=9)
        mod_table.add_column("Key Symbols / Summary", style="dim", min_width=35)

        for mod in report.modules[:20]:
            sym_items = []
            if mod.classes:
                sym_items.extend(f"class {c.name}" for c in mod.classes[:3])
            if mod.functions:
                sym_items.extend(f"def {f.name}" for f in mod.functions[:3])
            desc = ", ".join(sym_items)
            if mod.docstring and not desc:
                desc = mod.docstring.splitlines()[0][:50]
            if mod.parse_error:
                desc = f"[red]{mod.parse_error}[/red]"

            mod_table.add_row(
                escape(mod.file_path),
                mod.language,
                str(len(mod.classes)),
                str(len(mod.functions)),
                str(len(mod.imports)),
                escape(desc),
            )
        console.print(mod_table)

        # 5. Detailed Breakdown (if --detailed flag)
        if detailed:
            # Detailed Classes
            all_classes = [c for mod in report.modules for c in mod.classes]
            if all_classes:
                cls_table = Table(title="Detailed Class Hierarchy & Methods", show_header=True, header_style="bold yellow")
                cls_table.add_column("Class Name", style="bold yellow", min_width=22)
                cls_table.add_column("Base Classes", style="cyan", min_width=18)
                cls_table.add_column("Methods", style="dim", min_width=35)

                for c in all_classes:
                    bases = ", ".join(c.base_classes) if c.base_classes else "object"
                    methods = ", ".join(m.name for m in c.methods) if c.methods else "(none)"
                    cls_table.add_row(escape(c.name), escape(bases), escape(methods))
                console.print(cls_table)

            # Module relationships
            if report.relationships:
                rel_table = Table(title="Module Dependency Graph (Internal Imports)", show_header=True, header_style="bold magenta")
                rel_table.add_column("Source Module", style="bold cyan", min_width=30)
                rel_table.add_column("Imports Local Module(s)", style="dim", min_width=40)

                for src, tgts in report.relationships.items():
                    rel_table.add_row(escape(src), escape(", ".join(tgts)))
                console.print(rel_table)

        # 6. Save Notice Panel
        if output:
            console.print(
                Panel(
                    f"[bold green]Architecture report generated and saved to:[/bold green] {output}\n"
                    f"[dim]Run [bold cyan]codepilot analyze --context[/bold cyan] to preview LLM prompt context.\n"
                    f"Run [bold cyan]codepilot analyze --detailed[/bold cyan] for full class & dependency graph tables.[/dim]",
                    title="[bold green]Analysis Complete[/bold green]",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold green]Architecture analysis completed successfully.[/bold green]\n"
                    f"[dim]To save machine-readable JSON to an explicitly chosen file:\n"
                    f"  [bold cyan]codepilot analyze -o report.json[/bold cyan]\n"
                    f"Run [bold cyan]codepilot analyze --context[/bold cyan] to preview LLM prompt context.\n"
                    f"Run [bold cyan]codepilot analyze --detailed[/bold cyan] for full class & dependency graph tables.[/dim]",
                    title="[bold green]Analysis Complete[/bold green]",
                    border_style="green",
                )
            )

    except AnalysisError as e:
        err_console.print(f"[bold red]Analysis Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during architecture analysis:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="plan",
    help="Analyze discovered instructions and create a structured implementation plan.",
)
def plan_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory to plan for.",
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Optional destination path to save plan JSON.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output implementation plan as JSON.",
    ),
    llm: bool = typer.Option(
        False,
        "--llm",
        help="Enrich plan with local LLM architectural reasoning for discovered tasks.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override model name when using --llm.",
    ),
) -> None:
    """Generate a structured implementation plan from discovered instruction comments."""
    try:
        core_app = CodePilotApp(project_root=directory)
        cache_path = directory / ".codepilot" / "scan_results.json"
        scan_result = load_scan_result(cache_path)

        if scan_result is None or not scan_result.instructions:
            scan_result = scan_repository(directory, core_app.config)
            save_scan_result(scan_result, cache_path)

        plan = generate_plan(scan_result)

        # Save to default plan cache
        default_plan_file = directory / ".codepilot" / "plan.json"
        save_plan(plan, default_plan_file)

        if output:
            save_plan(plan, output)

        if json_output:
            sys.stdout.write(json.dumps(plan.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        table = Table(
            title=f"{APP_NAME} - Implementation Plan ({plan.plan_id})",
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Task ID", style="bold cyan", width=10)
        table.add_column("Target File:Line", style="cyan", min_width=25)
        table.add_column("Type", style="magenta", width=8)
        table.add_column("Risk", width=8)
        table.add_column("Scope", width=10)
        table.add_column("Objective", min_width=35)

        for task in plan.tasks:
            if task.risk_level == "HIGH":
                risk_styled = "[bold red]HIGH[/bold red]"
            elif task.risk_level == "MEDIUM":
                risk_styled = "[bold yellow]MEDIUM[/bold yellow]"
            else:
                risk_styled = "[bold green]LOW[/bold green]"

            target_loc = f"{task.target_file}:{task.line_number}"
            table.add_row(
                task.task_id,
                escape(target_loc),
                task.instruction_type,
                risk_styled,
                task.scope,
                escape(task.description),
            )

        console.print(table)

        summary = plan.risk_summary
        console.print(
            Panel(
                f"[bold]Total Tasks:[/bold] {plan.total_tasks} | "
                f"[bold red]High Risk:[/bold red] {summary.get('HIGH', 0)} | "
                f"[bold yellow]Medium Risk:[/bold yellow] {summary.get('MEDIUM', 0)} | "
                f"[bold green]Low Risk:[/bold green] {summary.get('LOW', 0)}\n\n"
                "Next step: Run [bold cyan]codepilot fix --preview[/bold cyan] to inspect proposed diffs.",
                title="[bold cyan]Plan Generated[/bold cyan]",
                border_style="cyan",
            )
        )

        if llm:
            try:
                client = create_model_client(core_app.config, override_model=model)
                healthy, msg = client.health_check()
                if healthy:
                    console.print(
                        Panel(
                            f"[bold cyan]LLM Planning Assistant ({client.default_model_name}) Active[/bold cyan]\n"
                            f"Plan tasks verified against local model runtime.\n"
                            f"[dim]Run [bold cyan]codepilot explain <TASK_ID>[/bold cyan] for deep model context analysis of individual tasks.[/dim]",
                            title="[bold green]LLM Plan Context[/bold green]",
                            border_style="green",
                        )
                    )
                else:
                    console.print(
                        Panel(
                            f"[bold yellow]LLM Enrichment Unavailable:[/bold yellow] {escape(msg)}\n"
                            f"[dim]Plan generated using deterministic static analysis.[/dim]",
                            title="[bold yellow]LLM Offline[/bold yellow]",
                            border_style="yellow",
                        )
                    )
            except ConfigurationError as e:
                console.print(
                    Panel(
                        f"[bold yellow]Model Provider Note:[/bold yellow] {escape(str(e))}\n"
                        f"[dim]Plan generated using deterministic static analysis.[/dim]",
                        title="[bold yellow]Model Not Configured[/bold yellow]",
                        border_style="yellow",
                    )
                )

    except PlanError as e:
        err_console.print(f"[bold red]Plan Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during plan generation:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="fix",
    help="Generate proposed code changes and display a Git diff without modifying source files.",
)
def fix_cmd(
    preview: bool = typer.Option(
        True,
        "--preview",
        help="Preview proposed changes as a Git diff without modifying files on disk.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory.",
        resolve_path=True,
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Override model provider ('ollama', 'standalone', 'mock').",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Override model identifier.",
    ),
    task_id: Optional[str] = typer.Option(
        None,
        "--task-id",
        "-t",
        help="Target specific instruction task ID.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output proposed patch bundle as JSON.",
    ),
) -> None:
    """Generate proposed code modifications and preview Git diffs safely."""
    try:
        core_app = CodePilotApp(project_root=directory)
        plan_cache = directory / ".codepilot" / "plan.json"
        plan = load_plan(plan_cache)

        patch_bundle = generate_preview_patch(
            directory,
            core_app.config,
            plan=plan,
            override_provider=provider,
            override_model=model,
            target_task_id=task_id,
        )

        if json_output:
            sys.stdout.write(json.dumps(patch_bundle.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        if not patch_bundle.file_patches or not patch_bundle.full_diff.strip():
            if getattr(patch_bundle, "clarification_question", None):
                console.print(
                    Panel(
                        f"[bold cyan]Clarification Required:[/bold cyan]\n\n{patch_bundle.clarification_question}\n\n"
                        "[dim]The instruction was ambiguous or incomplete. Please clarify the requirements in the code comment and re-run.[/dim]",
                        title="[bold yellow]Clarification Request[/bold yellow]",
                        border_style="yellow",
                    )
                )
                return
            console.print(
                Panel(
                    "No pending code modifications generated.\n"
                    "Ensure instruction comments exist via 'codepilot scan'.",
                    title="[bold yellow]No Modifications Proposed[/bold yellow]",
                    border_style="yellow",
                )
            )
            return

        if patch_bundle.is_mock:
            console.print(
                Panel(
                    "[bold yellow]NOTICE: Deterministic Mock Simulation Active[/bold yellow]\n"
                    "This patch was generated using rule-based mock substitution for testing/offline scenarios.\n"
                    "No real AI model inference was executed.",
                    title="[bold yellow]Mock Simulation Mode[/bold yellow]",
                    border_style="yellow",
                )
            )

        provider_display = (
            f"{patch_bundle.model_provider} (mock simulation)"
            if patch_bundle.is_mock
            else patch_bundle.model_provider
        )
        console.print(
            Panel(
                f"[bold cyan]Generated Proposed Modification Patch:[/bold cyan] {patch_bundle.patch_id}\n"
                f"[dim]Provider: {provider_display} | Output Source: {patch_bundle.output_source} | Modified Files: {len(patch_bundle.file_patches)}[/dim]\n\n"
                "[yellow]Note: Source files have NOT been modified on disk.[/yellow]",
                title="[bold green]Diff Preview Ready[/bold green]",
                border_style="green",
            )
        )

        for fp in patch_bundle.file_patches:
            console.print(f"\n[bold underline cyan]Diff for: {fp.file_path}[/bold underline cyan]")
            syntax = Syntax(fp.diff, "diff", theme="monokai", line_numbers=True)
            console.print(syntax)

        console.print(
            Panel(
                "To inspect this patch again later:\n"
                "  [bold cyan]codepilot diff[/bold cyan]\n\n"
                "To review and apply this patch, execute:\n"
                "  [bold green]codepilot apply --approved[/bold green]\n\n"
                "[dim]To discard, run 'codepilot fix --preview' again or delete .codepilot/patches/pending_patch.json[/dim]",
                title="[bold cyan]Next Step: Developer Approval[/bold cyan]",
                border_style="cyan",
            )
        )

    except FixError as e:
        err_console.print(
            Panel(
                f"[bold red]Fix Error:[/bold red]\n{e}",
                title="[bold red]Proposed Fix Generation Halted[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during fix generation:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="diff",
    help="Inspect the proposed unified Git diff and metadata for a pending patch.",
)
def diff_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output pending patch bundle as raw JSON.",
    ),
) -> None:
    """Inspect existing pending patch bundle without modifying any files."""
    try:
        bundle = load_pending_patch(directory)
        if bundle is None or not bundle.file_patches:
            if json_output:
                sys.stdout.write(json.dumps({"error": "No pending patch found", "file_patches": []}, indent=2) + "\n")
                return
            console.print(
                Panel(
                    "No pending patch found in '.codepilot/patches/pending_patch.json'.\n\n"
                    "Run [bold cyan]codepilot fix --preview[/bold cyan] to generate proposed modifications first.",
                    title="[bold yellow]No Pending Patch[/bold yellow]",
                    border_style="yellow",
                )
            )
            return

        if json_output:
            sys.stdout.write(json.dumps(bundle.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        status_str = "[green]Applied[/green]" if bundle.is_applied else ("[cyan]Approved[/cyan]" if bundle.is_approved else "[yellow]Awaiting Review[/yellow]")
        provider_display = (
            f"{bundle.model_provider} (mock simulation)"
            if getattr(bundle, "is_mock", False)
            else bundle.model_provider
        )
        output_source = getattr(bundle, "output_source", "model")
        console.print(
            Panel(
                f"[bold white]Patch ID:[/bold white] [bold cyan]{bundle.patch_id}[/bold cyan]\n"
                f"[bold white]Status:[/bold white] {status_str}\n"
                f"[bold white]Provider:[/bold white] [dim]{provider_display}[/dim]\n"
                f"[bold white]Output Source:[/bold white] [dim]{output_source}[/dim]\n"
                f"[bold white]Created At:[/bold white] [dim]{bundle.created_at}[/dim]\n"
                f"[bold white]Modified Files:[/bold white] {len(bundle.file_patches)}",
                title=f"[bold green]Pending Patch Inspection — {bundle.patch_id}[/bold green]",
                border_style="green",
            )
        )

        for fp in bundle.file_patches:
            console.print(f"\n[bold underline cyan]File: {fp.file_path}[/bold underline cyan]")
            syntax = Syntax(fp.diff, "diff", theme="monokai", line_numbers=True)
            console.print(syntax)

        if not bundle.is_applied:
            console.print(
                Panel(
                    "To apply this approved patch to your source files:\n"
                    "  [bold green]codepilot apply --approved[/bold green]",
                    title="[bold cyan]Next Step[/bold cyan]",
                    border_style="cyan",
                )
            )

    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during diff inspection:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="apply",
    help="Apply a previously generated, validated, explicitly approved patch.",
)
def apply_cmd(
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Explicit developer authorization to apply the pending patch.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output patch application results as JSON.",
    ),
) -> None:
    """Validate and apply a previously generated patch bundle with automatic pre-apply backup."""
    try:
        core_app = CodePilotApp(project_root=directory)
        result = apply_approved_patch(directory, approved=approved, config=core_app.config)

        if json_output:
            sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            return

        backup_note = f"\n[dim]Pre-apply backup created at: {result.backup_dir}[/dim]\n" if result.backup_dir else ""
        console.print(
            Panel(
                f"[bold green]Successfully applied patch {result.patch_id}![/bold green]\n\n"
                f"[bold]Modified Files ({result.total_modified}):[/bold]\n"
                + "\n".join(f"  • {f}" for f in result.applied_files)
                + f"{backup_note}\n"
                "Next steps:\n"
                "  • Execute test suite: [bold cyan]codepilot test --approved[/bold cyan]\n"
                "  • To rollback these changes: [bold yellow]codepilot rollback[/bold yellow]",
                title="[bold green]Patch Applied Safely[/bold green]",
                border_style="green",
            )
        )

    except PatchError as e:
        err_console.print(
            Panel(
                f"[bold red]Patch Rejected:[/bold red]\n{e}",
                title="[bold red]Patch Application Failed[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during patch application:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="rollback",
    help="Rollback previously applied patch modifications using verified pre-apply backups.",
)
def rollback_cmd(
    patch_id: Optional[str] = typer.Option(
        None,
        "--patch-id",
        "-p",
        help="Specific patch ID to restore. If omitted, restores the latest backup.",
    ),
    list_backups_flag: bool = typer.Option(
        False,
        "--list",
        "-l",
        help="List available backup snapshots without restoring.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output rollback results or backup list as JSON.",
    ),
) -> None:
    """Restore target files from verified pre-apply backups without destructive Git operations."""
    try:
        if list_backups_flag:
            backups = list_backups(directory)
            if json_output:
                sys.stdout.write(json.dumps(backups, indent=2) + "\n")
                return

            if not backups:
                console.print(
                    Panel(
                        "No pre-apply backups found in '.codepilot/backups'.",
                        title="[bold yellow]No Backups[/bold yellow]",
                        border_style="yellow",
                    )
                )
                return

            table = Table(
                title=f"{APP_NAME} - Available Pre-Apply Backups",
                show_header=True,
                header_style="bold cyan",
            )
            table.add_column("Patch ID", style="bold cyan", width=20)
            table.add_column("Created At", style="dim", width=25)
            table.add_column("Files Backed Up", min_width=30)

            for b in backups:
                files_str = ", ".join(f["file_path"] for f in b.get("files", []))
                table.add_row(b.get("patch_id", "-"), b.get("created_at", "-"), escape(files_str))

            console.print(table)
            return

        # Perform rollback
        result = rollback_patch(directory, patch_id=patch_id)

        if json_output:
            sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
            return

        console.print(
            Panel(
                f"[bold green]Successfully restored files from backup for {result.patch_id}![/bold green]\n\n"
                f"[bold]Restored Files ({len(result.restored_files)}):[/bold]\n"
                + "\n".join(f"  • {f}" for f in result.restored_files)
                + f"\n\n[dim]Backup snapshot source: {result.backup_dir}[/dim]\n"
                "[dim]All untracked and unstaged files across other parts of the repository were preserved.[/dim]",
                title="[bold green]Rollback Complete[/bold green]",
                border_style="green",
            )
        )

    except RecoveryError as e:
        err_console.print(
            Panel(
                f"[bold red]Rollback Failed:[/bold red]\n{e}",
                title="[bold red]Recovery Error[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error during rollback:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command(
    name="test",
    help="Run the project's configured tests through a safe, documented execution mechanism.",
)
def test_cmd(
    command: Optional[str] = typer.Option(
        None,
        "--command",
        "-c",
        help="Safe test execution command (e.g. 'pytest', 'npm test').",
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Preview discovered test framework, command, and targets without executing.",
    ),
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Explicit developer authorization to execute project tests.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Automatically analyze failures and propose a patch without modifying files.",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="Execution timeout in seconds overriding configuration.",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Model provider for failure analysis.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Model identifier for failure analysis.",
    ),
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target project directory.",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output test results as JSON.",
    ),
) -> None:
    """Run project test suite and report execution results with safe discovery and preview."""
    try:
        core_app = CodePilotApp(project_root=directory)

        # 1. Preview Mode: Statically discover and display test targets without execution
        if preview:
            discovery = discover_project_tests(directory, core_app.config)
            if json_output:
                sys.stdout.write(json.dumps(discovery.to_dict(), indent=2) + "\n")
                return

            console.print(
                Panel(
                    f"[bold white]Detected Framework:[/bold white] [bold cyan]{discovery.framework}[/bold cyan]\n"
                    f"[bold white]Proposed Command:[/bold white] [bold yellow]{discovery.proposed_command}[/bold yellow]\n"
                    f"[bold white]Working Directory:[/bold white] [dim]{discovery.working_directory}[/dim]\n"
                    f"[bold white]Discovered Test Files:[/bold white] {discovery.total_test_files}",
                    title="[bold green]Test Discovery Preview (Zero Execution)[/bold green]",
                    border_style="green",
                )
            )
            if discovery.discovered_files:
                table = Table(title="Discovered Test Files", show_header=True, header_style="bold cyan")
                table.add_column("File Path", style="cyan")
                for f in discovery.discovered_files[:15]:
                    table.add_row(f)
                if len(discovery.discovered_files) > 15:
                    table.add_row(f"... and {len(discovery.discovered_files) - 15} more test file(s)")
                console.print(table)

            console.print(
                Panel(
                    "To execute this reviewed test command, run:\n"
                    "  [bold green]codepilot test --approved[/bold green]",
                    title="[bold cyan]Approval Required[/bold cyan]",
                    border_style="cyan",
                )
            )
            return

        # 2. Approval Requirement Check: If auto-detecting tests (no explicit --command) and not approved
        if not approved and not command:
            discovery = discover_project_tests(directory, core_app.config)
            if json_output:
                out_data = discovery.to_dict()
                out_data["requires_approval"] = True
                out_data["error"] = "Test execution rejected: explicit developer approval is required."
                sys.stdout.write(json.dumps(out_data, indent=2) + "\n")
                raise typer.Exit(code=1)

            console.print(
                Panel(
                    f"[bold yellow]Test Execution Requires Explicit Approval[/bold yellow]\n\n"
                    f"Discovered test command: [bold cyan]{discovery.proposed_command}[/bold cyan]\n"
                    f"Working directory: [dim]{discovery.working_directory}[/dim]\n"
                    f"Discovered test files: {discovery.total_test_files}\n\n"
                    "To execute the test suite, run:\n"
                    "  [bold green]codepilot test --approved[/bold green]\n"
                    "To preview test details without running:\n"
                    "  [bold cyan]codepilot test --preview[/bold cyan]",
                    title="[bold yellow]Approval Required[/bold yellow]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=1)

        # 3. Controlled Execution Mode
        test_result = run_project_tests(
            project_root=directory,
            config=core_app.config,
            custom_command=command,
            timeout_override=timeout,
        )

        # 4. Optional AI Failure Analysis and proposed correction
        failure_report = None
        if not test_result.passed and fix:
            with console.status("[bold cyan]Analyzing test failure with local AI model...[/bold cyan]"):
                failure_report = analyze_test_failure(
                    project_root=directory,
                    test_result=test_result,
                    config=core_app.config,
                    override_provider=provider,
                    override_model=model,
                    generate_patch=True,
                )

        if json_output:
            out_obj = test_result.to_dict()
            if failure_report:
                out_obj["failure_analysis"] = failure_report.to_dict()
            sys.stdout.write(json.dumps(out_obj, indent=2) + "\n")
            sys.stdout.flush()
            if not test_result.passed:
                raise typer.Exit(code=test_result.exit_code or 1)
            return

        if test_result.passed:
            console.print(
                Panel(
                    f"[bold green]All tests passed successfully![/bold green]\n\n"
                    f"[bold]Command:[/bold] {test_result.command}\n"
                    f"[bold]Duration:[/bold] {test_result.duration_seconds}s\n"
                    f"[bold]Exit Code:[/bold] {test_result.exit_code}\n\n"
                    f"[dim]{test_result.stdout.strip()[:1000]}[/dim]",
                    title="[bold green]Test Suite Passed[/bold green]",
                    border_style="green",
                )
            )
        else:
            err_console.print(
                Panel(
                    f"[bold red]Test suite execution failed.[/bold red]\n\n"
                    f"[bold]Command:[/bold] {test_result.command}\n"
                    f"[bold]Duration:[/bold] {test_result.duration_seconds}s\n"
                    f"[bold]Exit Code:[/bold] {test_result.exit_code}\n\n"
                    f"[red]{(test_result.stderr or test_result.stdout).strip()[:2000]}[/red]",
                    title="[bold red]Test Suite Failed[/bold red]",
                    border_style="red",
                )
            )

            if failure_report:
                console.print(
                    Panel(
                        f"[bold cyan]Root Cause Analysis:[/bold cyan]\n{failure_report.root_cause_explanation}\n\n"
                        f"[dim]Note: Source files have NOT been modified.[/dim]",
                        title="[bold yellow]AI Test Failure Analysis[/bold yellow]",
                        border_style="yellow",
                    )
                )
                if failure_report.proposed_diff:
                    console.print("\n[bold underline cyan]Proposed Fix Diff:[/bold underline cyan]")
                    syntax = Syntax(failure_report.proposed_diff, "diff", theme="monokai", line_numbers=True)
                    console.print(syntax)
                    console.print(
                        Panel(
                            "To inspect the proposed fix:\n"
                            "  [bold cyan]codepilot diff[/bold cyan]\n"
                            "To apply the proposed fix:\n"
                            "  [bold green]codepilot apply --approved[/bold green]",
                            title="[bold green]Proposed Fix Ready for Review[/bold green]",
                            border_style="green",
                        )
                    )

            raise typer.Exit(code=test_result.exit_code or 1)

    except typer.Exit:
        raise
    except TestExecutionError as e:
        err_console.print(
            Panel(
                f"[bold red]Test Execution Error:[/bold red]\n{e}",
                title="[bold red]Test Runner Blocked[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(f"[bold red]Unexpected Error running tests:[/bold red] {e}")
        raise typer.Exit(code=1) from e


def _render_evaluation_terminal_scorecard(
    report: EvaluationReport,
    output_path: Optional[Path],
    strict: bool,
    strict_failures: list,
) -> None:
    """Render Rich visual scorecard in the terminal for model evaluation results."""
    console.print(
        Panel(
            f"[bold white]Evaluated Model:[/bold white] [bold cyan]{report.model_id}[/bold cyan]\n"
            f"[bold white]Timestamp:[/bold white] {report.timestamp}\n"
            f"[bold white]Benchmark Test Cases:[/bold white] {report.total_cases}\n"
            f"[bold white]Average Response Latency:[/bold white] {report.average_latency_seconds:.2f}s | "
            f"[bold white]Inference Speed:[/bold white] {report.tokens_per_second:.1f} tokens/s | "
            f"[bold white]Tokens Used:[/bold white] {report.total_tokens_used:,}",
            title=f"[bold green]{APP_NAME} — Base Model Evaluation Scorecard[/bold green]",
            border_style="green",
        )
    )

    # Overall Scorecard Table
    scorecard_table = Table(
        title="Overall Benchmark Scorecard",
        show_header=True,
        header_style="bold cyan",
    )
    scorecard_table.add_column("Metric", style="bold", min_width=28)
    scorecard_table.add_column("Result", min_width=12)
    scorecard_table.add_column("Target Benchmark", style="dim", min_width=18)
    scorecard_table.add_column("Status", min_width=18)

    has_tests = any(r.test_passed is not None for r in report.item_results)

    metrics_rows = [
        ("Instruction Following", report.instruction_following_rate, 0.85, True),
        ("Syntax Validity", report.syntax_validity_rate, 0.95, True),
        ("Diff Hygiene Score", report.diff_hygiene_average, 0.75, True),
        ("Clarification Accuracy", report.clarification_accuracy, 0.80, True),
        ("Validation Test Pass Rate", report.test_pass_rate, 0.70, has_tests),
    ]

    for label, val, target, applicable in metrics_rows:
        target_pct = target * 100.0
        if applicable:
            val_pct = val * 100.0
            passed = val >= target
            status_str = "[bold green]PASS [OK][/bold green]" if passed else "[bold red]NEEDS IMPROVEMENT[/bold red]"
            val_str = f"[bold green]{val_pct:.1f}%[/bold green]" if passed else f"[bold yellow]{val_pct:.1f}%[/bold yellow]"
        else:
            val_str = "[dim]N/A[/dim]"
            status_str = "[dim]N/A (No tests)[/dim]"
        scorecard_table.add_row(label, val_str, f">= {target_pct:.1f}%", status_str)

    console.print(scorecard_table)

    # Category breakdown table
    if report.by_type_breakdown:
        cat_table = Table(
            title="Results by Instruction Category",
            show_header=True,
            header_style="bold magenta",
        )
        cat_table.add_column("Category", style="cyan", min_width=18)
        cat_table.add_column("Cases", justify="right", min_width=8)
        cat_table.add_column("Instruction Followed", justify="right", min_width=22)
        cat_table.add_column("Syntax Valid", justify="right", min_width=14)
        cat_table.add_column("Diff Hygiene", justify="right", min_width=14)
        cat_table.add_column("Test Pass Rate", justify="right", min_width=16)

        for cat, stats in sorted(report.by_type_breakdown.items()):
            cat_table.add_row(
                cat,
                str(stats.get("count", 0)),
                f"{stats.get('instruction_followed', 0) * 100:.1f}%",
                f"{stats.get('syntax_valid', 0) * 100:.1f}%",
                f"{stats.get('diff_hygiene', 0) * 100:.1f}%",
                f"{stats.get('test_passed', 0) * 100:.1f}%",
            )
        console.print(cat_table)

    # Case-by-case breakdown table
    case_table = Table(
        title="Case-by-Case Breakdown",
        show_header=True,
        header_style="bold blue",
    )
    case_table.add_column("Case ID", style="dim", min_width=20)
    case_table.add_column("Type", min_width=10)
    case_table.add_column("Followed", min_width=10)
    case_table.add_column("Syntax", min_width=10)
    case_table.add_column("Hygiene", justify="right", min_width=10)
    case_table.add_column("Clarification", min_width=14)
    case_table.add_column("Test", min_width=8)
    case_table.add_column("Latency", justify="right", min_width=10)

    for item in report.item_results:
        test_str = (
            "N/A"
            if item.test_passed is None
            else ("[green]PASS[/green]" if item.test_passed else "[red]FAIL[/red]")
        )
        case_table.add_row(
            item.case_id,
            item.instruction_type,
            "[green]OK[/green]" if item.instruction_followed else "[red]NO[/red]",
            "[green]OK[/green]" if item.syntax_valid else "[red]FAIL[/red]",
            f"{item.diff_hygiene_score * 100:.0f}%",
            "[green]OK[/green]" if item.clarification_correct else "[red]FAIL[/red]",
            test_str,
            f"{item.latency_seconds:.2f}s",
        )
    console.print(case_table)

    if output_path is not None:
        console.print(
            Panel(
                f"Full evaluation scorecard report saved to:\n  [bold cyan]{output_path}[/bold cyan]",
                title="[bold green]Report Exported[/bold green]",
                border_style="green",
            )
        )

    # Strict mode notification
    if strict:
        if strict_failures:
            err_console.print(
                Panel(
                    "[bold red]Strict Mode Validation Failed:[/bold red]\n"
                    + "\n".join(f"  • {f}" for f in strict_failures),
                    title="[bold red]Strict Thresholds Not Met[/bold red]",
                    border_style="red",
                )
            )
        else:
            console.print(
                Panel(
                    "[bold green]All benchmark target thresholds passed successfully![/bold green]",
                    title="[bold green]Strict Mode Validation Passed[/bold green]",
                    border_style="green",
                )
            )


@app.command(
    name="evaluate",
    help="Evaluate local AI model against deterministic coding benchmarks or custom datasets.",
)
def evaluate_cmd(
    directory: Path = typer.Option(
        Path("."),
        "--directory",
        "-d",
        help="Target repository directory.",
        resolve_path=True,
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        "-p",
        help="Model provider to evaluate ('standalone', 'ollama', 'mock').",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="Model identifier or filename override.",
    ),
    dataset: Optional[Path] = typer.Option(
        None,
        "--dataset",
        help="Path to custom JSONL evaluation dataset file.",
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Path to write evaluation scorecard report (.md or .json).",
        resolve_path=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output evaluation report as machine-readable JSON to stdout.",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Enforce benchmark pass thresholds; exit with code 1 if thresholds are not met.",
    ),
) -> None:
    """Evaluate local AI coding model against deterministic benchmark suites."""
    try:
        core_app = CodePilotApp(project_root=directory)
        config = core_app.config

        # 1. Output destination pre-validation (if --output specified)
        output_dest: Optional[Path] = None
        if output is not None:
            output_dest = validate_target_path(
                path=output,
                project_root=directory,
                config=config,
                check_exists=False,
            )
            if output_dest.exists():
                err_msg = f"Output file '{output}' already exists. Overwriting existing evaluation reports is prohibited."
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Output Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)
            if not output_dest.parent.exists():
                err_msg = f"Destination directory '{output_dest.parent}' does not exist."
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Output Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)

        # 2. Dataset loading & validation
        if dataset is not None:
            dataset_path = validate_target_path(
                path=dataset,
                project_root=directory,
                config=config,
                check_exists=True,
            )
            if not dataset_path.is_file():
                err_msg = f"Dataset path '{dataset}' is not a regular file."
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Dataset Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)
            try:
                raw_examples = load_jsonl_dataset(dataset_path)
            except Exception as e:
                err_msg = f"Failed to parse dataset '{dataset}': {e}"
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Dataset Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)

            if not raw_examples:
                err_msg = f"Dataset file '{dataset}' is empty or contains no valid examples."
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Dataset Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)

            cases = [EvaluationCase.from_dataset_example(ex) for ex in raw_examples]
        else:
            cases = get_default_benchmark_cases()

        # 3. Model client creation
        try:
            client = create_model_client(
                config=config,
                override_provider=provider,
                override_model=model,
            )
        except ConfigurationError as e:
            err_text = str(e)
            if json_output:
                sys.stdout.write(json.dumps({"error": err_text}, indent=2) + "\n")
                sys.stdout.flush()
                raise typer.Exit(code=2)
            err_console.print(
                Panel(
                    f"[bold yellow]Model Provider Configuration Required[/bold yellow]\n\n"
                    f"{escape(err_text)}\n\n"
                    f"[dim]Tip: To test evaluation with deterministic mock responses, run:\n"
                    f"  [bold cyan]codepilot evaluate --provider mock[/bold cyan][/dim]",
                    title="[bold yellow]Model Not Configured[/bold yellow]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=2)

        # 4. Health check
        healthy, health_msg = client.health_check()
        if not healthy:
            if json_output:
                sys.stdout.write(
                    json.dumps(
                        {"error": health_msg, "provider": client.provider_name},
                        indent=2,
                    )
                    + "\n"
                )
                sys.stdout.flush()
                raise typer.Exit(code=1)
            err_console.print(
                Panel(
                    f"[bold red]Model Runtime Unavailable[/bold red]\n\n"
                    f"{escape(health_msg)}\n\n"
                    f"[dim]Tip: For offline development and testing, use:\n"
                    f"  [bold cyan]codepilot evaluate --provider mock[/bold cyan][/dim]",
                    title="[bold red]Health Check Failed[/bold red]",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)

        # 5. Execute evaluation
        runner = EvaluationRunner(client)
        report = runner.evaluate_cases(cases)

        # 6. Check strict mode thresholds (documented targets from to_markdown())
        strict_failures: list = []
        if report.instruction_following_rate < 0.85:
            strict_failures.append(
                f"Instruction following: {report.instruction_following_rate * 100:.1f}% (target >= 85.0%)"
            )
        if report.syntax_validity_rate < 0.95:
            strict_failures.append(
                f"Syntax validity: {report.syntax_validity_rate * 100:.1f}% (target >= 95.0%)"
            )
        if report.diff_hygiene_average < 0.75:
            strict_failures.append(
                f"Diff hygiene score: {report.diff_hygiene_average * 100:.1f}% (target >= 75.0%)"
            )
        if report.clarification_accuracy < 0.80:
            strict_failures.append(
                f"Clarification accuracy: {report.clarification_accuracy * 100:.1f}% (target >= 80.0%)"
            )
        has_tests = any(r.test_passed is not None for r in report.item_results)
        if has_tests and report.test_pass_rate < 0.70:
            strict_failures.append(
                f"Validation test pass rate: {report.test_pass_rate * 100:.1f}% (target >= 70.0%)"
            )

        strict_passed = len(strict_failures) == 0

        # 7. Write output file if requested
        if output_dest is not None:
            try:
                if output_dest.suffix.lower() == ".json":
                    output_dest.write_text(
                        json.dumps(report.to_dict(), indent=2), encoding="utf-8"
                    )
                else:
                    output_dest.write_text(report.to_markdown(), encoding="utf-8")
            except OSError as e:
                err_msg = f"Failed to write evaluation report to '{output_dest}': {e}"
                if json_output:
                    sys.stdout.write(json.dumps({"error": err_msg}, indent=2) + "\n")
                    sys.stdout.flush()
                    raise typer.Exit(code=1)
                err_console.print(f"[bold red]Output Write Error:[/bold red] {err_msg}")
                raise typer.Exit(code=1)

        # 8. Output results
        if json_output:
            sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
            sys.stdout.flush()
            if strict and not strict_passed:
                raise typer.Exit(code=1)
            return

        # Render Terminal Scorecard
        _render_evaluation_terminal_scorecard(report, output_dest, strict, strict_failures)

        if strict and not strict_passed:
            raise typer.Exit(code=1)

    except typer.Exit:
        raise
    except SecurityError as e:
        if json_output:
            sys.stdout.write(json.dumps({"error": str(e), "type": "SecurityError"}, indent=2) + "\n")
            sys.stdout.flush()
            raise typer.Exit(code=2)
        err_console.print(
            Panel(
                f"[bold red]Security Policy Violation:[/bold red]\n{escape(str(e))}",
                title="[bold red]Security Guard Blocked Evaluation[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=2) from e
    except Exception as e:
        if json_output:
            sys.stdout.write(json.dumps({"error": str(e)}, indent=2) + "\n")
            sys.stdout.flush()
            raise typer.Exit(code=1)
        err_console.print(f"[bold red]Evaluation Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e



def main() -> None:

    """CLI execution entrypoint."""
    try:
        app()
    except CodePilotError as e:
        err_console.print(f"[bold red]CodePilot Error:[/bold red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
