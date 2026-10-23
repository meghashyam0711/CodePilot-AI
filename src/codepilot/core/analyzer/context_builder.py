"""Local model architecture context builder.

Formats static architecture analysis into bounded, token-budgeted prompt context
for a local coding LLM without loading models or running inference.
"""

from dataclasses import asdict, dataclass, field
import fnmatch
from typing import Any, Dict, List, Optional, Set

from codepilot.core.analyzer.models import ArchitectureReport, ModuleInfo

SENSITIVE_PATTERNS = [
    "*.env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "credentials.json",
    "secrets.*",
    "*.bin",
    "*.pt",
    "*.pth",
    "*.safetensors",
    "*.gguf",
    "*.checkpoint",
    "*.h5",
    "*.ckpt",
]


@dataclass
class ModelProjectContext:
    """Structured, token-budgeted project context object for local LLM inference."""

    project_name: str
    project_type: str
    project_subtypes: List[str]
    relevant_files: List[str]
    symbols: List[Dict[str, Any]]
    context_text: str
    token_count: int
    excluded_sensitive_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ArchitectureContextBuilder:
    """Builds bounded prompt context and structured context objects for local LLMs."""

    # Approximate 4 characters per token heuristic
    CHARS_PER_TOKEN = 4

    @classmethod
    def build_model_context(
        cls,
        report: ArchitectureReport,
        max_tokens: int = 1500,
        target_files: Optional[List[str]] = None,
        task_description: Optional[str] = None,
    ) -> ModelProjectContext:
        """Produce a structured context object without invoking an LLM.

        Args:
            report: The generated ArchitectureReport.
            max_tokens: Maximum token budget for context text.
            target_files: Specific target files relevant to the active instruction.
            task_description: Optional instruction text to guide relevant file selection.

        Returns:
            ModelProjectContext: Complete structured context object with preserved line numbers.
        """
        max_chars = max_tokens * cls.CHARS_PER_TOKEN

        # Filter out sensitive files from target set
        sanitized_targets: List[str] = []
        excluded_sensitive: List[str] = []
        for tf in (target_files or []):
            is_sensitive = any(fnmatch.fnmatch(tf.lower(), pat) for pat in SENSITIVE_PATTERNS)
            if is_sensitive:
                excluded_sensitive.append(tf)
            else:
                sanitized_targets.append(tf)

        # Determine relevant files
        relevant_files: List[str] = list(sanitized_targets)

        # Include entry points if budget permits
        for ep in report.entry_points:
            if ep.file_path not in relevant_files and len(relevant_files) < 8:
                relevant_files.append(ep.file_path)

        # Extract structured symbols preserving file paths and line numbers
        symbols: List[Dict[str, Any]] = []
        target_set = set(relevant_files)

        for mod in report.modules:
            if mod.file_path in target_set:
                for cls_info in mod.classes:
                    symbols.append(
                        {
                            "kind": "class",
                            "name": cls_info.name,
                            "file_path": mod.file_path,
                            "line_number": cls_info.line_number,
                            "end_line_number": cls_info.end_line_number,
                            "base_classes": cls_info.base_classes,
                            "methods": [
                                {
                                    "name": m.name,
                                    "line_number": m.line_number,
                                    "args": m.args,
                                    "return_type": m.return_type,
                                }
                                for m in cls_info.methods
                            ],
                            "docstring": cls_info.docstring,
                        }
                    )
                for fn_info in mod.functions:
                    symbols.append(
                        {
                            "kind": "function",
                            "name": fn_info.name,
                            "file_path": mod.file_path,
                            "line_number": fn_info.line_number,
                            "end_line_number": fn_info.end_line_number,
                            "args": fn_info.args,
                            "return_type": fn_info.return_type,
                            "is_async": fn_info.is_async,
                            "docstring": fn_info.docstring,
                        }
                    )

        # Build context markdown text
        sections: List[str] = []

        # 1. Project High-Level Header
        subtypes_str = f" ({', '.join(report.project_subtypes)})" if report.project_subtypes else ""
        header = [
            "## Project Context",
            f"- **Project:** {report.project_name}",
            f"- **Type:** {report.project_type.capitalize()}{subtypes_str}",
            f"- **Files:** {report.structure.file_count} ({report.structure.total_lines_of_code} LOC)",
        ]

        if report.entry_points:
            ep_strs = [
                f"{ep.file_path} ({ep.symbol_name or ep.entry_type})"
                for ep in report.entry_points[:3]
            ]
            header.append(f"- **Entry Points:** {'; '.join(ep_strs)}")

        if report.config_files:
            header.append(f"- **Configuration:** {', '.join(report.config_files[:5])}")

        all_deps: List[str] = []
        for dm in report.dependency_manifests:
            all_deps.extend(dm.dependencies[:8])
        if all_deps:
            header.append(f"- **Dependencies:** {', '.join(all_deps[:8])}")

        if report.test_directories:
            header.append(f"- **Test Directories:** {', '.join(report.test_directories)}")

        sections.append("\n".join(header))

        # 2. Target Files Deep-Dive with Line Numbers
        if sanitized_targets:
            target_modules = [m for m in report.modules if m.file_path in set(sanitized_targets)]
            if target_modules:
                target_sec = ["### Target Files Analysis"]
                for tm in target_modules:
                    target_sec.append(f"#### `{tm.file_path}`")
                    if tm.docstring:
                        target_sec.append(f"Summary: {tm.docstring.splitlines()[0]}")

                    local_imps = [i.module for i in tm.imports if i.is_local]
                    ext_imps = [i.module for i in tm.imports if not i.is_local]
                    if local_imps:
                        target_sec.append(f"- Local imports: {', '.join(local_imps[:5])}")
                    if ext_imps:
                        target_sec.append(f"- External imports: {', '.join(ext_imps[:5])}")

                    for c in tm.classes:
                        bases = f"({', '.join(c.base_classes)})" if c.base_classes else ""
                        methods = [m.name for m in c.methods]
                        m_str = f" | methods: {', '.join(methods)}" if methods else ""
                        target_sec.append(f"- Line {c.line_number}: Class `{c.name}{bases}`{m_str}")

                    for f in tm.functions:
                        args = ", ".join(f.args)
                        ret = f" -> {f.return_type}" if f.return_type else ""
                        target_sec.append(f"- Line {f.line_number}: Function `{f.name}({args}){ret}`")

                sections.append("\n".join(target_sec))

        # 3. Overall Project Architecture & Modules
        mod_sec = ["### Project Architecture Overview"]
        if report.summary:
            mod_sec.append(report.summary)

        mod_sec.append("#### Key Modules:")
        for mod in report.modules[:25]:
            symbols_summary: List[str] = []
            if mod.classes:
                symbols_summary.append(f"{len(mod.classes)} class(es)")
            if mod.functions:
                symbols_summary.append(f"{len(mod.functions)} func(s)")
            sym_str = f" ({', '.join(symbols_summary)})" if symbols_summary else ""
            desc = f": {mod.docstring.splitlines()[0]}" if mod.docstring else ""
            mod_sec.append(f"- `{mod.file_path}`{sym_str}{desc}")

        if report.relationships:
            mod_sec.append("#### Module Dependencies:")
            for src, targets in list(report.relationships.items())[:10]:
                mod_sec.append(f"- `{src}` -> {', '.join(f'`{t}`' for t in targets[:4])}")

        sections.append("\n".join(mod_sec))

        # Assemble and enforce token limits
        full_text = "\n\n".join(sections)
        if len(full_text) > max_chars:
            truncation_notice = f"\n\n[... Project architecture context truncated to fit {max_tokens} tokens safety limit ...]"
            allowed_chars = max(0, max_chars - len(truncation_notice))
            full_text = full_text[:allowed_chars] + truncation_notice

        token_count = max(1, len(full_text) // cls.CHARS_PER_TOKEN)

        return ModelProjectContext(
            project_name=report.project_name,
            project_type=report.project_type,
            project_subtypes=report.project_subtypes,
            relevant_files=relevant_files,
            symbols=symbols,
            context_text=full_text,
            token_count=token_count,
            excluded_sensitive_files=excluded_sensitive,
        )

    @classmethod
    def build_context(
        cls,
        report: ArchitectureReport,
        max_tokens: int = 1500,
        target_files: Optional[List[str]] = None,
    ) -> str:
        """Backwards-compatible helper returning the bounded markdown prompt text."""
        model_ctx = cls.build_model_context(
            report=report,
            max_tokens=max_tokens,
            target_files=target_files,
        )
        return model_ctx.context_text
