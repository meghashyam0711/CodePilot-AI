"""Base interface for programming language extractors."""

from abc import ABC, abstractmethod
from pathlib import Path

from codepilot.core.analyzer.models import ModuleInfo


class LanguageExtractor(ABC):
    """Abstract base class for extracting architectural symbols from source code."""

    @abstractmethod
    def can_handle(self, file_path: Path) -> bool:
        """Return True if this extractor can parse the given file."""

    @abstractmethod
    def extract(self, file_path: Path, content: str, project_root: Path) -> ModuleInfo:
        """Extract architectural information (imports, classes, functions, entry points)."""
