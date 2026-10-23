"""Comment parser for detecting CodePilot AI instruction comments.

Uses Python tokenization to ensure strings and docstrings are never mistaken for comments,
and supports single-line, multiline, and cross-language comment conventions.
"""

from dataclasses import dataclass, field
import hashlib
import io
from pathlib import Path
import re
import tokenize
from typing import Dict, List, Optional, Set, Tuple

from codepilot.core.parser.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    GenericLanguageAdapter,
    LanguageAdapterRegistry,
    PythonLanguageAdapter,
)
from codepilot.core.parser.models import (
    INSTRUCTION_TYPE_ALIASES,
    SUPPORTED_INSTRUCTION_TYPES,
    InstructionTask,
    InstructionType,
    RawInstructionMatch,
)
from codepilot.core.parser.symbol_resolver import SymbolResolver
from codepilot.logging_config import get_logger

logger = get_logger("core.parser.comment_parser")

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


class InstructionCommentParser:
    """Parses source file content and extracts structured InstructionTask instances."""

    def __init__(
        self,
        project_root: Optional[Path] = None,
        adapter_registry: Optional[LanguageAdapterRegistry] = None,
    ) -> None:
        self.project_root = project_root or Path.cwd()
        self.adapter_registry = adapter_registry or DEFAULT_ADAPTER_REGISTRY

    def parse_file(
        self,
        file_path: Path,
        content: Optional[str] = None,
    ) -> Tuple[List[InstructionTask], List[str]]:
        """Parse instruction comments from a file.

        Args:
            file_path: Absolute or relative path to the file.
            content: Optional file content string. If None, read from disk.

        Returns:
            Tuple of (tasks, warnings).
        """
        full_path = file_path if file_path.is_absolute() else self.project_root / file_path
        try:
            rel_path = str(full_path.resolve().relative_to(self.project_root.resolve())).replace(
                "\\", "/"
            )
        except ValueError:
            rel_path = str(file_path).replace("\\", "/")

        if content is None:
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("Could not read file %s: %s", file_path, e)
                return [], [f"{rel_path}: Read error: {e}"]

        lines = content.splitlines()

        # Step 1: Extract raw comment matches via language adapter
        adapter = self.adapter_registry.get_adapter(full_path)
        matches, parse_warnings = adapter.extract_comments(lines, content, rel_path)

        # Step 2: Convert matches to structured InstructionTasks and resolve symbols
        tasks: List[InstructionTask] = []
        seen_keys: Set[Tuple[int, str]] = set()

        for m in matches:
            # Duplicate check for same line & prompt
            dup_key = (m.line_number, m.description.strip().lower())
            if dup_key in seen_keys:
                parse_warnings.append(
                    f"{rel_path}:{m.line_number}: Duplicate instruction ignored: '{m.description}'"
                )
                continue
            seen_keys.add(dup_key)

            # Determine canonical instruction type
            raw_upper = m.raw_type.upper()
            inst_type = INSTRUCTION_TYPE_ALIASES.get(raw_upper, raw_upper)
            warning = m.warning

            # Validate whether instruction type is officially supported
            if inst_type not in SUPPORTED_INSTRUCTION_TYPES:
                if not raw_upper.startswith("AI-"):
                    # Regular text that looked like a comment
                    continue
                warning = f"Unsupported instruction type '{m.raw_type}'"

            # Check for missing description
            clean_desc = m.description.strip()
            if not clean_desc and not m.continuation_lines:
                m.is_malformed = True
                warning = warning or f"Missing description for instruction '{m.raw_type}'"

            # Merge multiline continuation if present
            full_desc = clean_desc
            if m.continuation_lines:
                continuation_text = " ".join(l.strip() for l in m.continuation_lines)
                full_desc = f"{clean_desc} {continuation_text}".strip() if clean_desc else continuation_text

            # Surrounding context lines
            start_before = max(0, m.line_number - 4)
            context_before = lines[start_before : m.line_number - 1]
            start_after = m.line_number
            end_after = min(len(lines), m.line_number + 3)
            context_after = lines[start_after:end_after]

            # Resolve target symbol using AST / heuristics
            resolved = SymbolResolver.resolve(
                file_path=full_path,
                line_number=m.line_number,
                content=content,
                end_line_number=m.line_number + len(m.continuation_lines),
            )

            # Deterministic unique ID
            hash_src = f"{rel_path}:{m.line_number}:{inst_type}:{full_desc}".encode("utf-8")
            task_id = f"task-{hashlib.sha256(hash_src).hexdigest()[:10]}"

            task = InstructionTask(
                id=task_id,
                instruction_type=inst_type,
                description=full_desc,
                file_path=rel_path,
                line_number=m.line_number,
                target_symbol=resolved.symbol_name,
                target_symbol_type=resolved.symbol_type,
                raw_comment=m.raw_comment,
                context_before=context_before,
                context_after=context_after,
                multiline_lines=m.continuation_lines,
                confidence=resolved.confidence,
                is_malformed=m.is_malformed,
                warning=warning,
            )
            tasks.append(task)

            if warning:
                parse_warnings.append(f"{rel_path}:{m.line_number}: {warning}")

        return tasks, parse_warnings

    def _extract_python_comments(
        self,
        lines: List[str],
        content: str,
        rel_path: str,
    ) -> Tuple[List[RawInstructionMatch], List[str]]:
        """Extract Python instruction comments using tokenize to guarantee strings are excluded."""
        matches: List[RawInstructionMatch] = []
        warnings: List[str] = []

        try:
            reader = io.BytesIO(content.encode("utf-8")).readline
            tokens = list(tokenize.tokenize(reader))
        except Exception as e:
            warnings.append(f"{rel_path}: Tokenizer fallback due to syntax/encoding issue: {e}")
            return self._extract_generic_comments(lines, rel_path)

        # Filter strictly for tokenize.COMMENT tokens (guaranteeing strings & docstrings are ignored)
        comment_tokens = [tok for tok in tokens if tok.type == tokenize.COMMENT]
        comment_map: Dict[int, str] = {tok.start[0]: tok.string for tok in comment_tokens}

        idx = 0
        sorted_lines = sorted(comment_map.keys())
        while idx < len(sorted_lines):
            line_no = sorted_lines[idx]
            comment_text = comment_map[line_no]

            match = AI_INSTRUCTION_REGEX.search(comment_text)
            if match:
                raw_type = match.group(1)
                desc = (match.group(2) or "").strip()

                # Look ahead for consecutive continuation comment lines (e.g. # - step 1)
                continuation_lines: List[str] = []
                next_idx = idx + 1
                while next_idx < len(sorted_lines):
                    next_line_no = sorted_lines[next_idx]
                    if next_line_no != line_no + len(continuation_lines) + 1:
                        break  # Not strictly consecutive

                    next_text = comment_map[next_line_no]
                    # If the next comment starts another AI instruction, stop continuation
                    if AI_INSTRUCTION_REGEX.search(next_text):
                        break

                    # Check if continuation line (e.g. # - ..., # *, or indented continuation)
                    clean_next = next_text.lstrip("#").strip()
                    if clean_next.startswith(("-", "*", "•", ">")) or clean_next.startswith(" "):
                        continuation_lines.append(clean_next.lstrip("-*•> "))
                        next_idx += 1
                    elif next_text.startswith("#   "):  # Indented comment continuation
                        continuation_lines.append(clean_next)
                        next_idx += 1
                    else:
                        break

                matches.append(
                    RawInstructionMatch(
                        line_number=line_no,
                        raw_type=raw_type,
                        description=desc,
                        raw_comment=comment_text,
                        continuation_lines=continuation_lines,
                    )
                )
                idx = next_idx
            else:
                idx += 1

        return matches, warnings

    def _extract_generic_comments(
        self,
        lines: List[str],
        rel_path: str,
    ) -> Tuple[List[RawInstructionMatch], List[str]]:
        """Extract comments for non-Python or fallback files, supporting block & single comments."""
        matches: List[RawInstructionMatch] = []
        warnings: List[str] = []

        in_block = False
        block_type: Optional[str] = None
        block_start_line = 0
        block_desc_lines: List[str] = []
        raw_comment_start = ""

        for idx, line in enumerate(lines, start=1):
            # If inside multiline block comment (/* ... */ or <!-- ... -->)
            if in_block:
                close_match = BLOCK_CLOSE_REGEX.search(line)
                if close_match:
                    tail = close_match.group(1).strip()
                    if tail:
                        block_desc_lines.append(tail)
                    in_block = False

                    desc = " ".join(block_desc_lines).strip()
                    matches.append(
                        RawInstructionMatch(
                            line_number=block_start_line,
                            raw_type=block_type or "AI-TASK",
                            description=desc,
                            raw_comment=raw_comment_start,
                            continuation_lines=block_desc_lines[1:] if len(block_desc_lines) > 1 else [],
                        )
                    )
                    block_type = None
                    block_desc_lines = []
                else:
                    clean_line = line.strip().lstrip("* ").strip()
                    if clean_line:
                        block_desc_lines.append(clean_line)
                continue

            # Check for block comment opener
            block_match = BLOCK_OPEN_REGEX.search(line)
            if block_match and ("*/" not in line and "-->" not in line):
                in_block = True
                block_start_line = idx
                block_type = block_match.group(1)
                initial_desc = (block_match.group(2) or "").strip()
                block_desc_lines = [initial_desc] if initial_desc else []
                raw_comment_start = line.strip()
                continue

            # Check single line match
            match = AI_INSTRUCTION_REGEX.search(line)
            if match:
                raw_type = match.group(1)
                desc = (match.group(2) or "").strip()

                # Basic quote check to prevent matching inside simple string variables
                # e.g. str_val = "// AI-FIX: false alarm"
                trimmed = line.strip()
                if (trimmed.startswith(('"', "'")) and trimmed.endswith(('"', "'"))) or (
                    "=" in line and ('"' in line or "'" in line) and not trimmed.startswith(("//", "#", "/*", "<!--", "--"))
                ):
                    # Likely a string literal assignment, skip
                    continue

                matches.append(
                    RawInstructionMatch(
                        line_number=idx,
                        raw_type=raw_type,
                        description=desc,
                        raw_comment=line.strip(),
                    )
                )

        if in_block:
            warnings.append(f"{rel_path}:{block_start_line}: Unclosed block comment for instruction '{block_type}'")

        return matches, warnings
