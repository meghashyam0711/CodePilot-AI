"""Language adapter interface and implementations for comment parsing and symbol resolution.

Provides an extensible abstraction layer allowing language-specific tokenization,
comment syntax extraction, and AST/symbol resolution for Python and future language extensions.
"""

from abc import ABC, abstractmethod
import io
from pathlib import Path
import re
import tokenize
from typing import Dict, List, Optional, Set, Tuple

from codepilot.core.parser.models import RawInstructionMatch
from codepilot.core.parser.symbol_resolver import ResolvedSymbol, SymbolResolver
from codepilot.logging_config import get_logger

logger = get_logger("core.parser.adapters")

# Regex to match AI instruction comments across single-line and multiline comment styles:
# Python/Shell: # AI-FIX: ...
# JS/TS/C/Java: // AI-TASK: ... or /* AI-REFACTOR: ... */
# HTML/XML: <!-- AI-TEST: ... -->
# SQL: -- AI-DOCS: ...
AI_INSTRUCTION_REGEX = re.compile(
    r"""(?:^|\s)(?:#|//|--|<!--|/\*)\s*(AI-[A-Za-z0-9_-]+|AI|CODEPILOT)(?:\s*[:\-]\s*(.*?))?(?:\*/|-->)?$""",
    re.IGNORECASE,
)

# Multiline block comment opener e.g. /* AI-FIX: ... or <!-- AI-TASK: ...
BLOCK_OPEN_REGEX = re.compile(
    r"""(?:^|\s)(?:<!--|/\*)\s*(AI-[A-Za-z0-9_-]+|AI|CODEPILOT)(?:\s*[:\-]\s*(.*?))?$""",
    re.IGNORECASE,
)

# Multiline block comment closer e.g. ... */ or ... -->
BLOCK_CLOSE_REGEX = re.compile(r"""(.*?)(?:\*/|-->)\s*$""")


class BaseLanguageAdapter(ABC):
    """Abstract base class for language-specific comment parsing and symbol resolution."""

    @property
    @abstractmethod
    def language_id(self) -> str:
        """Unique identifier for the language (e.g. 'python', 'generic')."""
        pass

    @property
    @abstractmethod
    def supported_extensions(self) -> Set[str]:
        """Set of file extensions handled by this adapter (e.g. {'.py', '.pyi'})."""
        pass

    @abstractmethod
    def extract_comments(
        self,
        lines: List[str],
        content: str,
        rel_path: str,
    ) -> Tuple[List[RawInstructionMatch], List[str]]:
        """Extract instruction comments from file content.

        Args:
            lines: List of text lines in the file.
            content: Complete file content as a single string.
            rel_path: Repository-relative path for warning messages.

        Returns:
            Tuple of (list of RawInstructionMatch, list of warning strings).
        """
        pass

    @abstractmethod
    def resolve_symbol(
        self,
        file_path: Path,
        line_number: int,
        content: str,
        end_line_number: Optional[int] = None,
    ) -> ResolvedSymbol:
        """Resolve the target symbol (function/class/method/module) for an instruction."""
        pass


class PythonLanguageAdapter(BaseLanguageAdapter):
    """Python-specific comment adapter using standard library tokenize and AST.

    Guarantees that string literals and docstrings are never mistaken for comments.
    """

    @property
    def language_id(self) -> str:
        return "python"

    @property
    def supported_extensions(self) -> Set[str]:
        return {".py", ".pyi"}

    def extract_comments(
        self,
        lines: List[str],
        content: str,
        rel_path: str,
    ) -> Tuple[List[RawInstructionMatch], List[str]]:
        matches: List[RawInstructionMatch] = []
        warnings: List[str] = []

        try:
            reader = io.BytesIO(content.encode("utf-8")).readline
            tokens = list(tokenize.tokenize(reader))
        except Exception as e:
            warnings.append(f"{rel_path}: Tokenizer fallback due to syntax/encoding issue: {e}")
            # Fall back to line-by-line scanner if tokenize encounters an unrecoverable syntax error
            fallback_adapter = GenericLanguageAdapter()
            return fallback_adapter.extract_comments(lines, content, rel_path)

        # Filter strictly for tokenize.COMMENT tokens (guaranteeing strings & docstrings are ignored)
        comment_tokens = [tok for tok in tokens if tok.type == tokenize.COMMENT]
        comment_by_line: Dict[int, str] = {tok.start[0]: tok.string for tok in comment_tokens}

        idx = 0
        sorted_lines = sorted(comment_by_line.keys())

        while idx < len(sorted_lines):
            line_no = sorted_lines[idx]
            raw_comment = comment_by_line[line_no]

            m = AI_INSTRUCTION_REGEX.search(raw_comment)
            if not m:
                idx += 1
                continue

            raw_type = m.group(1)
            raw_desc = (m.group(2) or "").strip()

            continuation: List[str] = []
            next_idx = idx + 1
            curr_line = line_no

            while next_idx < len(sorted_lines):
                peek_line = sorted_lines[next_idx]
                if peek_line == curr_line + 1:
                    peek_comment = comment_by_line[peek_line].strip()
                    # Look for continuation bullet point e.g. "# - ..." or "#   - ..."
                    cont_match = re.match(r"""^#\s*[-*]\s*(.+)$""", peek_comment)
                    if cont_match:
                        continuation.append(cont_match.group(1).strip())
                        curr_line = peek_line
                        next_idx += 1
                        continue
                break

            matches.append(
                RawInstructionMatch(
                    line_number=line_no,
                    raw_type=raw_type,
                    description=raw_desc,
                    raw_comment=raw_comment,
                    continuation_lines=continuation,
                )
            )

            idx = next_idx

        return matches, warnings

    def resolve_symbol(
        self,
        file_path: Path,
        line_number: int,
        content: str,
        end_line_number: Optional[int] = None,
    ) -> ResolvedSymbol:
        module_name = file_path.stem
        effective_line = end_line_number or line_number
        py_symbol = SymbolResolver._resolve_python_ast(
            line_number=line_number,
            content=content,
            module_name=module_name,
            effective_line=effective_line,
        )
        if py_symbol:
            return py_symbol
        return SymbolResolver._resolve_heuristic(effective_line, content, module_name)


