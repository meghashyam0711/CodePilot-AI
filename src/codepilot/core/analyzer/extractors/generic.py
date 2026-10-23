"""Generic fallback extractor for non-Python languages using lightweight heuristics."""

from pathlib import Path
import re
from typing import List

from codepilot.core.analyzer.extractors.base import LanguageExtractor
from codepilot.core.analyzer.models import (
    ClassInfo,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
)

LANGUAGE_EXTENSION_MAP = {
    ".js": "JavaScript",
    ".jsx": "JSX",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
}

# Regex patterns for basic symbol detection across C-family / JS languages
JS_IMPORT_PATTERN = re.compile(
    r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\s*\(\s*['"]([^'"]+)['"]\))"""
)
GO_IMPORT_PATTERN = re.compile(r"""import\s+(?:\(\s*([^)]+)\s*\)|['"]([^'"]+)['"])""")
RUST_USE_PATTERN = re.compile(r"""use\s+([^;]+);""")
C_INCLUDE_PATTERN = re.compile(r"""#include\s+[<"]([^>"]+)[>"]""")

JS_FUNCTION_PATTERN = re.compile(
    r"""(?:function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)|const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>)"""
)
GO_FUNCTION_PATTERN = re.compile(r"""func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)\s*\(([^)]*)\)""")
RUST_FUNCTION_PATTERN = re.compile(r"""fn\s+([A-Za-z0-9_]+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)""")


class GenericLanguageExtractor(LanguageExtractor):
    """Fallback extractor extracting basic metrics and symbols for non-Python files."""

    def can_handle(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in LANGUAGE_EXTENSION_MAP

    def extract(self, file_path: Path, content: str, project_root: Path) -> ModuleInfo:
        rel_path = str(file_path.resolve().relative_to(project_root.resolve())).replace(
            "\\", "/"
        )
        ext = file_path.suffix.lower()
        lang = LANGUAGE_EXTENSION_MAP.get(ext, "Unknown")
        lines = content.splitlines()
        line_count = len(lines)

        imports: List[ImportInfo] = []
        functions: List[FunctionInfo] = []
        classes: List[ClassInfo] = []
        has_main_block = False

        for idx, line in enumerate(lines):
            line_stripped = line.strip()

            # JS / TS imports
            if ext in (".js", ".jsx", ".ts", ".tsx"):
                m_imp = JS_IMPORT_PATTERN.search(line_stripped)
                if m_imp:
                    mod = m_imp.group(1) or m_imp.group(2) or ""
                    imports.append(
                        ImportInfo(
                            module=mod,
                            names=[mod.split("/")[-1]],
                            line_number=idx + 1,
                            is_local=mod.startswith("."),
                        )
                    )
                m_fn = JS_FUNCTION_PATTERN.search(line_stripped)
                if m_fn:
                    name = m_fn.group(1) or m_fn.group(3) or "anonymous"
                    args = [a.strip() for a in (m_fn.group(2) or m_fn.group(4) or "").split(",") if a.strip()]
                    functions.append(
                        FunctionInfo(
                            name=name,
                            line_number=idx + 1,
                            end_line_number=idx + 1,
                            args=args,
                            is_async="async" in line_stripped,
                        )
                    )

            # Go imports & functions
            elif ext == ".go":
                if "package main" in line_stripped or "func main(" in line_stripped:
                    has_main_block = True
                m_fn = GO_FUNCTION_PATTERN.search(line_stripped)
                if m_fn:
                    functions.append(
                        FunctionInfo(
                            name=m_fn.group(1),
                            line_number=idx + 1,
                            end_line_number=idx + 1,
                            args=[a.strip() for a in m_fn.group(2).split(",") if a.strip()],
                        )
                    )

            # Rust use & functions
            elif ext == ".rs":
                m_use = RUST_USE_PATTERN.search(line_stripped)
                if m_use:
                    mod = m_use.group(1).strip()
                    imports.append(
                        ImportInfo(
                            module=mod,
                            names=[mod.split("::")[-1]],
                            line_number=idx + 1,
                            is_local=mod.startswith("crate::") or mod.startswith("super::"),
                        )
                    )
                if "fn main(" in line_stripped:
                    has_main_block = True
                m_fn = RUST_FUNCTION_PATTERN.search(line_stripped)
                if m_fn:
                    functions.append(
                        FunctionInfo(
                            name=m_fn.group(1),
                            line_number=idx + 1,
                            end_line_number=idx + 1,
                            args=[a.strip() for a in m_fn.group(2).split(",") if a.strip()],
                        )
                    )

            # C / C++ includes
            elif ext in (".c", ".cpp", ".cc", ".h", ".hpp"):
                m_inc = C_INCLUDE_PATTERN.search(line_stripped)
                if m_inc:
                    inc = m_inc.group(1)
                    imports.append(
                        ImportInfo(
                            module=inc,
                            names=[inc],
                            line_number=idx + 1,
                            is_local='"' in line_stripped,
                        )
                    )
                if "int main(" in line_stripped or "void main(" in line_stripped:
                    has_main_block = True

        return ModuleInfo(
            file_path=rel_path,
            language=lang,
            imports=imports,
            classes=classes,
            functions=functions,
            has_main_block=has_main_block,
            line_count=line_count,
        )
