"""Fix engine for CodePilot AI.

Generates proposed code modifications for discovered AI instruction comments
and formats standard unified Git diffs without modifying source files on disk.
Enforces security boundaries, path traversal guards, and protected-file rules.
"""

import ast
import copy
from dataclasses import asdict, dataclass, field, fields as dataclasses_fields
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import textwrap
from typing import Any, Dict, List, Optional, Tuple

from codepilot.config import CodePilotConfig
from codepilot.core.locking import acquire_lock, release_lock
from codepilot.core.planner import Plan
from codepilot.core.scanner import scan_repository
from codepilot.core.security import validate_target_path
from codepilot.exceptions import FixError, SecurityError
from codepilot.logging_config import get_logger

logger = get_logger("core.fixer")

PATCHES_DIR = ".codepilot/patches"
PENDING_PATCH_FILENAME = "pending_patch.json"

# Advisory lock serializing fix generation within a repository, so concurrent runs
# cannot race on the shared pending patch bundle.
FIX_LOCK_FILENAME = ".codepilot/fix.lock"


@dataclass
class FilePatch:
    """Contains patch details for a single target file."""

    file_path: str
    original_hash: str
    original_content: str
    proposed_content: str
    diff: str
    instruction_type: Optional[str] = None
    # Risky constructs this patch introduces (see core.patch_risk). Advisory metadata that
    # travels with the patch so 'diff' and 'apply' can surface it as prominently as the
    # preview did, rather than relying on the reviewer having noticed it the first time.
    risk_findings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilePatch":
        # Unknown keys are ignored rather than raising TypeError, so a patch bundle
        # written by a different CodePilot version still loads instead of being
        # reported to the developer as "no pending patch found".
        known = {f.name for f in dataclasses_fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PatchBundle:
    """Represents a validated bundle of proposed modifications awaiting approval."""

    patch_id: str
    created_at: str
    project_root: str
    model_provider: str
    file_patches: List[FilePatch]
    full_diff: str
    is_approved: bool = False
    is_applied: bool = False
    is_mock: bool = False
    output_source: str = "model"
    status: str = "success"
    clarification_question: Optional[str] = None
    # Tasks (or whole files) that could not be turned into a usable change. Recorded so a
    # partially successful run can still be staged while telling the developer exactly
    # what was skipped and why.
    task_failures: List[Dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.model_provider == "mock":
            self.is_mock = True
            if self.output_source == "model":
                self.output_source = "mock"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "patch_id": self.patch_id,
            "created_at": self.created_at,
            "project_root": self.project_root,
            "model_provider": self.model_provider,
            "file_patches": [fp.to_dict() for fp in self.file_patches],
            "full_diff": self.full_diff,
            "is_approved": self.is_approved,
            "is_applied": self.is_applied,
            "is_mock": self.is_mock,
            "output_source": self.output_source,
            "status": self.status,
        }
        if self.clarification_question is not None:
            d["clarification_question"] = self.clarification_question
        if self.task_failures:
            d["task_failures"] = list(self.task_failures)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatchBundle":
        file_patches = [FilePatch.from_dict(fp) for fp in data.get("file_patches", [])]
        provider = data.get("model_provider", "")
        is_mock_default = (provider == "mock")
        is_mock = data.get("is_mock", is_mock_default)
        output_source = data.get("output_source", "mock" if is_mock else "model")
        return cls(
            patch_id=data.get("patch_id", ""),
            created_at=data.get("created_at", ""),
            project_root=data.get("project_root", ""),
            model_provider=provider,
            file_patches=file_patches,
            full_diff=data.get("full_diff", ""),
            is_approved=data.get("is_approved", False),
            is_applied=data.get("is_applied", False),
            is_mock=is_mock,
            output_source=output_source,
            status=data.get("status", "success"),
            clarification_question=data.get("clarification_question"),
            task_failures=data.get("task_failures", []) or [],
        )



def compute_sha256(content: str) -> str:
    """Compute SHA256 hex digest of string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class DocstringStripper(ast.NodeTransformer):
    """AST transformer that removes docstrings from modules, classes, and functions."""

    def _strip_docstring(self, body: List[ast.stmt]) -> List[ast.stmt]:
        if not body:
            return body
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            return body[1:]
        return body

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        self.generic_visit(node)
        node.body = self._strip_docstring(node.body)
        return node


def verify_executable_ast_invariance(orig_code: str, proposed_code: str) -> Tuple[bool, str]:
    """Verify that proposed_code only modifies or adds docstrings or comments.

    Strips docstrings from both ASTs and compares the normalized executable AST dumps.
    Any difference in imports, signatures, decorators, expressions, or control flow
    fails validation.

    Args:
        orig_code: Original Python source string.
        proposed_code: Candidate Python source string.

    Returns:
        (is_invariant, reason)
    """
    try:
        orig_tree = ast.parse(orig_code.lstrip("﻿"))
    except SyntaxError as e:
        return False, f"Original code has syntax error: {e}"

    try:
        proposed_tree = ast.parse(proposed_code.lstrip("﻿"))
    except SyntaxError as e:
        return False, f"Proposed code has syntax error: {e}"

    orig_stripped = DocstringStripper().visit(copy.deepcopy(orig_tree))
    proposed_stripped = DocstringStripper().visit(copy.deepcopy(proposed_tree))

    orig_dump = ast.dump(orig_stripped, include_attributes=False)
    proposed_dump = ast.dump(proposed_stripped, include_attributes=False)

    if orig_dump != proposed_dump:
        return False, "Executable AST differs between original and proposed code."

    return True, "Executable AST is identical (documentation-only change verified)."


def extract_documentation_content(
    clean_code: str,
    target_symbol: Optional[str] = None,
) -> Tuple[bool, str, bool]:
    """Extract strictly the docstring or comments from model output for AI-DOCS tasks.

    Handles outputs containing:
    1. Pure comments (# ...)
    2. Pure docstrings (triple-quoted strings)
    3. Docstrings followed by duplicated function bodies
    4. Full function/class re-declarations containing docstrings
    5. Classes containing nested target methods with docstrings and re-emitted bodies
    6. Nested classes containing target methods

    Rejects:
    - Empty or whitespace-only docstrings
    - Model outputs targeting the wrong symbol
    - Ambiguous model outputs with multiple conflicting candidates
    - Outputs containing only executable statements without docstrings/comments

    Args:
        clean_code: Cleaned model output string.
        target_symbol: Optional name of the enclosing function/method/class symbol.

    Returns:
        (is_valid, extracted_content, is_docstring)
    """
    clean = textwrap.dedent(clean_code.expandtabs(4)).strip()
    if not clean:
        return False, "", False

    lines = clean.splitlines(keepends=True)

    # Case 1: Pure comments
    if all(line.strip().startswith("#") or not line.strip() for line in lines):
        return True, clean, False

    # Case 2: Parse as AST to extract docstring
    tree = None
    offset_line = 0
    try:
        tree = ast.parse(clean)
    except SyntaxError:
        try:
            dummy_code = "def _dummy_func_():\n" + textwrap.indent(clean, "    ")
            tree = ast.parse(dummy_code)
            offset_line = 1
        except SyntaxError:
            return False, "", False

    if tree is None:
        return False, "", False

    def _collect_named_nodes(parent_node: ast.AST, parent_path: str = "") -> List[Tuple[ast.AST, str, str]]:
        collected = []
        for child in getattr(parent_node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qname = f"{parent_path}.{child.name}" if parent_path else child.name
                collected.append((child, qname, child.name))
                collected.extend(_collect_named_nodes(child, qname))
        return collected

    target_body = None
    if offset_line == 1:
        if tree.body and isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
            target_body = tree.body[0].body
    else:
        named_nodes = _collect_named_nodes(tree)
        target_sym = target_symbol.strip() if target_symbol else None

        if target_sym:
            # 1. Exact qualified match
            exact = [n for n, q, s in named_nodes if q == target_sym]
            if len(exact) == 1:
                target_body = exact[0].body
            else:
                # 2. Suffix / simple name match
                matches = [
                    n for n, q, s in named_nodes
                    if s == target_sym
                    or q.endswith("." + target_sym)
                    or ("." in target_sym and target_sym.endswith("." + s) and s == target_sym.split(".")[-1])
                ]
                if len(matches) == 1:
                    target_body = matches[0].body
                elif len(matches) > 1:
                    # Ambiguous target symbol matching multiple definitions
                    return False, "", False
                elif named_nodes:
                    # Named nodes exist in tree, but none matched target_sym -> wrong target
                    return False, "", False
                else:
                    # No named definitional nodes in tree (pure statements/docstring)
                    target_body = tree.body
        else:
            if len(named_nodes) == 1:
                target_body = named_nodes[0][0].body
            elif len(named_nodes) > 1:
                # Multiple definitional candidates without a target symbol to disambiguate
                return False, "", False
            else:
                target_body = tree.body

    if not target_body:
        return False, "", False

    first_stmt = target_body[0]
    if (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
    ):
        raw_val = first_stmt.value.value
        if not isinstance(raw_val, str) or not raw_val.strip():
            # Reject empty docstrings
            return False, "", False

        start_idx = first_stmt.lineno - 1 - offset_line
        end_idx = getattr(first_stmt, "end_lineno", first_stmt.lineno) - offset_line
        if 0 <= start_idx < len(lines):
            doc_lines = lines[start_idx:end_idx]
            extracted_doc = textwrap.dedent("".join(doc_lines)).strip()
            if not extracted_doc:
                return False, "", False
            return True, extracted_doc, True

    return False, "", False


def apply_documentation_replacement(
    lines: List[str],
    task_line_number: int,
    doc_content: str,
    is_docstring: bool,
) -> List[str]:
    """Safely apply a docstring or comment replacement to target file lines.

    Preserves all existing executable statements. If a docstring is provided for a
    function or class, it is placed inside the body directly under the header line.
    If an existing docstring was present, it is cleanly replaced without duplication.

    Args:
        lines: List of original file lines.
        task_line_number: 1-indexed line number of the AI-DOCS comment.
        doc_content: Docstring or comment string to insert.
        is_docstring: True if doc_content is a docstring, False if pure comments.

    Returns:
        New list of lines with documentation applied.
    """
    new_lines = list(lines)
    target_idx = task_line_number - 1
    if target_idx < 0 or target_idx >= len(new_lines):
        return new_lines

    target_line = new_lines[target_idx]
    target_indent = re.match(r"^(\s*)", target_line).group(1)

    if not is_docstring:
        formatted_comments = []
        for c_line in doc_content.splitlines():
            if c_line.strip():
                formatted_comments.append(f"{target_indent}{c_line}\n")
            else:
                formatted_comments.append("\n")
        new_lines[target_idx] = "".join(formatted_comments)
        return new_lines

    # Check if target_idx is directly above def/class or decorator
    lookahead_idx = target_idx + 1
    is_above_def = False
    def_idx = -1

    while lookahead_idx < len(new_lines):
        next_l = new_lines[lookahead_idx].strip()
        if not next_l or next_l.startswith("#"):
            lookahead_idx += 1
            continue
        if next_l.startswith("@"):
            lookahead_idx += 1
            continue
        if next_l.startswith(("def ", "async def ", "class ")):
            is_above_def = True
            def_idx = lookahead_idx
            break
        break

    if is_above_def and def_idx != -1:
        # Remove the # AI-DOCS comment line above def/class
        new_lines.pop(target_idx)
        def_idx -= 1

        def_line = new_lines[def_idx]
        def_indent = re.match(r"^(\s*)", def_line).group(1)
        body_indent = def_indent + "    "

        formatted_doc = []
        for d_line in doc_content.splitlines():
            if d_line.strip():
                formatted_doc.append(f"{body_indent}{d_line}\n")
            else:
                formatted_doc.append("\n")

        # Advance insert_idx past multiline function/class signatures to the closing colon
        insert_idx = def_idx + 1
        paren_depth = 0
        header_ended = False
        for cur_i in range(def_idx, len(new_lines)):
            line_str = new_lines[cur_i]
            for ch in line_str:
                if ch in "([":
                    paren_depth += 1
                elif ch in ")]":
                    paren_depth = max(0, paren_depth - 1)
            if paren_depth == 0 and ":" in line_str:
                insert_idx = cur_i + 1
                header_ended = True
                break
        if not header_ended:
            insert_idx = def_idx + 1

        # Check and remove existing docstring directly under def_line if present
        if insert_idx < len(new_lines):
            next_l = new_lines[insert_idx].strip()
            if next_l.startswith(('"""', "'''")):
                quote = next_l[:3]
                if quote in next_l[3:]:
                    new_lines.pop(insert_idx)
                else:
                    new_lines.pop(insert_idx)
                    while insert_idx < len(new_lines):
                        curr = new_lines[insert_idx]
                        new_lines.pop(insert_idx)
                        if quote in curr:
                            break

        new_lines.insert(insert_idx, "".join(formatted_doc))
        return new_lines

    else:
        # # AI-DOCS comment is inside the body
        formatted_doc = []
        for d_line in doc_content.splitlines():
            if d_line.strip():
                formatted_doc.append(f"{target_indent}{d_line}\n")
            else:
                formatted_doc.append("\n")

        new_lines[target_idx] = "".join(formatted_doc)

        # Check and remove existing docstring immediately following target_idx
        if target_idx + 1 < len(new_lines):
            next_l = new_lines[target_idx + 1].strip()
            if next_l.startswith(('"""', "'''")):
                quote = next_l[:3]
                if quote in next_l[3:]:
                    new_lines.pop(target_idx + 1)
                else:
                    new_lines.pop(target_idx + 1)
                    while target_idx + 1 < len(new_lines):
                        curr = new_lines[target_idx + 1]
                        new_lines.pop(target_idx + 1)
                        if quote in curr:
                            break

        return new_lines


def generate_mock_file_fix(
    original_lines: List[str],
    line_number: int,
    prompt: str,
    instruction_type: Optional[str] = None,
) -> List[str]:
    """Deterministically generate simulated fix for test environments.

    Replaces the instruction comment line with an appropriate implementation,
    preserving exact indentation and surrounding code.
    """
    if line_number < 1 or line_number > len(original_lines):
        return list(original_lines)

    target_idx = line_number - 1
    target_line = original_lines[target_idx]

    # Detect indentation
    indent_match = re.match(r"^(\s*)", target_line)
    indent = indent_match.group(1) if indent_match else ""

    prompt_clean = prompt.strip().lower()

    # If targeting a function definition header for a non-syntax fix, target the body
    if target_line.strip().startswith(("def ", "async def ")) and not ("syntax" in prompt_clean):
        body_indent = indent + "    "
        if target_idx + 1 < len(original_lines) and original_lines[target_idx + 1].startswith(body_indent):
            return generate_mock_file_fix(
                original_lines,
                line_number + 1,
                prompt,
                instruction_type=instruction_type,
            )
        else:
            new_lines = list(original_lines)
            if "return true" in prompt_clean:
                body_fix = f"{body_indent}return True\n"
            elif "return false" in prompt_clean:
                body_fix = f"{body_indent}return False\n"
            elif "zero" in prompt_clean or "division" in prompt_clean:
                body_fix = f"{body_indent}if b == 0:\n{body_indent}    return 0\n{body_indent}return a / b\n"
            elif "even" in prompt_clean:
                body_fix = f"{body_indent}return n % 2 == 0\n"
            else:
                body_fix = f"{body_indent}# Automated fix for: {prompt.strip()}\n{body_indent}pass\n"
            new_lines.insert(target_idx + 1, body_fix)
            return new_lines


    # Rule-based deterministic substitutions for testing and offline scenarios
    if instruction_type == "AI-DOCS" or (
        "doc" in prompt_clean or "document" in prompt_clean or "ai-docs" in prompt_clean
    ):
        if prompt.strip().startswith(('"""', "'''")):
            doc_text = prompt.strip()
        else:
            doc_text = f'"""{prompt.strip()}."""'
        return apply_documentation_replacement(
            original_lines, line_number, doc_text, is_docstring=True
        )
    elif "return true" in prompt_clean:
        replacement = f"{indent}return True\n"
    elif "return false" in prompt_clean:
        replacement = f"{indent}return False\n"
    elif "even" in prompt_clean:
        replacement = f"{indent}return n % 2 == 0\n"
    elif "add" in prompt_clean or "sum" in prompt_clean:
        replacement = f"{indent}return a + b\n"
    elif "zero" in prompt_clean or "division" in prompt_clean:
        if "return" in target_line:
            replacement = f"{indent}if b == 0:\n{indent}    return 0\n{target_line}"
        else:
            replacement = f"{indent}if b == 0:\n{indent}    return 0\n{indent}return a / b\n"
    elif "syntax" in prompt_clean:
        trimmed = target_line.rstrip()
        if trimmed and not trimmed.endswith(":") and any(trimmed.startswith(kw) for kw in ("def ", "class ", "if ", "for ", "while ", "try", "except", "finally", "with ", "elif ")):
            replacement = f"{trimmed}:\n"
        else:
            replacement = f"{indent}# Fixed syntax error\n{indent}pass\n"
    elif "todo" in prompt_clean or "implement" in prompt_clean:
        action = prompt.strip()
        replacement = f"{indent}# Resolved: {action}\n{indent}pass\n"
    elif "fix" in prompt_clean:
        if target_line.strip().startswith("#"):
            replacement = f"{indent}# Applied automated fix for: {prompt.strip()}\n"
        else:
            replacement = f"{indent}# Applied automated fix for: {prompt.strip()}\n{target_line}"
    else:
        if target_line.strip().startswith("#"):
            replacement = f"{indent}# Completed AI Task: {prompt.strip()}\n"
        else:
            replacement = f"{indent}# Completed AI Task: {prompt.strip()}\n{target_line}"

    new_lines = list(original_lines)
    new_lines[target_idx] = replacement
    return new_lines


def find_enclosing_function(file_content: str, line_number: int) -> Optional[str]:
    """Find the name of the innermost function enclosing the given line number in Python code.

    Args:
        file_content: Full text of the Python source file.
        line_number: 1-indexed target line number.

    Returns:
        The string name of the enclosing function/method, or None if not inside a function.
    """
    try:
        tree = ast.parse(file_content.lstrip("﻿"))
    except Exception:
        return None

    enclosing: Optional[str] = None
    smallest_span = float("inf")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", None)
            if start is not None and end is not None:
                if start <= line_number <= end:
                    span = end - start
                    if span < smallest_span:
                        smallest_span = span
                        enclosing = node.name

    if enclosing is None:
        smallest_class_span = float("inf")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                start = getattr(node, "lineno", None)
                end = getattr(node, "end_lineno", None)
                if start is not None and end is not None:
                    if start <= line_number <= end:
                        span = end - start
                        if span < smallest_class_span:
                            smallest_class_span = span
                            enclosing = node.name

    return enclosing


def unwrap_nested_function_body(extracted_code: str, target_symbol: Optional[str]) -> str:
    """Safely unwrap redundant function signature when a model re-declares the enclosing function.

    Compact coding LLMs sometimes re-declare `def func(...):` even when prompted
    to output only inner replacement statements. This helper safely extracts the
    inner body statements if and only if the extracted code is a single function
    whose name strictly matches `target_symbol`.

    If the code contains multiple definitions, an unrelated function name,
    invalid syntax, or no enclosing symbol is targeted, the code is returned
    unchanged to allow standard syntax validation and security checks to handle it.

    Args:
        extracted_code: Raw extracted code snippet from model response.
        target_symbol: Expected name of the enclosing function.

    Returns:
        Dedented inner body string if strictly matched; otherwise original extracted_code.
    """
    if not target_symbol or not target_symbol.strip():
        return extracted_code

    clean_code = textwrap.dedent(extracted_code).strip()
    if not clean_code:
        return extracted_code

    try:
        tree = ast.parse(clean_code)
    except SyntaxError:
        # Never alter code that fails to parse; standard validation will report it
        return extracted_code

    # Must be exactly one top-level statement
    if len(tree.body) != 1:
        return extracted_code

    top_node = tree.body[0]
    if not isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return extracted_code

    # The function name must strictly match target_symbol
    if top_node.name != target_symbol.strip():
        return extracted_code

    # Must have a non-empty body
    if not top_node.body:
        return extracted_code

    # Extract lines corresponding to the body statements
    lines = clean_code.splitlines()
    body_start_line = top_node.body[0].lineno - 1  # 0-indexed
    body_end_line = getattr(top_node.body[-1], "end_lineno", len(lines))

    if body_start_line >= len(lines):
        return extracted_code

    body_lines = lines[body_start_line:body_end_line]
    unwrapped = textwrap.dedent("\n".join(body_lines)).strip()
    return unwrapped if unwrapped else extracted_code


def generate_preview_patch(
    project_root: Path,
    config: CodePilotConfig,
    plan: Optional[Plan] = None,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
    target_task_id: Optional[str] = None,
    adhoc_task: Optional[Any] = None,
) -> PatchBundle:
    """Generate proposed code modifications and unified Git diff.

    Serialized by an advisory lock. Two concurrent fix runs in the same repository both
    write '.codepilot/patches/pending_patch.json', so without the lock the later run
    silently overwrote the earlier one's staged patch and the developer could approve a
    diff that no longer matched what they had reviewed.

    See _generate_preview_patch_unlocked for the full contract.
    """
    lock_acquired = False
    try:
        acquire_lock(
            project_root,
            FIX_LOCK_FILENAME,
            error_factory=FixError,
            busy_message=lambda pid, lock_file: (
                f"Another fix generation (PID {pid}) is already running in this "
                "repository.\n"
                "Concurrent runs would overwrite each other's staged patch, so this run "
                "was stopped.\n"
                f"Lock file: '{lock_file}'"
            ),
            operation="fix generation",
        )
        lock_acquired = True
        return _generate_preview_patch_unlocked(
            project_root,
            config,
            plan=plan,
            override_provider=override_provider,
            override_model=override_model,
            target_task_id=target_task_id,
            adhoc_task=adhoc_task,
        )
    finally:
        if lock_acquired:
            release_lock(project_root, FIX_LOCK_FILENAME)


def _generate_preview_patch_unlocked(
    project_root: Path,
    config: CodePilotConfig,
    plan: Optional[Plan] = None,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
    target_task_id: Optional[str] = None,
    adhoc_task: Optional[Any] = None,
) -> PatchBundle:
    """Generate proposed code modifications and unified Git diff.

    DOES NOT modify source files on disk. Enforces strict repository boundaries
    and protected-file restrictions.

    Args:
        project_root: Root directory of the repository.
        config: Validated CodePilot configuration.
        plan: Optional precomputed Plan. If None, scans the repo.
        override_provider: Optional runtime provider override.
        override_model: Optional model identifier override.
        target_task_id: Optional task ID to isolate patch generation.
        adhoc_task: Optional ad-hoc InstructionTask for command-driven fixing.

    Returns:
        PatchBundle: Contains generated diffs and file states.

    Raises:
        FixError: If LLM is not configured, files violate security boundaries, or generation fails.
    """
    provider = (override_provider or config.model_provider).strip().lower()

    # Requirement 11: If LLM is not configured, report clearly
    if provider == "none":
        raise FixError(
            "AI model provider is not configured (current: 'none').\n"
            "To generate AI fixes, configure 'model_provider' in '.codepilot.toml' or set\n"
            "the 'CODEPILOT_MODEL_PROVIDER' environment variable (e.g. 'openai', 'anthropic',\n"
            "'gemini', 'ollama', or 'mock') and supply required credentials."
        )

    # Check external providers for required API keys
    if provider in ("openai", "anthropic", "gemini"):
        key_env_var = f"{provider.upper()}_API_KEY"
        if not os.environ.get(key_env_var):
            raise FixError(
                f"Model provider '{provider}' requires environment variable '{key_env_var}', which is not set."
            )
        raise FixError(
            f"External cloud provider '{provider}' is not supported in local-first mode. "
            "Please use 'ollama', 'standalone', or 'mock'."
        )

    if provider not in ("ollama", "standalone", "gguf", "mock"):
        raise FixError(
            f"Unsupported model provider '{provider}'. Supported providers are: 'ollama', 'standalone', 'mock'."
        )

    root = project_root.resolve()

    # Handle ad-hoc command-driven task
    if adhoc_task is not None:
        from codepilot.core.planner import PlanTask
        t_id = getattr(adhoc_task, "id", None) or "adhoc-task-1"
        t_file = getattr(adhoc_task, "file_path", "")
        t_line = getattr(adhoc_task, "line_number", 1) or 1
        t_desc = getattr(adhoc_task, "description", "") or getattr(adhoc_task, "prompt", "")
        t_type = getattr(adhoc_task, "instruction_type", "AI-FIX")
        t_sym = getattr(adhoc_task, "target_symbol", None)
        t_sym_type = getattr(adhoc_task, "target_symbol_type", None)

        adhoc_plan_task = PlanTask(
            task_id=t_id,
            instruction_id=t_id,
            target_file=t_file,
            line_number=t_line,
            instruction_type=t_type,
            description=t_desc,
            risk_level="MEDIUM",
            scope="FUNCTION" if t_sym else "MODULE",
            steps=[f"1. Repair {t_file}:{t_line} for {t_desc}"],
            target_symbol=t_sym,
            target_symbol_type=t_sym_type,
        )
        plan = Plan(
            plan_id="plan-adhoc",
            project_root=str(root),
            created_at=datetime.now(timezone.utc).isoformat(),
            tasks=[adhoc_plan_task],
        )

    # Discover instructions if plan not provided
    if plan is None or not plan.tasks:
        scan_result = scan_repository(root, config)
        if not scan_result.instructions and not getattr(scan_result, "static_findings", []):
            return PatchBundle(
                patch_id="patch-empty",
                created_at=datetime.now(timezone.utc).isoformat(),
                project_root=str(root),
                model_provider=provider,
                file_patches=[],
                full_diff="",
                is_mock=(provider == "mock"),
                output_source="mock" if provider == "mock" else "model",
                status="no_modifications",
            )
        from codepilot.core.planner import generate_plan

        plan = generate_plan(scan_result)

    if not plan.tasks:
        return PatchBundle(
            patch_id="patch-empty",
            created_at=datetime.now(timezone.utc).isoformat(),
            project_root=str(root),
            model_provider=provider,
            file_patches=[],
            full_diff="",
            is_mock=(provider == "mock"),
            output_source="mock" if provider == "mock" else "model",
            status="no_modifications",
        )

    tasks_to_process = plan.tasks
    if target_task_id:
        clean_id = target_task_id.strip()
        tasks_to_process = [
            t for t in plan.tasks if t.task_id == clean_id or clean_id in t.task_id
        ]
        if not tasks_to_process:
            raise FixError(f"Target task ID '{target_task_id}' not found in current plan.")

    # Group tasks by target file
    tasks_by_file: Dict[str, List[Any]] = {}
    for task in tasks_to_process:
        tasks_by_file.setdefault(task.target_file, []).append(task)

    # Initialize and pre-validate model runtime if not in explicit mock mode
    model_client = None
    if provider != "mock":
        from codepilot.core.models.factory import create_model_client

        try:
            model_client = create_model_client(
                config=config,
                override_provider=provider,
                override_model=override_model,
            )
        except Exception as e:
            raise FixError(f"Failed to initialize model provider '{provider}': {e}") from e

        try:
            healthy, msg = model_client.health_check()
            if not healthy:
                raise FixError(f"Model runtime '{provider}' is unavailable: {msg}")
        except FixError:
            raise
        except Exception as e:
            raise FixError(f"Model runtime '{provider}' health check failed: {e}") from e

    file_patches: List[FilePatch] = []
    full_diff_pieces: List[str] = []
    clarification_questions: List[Dict[str, str]] = []
    # Per-task and per-file failures, collected rather than raised, so that one unusable
    # model output does not discard the work of every other task in the run.
    task_failures: List[Dict[str, str]] = []

    for rel_path, tasks in tasks_by_file.items():
        # Security Boundary Enforcement: validate target path
        try:
            file_path = validate_target_path(rel_path, root, config)
        except SecurityError as e:
            raise FixError(f"Security boundary rejected target file '{rel_path}': {e}") from e

        if not file_path.is_file():
            logger.warning("Target file %s no longer exists, skipping.", rel_path)
            continue

        try:
            original_content = file_path.read_text(encoding="utf-8")
        except OSError as e:
            raise FixError(f"Failed to read target file '{rel_path}': {e}") from e

        original_hash = compute_sha256(original_content)
        original_lines = original_content.splitlines(keepends=True)

        current_lines = list(original_lines)

        # Apply fixes in descending order of line numbers so line shifts don't affect earlier tasks
        sorted_tasks = sorted(tasks, key=lambda t: t.line_number, reverse=True)

        for task in sorted_tasks:
            if provider == "mock":
                current_lines = generate_mock_file_fix(
                    current_lines,
                    task.line_number,
                    task.description,
                    instruction_type=getattr(task, "instruction_type", None),
                )
            else:
                # Live / standalone LLM integration
                from codepilot.core.context.engine import ProjectContextEngine
                from codepilot.core.context.prompt import build_model_request
                from codepilot.core.evaluation.metrics import extract_code_from_completion
                from codepilot.core.parser.models import InstructionTask

                enclosing_func = None
                if rel_path.endswith((".py", ".pyi")):
                    enclosing_func = find_enclosing_function(
                        "".join(current_lines), task.line_number
                    )

                symbol_name = getattr(task, "target_symbol", None) or enclosing_func

                ctx_engine = ProjectContextEngine(project_root=root)
                inst_task = InstructionTask(
                    id=task.task_id,
                    file_path=task.target_file,
                    line_number=task.line_number,
                    instruction_type=task.instruction_type,
                    description=task.description,
                    target_symbol=symbol_name,
                    target_symbol_type="function" if symbol_name else None,
                )
                bundle = ctx_engine.build_context(inst_task, token_budget=config.max_context_tokens)
                if not bundle.is_safe:
                    # Fatal, never downgraded to a per-task failure: a security boundary
                    # violation must stop the whole run rather than be reported as one
                    # task that happened not to work.
                    raise FixError(
                        f"Security violation building context for task '{task.task_id}': {bundle.safety_warning}"
                    )

                # Snapshot so that a task which fails midway leaves no partial edit behind
                # and the tasks that already succeeded for this file are preserved.
                lines_before_task = list(current_lines)
                try:
                    req = build_model_request(bundle, max_tokens=1024, temperature=0.1)
                    try:
                        resp = model_client.generate(req, model_name=override_model)
                    except Exception as e:
                        raise FixError(
                            f"Model inference failed for task '{task.task_id}': {e}"
                        ) from e

                    if not resp or not resp.completion or not resp.completion.strip():
                        raise FixError(
                            f"Model returned an empty response for task '{task.task_id}' in '{rel_path}'."
                        )

                    # Route Check: If task routed to CLARIFICATION, record question and skip patch extraction
                    route = req.metadata.get("route") if req.metadata else None
                    if route == "clarification":
                        clarification_text = resp.completion.strip()
                        logger.info(
                            "Task '%s' in '%s' routed to CLARIFICATION: %s",
                            task.task_id,
                            rel_path,
                            clarification_text[:100],
                        )
                        clarification_questions.append({
                            "task_id": task.task_id,
                            "file_path": rel_path,
                            "question": clarification_text,
                        })
                        continue

                    extracted = extract_code_from_completion(resp.completion)
                    if not extracted or not extracted.strip():
                        raise FixError(
                            f"Could not extract usable code modification from model completion for task '{task.task_id}' in '{rel_path}'.\n"
                            f"Raw completion: {resp.completion[:200]!r}"
                        )

                    # Secrets are redacted from the context sent to the model. If the model
                    # echoes a redaction placeholder back, applying it would overwrite a real
                    # credential in the developer's file with the placeholder text, so the
                    # patch is refused instead.
                    from codepilot.core.security import (
                        PRIVATE_KEY_PLACEHOLDER,
                        REDACTION_PLACEHOLDER,
                    )

                    if REDACTION_PLACEHOLDER in extracted or PRIVATE_KEY_PLACEHOLDER in extracted:
                        raise FixError(
                            f"Model output for task '{task.task_id}' in '{rel_path}' contains a "
                            "secret-redaction placeholder, which means the model echoed back "
                            "content that was masked before submission.\n"
                            "Applying it would overwrite a real credential in your file with "
                            "placeholder text, so the patch was rejected. Move the hardcoded "
                            "credential into an environment variable and retry."
                        )

                    # For AI-DOCS tasks: strictly extract docstring/comments and apply replacement safely
                    if task.instruction_type == "AI-DOCS":
                        valid_doc, doc_content, is_doc = extract_documentation_content(
                            extracted, target_symbol=symbol_name
                        )
                        if not valid_doc or not doc_content.strip():
                            raise FixError(
                                f"Model output for AI-DOCS task '{task.task_id}' in '{rel_path}' does not contain "
                                f"a valid docstring or comments: {extracted[:200]!r}"
                            )
                        current_lines = apply_documentation_replacement(
                            current_lines, task.line_number, doc_content, is_docstring=is_doc
                        )
                    else:
                        # Safely unwrap redundant function signature if model re-declared the enclosing function
                        if symbol_name:
                            extracted = unwrap_nested_function_body(extracted, target_symbol=symbol_name)

                        target_idx = task.line_number - 1
                        if 0 <= target_idx < len(current_lines):
                            indent_match = re.match(r"^(\s*)", current_lines[target_idx])
                            indent = indent_match.group(1) if indent_match else ""
                            clean_code = textwrap.dedent(extracted.expandtabs(4)).strip()
                            formatted_lines = []
                            for code_line in clean_code.splitlines():
                                if code_line.strip():
                                    formatted_lines.append(f"{indent}{code_line}\n")
                                else:
                                    formatted_lines.append("\n")
                            replacement = "".join(formatted_lines)
                            current_lines[target_idx] = replacement

                            # If next line is a placeholder statement at the same indentation level, remove it
                            if target_idx + 1 < len(current_lines):
                                next_line = current_lines[target_idx + 1]
                                next_indent_match = re.match(r"^(\s*)", next_line)
                                next_indent = next_indent_match.group(1) if next_indent_match else ""
                                if next_indent == indent and next_line.strip() in (
                                    "pass",
                                    "...",
                                    "raise NotImplementedError",
                                    "raise NotImplementedError()",
                                ):
                                    current_lines.pop(target_idx + 1)

                            # Drop a stale statement the model already re-emitted.
                            #
                            # When the instruction comment sits above the code it describes, a
                            # model asked for "only the replacement statements" commonly returns
                            # the whole body, including the statement that follows the comment.
                            # Replacing only the comment line then leaves that statement twice --
                            # e.g. a duplicated, unreachable 'return a / b' after the new guard
                            # clause. Removing it requires an exact match at the same indentation
                            # against the last statement that was inserted, so unrelated code is
                            # never touched.
                            inserted_statements = [
                                ln.strip() for ln in clean_code.splitlines() if ln.strip()
                            ]
                            if inserted_statements and target_idx + 1 < len(current_lines):
                                following = current_lines[target_idx + 1]
                                following_indent_match = re.match(r"^(\s*)", following)
                                following_indent = (
                                    following_indent_match.group(1)
                                    if following_indent_match
                                    else ""
                                )
                                if (
                                    following_indent == indent
                                    and following.strip() == inserted_statements[-1]
                                ):
                                    current_lines.pop(target_idx + 1)

                except FixError as task_error:
                    # One task's output being unusable must not discard the work of the
                    # other tasks in this run. Roll this task back to the pre-task state,
                    # record why it failed, and continue with the remaining tasks.
                    current_lines = lines_before_task
                    logger.warning(
                        "Task '%s' in '%s' failed and was skipped: %s",
                        task.task_id,
                        rel_path,
                        task_error,
                    )
                    task_failures.append({
                        "task_id": str(task.task_id),
                        "file_path": rel_path,
                        "instruction_type": str(getattr(task, "instruction_type", "") or ""),
                        "reason": str(task_error),
                    })
                    continue

        proposed_content = "".join(current_lines)

        # Validate Python syntax if target is a Python file.
        #
        # A validation failure here is recorded against this file and the file is dropped
        # from the patch, rather than aborting the run: other files in the same run may
        # have produced perfectly good changes, and discarding those too would waste the
        # inference already paid for.
        file_rejection: Optional[str] = None
        if rel_path.endswith(".py"):
            try:
                # A leading UTF-8 BOM is stripped before validation; otherwise every
                # BOM-encoded file parsed as a syntax error and syntax validation was
                # silently skipped for it (original and proposed both "failing").
                ast.parse(proposed_content.lstrip("﻿"))
            except SyntaxError as syn_err:
                orig_syntax_ok = False
                try:
                    ast.parse(original_content.lstrip("﻿"))
                    orig_syntax_ok = True
                except SyntaxError:
                    pass

                if orig_syntax_ok:
                    file_rejection = (
                        f"Patch validation failed for '{rel_path}': proposed modification "
                        f"introduces a Python syntax error on line {syn_err.lineno}: {syn_err.msg}"
                    )

            # Enforce executable AST invariance only when EVERY task for this file is
            # documentation-only. A file that also carries AI-FIX / AI-TASK / AI-REFACTOR
            # work is expected to change executable code, so the invariance rule must not
            # be applied to the file as a whole (it would reject the entire patch).
            if file_rejection is None and tasks and all(
                getattr(t, "instruction_type", None) == "AI-DOCS" for t in tasks
            ):
                is_invariant, reason = verify_executable_ast_invariance(
                    original_content, proposed_content
                )
                if not is_invariant:
                    file_rejection = (
                        f"Patch validation failed for '{rel_path}': AI-DOCS task violated "
                        f"executable AST invariance: {reason}"
                    )

        if file_rejection is not None:
            logger.warning("Dropping patch for '%s': %s", rel_path, file_rejection)
            task_failures.append({
                "task_id": ", ".join(str(t.task_id) for t in tasks),
                "file_path": rel_path,
                "instruction_type": "",
                "reason": file_rejection,
            })
            continue

        # Generate standard Git-compatible unified diff
        diff_lines = list(
            difflib.unified_diff(
                original_content.splitlines(),
                proposed_content.splitlines(),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
                lineterm="",
            )
        )

        if diff_lines:
            file_diff_str = "\n".join(diff_lines) + "\n"

            # Screen what this change introduces so dangerous constructs are called out in
            # the diff preview instead of depending on the reviewer spotting a single line.
            from codepilot.core.patch_risk import screen_file_change

            risk_findings = [
                f.to_dict()
                for f in screen_file_change(rel_path, original_content, proposed_content)
            ]

            # Record the effective instruction type for the file so that apply-time
            # invariance enforcement matches preview-time enforcement exactly.
            #
            # "AI-DOCS" is recorded only when every task for this file is documentation-only.
            # When the file also carries executable work, the driving non-docs type is
            # recorded instead -- an explicit value is required so the patcher does not fall
            # back to scanning the file body for an 'AI-DOCS' marker and wrongly subject a
            # mixed change to documentation-only invariance rules.
            task_types = [getattr(t, "instruction_type", None) for t in tasks]
            non_docs = [t for t in task_types if t and t != "AI-DOCS"]
            if tasks and not non_docs and any(t == "AI-DOCS" for t in task_types):
                file_inst_type = "AI-DOCS"
            elif non_docs:
                file_inst_type = non_docs[0]
            else:
                file_inst_type = None
            file_patches.append(
                FilePatch(
                    file_path=rel_path,
                    original_hash=original_hash,
                    original_content=original_content,
                    proposed_content=proposed_content,
                    diff=file_diff_str,
                    instruction_type=file_inst_type,
                    risk_findings=risk_findings,
                )
            )
            full_diff_pieces.append(file_diff_str)

    full_diff = "\n".join(full_diff_pieces)
    patch_id_hash = hashlib.sha256(
        f"{root}:{full_diff}:{datetime.now(timezone.utc).timestamp()}".encode("utf-8")
    ).hexdigest()[:12]

    is_mock = (provider == "mock")
    clarification_str: Optional[str] = None
    if clarification_questions:
        clarification_str = "\n\n".join(
            f"[{q['file_path']} - {q['task_id']}]\n{q['question']}"
            if len(clarification_questions) > 1
            else q["question"]
            for q in clarification_questions
        )
    # Nothing usable was produced and something went wrong: there is no partial result to
    # stage, so report the failures as an error rather than returning an empty bundle that
    # looks like "no work to do".
    if not file_patches and not clarification_questions and task_failures:
        detail = "\n\n".join(
            f"- [{f['file_path']} {f['task_id']}] {f['reason']}" for f in task_failures
        )
        if len(task_failures) == 1:
            raise FixError(task_failures[0]["reason"])
        raise FixError(
            f"All {len(task_failures)} task(s) failed; no patch was generated.\n\n{detail}"
        )

    if file_patches:
        status = "partial_success" if task_failures else "success"
    elif clarification_questions:
        status = "clarification_requested"
    else:
        status = "no_modifications"

    bundle = PatchBundle(
        patch_id=f"patch-{patch_id_hash}" if file_patches else "patch-empty",
        created_at=datetime.now(timezone.utc).isoformat(),
        project_root=str(root),
        model_provider=provider,
        file_patches=file_patches,
        full_diff=full_diff,
        is_approved=False,
        is_applied=False,
        is_mock=is_mock,
        output_source="mock" if is_mock else "model",
        status=status,
        clarification_question=clarification_str,
        task_failures=task_failures,
    )

    # Save to pending patch file for approval workflow only if modifications exist
    if file_patches:
        save_pending_patch(root, bundle)
    return bundle


def get_pending_patch_path(project_root: Path) -> Path:
    """Return path to pending patch storage file."""
    return project_root / PATCHES_DIR / PENDING_PATCH_FILENAME


def save_pending_patch(project_root: Path, patch_bundle: PatchBundle) -> Path:
    """Save pending patch bundle to disk."""
    target_file = get_pending_patch_path(project_root)
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(
            json.dumps(patch_bundle.to_dict(), indent=2), encoding="utf-8"
        )
        return target_file
    except OSError as e:
        raise FixError(f"Failed to save pending patch bundle: {e}") from e


def load_pending_patch(project_root: Path) -> Optional[PatchBundle]:
    """Load pending patch bundle from disk if it exists.

    Raises:
        FixError: If a pending patch file exists but cannot be parsed. A damaged patch
            is reported as such rather than silently presented as "no pending patch",
            which previously sent developers to re-run 'fix' with no explanation.
    """
    target_file = get_pending_patch_path(project_root)
    if not target_file.is_file():
        return None
    try:
        data = json.loads(target_file.read_text(encoding="utf-8"))
        return PatchBundle.from_dict(data)
    except Exception as e:
        logger.warning("Could not read pending patch from %s: %s", target_file, e)
        raise FixError(
            f"The pending patch file at '{target_file}' is damaged and could not be read: {e}\n"
            "Delete the file and run 'codepilot fix --preview' again to regenerate it."
        ) from e
