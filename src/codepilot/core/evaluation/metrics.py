"""Metrics computation for coding model evaluation (hygiene, syntax, tests, and task fidelity)."""

import ast
import difflib
import re
import textwrap
from typing import Optional


def _is_probable_code(text: str) -> bool:
    """Check if raw text without markdown fences is probable code or diff rather than conversational prose."""
    stripped = text.strip()
    if not stripped:
        return False

    # Check for unified diff headers
    if stripped.startswith(("--- ", "+++ ", "@@ ")) or "\n@@ " in stripped:
        return True

    # Check for direct Python AST parse
    try:
        ast.parse(stripped)
        return True
    except SyntaxError:
        pass

    # Check dedented AST parse
    try:
        ast.parse(textwrap.dedent(stripped))
        return True
    except SyntaxError:
        pass

    # Check AST parse wrapped in function (for individual statements like 'return a + b')
    try:
        wrapped = "def _wrapper():\n" + textwrap.indent(textwrap.dedent(stripped), "    ")
        ast.parse(wrapped)
        return True
    except SyntaxError:
        pass

    return False


def _strip_context_delimiter_lines(code: str) -> str:
    """Strip prompt-boundary context delimiter tags echoed as standalone wrapper lines.

    Preserves legitimate code, indentation, comments, and strings containing angle brackets.
    Does not remove arbitrary angle-bracket syntax or repair unrelated syntax errors.
    """
    if not code or ("<untrusted_repository_context>" not in code and "</untrusted_repository_context>" not in code):
        return code

    lines = code.splitlines()
    filtered = [
        line
        for line in lines
        if not re.match(r"^\s*(?:#\s*)?</?untrusted_repository_context>\s*$", line, re.IGNORECASE)
    ]
    return "\n".join(filtered).strip()


def extract_code_from_completion(completion: str) -> str:
    """Extract code block or diff from model markdown completion.

    Supports both closed (```...```) and unclosed/truncated (```...<EOF>) markdown fences.
    Handles Unix and Windows line endings.
    Narrowly removes prompt-only <untrusted_repository_context> wrapper tags echoed inside fences.
    If no fences are found, only returns the text if it represents valid raw code or diff;
    never returns conversational preambles or explanations as code.
    """
    if not completion:
        return ""

    # Look for python code blocks (closed or unclosed trailing)
    py_blocks = re.findall(r"```(?:python|py)\r?\n(.*?)(?:```|$)", completion, re.DOTALL | re.IGNORECASE)
    for block in py_blocks:
        cleaned = _strip_context_delimiter_lines(block.strip())
        if cleaned:
            return cleaned

    # Look for diff code blocks (closed or unclosed trailing)
    diff_blocks = re.findall(r"```(?:diff|patch)\r?\n(.*?)(?:```|$)", completion, re.DOTALL | re.IGNORECASE)
    for block in diff_blocks:
        cleaned = _strip_context_delimiter_lines(block.strip())
        if cleaned:
            return cleaned

    # Look for any markdown fenced block (closed or unclosed trailing)
    any_blocks = re.findall(r"```(?:\w+)?\r?\n(.*?)(?:```|$)", completion, re.DOTALL)
    for block in any_blocks:
        cleaned = _strip_context_delimiter_lines(block.strip())
        if cleaned:
            return cleaned

    # If no code fences found, return text only if it parses as valid raw code or diff
    stripped_completion = _strip_context_delimiter_lines(completion.strip())
    if _is_probable_code(stripped_completion):
        return stripped_completion

    # Avoid returning conversational prose/explanations as code
    return ""


def check_syntax_validity(code: str, language: str = "python") -> bool:
    """Check whether code is syntactically valid."""
    if not code.strip():
        return False

    if language.lower() == "python":
        # Check direct parse
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            pass

        # Check dedented parse
        try:
            ast.parse(textwrap.dedent(code))
            return True
        except SyntaxError:
            return False

    # For other languages, default to true if non-empty
    return True


