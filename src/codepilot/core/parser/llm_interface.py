"""Model-context interface preparing structured task context for future local LLM integration.

Creates token-budgeted, symbol-aware context bundles combining instruction tasks, target symbol
definitions, surrounding file contents, and project architecture summaries without loading or invoking any model.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from codepilot.core.analyzer.models import ArchitectureReport
from codepilot.core.parser.models import InstructionTask
from codepilot.logging_config import get_logger

logger = get_logger("core.parser.llm_interface")

# Sensitive file exclusion patterns for prompt safety
SENSITIVE_FILENAME_PATTERNS = {
    ".env",
    ".env.local",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_rsa.pub",
}


@dataclass
class ModelTaskContext:
    """Structured context bundle prepared for a local coding LLM to execute a task."""

    task: InstructionTask
    file_path: str
    target_symbol: Optional[str]
    target_symbol_type: Optional[str]
    target_code_snippet: str
    file_imports: List[str] = field(default_factory=list)
    project_architecture_summary: Optional[str] = None
    prompt_text: str = ""
    estimated_tokens: int = 0
    is_safe: bool = True
    safety_warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "file_path": self.file_path,
            "target_symbol": self.target_symbol,
            "target_symbol_type": self.target_symbol_type,
            "target_code_snippet": self.target_code_snippet,
            "file_imports": self.file_imports,
            "project_architecture_summary": self.project_architecture_summary,
            "prompt_text": self.prompt_text,
            "estimated_tokens": self.estimated_tokens,
            "is_safe": self.is_safe,
            "safety_warning": self.safety_warning,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelTaskContext":
        task = InstructionTask.from_dict(data.get("task", {}))
        return cls(
            task=task,
            file_path=data.get("file_path", ""),
            target_symbol=data.get("target_symbol"),
            target_symbol_type=data.get("target_symbol_type"),
            target_code_snippet=data.get("target_code_snippet", ""),
            file_imports=data.get("file_imports", []),
            project_architecture_summary=data.get("project_architecture_summary"),
            prompt_text=data.get("prompt_text", ""),
            estimated_tokens=data.get("estimated_tokens", 0),
            is_safe=data.get("is_safe", True),
            safety_warning=data.get("safety_warning"),
        )


class TaskContextBuilder:
    """Builds token-budgeted prompt context objects for an InstructionTask."""

    @staticmethod
    def build_task_context(
        task: InstructionTask,
        project_root: Path,
        arch_report: Optional[ArchitectureReport] = None,
        max_snippet_lines: int = 40,
        token_budget: int = 1500,
    ) -> ModelTaskContext:
        """Create a structured ModelTaskContext for a given instruction task."""
        full_path = project_root / task.file_path

        # Hardware & security check: exclude sensitive files
        if full_path.name.lower() in SENSITIVE_FILENAME_PATTERNS or full_path.suffix.lower() in (
            ".key",
            ".pem",
            ".p12",
        ):
            return ModelTaskContext(
                task=task,
                file_path=task.file_path,
                target_symbol=task.target_symbol,
                target_symbol_type=task.target_symbol_type,
                target_code_snippet="",
                is_safe=False,
                safety_warning="Target file is a sensitive credential/secret file and is excluded from model context.",
                estimated_tokens=50,
            )

        # Extract file contents and snippet around line_number
        lines: List[str] = []
        if full_path.is_file():
            try:
                lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as e:
                logger.warning("Could not read file %s for task context: %s", full_path, e)

        start_line = max(1, task.line_number - 5)
        end_line = min(len(lines), start_line + max_snippet_lines)

        snippet_lines: List[str] = []
        for i in range(start_line, end_line + 1):
            marker = " >> " if i == task.line_number else "    "
            line_content = lines[i - 1] if i - 1 < len(lines) else ""
            snippet_lines.append(f"{marker}Line {i:4d}: {line_content}")

        snippet = "\n".join(snippet_lines)

        # Extract imports from architecture report if available
        imports: List[str] = []
        arch_summary: Optional[str] = None
        if arch_report:
            arch_summary = arch_report.summary
            for mod in arch_report.modules:
                if mod.file_path == task.file_path:
                    imports = [f"import {imp.module}" for imp in mod.imports[:10]]
                    break

        # Generate structured prompt text
        prompt_parts: List[str] = [
            f"# CodePilot AI — Modification Task [{task.instruction_type}]",
            f"**File:** `{task.file_path}` (Line {task.line_number})",
            f"**Target Symbol:** `{task.target_symbol or 'module'}` ({task.target_symbol_type or 'unknown'})",
            f"**Instruction:** {task.description}",
            "",
            "## Source Code Context:",
            "```python" if task.file_path.endswith((".py", ".pyi")) else "```",
            snippet,
            "```",
        ]

        if imports:
            prompt_parts.extend(["", "## Module Imports:", "\n".join(f"- `{imp}`" for imp in imports)])

        if arch_summary:
            prompt_parts.extend(["", "## Project Overview:", arch_summary])

        prompt_parts.extend(
            [
                "",
                "## Instruction Goal:",
                f"Implement the requested modification for {task.instruction_type}: '{task.description}'.",
                "Ensure changes are minimal, preserve existing code style, and do not break tests.",
            ]
        )

        prompt_text = "\n".join(prompt_parts)

        # Token budgeting (~4 characters per token estimate)
        estimated_tokens = len(prompt_text) // 4
        if estimated_tokens > token_budget:
            # Truncate snippet safely
            max_chars = token_budget * 4
            prompt_text = prompt_text[:max_chars] + "\n\n... [Context truncated to fit token budget] ..."
            estimated_tokens = token_budget

        return ModelTaskContext(
            task=task,
            file_path=task.file_path,
            target_symbol=task.target_symbol,
            target_symbol_type=task.target_symbol_type,
            target_code_snippet=snippet,
            file_imports=imports,
            project_architecture_summary=arch_summary,
            prompt_text=prompt_text,
            estimated_tokens=estimated_tokens,
            is_safe=True,
        )
