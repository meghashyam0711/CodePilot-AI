"""Symbol resolver associating instruction comments with code symbols (functions, classes, methods, modules).

Uses Python AST and multi-language heuristics to determine the precise target code symbol
enclosing or immediately following an AI instruction comment.
"""

import ast
from pathlib import Path
import re
from typing import NamedTuple, Optional, Tuple


class ResolvedSymbol(NamedTuple):
    """Result of symbol resolution for an instruction comment."""

    symbol_name: str
    symbol_type: str  # 'function', 'method', 'class', 'module'
    confidence: str  # 'high' (AST-confirmed), 'heuristic'
    start_line: Optional[int] = None
    end_line: Optional[int] = None


# Heuristic patterns for non-Python or syntax-error fallback
JS_FN_PATTERN = re.compile(
    r"""(?:function\s+([A-Za-z0-9_$]+)|(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>)"""
)
JS_CLASS_PATTERN = re.compile(r"""class\s+([A-Za-z0-9_$]+)""")
GO_FN_PATTERN = re.compile(r"""func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)""")
RUST_FN_PATTERN = re.compile(r"""(?:pub\s+)?fn\s+([A-Za-z0-9_]+)""")
JAVA_CLASS_PATTERN = re.compile(r"""(?:public|protected|private)?\s*class\s+([A-Za-z0-9_]+)""")