def check_instruction_followed(instruction_type: str, completion: str) -> bool:
    """Verify that model completion adheres to the specific instruction type."""
    if not completion or len(completion.strip()) < 10:
        return False

    comp_lower = completion.lower()

    if instruction_type == "AI-FIX":
        # Should identify bug/cause or propose fix/correction
        indicators = ["fix", "correct", "bug", "error", "handle", "safely", "prevent", "none", "def "]
        return any(ind in comp_lower for ind in indicators)

    elif instruction_type == "AI-TASK":
        # Should contain code implementation or function
        indicators = ["def ", "class ", "implement", "return ", "if ", "raise "]
        return any(ind in comp_lower for ind in indicators)

    elif instruction_type == "AI-REFACTOR":
        # Should refactor / improve structure
        indicators = ["refactor", "improve", "clean", "simplify", "def ", "return "]
        return any(ind in comp_lower for ind in indicators)

    elif instruction_type == "AI-TEST":
        # Should contain test function or assertions
        indicators = ["def test_", "assert ", "pytest", "testcase", "self.assert"]
        return any(ind in comp_lower for ind in indicators)

    elif instruction_type == "AI-DOCS":
        # Should contain docstring or documentation (including line and block comments)
        indicators = ['"""', "'''", "args:", "returns:", "raises:", ":param", ":return:", "docstring", "# ", "// ", "/*"]
        return any(ind in comp_lower for ind in indicators)

    return True


def check_clarification_accuracy(should_request: bool, completion: str) -> bool:
    """Check if model correctly requested clarification when appropriate.

    Evaluates whether the completion contains standard clarification inquiry markers,
    such as requests for missing criteria, specifications, requirements, or behavior.
    """
    comp_lower = completion.lower()
    clarification_markers = [
        "clarif",
        "could you specify",
        "please specify",
        "please clarify",
        "could you clarify",
        "can you clarify",
        "can you specify",
        "could you provide",
        "please provide",
        "which approach",
        "more information",
        "more details",
        "ambiguous",
        "insufficient context",
        "need more details",
        "what should happen",
        "what specific",
        "what criteria",
        "what requirements",
        "which criteria",
        "which requirements",
        "what behavior",
        "what is the expected",
    ]

    asked_clarification = any(marker in comp_lower for marker in clarification_markers)

    if should_request:
        # Expected to ask for clarification
        return asked_clarification
    else:
        # Expected to proceed without unneeded refusal/clarification loop
        return not asked_clarification


def calculate_diff_hygiene(expected: str, proposed: str) -> float:
    """Calculate diff hygiene score (0.0 to 1.0).
    
    1.0 means the proposed code is focused, relevant, and minimal with zero
    hallucinated code or unnecessary rewrites.
    """
    if not expected or not proposed:
        return 0.0

    # Extract clean lines
    exp_lines = [l.strip() for l in expected.splitlines() if l.strip()]
    prop_lines = [l.strip() for l in proposed.splitlines() if l.strip()]

    if not exp_lines or not prop_lines:
        return 0.0

    matcher = difflib.SequenceMatcher(None, exp_lines, prop_lines)
    ratio = matcher.ratio()

    # Penalize extreme code bloat (e.g. proposed 5x more lines than expected)
    length_ratio = len(prop_lines) / max(1, len(exp_lines))
    if length_ratio > 3.0:
        penalty = min(0.5, (length_ratio - 3.0) * 0.1)
        ratio = max(0.0, ratio - penalty)

    return round(ratio, 4)


def run_validation_test_safely(modified_code: str, test_code: str) -> bool:
    """Execute validation test safely in an isolated in-memory Python namespace."""
    if not modified_code or not test_code:
        return False

    # Safe builtins forbidding dangerous OS/process operations
    builtins_dict = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    safe_builtins = {
        k: v
        for k, v in builtins_dict.items()
        if k not in ["quit", "exit", "eval", "exec", "__import__", "open", "breakpoint", "input"]
    }
    # Provide safe math, sys, and basic utilities
    import math

    namespace = {
        "__builtins__": safe_builtins,
        "math": math,
    }

    try:
        # Execute modified code first
        exec(modified_code, namespace)
        # Execute test code
        exec(test_code, namespace)

        # If test functions were defined (e.g. test_*), run them
        for name, obj in list(namespace.items()):
            if name.startswith("test_") and callable(obj):
                obj()

        return True
    except Exception:
        return False
