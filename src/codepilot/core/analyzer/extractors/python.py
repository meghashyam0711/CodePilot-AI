"""Python AST static analysis extractor.

Extracts imports, classes, functions, docstrings, decorators, and main blocks
from Python source files without executing code or loading an LLM.
"""

import ast
from pathlib import Path
import sys
from typing import Any, List, Optional, Set

from codepilot.core.analyzer.extractors.base import LanguageExtractor
from codepilot.core.analyzer.models import (
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
)


def _safe_unparse(node: Optional[ast.AST]) -> Optional[str]:
    """Safely unparse an AST node to source text representation."""
    if node is None:
        return None
    try:
        return ast.unparse(node).strip()
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{_safe_unparse(node.value)}.{node.attr}"
        return None


def _extract_args(args_node: ast.arguments) -> List[str]:
    """Extract human-readable list of argument names from an AST arguments node."""
    arg_names: List[str] = []
    # Positional-only args (Python 3.8+)
    for a in getattr(args_node, "posonlyargs", []):
        arg_names.append(a.arg)
    # Standard positional args
    for a in args_node.args:
        arg_names.append(a.arg)
    # Vararg (*args)
    if args_node.vararg:
        arg_names.append(f"*{args_node.vararg.arg}")
    # Keyword-only args
    for a in args_node.kwonlyargs:
        arg_names.append(a.arg)
    # Kwarg (**kwargs)
    if args_node.kwarg:
        arg_names.append(f"**{args_node.kwarg.arg}")
    return arg_names


def _extract_decorators(decorator_list: List[ast.expr]) -> List[str]:
    """Extract decorator names or expressions."""
    decorators: List[str] = []
    for d in decorator_list:
        text = _safe_unparse(d)
        if text:
            decorators.append(text)
    return decorators


def _extract_calls(body: List[ast.stmt]) -> List[str]:
    """Collect unique called function/method names within a statement block."""
    calls: Set[str] = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Call):
            func_name = _safe_unparse(node.func)
            if func_name and not func_name.startswith("("):
                calls.add(func_name)
    return sorted(calls)


def _is_main_block(if_node: ast.If) -> bool:
    """Check if an ast.If represents `if __name__ == '__main__':`."""
    test = if_node.test
    if isinstance(test, ast.Compare):
        left = test.left
        if isinstance(left, ast.Name) and left.id == "__name__":
            for comp in test.comparators:
                if isinstance(comp, ast.Constant) and comp.value in ("__main__",):
                    return True
                # Python < 3.8 compatibility fallback
                if isinstance(comp, getattr(ast, "Str", ())) and getattr(comp, "s", "") == "__main__":
                    return True
    return False


class PythonLanguageExtractor(LanguageExtractor):
    """AST-based symbol extractor for Python source files."""

    SUPPORTED_EXTENSIONS = {".py", ".pyi"}

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS

    def extract(self, file_path: Path, content: str, project_root: Path) -> ModuleInfo:
        rel_path = str(file_path.resolve().relative_to(project_root.resolve())).replace(
            "\\", "/"
        )
        line_count = len(content.splitlines())

        try:
            tree = ast.parse(content, filename=str(file_path))
        except (SyntaxError, ValueError, MemoryError, RecursionError) as e:
            # Safely handle syntax or parse errors without crashing
            return ModuleInfo(
                file_path=rel_path,
                language="Python",
                line_count=line_count,
                parse_error=f"AST parse error: {e}",
            )

        module_docstring = ast.get_docstring(tree)
        imports: List[ImportInfo] = []
        classes: List[ClassInfo] = []
        functions: List[FunctionInfo] = []
        has_main_block = False

        # Build list of local module prefixes based on project files
        local_py_names: Set[str] = set()
        for p in project_root.rglob("*.py"):
            local_py_names.add(p.stem)

        for stmt in tree.body:
            # Check main block
            if isinstance(stmt, ast.If) and _is_main_block(stmt):
                has_main_block = True

            # Imports: import a, b as c
            elif isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    is_local = alias.name.split(".")[0] in local_py_names
                    imports.append(
                        ImportInfo(
                            module=alias.name,
                            names=[alias.name],
                            alias=alias.asname,
                            is_from=False,
                            line_number=stmt.lineno,
                            is_local=is_local,
                        )
                    )

            # Imports: from a.b import c, d as e
            elif isinstance(stmt, ast.ImportFrom):
                module_name = stmt.module or ""
                names = [a.name for a in stmt.names]
                is_local = (
                    stmt.level > 0
                    or module_name.split(".")[0] in local_py_names
                )
                imports.append(
                    ImportInfo(
                        module=module_name,
                        names=names,
                        alias=stmt.names[0].asname if len(stmt.names) == 1 else None,
                        is_from=True,
                        line_number=stmt.lineno,
                        is_local=is_local,
                    )
                )

            # Class definitions
            elif isinstance(stmt, ast.ClassDef):
                base_classes = [_safe_unparse(b) for b in stmt.bases if _safe_unparse(b)]
                class_methods: List[FunctionInfo] = []
                for item in stmt.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_methods.append(
                            FunctionInfo(
                                name=item.name,
                                line_number=item.lineno,
                                end_line_number=getattr(item, "end_lineno", item.lineno),
                                args=_extract_args(item.args),
                                return_type=_safe_unparse(item.returns),
                                decorators=_extract_decorators(item.decorator_list),
                                docstring=ast.get_docstring(item),
                                is_async=isinstance(item, ast.AsyncFunctionDef),
                                calls=_extract_calls(item.body),
                            )
                        )

                classes.append(
                    ClassInfo(
                        name=stmt.name,
                        line_number=stmt.lineno,
                        end_line_number=getattr(stmt, "end_lineno", stmt.lineno),
                        base_classes=base_classes,
                        methods=class_methods,
                        docstring=ast.get_docstring(stmt),
                        decorators=_extract_decorators(stmt.decorator_list),
                    )
                )

            # Top-level Functions
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    FunctionInfo(
                        name=stmt.name,
                        line_number=stmt.lineno,
                        end_line_number=getattr(stmt, "end_lineno", stmt.lineno),
                        args=_extract_args(stmt.args),
                        return_type=_safe_unparse(stmt.returns),
                        decorators=_extract_decorators(stmt.decorator_list),
                        docstring=ast.get_docstring(stmt),
                        is_async=isinstance(stmt, ast.AsyncFunctionDef),
                        calls=_extract_calls(stmt.body),
                    )
                )

        return ModuleInfo(
            file_path=rel_path,
            language="Python",
            imports=imports,
            classes=classes,
            functions=functions,
            has_main_block=has_main_block,
            line_count=line_count,
            docstring=module_docstring,
        )
