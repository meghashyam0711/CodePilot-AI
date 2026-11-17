"""Verification commands: test execution and model benchmark evaluation.
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
from codepilot.core.app import CodePilotApp
from codepilot.core.dataset.builder import load_jsonl_dataset
from codepilot.core.evaluation import (
    EvaluationCase,
    EvaluationRunner,
    get_default_benchmark_cases,
)
from codepilot.core.models import create_model_client
from codepilot.core.security import validate_target_path
from codepilot.core.test_analyzer import analyze_test_failure
from codepilot.core.tester import discover_project_tests, run_project_tests
from codepilot.exceptions import ConfigurationError, SecurityError, TestExecutionError
from codepilot.cli._context import app, console, err_console
from codepilot.cli._render import _render_evaluation_terminal_scorecard


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

            if not discovery.is_supported:
                console.print(
                    Panel(
                        f"[bold yellow]No Test Framework or Tests Detected[/bold yellow]\n\n"
                        f"Could not automatically detect a test framework in '{directory.resolve()}'.\n"
                        "To preview or execute tests, please configure 'test_command' in '.codepilot.toml'\n"
                        "or supply an explicit test command via '--command'.\n\n"
                        "Example: [cyan]codepilot test --command \"pytest\" --preview[/cyan]",
                        title="[bold yellow]Test Framework Not Found[/bold yellow]",
                        border_style="yellow",
                    )
                )
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
            if not discovery.is_supported:
                err_msg = (
                    f"Could not detect test framework in project root '{directory.resolve()}'.\n"
                    "Configure 'test_command' in '.codepilot.toml' or supply a command via '--command'."
                )
                if json_output:
                    out_data = discovery.to_dict()
                    out_data["error"] = err_msg
                    sys.stdout.write(json.dumps(out_data, indent=2) + "\n")
                    raise typer.Exit(code=1)

                err_console.print(
                    Panel(
                        f"[bold red]Test Detection Error[/bold red]\n\n{err_msg}\n\n"
                        "Example: [cyan]codepilot test --command \"pytest\" --approved[/cyan]",
                        title="[bold red]Test Framework Not Found[/bold red]",
                        border_style="red",
                    )
                )
                raise typer.Exit(code=1)

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