class SymbolResolver:
    """Associates line numbers of instruction comments with target architectural symbols."""

    @staticmethod
    def resolve(
        file_path: Path,
        line_number: int,
        content: str,
        end_line_number: Optional[int] = None,
    ) -> ResolvedSymbol:
        """Resolve the target symbol for an instruction at a given line in a file."""
        module_name = file_path.stem
        suffix = file_path.suffix.lower()
        effective_line = end_line_number or line_number

        if suffix in (".py", ".pyi"):
            py_symbol = SymbolResolver._resolve_python_ast(line_number, content, module_name, effective_line=effective_line)
            if py_symbol:
                return py_symbol

        # Heuristic resolution for non-Python or unparseable Python
        return SymbolResolver._resolve_heuristic(effective_line, content, module_name)

    @staticmethod
    def _resolve_python_ast(
        line_number: int,
        content: str,
        module_name: str,
        effective_line: Optional[int] = None,
    ) -> Optional[ResolvedSymbol]:
        """Resolve Python symbols using AST node inspection."""
        try:
            tree = ast.parse(content)
        except Exception:
            return None

        eff_line = effective_line or line_number

        # 1. Check if effective_line immediately precedes a function or class definition
        # (e.g. comment at line N, decorator or def at line N+1 or N+2)
        next_symbol = SymbolResolver._find_preceding_python_symbol(tree, eff_line)
        if next_symbol:
            return next_symbol

        # 2. Check if line_number is enclosed within a method, class, or function
        enclosing_symbol = SymbolResolver._find_enclosing_python_symbol(tree, line_number)
        if enclosing_symbol:
            return enclosing_symbol

        # 3. Default to module
        return ResolvedSymbol(
            symbol_name=module_name,
            symbol_type="module",
            confidence="high",
            start_line=1,
            end_line=len(content.splitlines()),
        )

    @staticmethod
    def _get_node_start_line(node: ast.AST) -> int:
        """Get the earliest starting line of a node, taking decorators into account."""
        if hasattr(node, "decorator_list") and node.decorator_list:
            first_dec = min(d.lineno for d in node.decorator_list if hasattr(d, "lineno"))
            return min(first_dec, node.lineno)
        return getattr(node, "lineno", 1)

    @staticmethod
    def _find_preceding_python_symbol(tree: ast.AST, line_number: int) -> Optional[ResolvedSymbol]:
        """Detect if comment at line_number is placed right above a function/class/method definition."""
        best_candidate: Optional[Tuple[int, ResolvedSymbol]] = None

        def _scan_preceding(parent: ast.AST, prefix: str = "") -> None:
            nonlocal best_candidate
            for node in getattr(parent, "body", []):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = SymbolResolver._get_node_start_line(node)
                    end = getattr(node, "end_lineno", start)
                    qname = f"{prefix}.{node.name}" if prefix else node.name

                    if 1 <= (start - line_number) <= 2:
                        dist = start - line_number
                        if best_candidate is None or dist < best_candidate[0]:
                            stype = "class" if isinstance(node, ast.ClassDef) else ("method" if prefix else "function")
                            best_candidate = (
                                dist,
                                ResolvedSymbol(
                                    symbol_name=qname,
                                    symbol_type=stype,
                                    confidence="high",
                                    start_line=start,
                                    end_line=end,
                                ),
                            )

                    if hasattr(node, "body"):
                        _scan_preceding(node, qname)

        _scan_preceding(tree)
        return best_candidate[1] if best_candidate else None

    @staticmethod
    def _find_enclosing_python_symbol(tree: ast.AST, line_number: int) -> Optional[ResolvedSymbol]:
        """Find the innermost function, method, or class enclosing line_number."""
        def _resolve_in_node(parent: ast.AST, prefix: str = "") -> Optional[ResolvedSymbol]:
            for node in getattr(parent, "body", []):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = SymbolResolver._get_node_start_line(node)
                    end = getattr(node, "end_lineno", start)
                    if start <= line_number <= end:
                        qname = f"{prefix}.{node.name}" if prefix else node.name
                        nested = _resolve_in_node(node, qname)
                        if nested:
                            return nested
                        if isinstance(node, ast.ClassDef):
                            stype = "class"
                        elif prefix:
                            stype = "method"
                        else:
                            stype = "function"
                        return ResolvedSymbol(
                            symbol_name=qname,
                            symbol_type=stype,
                            confidence="high",
                            start_line=start,
                            end_line=end,
                        )
            return None

        return _resolve_in_node(tree)

    @staticmethod
    def _resolve_heuristic(
        line_number: int,
        content: str,
        module_name: str,
    ) -> ResolvedSymbol:
        """Heuristic symbol resolver for non-Python or syntax-error code."""
        lines = content.splitlines()

        # Check subsequent 3 lines for definition
        for i in range(line_number, min(len(lines), line_number + 3)):
            line = lines[i]

            # Python def / class fallback
            py_fn_match = re.search(r"""(?:def|class)\s+([A-Za-z0-9_]+)""", line)
            if py_fn_match:
                stype = "class" if "class" in line else "function"
                return ResolvedSymbol(
                    symbol_name=py_fn_match.group(1),
                    symbol_type=stype,
                    confidence="heuristic",
                    start_line=i + 1,
                )

            # JS / TS
            js_fn = JS_FN_PATTERN.search(line)
            if js_fn:
                name = js_fn.group(1) or js_fn.group(2)
                return ResolvedSymbol(symbol_name=name, symbol_type="function", confidence="heuristic", start_line=i + 1)

            js_cls = JS_CLASS_PATTERN.search(line)
            if js_cls:
                return ResolvedSymbol(symbol_name=js_cls.group(1), symbol_type="class", confidence="heuristic", start_line=i + 1)

            # Go / Rust
            go_fn = GO_FN_PATTERN.search(line)
            if go_fn:
                return ResolvedSymbol(symbol_name=go_fn.group(1), symbol_type="function", confidence="heuristic", start_line=i + 1)

            rust_fn = RUST_FN_PATTERN.search(line)
            if rust_fn:
                return ResolvedSymbol(symbol_name=rust_fn.group(1), symbol_type="function", confidence="heuristic", start_line=i + 1)

        # Default fallback to module
        return ResolvedSymbol(
            symbol_name=module_name,
            symbol_type="module",
            confidence="heuristic",
            start_line=1,
            end_line=len(lines),
        )