class GenericLanguageAdapter(BaseLanguageAdapter):
    """Fallback comment adapter for JavaScript, TypeScript, Go, Rust, Java, C/C++, HTML, SQL."""

    @property
    def language_id(self) -> str:
        return "generic"

    @property
    def supported_extensions(self) -> Set[str]:
        return {
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            ".go",
            ".rs",
            ".java",
            ".c",
            ".cpp",
            ".cc",
            ".h",
            ".hpp",
            ".cs",
            ".html",
            ".htm",
            ".xml",
            ".sql",
            ".sh",
            ".bash",
            ".yaml",
            ".yml",
        }

    def extract_comments(
        self,
        lines: List[str],
        content: str,
        rel_path: str,
    ) -> Tuple[List[RawInstructionMatch], List[str]]:
        matches: List[RawInstructionMatch] = []
        warnings: List[str] = []

        i = 0
        num_lines = len(lines)

        while i < num_lines:
            line = lines[i]
            line_no = i + 1

            # Check single-line or closed block comment
            m = AI_INSTRUCTION_REGEX.search(line)
            if m:
                raw_type = m.group(1)
                raw_desc = (m.group(2) or "").strip()

                continuation: List[str] = []
                j = i + 1
                while j < num_lines:
                    peek_line = lines[j].strip()
                    cont_match = re.match(r"""^(?:#|//|--)\s*[-*]\s*(.+)$""", peek_line)
                    if cont_match:
                        continuation.append(cont_match.group(1).strip())
                        j += 1
                    else:
                        break

                matches.append(
                    RawInstructionMatch(
                        line_number=line_no,
                        raw_type=raw_type,
                        description=raw_desc,
                        raw_comment=line.strip(),
                        continuation_lines=continuation,
                    )
                )
                i = j
                continue

            # Check open multiline block comment
            block_open = BLOCK_OPEN_REGEX.search(line)
            if block_open:
                raw_type = block_open.group(1)
                first_desc = (block_open.group(2) or "").strip()
                block_lines: List[str] = [first_desc] if first_desc else []
                raw_block = [line.strip()]

                j = i + 1
                closed = False
                while j < num_lines:
                    b_line = lines[j]
                    raw_block.append(b_line.strip())
                    close_m = BLOCK_CLOSE_REGEX.search(b_line)
                    if close_m:
                        rem = (close_m.group(1) or "").strip()
                        rem = re.sub(r"""^[\s*]+""", "", rem).strip()
                        if rem:
                            block_lines.append(rem)
                        closed = True
                        j += 1
                        break
                    else:
                        clean_b = re.sub(r"""^[\s*]+""", "", b_line).strip()
                        if clean_b:
                            block_lines.append(clean_b)
                        j += 1

                if not closed:
                    warnings.append(f"{rel_path}:{line_no}: Unclosed multiline block comment")

                primary_desc = block_lines[0] if block_lines else ""
                extra_lines = block_lines[1:] if len(block_lines) > 1 else []

                matches.append(
                    RawInstructionMatch(
                        line_number=line_no,
                        raw_type=raw_type,
                        description=primary_desc,
                        raw_comment="\n".join(raw_block),
                        continuation_lines=extra_lines,
                        warning="Unclosed block comment" if not closed else None,
                    )
                )
                i = j
                continue

            i += 1

        return matches, warnings

    def resolve_symbol(
        self,
        file_path: Path,
        line_number: int,
        content: str,
        end_line_number: Optional[int] = None,
    ) -> ResolvedSymbol:
        module_name = file_path.stem
        effective_line = end_line_number or line_number
        return SymbolResolver._resolve_heuristic(effective_line, content, module_name)


class LanguageAdapterRegistry:
    """Registry managing language adapters by file extension."""

    def __init__(self) -> None:
        self._adapters: List[BaseLanguageAdapter] = []
        self._extension_map: Dict[str, BaseLanguageAdapter] = {}
        self._generic_adapter = GenericLanguageAdapter()

        # Register default adapters
        self.register(PythonLanguageAdapter())
        self.register(self._generic_adapter)

    def register(self, adapter: BaseLanguageAdapter) -> None:
        """Register a new language adapter."""
        self._adapters.append(adapter)
        for ext in adapter.supported_extensions:
            self._extension_map[ext.lower()] = adapter

    def get_adapter(self, file_path: Path) -> BaseLanguageAdapter:
        """Get the appropriate language adapter for a file."""
        suffix = file_path.suffix.lower()
        return self._extension_map.get(suffix, self._generic_adapter)


# Global default registry instance
DEFAULT_ADAPTER_REGISTRY = LanguageAdapterRegistry()
