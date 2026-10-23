"""AI Test failure analysis and error correction engine for CodePilot AI.

Analyzes test execution failures, extracts stack traces and failing source context,
queries local LLMs for root cause explanation, and proposes minimal unified diffs
without modifying source files on disk.
"""

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import difflib
import json
from pathlib import Path
import re
import textwrap
from typing import Any, Dict, List, Optional

from codepilot.config import CodePilotConfig
from codepilot.core.context.engine import ProjectContextEngine
from codepilot.core.context.prompt import build_model_request
from codepilot.core.evaluation.metrics import extract_code_from_completion
from codepilot.core.fixer import (
    FilePatch,
    PatchBundle,
    compute_sha256,
    save_pending_patch,
)
from codepilot.core.models.base import BaseModelClient, ModelRequest
from codepilot.core.models.factory import create_model_client
from codepilot.core.parser.models import InstructionTask
from codepilot.core.security import validate_target_path
from codepilot.core.tester import FailureDetail, TestRunResult, parse_test_failures
from codepilot.exceptions import FixError, SecurityError
from codepilot.logging_config import get_logger

logger = get_logger("core.test_analyzer")


def _integrate_repaired_code(
    orig_content: str, extracted_code: str, failing_line: Optional[int] = None
) -> str:
    """Safely integrate extracted repair code into the original file content.

    If extracted_code is a single function/class that exists in orig_content,
    replaces only that node's line span while preserving imports, globals, and
    other functions. Otherwise returns extracted_code as full replacement.
    """
    try:
        cand_tree = ast.parse(extracted_code)
        orig_tree = ast.parse(orig_content)
    except Exception:
        return extracted_code

    if len(cand_tree.body) == 1 and isinstance(
        cand_tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ):
        cand_node = cand_tree.body[0]
        cand_name = cand_node.name

        matching_node = None
        for node in ast.walk(orig_tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == cand_name:
                if failing_line is not None and getattr(node, "lineno", 0) <= failing_line <= getattr(node, "end_lineno", 0):
                    matching_node = node
                    break
                elif matching_node is None:
                    matching_node = node

        if matching_node and hasattr(matching_node, "lineno") and hasattr(matching_node, "end_lineno"):
            orig_lines = orig_content.splitlines(keepends=True)
            start_idx = matching_node.lineno - 1
            end_idx = matching_node.end_lineno

            orig_indent = ""
            if 0 <= start_idx < len(orig_lines):
                m = re.match(r"^(\s*)", orig_lines[start_idx])
                if m:
                    orig_indent = m.group(1)

            cand_clean = textwrap.dedent(extracted_code).strip()
            rep_lines = []
            for cl in cand_clean.splitlines():
                if cl.strip():
                    rep_lines.append(f"{orig_indent}{cl}\n")
                else:
                    rep_lines.append("\n")

            new_lines = orig_lines[:start_idx] + rep_lines + orig_lines[end_idx:]
            return "".join(new_lines)

    return extracted_code



@dataclass
class FailureAnalysisReport:
    """Outcome of AI test failure analysis and proposed correction."""

    test_command: str
    failures: List[FailureDetail]
    root_cause_explanation: str
    proposed_patch_id: Optional[str] = None
    proposed_diff: Optional[str] = None
    target_file: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_command": self.test_command,
            "failures": [f.to_dict() for f in self.failures],
            "root_cause_explanation": self.root_cause_explanation,
            "proposed_patch_id": self.proposed_patch_id,
            "proposed_diff": self.proposed_diff,
            "target_file": self.target_file,
        }


