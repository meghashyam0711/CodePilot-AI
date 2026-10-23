"""Instruction parser engine orchestrating repository scanning and structured task creation."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import List, Optional, Tuple, Union

from codepilot.config import CodePilotConfig, load_config
from codepilot.core.analyzer.discovery import discover_files
from codepilot.core.parser.comment_parser import InstructionCommentParser
from codepilot.core.parser.models import InstructionTask, ParseResult
from codepilot.exceptions import ScanError
from codepilot.logging_config import get_logger

logger = get_logger("core.parser.engine")


class InstructionParserEngine:
    """Engine that coordinates comment parsing and symbol resolution across a repository."""

    def __init__(
        self,
        project_root: Optional[Union[str, Path]] = None,
        config: Optional[CodePilotConfig] = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path.cwd().resolve()
        )
        self.config = config or load_config(project_root=self.project_root)
        self.comment_parser = InstructionCommentParser(project_root=self.project_root)

    def parse(
        self,
        max_files: Optional[int] = None,
        max_file_size: Optional[int] = None,
        max_depth: Optional[int] = None,
    ) -> ParseResult:
        """Scan repository source files and extract structured InstructionTasks."""
        root = self.project_root
        if not root.is_dir():
            raise ScanError(f"Target directory does not exist or is not a directory: {root}")

        try:
            # Discover valid, non-binary, non-protected source files
            discovered_files, _ = discover_files(
                project_root=root,
                max_files=max_files or 5000,
                max_file_size=max_file_size or getattr(self.config, "max_input_size", 1_000_000),
                max_depth=max_depth or 10,
                custom_ignore_dirs=self.config.protected_directories,
                custom_ignore_patterns=self.config.protected_files,
            )

            all_tasks: List[InstructionTask] = []
            all_warnings: List[str] = []

            for file_path in discovered_files:
                tasks, warnings = self.comment_parser.parse_file(file_path=file_path)
                all_tasks.extend(tasks)
                all_warnings.extend(warnings)

            # Calculate counts by type
            type_counter = Counter(t.instruction_type for t in all_tasks)
            counts_by_type = dict(type_counter.most_common())

            return ParseResult(
                project_root=str(root),
                scanned_files_count=len(discovered_files),
                tasks=all_tasks,
                warnings=all_warnings,
                counts_by_type=counts_by_type,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        except Exception as e:
            raise ScanError(f"Failed to parse instruction comments: {e}") from e

    parse_repository = parse


def parse_repository(
    project_root: Path,
    config: Optional[CodePilotConfig] = None,
) -> ParseResult:
    """Convenience function to parse AI instruction comments across a repository."""
    engine = InstructionParserEngine(project_root=project_root, config=config)
    return engine.parse()


def parse_file(
    file_path: Path,
    project_root: Optional[Path] = None,
) -> Tuple[List[InstructionTask], List[str]]:
    """Convenience function to parse instruction comments from a single file."""
    root = project_root or (file_path.parent if file_path.is_file() else Path.cwd())
    parser = InstructionCommentParser(project_root=root)
    return parser.parse_file(file_path=file_path)
