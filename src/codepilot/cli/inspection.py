"""Read-only inspection commands: scan, tasks, explain, analyze and plan.

None of these modify files on disk.
"""

import json
from pathlib import Path
import sys
from typing import Optional
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
import typer
from codepilot.core.analyzer import ArchitectureContextBuilder, ProjectAnalyzer
from codepilot.core.app import CodePilotApp
from codepilot.core.context import ProjectContextEngine, build_model_request, build_user_prompt
from codepilot.core.models import create_model_client
from codepilot.core.parser import (
    INSTRUCTION_TYPE_ALIASES,
    InstructionParserEngine,
    InstructionTask,
)
from codepilot.core.planner import generate_plan, save_plan
from codepilot.core.scanner import load_scan_result, save_scan_result, scan_repository
from codepilot.exceptions import (
    AnalysisError,
    CodePilotError,
    ConfigurationError,
    PlanError,
    ScanError,
)
from codepilot.version import APP_NAME
from codepilot.cli._context import app, console, err_console


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

        # Render Project Overview Table
        overview_table = Table(
            title=f"{APP_NAME} - Project Scan Overview",
            show_header=True,
            header_style="bold cyan",
        )
        overview_table.add_column("Scan Property", style="cyan", width=25)
        overview_table.add_column("Details", style="bold white", min_width=30)

        overview_table.add_row("Scanned Source Files", str(scan_result.scanned_files_count))
        if scan_result.file_types:
            types_str = ", ".join(f"{ext} ({cnt})" for ext, cnt in scan_result.file_types.items())
            overview_table.add_row("Supported File Types", types_str)
        if scan_result.config_files:
            overview_table.add_row("Config Files Discovered", ", ".join(scan_result.config_files[:5]))
        if scan_result.test_directories:
            overview_table.add_row("Test Directories", ", ".join(scan_result.test_directories))
        if scan_result.skipped_files:
            overview_table.add_row("Skipped / Excluded Files", f"{len(scan_result.skipped_files)} file(s)")

        console.print(overview_table)

        # Render static analysis findings if detected
        if scan_result.static_findings:
            findings_table = Table(
                title=f"{APP_NAME} - Static Code Health & Defect Observations",
                show_header=True,
                header_style="bold red",
            )
            findings_table.add_column("File", style="cyan", min_width=25)
            findings_table.add_column("Line", style="bold yellow", width=6)
            findings_table.add_column("Type", style="bold magenta", width=14)
            findings_table.add_column("Severity", width=10)
            findings_table.add_column("Finding Details", min_width=35)

            for finding in scan_result.static_findings:
                sev = finding.get("severity", "warning")
                sev_color = "red" if sev == "error" else "yellow"
                findings_table.add_row(
                    escape(str(finding.get("file", "-"))),
                    str(finding.get("line", 1)),
                    escape(str(finding.get("type", "static_issue"))),
                    f"[{sev_color}]{sev.upper()}[/{sev_color}]",
                    escape(str(finding.get("message", ""))),
                )
            console.print(findings_table)

        # Render discovered AI comments (optional task hints)
        if scan_result.instructions:
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

            counts_str = ""
            if scan_result.counts_by_type:
                counts_str = "\n[bold cyan]Counts:[/bold cyan] " + " | ".join(
                    f"[bold white]{k}:[/bold white] {v}" for k, v in scan_result.counts_by_type.items()
                ) + "\n"
            console.print(
                Panel(
                    f"[bold green]Discovered {len(scan_result.instructions)} AI instruction comment(s)[/bold green] "
                    f"across {scan_result.scanned_files_count} scanned files.{counts_str}\n"
                    "Note: In-code instruction comments are optional task hints.\n"
                    "Next step: Run [bold cyan]codepilot tasks[/bold cyan] to inspect symbol mapping,\n"
                    "or run [bold cyan]codepilot plan[/bold cyan] to generate an implementation plan.",
                    title="[bold green]Scan Complete[/bold green]",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel(
                    f"Scanned {scan_result.scanned_files_count} files in '{directory}'.\n"
                    "No AI instruction comments found (special comments are optional).\n\n"
                    "You can run command-driven workflows directly:\n"
                    "  • [cyan]codepilot analyze[/cyan]              Inspect architecture, dependencies & symbols\n"
                    "  • [cyan]codepilot fix <file> --issue <msg>[/cyan]  Investigate and preview fixes\n"
                    "  • [cyan]codepilot test --preview[/cyan]          Discover and review test suite\n\n"
                    "[dim]Optional comment hints: # AI-FIX: ..., # AI-TASK: ...[/dim]",
                    title="[bold green]Scan Complete — Ready for Commands[/bold green]",
                    border_style="green",
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

        # 6. Code Quality Concerns & Static Analysis Observations
        static_concerns = []
        for mod in report.modules:
            if mod.parse_error:
                static_concerns.append((mod.file_path, "Parse/Syntax Error", mod.parse_error))
        for w in report.warnings:
            if not any(w in sc[2] for sc in static_concerns):
                static_concerns.append(("-", "Observation / Exclusion", w))

        if static_concerns:
            concerns_table = Table(
                title=f"{APP_NAME} - Code Quality Concerns & Static Analysis Observations",
                show_header=True,
                header_style="bold red",
            )
            concerns_table.add_column("Location / File", style="cyan", min_width=25)
            concerns_table.add_column("Category", style="yellow", width=25)
            concerns_table.add_column("Details", min_width=40)

            for loc, cat, det in static_concerns:
                concerns_table.add_row(escape(loc), escape(cat), escape(det))
            console.print(concerns_table)

        # 7. Save Notice Panel
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
