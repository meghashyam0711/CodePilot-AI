"""Gitignore matcher, exclusion rules, and safe path validator.

Provides standard library-based pattern matching for .gitignore rules,
built-in standard exclusion filters, and path traversal guards.
"""

import fnmatch
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Optional, Set, Tuple


DEFAULT_IGNORED_DIRS: Set[str] = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".coverage",
    "htmlcov",
    "node_modules",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "eggs",
    ".eggs",
    ".codepilot",
    ".next",
    ".nuxt",
    "target",  # Rust / Java
    "bin",
    "obj",
    "checkpoints",
    "weights",
    ".huggingface",
}

DEFAULT_IGNORED_PATTERNS: List[str] = [
    # Build & package artifacts
    "*.egg-info",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".DS_Store",
    "Thumbs.db",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.obj",
    "*.o",
    "*.a",
    "*.lib",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "poetry.lock",
    "Cargo.lock",
    # Large model weights & checkpoints
    "*.bin",
    "*.pt",
    "*.pth",
    "*.onnx",
    "*.safetensors",
    "*.gguf",
    "*.checkpoint",
    "*.h5",
    "*.ckpt",
    "*.tflite",
    # Sensitive credentials & secrets
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "credentials.json",
    ".env",
    ".env.*",
]



class GitIgnoreRule:
    """Represents a single parsed .gitignore rule."""

    def __init__(self, pattern: str, base_dir: Path) -> None:
        self.original = pattern
        self.base_dir = base_dir.resolve()
        self.is_negation = pattern.startswith("!")
        clean_pattern = pattern[1:] if self.is_negation else pattern
        self.dir_only = clean_pattern.endswith("/")
        clean_pattern = clean_pattern.rstrip("/")

        # Leading slash means pattern is relative to base_dir
        if clean_pattern.startswith("/"):
            self.relative_to_base = True
            clean_pattern = clean_pattern[1:]
        elif "/" in clean_pattern:
            self.relative_to_base = True
        else:
            self.relative_to_base = False

        self.pattern = clean_pattern

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        """Check whether the rule matches a path relative to repository root."""
        if self.dir_only and not is_dir:
            return False

        rel_path = rel_path.replace("\\", "/").strip("/")

        if self.relative_to_base:
            # Match path exactly or under directory
            if fnmatch.fnmatch(rel_path, self.pattern):
                return True
            if fnmatch.fnmatch(rel_path, f"{self.pattern}/*"):
                return True
            return False

        # Pattern without slash matches any filename or dirname in path
        path_parts = rel_path.split("/")
        for part in path_parts:
            if fnmatch.fnmatch(part, self.pattern):
                return True
        return False


class GitIgnoreMatcher:
    """Manages .gitignore rules and default repository exclusions."""

    def __init__(
        self,
        project_root: Path,
        custom_ignore_dirs: Optional[List[str]] = None,
        custom_ignore_patterns: Optional[List[str]] = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.ignored_dirs = set(DEFAULT_IGNORED_DIRS)
        if custom_ignore_dirs:
            self.ignored_dirs.update(d.strip("/\\").lower() for d in custom_ignore_dirs)

        self.ignored_patterns = list(DEFAULT_IGNORED_PATTERNS)
        if custom_ignore_patterns:
            self.ignored_patterns.extend(custom_ignore_patterns)

        self.rules: List[GitIgnoreRule] = []
        self._load_gitignore_files()

    def _load_gitignore_files(self) -> None:
        """Load .gitignore files from the root of the project."""
        root_gitignore = self.project_root / ".gitignore"
        if root_gitignore.is_file():
            self._parse_file(root_gitignore, self.project_root)

    def _parse_file(self, gitignore_path: Path, base_dir: Path) -> None:
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                self.rules.append(GitIgnoreRule(line, base_dir))
        except OSError:
            pass

    def should_ignore_directory(self, dir_name: str, rel_path: str) -> bool:
        """Check whether a directory should be pruned from scanning."""
        dir_clean = dir_name.lower().strip("/\\")

        # Built-in directory exclusions
        if dir_clean in self.ignored_dirs:
            return True

        for pat in self.ignored_patterns:
            if fnmatch.fnmatch(dir_clean, pat):
                return True

        # Check gitignore rules
        return self._check_rules(rel_path, is_dir=True)

    def should_ignore_file(self, file_path: Path) -> bool:
        """Check whether a file should be excluded from analysis."""
        try:
            rel_path = str(file_path.resolve().relative_to(self.project_root)).replace(
                "\\", "/"
            )
        except ValueError:
            return True

        filename = file_path.name

        # Check parent directory components
        parts = rel_path.split("/")[:-1]
        for part in parts:
            if part.lower() in self.ignored_dirs:
                return True

        # Check pattern exclusions on filename
        for pat in self.ignored_patterns:
            if fnmatch.fnmatch(filename, pat) or fnmatch.fnmatch(rel_path, pat):
                return True

        # Check gitignore rules
        return self._check_rules(rel_path, is_dir=False)

    def _check_rules(self, rel_path: str, is_dir: bool) -> bool:
        """Evaluate gitignore rules in order; later rules override earlier ones."""
        ignored = False
        for rule in self.rules:
            if rule.matches(rel_path, is_dir):
                if rule.is_negation:
                    ignored = False
                else:
                    ignored = True
        return ignored


def is_safe_path(target_path: Path, project_root: Path) -> bool:
    """Verify that a path resides safely inside the project root without traversal."""
    try:
        resolved_root = project_root.resolve()
        resolved_target = target_path.resolve()
        resolved_target.relative_to(resolved_root)
        return True
    except (ValueError, OSError):
        return False
