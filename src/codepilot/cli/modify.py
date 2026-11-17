"""Code modification commands: fix, diff, apply and rollback.

These are the commands that can write to source files, and every one of them is gated
behind explicit developer approval.
"""

import ast
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
import typer
from codepilot.core.app import CodePilotApp
from codepilot.core.fixer import generate_preview_patch, load_pending_patch
from codepilot.core.parser import InstructionTask
from codepilot.core.patcher import apply_approved_patch
from codepilot.core.planner import load_plan
from codepilot.core.recovery import (
    detect_post_apply_divergence,
    list_backups,
    resolve_backup_manifest,
    rollback_patch,
)
from codepilot.core.scanner import SOURCE_EXTENSIONS, scan_repository
from codepilot.exceptions import FixError, PatchError, RecoveryError
from codepilot.version import APP_NAME
from codepilot.cli._context import app, console, err_console
from codepilot.cli._render import (
    _looks_like_file_path,
    _render_patch_risk_warning,
    _render_skipped_tasks,
)


@app.command(
    name="fix",
    help="Generate proposed code changes and display a Git diff without modifying source files.",
)
def fix_cmd(
    target: Optional[str] = typer.Argument(
        None,
        help="Target file path, issue description, or task ID to fix.",
    ),
    issue: Optional[str] = typer.Option(
        None,
        "--issue",
        "-i",
        help="Explicit issue or repair description.",
    ),
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
        root = directory.resolve()

        plan_cache = directory / ".codepilot" / "plan.json"
        plan = load_plan(plan_cache)

        adhoc_task: Optional[InstructionTask] = None
        investigation_explanation: Optional[Dict[str, Any]] = None

        # 1. Determine if an explicit task_id is supplied
        chosen_task_id = task_id
        if not chosen_task_id and target and plan and any(t.task_id == target.strip() or target.strip() in t.task_id for t in plan.tasks):
            chosen_task_id = target.strip()

        # 2. If no existing task ID, handle explicit target file or issue description
        if not chosen_task_id and (target or issue):
            target_str = (target or "").strip()
            issue_str = (issue or "").strip()

            target_file_rel = None
            target_line = 1
            prompt_desc = issue_str

            # Check if target_str points to an existing file in the project
            cand_path = root / target_str if target_str else None
            if cand_path and cand_path.is_file():
                try:
                    target_file_rel = str(cand_path.relative_to(root)).replace("\\", "/")
                except ValueError:
                    target_file_rel = target_str
                if not prompt_desc:
                    prompt_desc = f"Fix code issues and improve implementation in '{target_file_rel}'"
            elif target_str and _looks_like_file_path(target_str):
                # The target was clearly meant as a file but does not exist. Treating it as
                # free-form prose would silently retarget the fix at an unrelated file, so
                # report the bad path instead of guessing.
                raise FixError(
                    f"Target file '{target_str}' does not exist in '{root}'.\n"
                    "Check the path (it is interpreted relative to the project root), or "
                    "describe the problem instead:\n"
                    "  codepilot fix --issue '<description>'"
                )
            elif target_str:
                # target_str is an issue description
                if not prompt_desc:
                    prompt_desc = target_str
                # Search if prompt_desc mentions an existing source file
                for sf in root.rglob("*"):
                    if sf.is_file() and sf.suffix.lower() in SOURCE_EXTENSIONS:
                        rel = str(sf.relative_to(root)).replace("\\", "/")
                        if sf.name in prompt_desc or rel in prompt_desc:
                            target_file_rel = rel
                            break

            # If still no file identified, pick first finding or first discovered file
            if not target_file_rel:
                scan_res = scan_repository(root, core_app.config)
                if scan_res.static_findings:
                    target_file_rel = scan_res.static_findings[0]["file"]
                    target_line = scan_res.static_findings[0].get("line", 1)
                elif scan_res.discovered_files:
                    target_file_rel = scan_res.discovered_files[0]
                else:
                    raise FixError(f"No target source file could be resolved for '{target_str or prompt_desc}'.")

            # Check for enclosed function or symbol in target file
            target_symbol = None
            full_target_file = root / target_file_rel
            if full_target_file.is_file():
                try:
                    f_content = full_target_file.read_text(encoding="utf-8", errors="replace")
                    tree = ast.parse(f_content.lstrip("﻿"))
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if node.name.lower() in prompt_desc.lower() or not target_symbol:
                                target_symbol = node.name
                                if node.body:
                                    target_line = node.body[0].lineno
                                else:
                                    target_line = node.lineno
                except SyntaxError as syn:
                    target_line = getattr(syn, "lineno", 1) or 1
                except Exception:
                    pass

            adhoc_task = InstructionTask(
                id=f"cmd-fix-{hashlib.sha256(f'{target_file_rel}:{prompt_desc}'.encode()).hexdigest()[:8]}",
                file_path=target_file_rel,
                line_number=target_line,
                instruction_type="AI-FIX",
                description=prompt_desc,
                target_symbol=target_symbol,
                target_symbol_type="function" if target_symbol else None,
            )

            investigation_explanation = {
                "target_file": target_file_rel,
                "line": target_line,
                "suspected_error": prompt_desc,
                "evidence": f"File: {target_file_rel}:{target_line}" + (f" (symbol '{target_symbol}')" if target_symbol else ""),
                "likely_cause": f"Issue or requirement specified via CLI command: '{prompt_desc}'.",
                "proposed_correction": f"Formulate and stage candidate code patch in '{target_file_rel}'.",
            }

        # 3. If no target specified at all: inspect existing plan, comments, or static findings
        elif not chosen_task_id:
            if plan and plan.tasks:
                # Existing plan with tasks exists; will be processed directly
                pass
            else:
                scan_res = scan_repository(root, core_app.config)
                if scan_res.instructions:
                    # Comments exist; will be handled by standard plan generation
                    pass
                elif scan_res.static_findings:
                    findings = scan_res.static_findings
                    if len(findings) == 1:
                        # Exactly one finding: target is clear
                        f = findings[0]
                        target_file_rel = f["file"]
                        target_line = f.get("line", 1)
                        f_msg = f.get("message", "Static defect detected")
                        adhoc_task = InstructionTask(
                            id="finding-fix-1",
                            file_path=target_file_rel,
                            line_number=target_line,
                            instruction_type="AI-FIX",
                            description=f"Fix static finding: {f_msg}",
                            target_symbol=None,
                            target_symbol_type=None,
                        )
                        investigation_explanation = {
                            "target_file": target_file_rel,
                            "line": target_line,
                            "suspected_error": f.get("type", "static_defect"),
                            "evidence": f"{target_file_rel}:{target_line} - {f_msg}",
                            "likely_cause": f"Static analysis identified {f.get('type')}: {f_msg}",
                            "proposed_correction": f"Correct syntax/implementation in '{target_file_rel}' at line {target_line}.",
                        }
                    else:
                        # Target is ambiguous: multiple findings exist
                        table = Table(
                            title=f"{APP_NAME} - Available Static Findings (Target is Ambiguous)",
                            show_header=True,
                            header_style="bold yellow",
                        )
                        table.add_column("#", style="bold", width=4)
                        table.add_column("File", style="cyan", min_width=25)
                        table.add_column("Line", style="yellow", width=6)
                        table.add_column("Severity", width=10)
                        table.add_column("Finding Details", min_width=35)

                        for idx, f in enumerate(findings, start=1):
                            table.add_row(
                                str(idx),
                                escape(f.get("file", "")),
                                str(f.get("line", 1)),
                                f.get("severity", "warning").upper(),
                                escape(f.get("message", "")),
                            )

                        if json_output:
                            sys.stdout.write(json.dumps({
                                "ambiguous": True,
                                "message": "Multiple findings available. Specify target file or issue to fix.",
                                "findings": findings,
                            }, indent=2) + "\n")
                            return

                        console.print(table)
                        chosen_idx = None
                        if sys.stdin.isatty():
                            try:
                                val = typer.prompt(f"Select a finding number to fix (1-{len(findings)}) or 'q' to abort", default="1")
                                if val.strip().lower() not in ("q", "quit", "exit"):
                                    chosen_idx = int(val.strip()) - 1
                            except Exception:
                                pass

                        if chosen_idx is not None and 0 <= chosen_idx < len(findings):
                            f = findings[chosen_idx]
                            target_file_rel = f["file"]
                            target_line = f.get("line", 1)
                            f_msg = f.get("message", "Static defect detected")
                            adhoc_task = InstructionTask(
                                id=f"finding-fix-{chosen_idx + 1}",
                                file_path=target_file_rel,
                                line_number=target_line,
                                instruction_type="AI-FIX",
                                description=f"Fix static finding: {f_msg}",
                                target_symbol=None,
                                target_symbol_type=None,
                            )
                            investigation_explanation = {
                                "target_file": target_file_rel,
                                "line": target_line,
                                "suspected_error": f.get("type", "static_defect"),
                                "evidence": f"{target_file_rel}:{target_line} - {f_msg}",
                                "likely_cause": f"Static analysis identified {f.get('type')}: {f_msg}",
                                "proposed_correction": f"Correct syntax/implementation in '{target_file_rel}' at line {target_line}.",
                            }
                        else:
                            console.print(
                                Panel(
                                    "Multiple findings detected in repository. Please specify the target file or issue:\n"
                                    "  [bold cyan]codepilot fix <file_path> --issue '<description>'[/bold cyan]\n"
                                    "Example: [cyan]codepilot fix src/calculator.py --issue 'fix division by zero'[/cyan]",
                                    title="[bold yellow]Target Selection Required[/bold yellow]",
                                    border_style="yellow",
                                )
                            )
                            return
                else:
                    # No comments and no static findings
                    if json_output:
                        sys.stdout.write(json.dumps({
                            "status": "no_targets",
                            "message": "No actionable findings or instruction tasks detected in the project.",
                        }, indent=2) + "\n")
                        return
                    console.print(
                        Panel(
                            "No actionable static findings or instruction comments detected in the project.\n\n"
                            "To fix a specific file or issue, provide the target directly:\n"
                            "  [bold cyan]codepilot fix <file_path> --issue '<description>'[/bold cyan]\n"
                            "Example: [cyan]codepilot fix src/utils.py --issue 'handle None values safely'[/cyan]\n\n"
                            "Other available commands:\n"
                            "  • Run full static analysis: [cyan]codepilot analyze[/cyan]\n"
                            "  • Run project test suite:    [cyan]codepilot test --preview[/cyan]",
                            title="[bold yellow]No Targets Found[/bold yellow]",
                            border_style="yellow",
                        )
                    )
                    return

        # Display investigation explanation if available
        if investigation_explanation and not json_output:
            console.print(
                Panel(
                    f"[bold white]Target:[/bold white] [bold cyan]{escape(investigation_explanation['target_file'])}:{investigation_explanation['line']}[/bold cyan]\n"
                    f"[bold white]Suspected Problem:[/bold white] [yellow]{escape(investigation_explanation['suspected_error'])}[/yellow]\n\n"
                    f"[bold white]Evidence:[/bold white]\n{escape(investigation_explanation['evidence'])}\n\n"
                    f"[bold white]Likely Cause:[/bold white] {escape(investigation_explanation['likely_cause'])}\n\n"
                    f"[bold white]Proposed Correction:[/bold white] {escape(investigation_explanation['proposed_correction'])}\n\n"
                    "[dim]Generating candidate unified diff preview...[/dim]",
                    title="[bold cyan]Investigation & Proposed Fix[/bold cyan]",
                    border_style="cyan",
                )
            )

        patch_bundle = generate_preview_patch(
            directory,
            core_app.config,
            plan=plan if not adhoc_task else None,
            override_provider=provider,
            override_model=model,
            target_task_id=chosen_task_id,
            adhoc_task=adhoc_task,
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
                        "[dim]The instruction was ambiguous or incomplete. Please clarify the requirements and re-run.[/dim]",
                        title="[bold yellow]Clarification Request[/bold yellow]",
                        border_style="yellow",
                    )
                )
                return
            console.print(
                Panel(
                    "No pending code modifications generated.\n"
                    "Ensure target file and issue description are valid.",
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

        _render_patch_risk_warning(patch_bundle)
        _render_skipped_tasks(patch_bundle)

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

        _render_patch_risk_warning(bundle)
        _render_skipped_tasks(bundle)

        if not bundle.is_applied:
            console.print(
                Panel(
                    "To apply this approved patch to your source files:\n"
                    "  [bold green]codepilot apply --approved[/bold green]",
                    title="[bold cyan]Next Step[/bold cyan]",
                    border_style="cyan",
                )
            )

    except FixError as e:
        err_console.print(
            Panel(
                f"[bold red]Pending patch could not be read:[/bold red]\n{e}",
                title="[bold red]Damaged Patch File[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(code=1) from e
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

        # Capture risk metadata before applying: a successful apply consumes the pending
        # patch, and the developer should be told what was just written to their files.
        pending_risk: Optional[Any] = None
        try:
            pending_risk = load_pending_patch(directory)
        except FixError:
            pending_risk = None  # damaged patch is reported by apply_approved_patch below

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

        if pending_risk is not None and _render_patch_risk_warning(pending_risk):
            err_console.print(
                "[dim]These constructs are now present in your working tree. "
                "To undo: codepilot rollback[/dim]"
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
    approved: bool = typer.Option(
        False,
        "--approved",
        help="Explicit developer authorization to overwrite working files from the backup.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Restore even if a target file was modified after the patch was applied (discards those edits).",
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

        # Rollback writes to source files, so it requires the same explicit authorization
        # as 'apply' and 'test'. Previously a bare 'codepilot rollback' overwrote the
        # working tree immediately, which was the only mutating command with no gate.
        if not approved:
            err_console.print(
                Panel(
                    "[bold yellow]Rollback requires explicit approval.[/bold yellow]\n\n"
                    "Restoring a snapshot overwrites the current contents of every file "
                    "it covers.\n\n"
                    "Review the available snapshots first:\n"
                    "  [bold]codepilot rollback --list[/bold]\n\n"
                    "Then restore with:\n"
                    "  [bold]codepilot rollback --approved[/bold]",
                    title="[bold yellow]Approval Required[/bold yellow]",
                    border_style="yellow",
                )
            )
            raise typer.Exit(code=1)

        # Rollback overwrites the working copy of every file in the snapshot. If a file was
        # edited after the patch was applied, restoring it would silently discard that work,
        # so the developer is told and must opt in with --force.
        if not force:
            manifest = resolve_backup_manifest(directory, patch_id=patch_id)
            diverged = detect_post_apply_divergence(directory, manifest)
            if diverged:
                err_console.print(
                    Panel(
                        "[bold yellow]Rollback stopped to protect your changes.[/bold yellow]\n\n"
                        f"These file(s) were modified after patch [bold]{manifest.patch_id}[/bold] "
                        "was applied. Restoring the backup would discard those edits:\n"
                        + "\n".join(f"  • {escape(f)}" for f in diverged)
                        + "\n\nCommit or save your work first, or overwrite it deliberately:\n"
                        "  [bold]codepilot rollback --force[/bold]",
                        title="[bold yellow]Unsaved Changes Detected[/bold yellow]",
                        border_style="yellow",
                    )
                )
                raise typer.Exit(code=1)

        # Perform rollback
        result = rollback_patch(directory, patch_id=patch_id, force=force)

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
