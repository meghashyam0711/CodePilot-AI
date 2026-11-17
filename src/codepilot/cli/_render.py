"""Shared terminal rendering helpers for the CodePilot CLI.

Kept separate from the command modules so that patch-review output (risk findings,
skipped tasks, evaluation scorecards) is presented identically wherever it appears.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from codepilot.core.evaluation import EvaluationReport
from codepilot.core.scanner import SOURCE_EXTENSIONS
from codepilot.version import APP_NAME
from codepilot.cli._context import console, err_console


def _render_skipped_tasks(patch_bundle: Any) -> int:
    """Report tasks that produced no usable change, so a partial run is never silent.

    A patch that covers only some of the requested tasks must say so: the developer would
    otherwise apply it believing every task had been handled. Returns the failure count.
    """
    failures = list(getattr(patch_bundle, "task_failures", []) or [])
    if not failures:
        return 0

    table = Table(
        title="Skipped Tasks — No Usable Change Produced",
        show_header=True,
        header_style="bold yellow",
    )
    table.add_column("Task", style="cyan", min_width=10)
    table.add_column("File", style="cyan", min_width=14)
    table.add_column("Reason", min_width=44)

    for failure in failures:
        reason = " ".join(str(failure.get("reason", "")).split())
        if len(reason) > 300:
            reason = reason[:297] + "..."
        table.add_row(
            escape(str(failure.get("task_id", "-"))),
            escape(str(failure.get("file_path", "-"))),
            escape(reason),
        )

    err_console.print(table)
    applied = len(getattr(patch_bundle, "file_patches", []) or [])
    err_console.print(
        Panel(
            f"[bold]{len(failures)} task(s) were skipped[/bold] and are NOT part of this patch.\n"
            f"The remaining work was staged normally across {applied} file(s).\n\n"
            "[dim]Re-run 'codepilot fix' targeting a skipped task to retry it, or address "
            "the reason above first. A larger local model usually resolves output-format "
            "failures.[/dim]",
            title="[bold yellow]Partially Completed[/bold yellow]",
            border_style="yellow",
        )
    )
    return len(failures)

def _render_patch_risk_warning(patch_bundle: Any) -> int:
    """Print a security notice for risky constructs a patch introduces.

    The two-stage approval model assumes the developer reads the diff, but a single added
    line that shells out or evaluates a string is easy to miss. This surfaces those lines
    explicitly wherever a patch is shown. Returns the number of findings reported.
    """
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for fp in getattr(patch_bundle, "file_patches", []) or []:
        for finding in getattr(fp, "risk_findings", []) or []:
            rows.append((fp.file_path, finding))

    if not rows:
        return 0

    severity_styles = {"HIGH": "bold red", "MEDIUM": "bold yellow", "LOW": "cyan"}
    has_high = any(f.get("severity") == "HIGH" for _, f in rows)

    table = Table(
        title="Security Review — Constructs Introduced By This Patch",
        show_header=True,
        header_style="bold red" if has_high else "bold yellow",
    )
    table.add_column("Severity", min_width=8)
    table.add_column("File", style="cyan", min_width=16)
    table.add_column("Line", justify="right", min_width=5)
    table.add_column("Finding", min_width=40)

    for file_path, finding in rows:
        severity = str(finding.get("severity", "LOW"))
        style = severity_styles.get(severity, "cyan")
        table.add_row(
            f"[{style}]{severity}[/{style}]",
            escape(file_path),
            str(finding.get("line_number", "-")),
            escape(str(finding.get("message", ""))),
        )

    err_console.print(table)
    err_console.print(
        Panel(
            f"[bold]{len(rows)} risky construct(s)[/bold] would be added to your codebase "
            "by this patch.\n"
            "These were not present in the original file(s). Read the highlighted lines in "
            "the diff above before approving.\n\n"
            "[dim]CodePilot does not block the patch: nothing is written to disk until you "
            "run 'apply --approved'.[/dim]",
            title=(
                "[bold red]Review Carefully Before Approving[/bold red]"
                if has_high
                else "[bold yellow]Review Before Approving[/bold yellow]"
            ),
            border_style="red" if has_high else "yellow",
        )
    )
    return len(rows)

def _looks_like_file_path(value: str) -> bool:
    """Heuristically decide whether a CLI target was meant as a file path, not prose.

    Used so that 'codepilot fix <path>' reports a mistyped or missing path instead of
    reinterpreting it as an issue description and silently fixing an unrelated file.
    A value qualifies only when it carries no whitespace (free-form descriptions do) and
    either ends in a known source extension or contains a directory separator.
    """
    candidate = value.strip()
    if not candidate or any(ch.isspace() for ch in candidate):
        return False
    if Path(candidate).suffix.lower() in SOURCE_EXTENSIONS:
        return True
    return "/" in candidate or "\\" in candidate

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
