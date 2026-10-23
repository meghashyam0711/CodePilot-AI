"""Project Context Engine for gathering, selecting, and bounding repository context for local LLMs."""

import ast
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from codepilot.config import DEFAULT_PROTECTED_FILES
from codepilot.core.context.models import (
    CodeSnippet,
    ProjectContextBundle,
    RelatedContext,
)
from codepilot.core.parser.models import InstructionTask
from codepilot.logging_config import get_logger

logger = get_logger("core.context.engine")

# Patterns identifying sensitive files that must never be read or included in LLM context
SENSITIVE_PATTERNS = [
    re.compile(r"""^\.env(?:\..*)?$""", re.IGNORECASE),
    re.compile(r""".*\.pem$""", re.IGNORECASE),
    re.compile(r""".*\.key$""", re.IGNORECASE),
    re.compile(r"""credentials\.json$""", re.IGNORECASE),
    re.compile(r"""secrets?\..*$""", re.IGNORECASE),
    re.compile(r"""id_rsa(?:\..*)?$""", re.IGNORECASE),
]

# Estimated token overhead for system prompt instructions and template wrapper delimiters
DEFAULT_SYSTEM_OVERHEAD_TOKENS: int = 400


class ProjectContextEngine:
    """Gathers bounded, relevant context for an instruction task while strictly excluding secrets."""

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = (project_root or Path.cwd()).resolve()

    def is_sensitive_path(self, rel_or_abs_path: str) -> bool:
        """Check if a file path matches known credential, secret, or key patterns."""
        p = Path(rel_or_abs_path)
        name = p.name
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(name) or pattern.search(rel_or_abs_path):
                return True
        return False

    def build_context(
        self,
        task: InstructionTask,
        token_budget: int = 2048,
    ) -> ProjectContextBundle:
        """Gather context around a task while adhering to token budgets and security boundaries.

        Args:
            task: InstructionTask to gather context for.
            token_budget: Maximum estimated tokens for the entire context bundle.

        Returns:
            ProjectContextBundle containing snippets, imports, tests, and architecture notes.
        """
        warnings: List[str] = []

        # 1. Security Check: Block sensitive files immediately
        if self.is_sensitive_path(task.file_path):
            return ProjectContextBundle(
                task=task,
                is_safe=False,
                safety_warning=(
                    f"Refusing to gather context: '{task.file_path}' matches sensitive credential/secret "
                    f"patterns. Never send secrets to an LLM."
                ),
                warnings=[f"Access blocked for sensitive file: {task.file_path}"],
            )

        full_path = (self.project_root / task.file_path).resolve()
        if not full_path.exists() or not full_path.is_file():
            return ProjectContextBundle(
                task=task,
                is_safe=True,
                warnings=[f"Target file not found on disk: {task.file_path}"],
            )

        # 2. Read target file safely
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            lines = content.splitlines()
        except Exception as e:
            return ProjectContextBundle(
                task=task,
                is_safe=True,
                warnings=[f"Could not read target file '{task.file_path}': {e}"],
            )

        # 3. Extract code snippet
        target_snippet = self._extract_snippet(lines, task, task.file_path)

        # 4. Extract imports from target file
        imports = self._extract_imports(lines, full_path.suffix.lower())

        # 5. Discover related test files
        related_contexts = self._discover_related_contexts(full_path, task)

        # 6. Gather lightweight architecture notes
        arch_summary = self._gather_architecture_summary()

        # 7. Token estimation and budgeting
        estimated_tokens = self._estimate_tokens(
            task=task,
            snippet=target_snippet,
            imports=imports,
            related=related_contexts,
            arch_summary=arch_summary,
        )

        is_truncated = False
        if estimated_tokens > token_budget:
            # Budget exceeded: trim related contexts first
            is_truncated = True
            related_contexts = related_contexts[:1]
            estimated_tokens = self._estimate_tokens(
                task=task,
                snippet=target_snippet,
                imports=imports,
                related=related_contexts,
                arch_summary=arch_summary,
            )
            warnings.append(
                f"Context exceeded token budget ({token_budget} tokens). Auxiliary context was trimmed."
            )

        return ProjectContextBundle(
            task=task,
            target_snippet=target_snippet,
            imports=imports,
            related_contexts=related_contexts,
            architecture_summary=arch_summary,
            project_type="Python" if full_path.suffix in (".py", ".pyi") else "Other",
            estimated_tokens=estimated_tokens,
            is_truncated=is_truncated,
            is_safe=True,
            warnings=warnings,
        )

    def _extract_snippet(
        self,
        lines: List[str],
        task: InstructionTask,
        rel_path: str,
    ) -> CodeSnippet:
        """Extract line-numbered code snippet for target symbol or surrounding lines."""
        total_lines = len(lines)
        if total_lines == 0:
            return CodeSnippet(
                file_path=rel_path,
                start_line=1,
                end_line=1,
                content="",
                symbol_name=task.target_symbol,
                symbol_type=task.target_symbol_type,
            )

        # Default span: 20 lines before and after comment
        line_idx = max(0, min(task.line_number - 1, total_lines - 1))
        start_line = max(1, task.line_number - 15)
        end_line = min(total_lines, task.line_number + 25)

        # If Python and symbol is resolved to a function or class, attempt to locate exact AST span
        if rel_path.endswith((".py", ".pyi")) and task.target_symbol and task.target_symbol != Path(rel_path).stem:
            try:
                tree = ast.parse("\n".join(lines))
                symbol_span = self._find_ast_symbol_span(tree, task.target_symbol)
                if symbol_span:
                    s_start, s_end = symbol_span
                    # Pad slightly for surrounding context
                    start_line = max(1, s_start - 2)
                    end_line = min(total_lines, s_end + 2)
            except Exception:
                pass

        snippet_content = "\n".join(lines[start_line - 1 : end_line])
        return CodeSnippet(
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            content=snippet_content,
            symbol_name=task.target_symbol,
            symbol_type=task.target_symbol_type,
        )

    def _find_ast_symbol_span(self, tree: ast.AST, symbol_name: str) -> Optional[Tuple[int, int]]:
        """Find start and end line numbers for a function, method, or class in Python AST recursively."""
        parts = symbol_name.split(".")

        def _match_path(nodes: List[ast.AST], remaining_parts: List[str]) -> Optional[Tuple[int, int]]:
            if not remaining_parts:
                return None
            target = remaining_parts[0]
            is_last = len(remaining_parts) == 1

            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == target:
                    if is_last:
                        return getattr(node, "lineno", 1), getattr(node, "end_lineno", getattr(node, "lineno", 1))
                    body_nodes = getattr(node, "body", [])
                    res = _match_path(body_nodes, remaining_parts[1:])
                    if res is not None:
                        return res
            return None

        # Start search from top-level children of AST module
        top_nodes = list(ast.iter_child_nodes(tree))
        span = _match_path(top_nodes, parts)
        if span is not None:
            return span

        # Fallback for unqualified symbols defined deeper in the tree
        if len(parts) == 1:
            target = parts[0]
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and getattr(node, "name", "") == target:
                    return getattr(node, "lineno", 1), getattr(node, "end_lineno", getattr(node, "lineno", 1))

        return None

    def _extract_imports(self, lines: List[str], suffix: str) -> List[str]:
        """Extract import statements from file lines."""
        extracted: List[str] = []
        if suffix in (".py", ".pyi"):
            for line in lines:
                s = line.strip()
                if s.startswith("import ") or s.startswith("from "):
                    extracted.append(s)
                    if len(extracted) >= 15:
                        break
        elif suffix in (".js", ".ts", ".jsx", ".tsx"):
            for line in lines:
                s = line.strip()
                if s.startswith("import ") or s.startswith("const ") and "require(" in s:
                    extracted.append(s)
                    if len(extracted) >= 15:
                        break
        return extracted

    def _discover_related_contexts(
        self,
        full_path: Path,
        task: InstructionTask,
    ) -> List[RelatedContext]:
        """Find related test files or callers."""
        related: List[RelatedContext] = []
        stem = full_path.stem

        # Look for matching test files in tests/
        test_candidates = [
            self.project_root / "tests" / f"test_{stem}.py",
            self.project_root / "tests" / f"{stem}_test.py",
            full_path.parent / f"test_{stem}.py",
        ]

        for cand in test_candidates:
            if cand.exists() and cand.is_file():
                try:
                    rel_cand = str(cand.relative_to(self.project_root)).replace("\\", "/")
                    t_lines = cand.read_text(encoding="utf-8", errors="replace").splitlines()
                    test_fns = [l.strip() for l in t_lines if l.strip().startswith("def test_")]
                    summary = f"Matching test suite ({len(test_fns)} test cases: {', '.join(test_fns[:4])})"
                    related.append(
                        RelatedContext(
                            file_path=rel_cand,
                            relationship="test",
                            summary=summary,
                        )
                    )
                    break
                except Exception:
                    pass

        return related

    def _gather_architecture_summary(self) -> str:
        """Provide concise architectural notes about the project."""
        summary_parts = [f"Project Root: {self.project_root.name}"]
        if (self.project_root / "pyproject.toml").exists():
            summary_parts.append("Package Type: Python (pyproject.toml)")
        elif (self.project_root / "package.json").exists():
            summary_parts.append("Package Type: Node/JavaScript (package.json)")

        # Mention tests directory if present
        if (self.project_root / "tests").exists():
            summary_parts.append("Automated Test Directory: tests/")

        return " | ".join(summary_parts)

    def _estimate_tokens(
        self,
        task: InstructionTask,
        snippet: Optional[CodeSnippet],
        imports: List[str],
        related: List[RelatedContext],
        arch_summary: Optional[str],
        include_overhead: bool = True,
    ) -> int:
        """Estimate token count for the gathered context bundle, including system prompt overhead."""
        total_chars = len(task.description) + len(task.file_path)
        if snippet:
            total_chars += len(snippet.content)
        total_chars += sum(len(imp) for imp in imports)
        for r in related:
            total_chars += len(r.file_path) + len(r.summary) + len(r.snippet or "")
        if arch_summary:
            total_chars += len(arch_summary)

        # Rule of thumb: ~4 characters per token for context content
        content_tokens = total_chars // 4
        overhead = DEFAULT_SYSTEM_OVERHEAD_TOKENS if include_overhead else 0
        return max(1, content_tokens + overhead)
