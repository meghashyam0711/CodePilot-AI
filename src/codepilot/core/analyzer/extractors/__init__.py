"""Extractor registry and factory."""

from pathlib import Path
from typing import List, Optional

from codepilot.core.analyzer.extractors.base import LanguageExtractor
from codepilot.core.analyzer.extractors.generic import GenericLanguageExtractor
from codepilot.core.analyzer.extractors.python import PythonLanguageExtractor


DEFAULT_EXTRACTORS: List[LanguageExtractor] = [
    PythonLanguageExtractor(),
    GenericLanguageExtractor(),
]


def get_extractor_for_file(
    file_path: Path,
    custom_extractors: Optional[List[LanguageExtractor]] = None,
) -> Optional[LanguageExtractor]:
    """Find the first matching language extractor for a given file."""
    extractors = custom_extractors or DEFAULT_EXTRACTORS
    for extractor in extractors:
        if extractor.can_handle(file_path):
            return extractor
    return None