def analyze_test_failure(
    project_root: Path,
    test_result: TestRunResult,
    config: CodePilotConfig,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
    generate_patch: bool = True,
) -> FailureAnalysisReport:
    """Analyze a failed test run, query local LLM, and propose a reviewable patch.

    Args:
        project_root: Repository root.
        test_result: Outcome of failed test execution.
        config: CodePilot configuration.
        override_provider: Optional provider override.
        override_model: Optional model override.
        generate_patch: Whether to generate a proposed fix patch.

    Returns:
        FailureAnalysisReport: Details of failures, AI root cause analysis, and optional patch.
    """
    root = project_root.resolve()
    failures = parse_test_failures(test_result.stdout, test_result.stderr)

    if not failures:
        # Fallback generic failure detail if regex didn't capture specific pytest lines
        failures.append(
            FailureDetail(
                test_id="test_suite_failure",
                failing_file="tests",
                failing_line=None,
                error_type="TestFailure",
                error_message="Test runner returned non-zero exit code.",
                traceback=(test_result.stderr or test_result.stdout)[:2000],
            )
        )

    primary_failure = failures[0]
    target_rel = primary_failure.failing_file

    # Check if target file exists and is within repository boundaries
    target_path: Optional[Path] = None
    try:
        target_path = validate_target_path(target_rel, root, config)
    except SecurityError as e:
        logger.warning("Target failing file '%s' violates security boundaries: %s", target_rel, e)

    explanation = ""
    proposed_diff: Optional[str] = None
    proposed_patch_id: Optional[str] = None

    provider = (override_provider or config.model_provider or "mock").strip().lower()

    if provider == "mock":
        explanation = (
            f"Hypothesis: The test '{primary_failure.test_id}' failed due to {primary_failure.error_type}: "
            f"'{primary_failure.error_message}'. "
            f"Likely cause is an unhandled boundary condition in '{primary_failure.failing_file}'."
        )
        if generate_patch and target_path and target_path.is_file():
            try:
                orig_content = target_path.read_text(encoding="utf-8")
                orig_lines = orig_content.splitlines(keepends=True)
                new_lines = list(orig_lines)
                # Append or insert automated edge case fix comment
                if primary_failure.failing_line and 0 < primary_failure.failing_line <= len(new_lines):
                    idx = primary_failure.failing_line - 1
                    indent = re.match(r"^(\s*)", new_lines[idx]).group(1) if re.match(r"^(\s*)", new_lines[idx]) else ""
                    new_lines.insert(idx, f"{indent}# Applied automated fix for: {primary_failure.error_type}\n")
                else:
                    new_lines.append(f"\n# Applied automated fix for: {primary_failure.error_type}\n")

                proposed_content = "".join(new_lines)
                diff_lines = list(
                    difflib.unified_diff(
                        orig_lines,
                        new_lines,
                        fromfile=f"a/{target_rel}",
                        tofile=f"b/{target_rel}",
                        lineterm="",
                    )
                )
                if diff_lines:
                    diff_str = "\n".join(diff_lines) + "\n"
                    proposed_diff = diff_str
                    proposed_patch_id = f"patch-fix-{int(datetime.now(timezone.utc).timestamp())}"
                    fp = FilePatch(
                        file_path=target_rel,
                        original_hash=compute_sha256(orig_content),
                        original_content=orig_content,
                        proposed_content=proposed_content,
                        diff=diff_str,
                    )
                    bundle = PatchBundle(
                        patch_id=proposed_patch_id,
                        created_at=datetime.now(timezone.utc).isoformat(),
                        project_root=str(root),
                        model_provider="mock",
                        file_patches=[fp],
                        full_diff=diff_str,
                    )
                    save_pending_patch(root, bundle)
            except Exception as e:
                logger.warning("Could not generate mock failure patch: %s", e)

    else:
        # Live / Standalone client query
        try:
            client = create_model_client(config, override_provider=provider, override_model=override_model)

            orig_content: Optional[str] = None
            if target_path and target_path.is_file():
                try:
                    orig_content = target_path.read_text(encoding="utf-8")
                except Exception as read_err:
                    logger.warning("Could not read target failing file '%s': %s", target_path, read_err)

            target_code_context = ""
            if orig_content is not None:
                target_code_context = (
                    f"Target File: {target_rel}\n"
                    f"```python\n{orig_content}\n```\n\n"
                )

            if generate_patch and orig_content is not None:
                instructions = (
                    f"1. Explain the likely root cause of this failure.\n"
                    f"2. Provide the corrected code in a ```python ... ``` code block."
                )
            else:
                instructions = "Explain the likely root cause of this failure and suggest how to resolve it."

            prompt = (
                f"A test failure occurred:\n\n"
                f"Test: {primary_failure.test_id}\n"
                f"Error: {primary_failure.error_type}: {primary_failure.error_message}\n\n"
                f"<untrusted_repository_context>\n"
                f"{target_code_context}"
                f"Traceback:\n{primary_failure.traceback}\n"
                f"</untrusted_repository_context>\n\n"
                f"{instructions}"
            )
            req = ModelRequest(
                prompt=prompt,
                system_prompt="You are an expert AI code debugger. Analyze the error and suggest minimal, correct fixes.",
                max_tokens=1024,
                temperature=0.1,
            )
            resp = client.generate(req)
            explanation = resp.completion or ""

            if generate_patch and orig_content is not None and resp and resp.completion:
                extracted = extract_code_from_completion(resp.completion)
                if extracted and extracted.strip():
                    proposed_content = _integrate_repaired_code(
                        orig_content, extracted, primary_failure.failing_line
                    )
                    if proposed_content != orig_content:
                        orig_lines = orig_content.splitlines(keepends=True)
                        new_lines = proposed_content.splitlines(keepends=True)
                        diff_lines = list(
                            difflib.unified_diff(
                                orig_lines,
                                new_lines,
                                fromfile=f"a/{target_rel}",
                                tofile=f"b/{target_rel}",
                                lineterm="",
                            )
                        )
                        if diff_lines:
                            diff_str = "\n".join(diff_lines) + "\n"
                            proposed_diff = diff_str
                            proposed_patch_id = f"patch-fix-{int(datetime.now(timezone.utc).timestamp())}"
                            fp = FilePatch(
                                file_path=target_rel,
                                original_hash=compute_sha256(orig_content),
                                original_content=orig_content,
                                proposed_content=proposed_content,
                                diff=diff_str,
                            )
                            bundle = PatchBundle(
                                patch_id=proposed_patch_id,
                                created_at=datetime.now(timezone.utc).isoformat(),
                                project_root=str(root),
                                model_provider=provider,
                                file_patches=[fp],
                                full_diff=diff_str,
                            )
                            save_pending_patch(root, bundle)
        except Exception as e:
            explanation = f"Could not complete AI failure analysis via model client: {e}"

    return FailureAnalysisReport(
        test_command=test_result.command,
        failures=failures,
        root_cause_explanation=explanation,
        proposed_patch_id=proposed_patch_id,
        proposed_diff=proposed_diff,
        target_file=target_rel if target_path and target_path.is_file() else None,
    )
