"""Repository file discovery, project type classification, and manifest parsing."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

from codepilot.core.analyzer.gitignore import GitIgnoreMatcher, is_safe_path
from codepilot.core.analyzer.models import (
    DependencyManifestInfo,
    ProjectStructure,
)
from codepilot.logging_config import get_logger

logger = get_logger("core.analyzer.discovery")

MAX_DEFAULT_FILE_SIZE = 1_048_576  # 1 MB safety ceiling
MAX_DEFAULT_FILES = 5_000  # Prevent runaway scans in massive workspaces

# Map extensions to primary human-readable language names
LANGUAGE_EXTENSIONS: Dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JSX",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".c": "C",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".h": "C/C++ Header",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "SASS",
    ".sql": "SQL",
    ".toml": "TOML",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".xml": "XML",
    ".md": "Markdown",
    ".rst": "ReStructuredText",
    ".txt": "Text",
}

KEY_CONFIG_FILENAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "pytest.ini",
    "tox.ini",
    ".flake8",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".codepilot.toml",
    "package.json",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
}


def is_binary_file(file_path: Path) -> bool:
    """Check if file is binary by inspecting first 4096 bytes."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(4096)
            return b"\x00" in chunk
    except OSError:
        return True


def detect_git_root(start_path: Path) -> Optional[Path]:
    """Find the root of the Git repository containing start_path."""
    git_root, _ = detect_git_info(start_path)
    return git_root


def detect_git_info(start_path: Path) -> Tuple[Optional[Path], Optional[str]]:
    """Find the root of the Git repository and current active branch if available."""
    curr = start_path.resolve()
    git_root: Optional[Path] = None
    while curr != curr.parent:
        if (curr / ".git").exists():
            git_root = curr
            break
        curr = curr.parent

    if not git_root:
        return None, None

    branch: Optional[str] = None
    head_file = git_root / ".git" / "HEAD"
    if head_file.is_file():
        try:
            head_content = head_file.read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                branch = head_content[len("ref: refs/heads/"):].strip()
            elif len(head_content) >= 7:
                branch = head_content[:8]
        except OSError:
            pass

    return git_root, branch


def detect_test_suites(
    project_root: Path,
    discovered_files: List[Path],
) -> Tuple[List[str], Dict[str, Any]]:
    """Identify test directories and test configuration."""
    test_dirs: Set[str] = set()
    test_config: Dict[str, Any] = {}
    root = project_root.resolve()

    for f in discovered_files:
        try:
            rel = str(f.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            continue

        parts = rel.split("/")
        for p in parts[:-1]:
            if p.lower() in ("tests", "test", "spec", "specs", "__tests__", "testing"):
                test_dirs.add(p)

        name_lower = f.name.lower()
        if name_lower == "pytest.ini":
            test_config["runner"] = "pytest"
            test_config["config_file"] = rel
        elif name_lower == "tox.ini":
            test_config["tox_config"] = rel
        elif name_lower in ("jest.config.js", "jest.config.ts"):
            test_config["runner"] = "jest"
            test_config["config_file"] = rel
        elif name_lower == "pyproject.toml":
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
                if "tool" in data and "pytest" in data["tool"]:
                    test_config["runner"] = "pytest"
                    test_config["pytest_in_pyproject"] = True
            except Exception:
                pass

    return sorted(test_dirs), test_config


def discover_files(
    project_root: Path,
    max_files: int = MAX_DEFAULT_FILES,
    max_file_size: int = MAX_DEFAULT_FILE_SIZE,
    max_depth: int = 10,
    custom_ignore_dirs: Optional[List[str]] = None,
    custom_ignore_patterns: Optional[List[str]] = None,
) -> Tuple[List[Path], ProjectStructure]:
    """Safely traverse project_root, respecting .gitignore, depth, and file constraints."""
    root = project_root.resolve()
    matcher = GitIgnoreMatcher(
        project_root=root,
        custom_ignore_dirs=custom_ignore_dirs,
        custom_ignore_patterns=custom_ignore_patterns,
    )

    discovered_files: List[Path] = []
    dir_count = 0
    total_loc = 0
    excluded_count = 0
    symlinks_count = 0
    language_counts: Counter[str] = Counter()
    language_lines: Counter[str] = Counter()
    top_level_dirs: Set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda err: logger.debug("Walk error: %s", err)):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""

        # Depth limit guard
        depth = rel_dir.count("/") + 1 if rel_dir else 0
        if depth >= max_depth:
            logger.debug("Reached max traversal depth %d at %s", max_depth, rel_dir)
            dirnames[:] = []
            continue

        # Prune ignored directories in-place
        kept_dirs = []
        for d in dirnames:
            child_rel = f"{rel_dir}/{d}".strip("/")
            if not matcher.should_ignore_directory(d, child_rel):
                kept_dirs.append(d)
                if not rel_dir:
                    top_level_dirs.add(d)
            else:
                excluded_count += 1
        dirnames[:] = kept_dirs
        dir_count += len(dirnames)

        curr_dir_path = Path(dirpath)

        for filename in filenames:
            file_path = curr_dir_path / filename

            # Symlink tracking & safety
            if file_path.is_symlink():
                symlinks_count += 1
                if not is_safe_path(file_path, root):
                    logger.warning("Skipping unsafe symlink outside repo: %s", file_path)
                    excluded_count += 1
                    continue

            # Safe path check
            if not is_safe_path(file_path, root):
                excluded_count += 1
                continue

            # Check ignore rules
            if matcher.should_ignore_file(file_path):
                excluded_count += 1
                continue

            # Check file size limit
            try:
                st = file_path.stat()
                if st.st_size > max_file_size:
                    excluded_count += 1
                    continue
            except OSError:
                excluded_count += 1
                continue

            # Check binary file
            if is_binary_file(file_path):
                excluded_count += 1
                continue

            ext = file_path.suffix.lower()
            lang = LANGUAGE_EXTENSIONS.get(ext, "Other")
            language_counts[lang] += 1

            # Count lines safely for text files
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = sum(1 for _ in f)
                    total_loc += lines
                    language_lines[lang] += lines
            except Exception:
                pass

            discovered_files.append(file_path)

            if len(discovered_files) >= max_files:
                logger.warning("Reached maximum file discovery limit (%d)", max_files)
                break

        if len(discovered_files) >= max_files:
            break

    structure = ProjectStructure(
        directory_count=dir_count,
        file_count=len(discovered_files),
        total_lines_of_code=total_loc,
        excluded_count=excluded_count,
        symlinks_count=symlinks_count,
        languages=dict(language_counts.most_common()),
        language_lines=dict(language_lines.most_common()),
        top_level_directories=sorted(top_level_dirs),
    )

    return discovered_files, structure



def detect_project_type(
    project_root: Path,
    discovered_files: List[Path],
    structure: ProjectStructure,
) -> Tuple[str, List[str]]:
    """Determine the primary project type and architectural subtypes."""
    filenames = {f.name.lower() for f in discovered_files}
    subtypes: List[str] = []

    # Check Python
    has_pyproject = "pyproject.toml" in filenames
    has_setup = "setup.py" in filenames or "setup.cfg" in filenames
    has_reqs = "requirements.txt" in filenames
    py_files_count = structure.languages.get("Python", 0)

    if has_pyproject or has_setup or has_reqs or py_files_count > 0:
        if py_files_count >= structure.file_count * 0.3 or has_pyproject or has_setup:
            if "src" in structure.top_level_directories or has_setup or has_pyproject:
                subtypes.append("package")

            # Check if CLI tool
            for f in discovered_files:
                if f.name.lower() in ("cli.py", "__main__.py"):
                    subtypes.append("cli")
                    break

            return "python", sorted(set(subtypes))

    # Check Node / JS / TS
    if "package.json" in filenames:
        ts_count = structure.languages.get("TypeScript", 0)
        js_count = structure.languages.get("JavaScript", 0)
        primary = "typescript" if ts_count > js_count else "javascript"
        subtypes.append("node")
        return primary, subtypes

    # Check Rust
    if "cargo.toml" in filenames or structure.languages.get("Rust", 0) > 0:
        return "rust", ["cargo"]

    # Check Go
    if "go.mod" in filenames or structure.languages.get("Go", 0) > 0:
        return "go", ["modules"]

    # Check Java
    if "pom.xml" in filenames or "build.gradle" in filenames:
        return "java", ["jvm"]

    # Check C / C++
    c_count = structure.languages.get("C", 0) + structure.languages.get("C++", 0)
    if "cmakelists.txt" in filenames or "makefile" in filenames or c_count > 0:
        return "c/cpp", ["native"]

    return "generic", []


def find_configuration_files(
    project_root: Path,
    discovered_files: List[Path],
) -> List[str]:
    """Identify key project configuration files."""
    config_paths: List[str] = []
    root = project_root.resolve()

    for f in discovered_files:
        name_lower = f.name.lower()
        if name_lower in KEY_CONFIG_FILENAMES or f.name.startswith(".codepilot"):
            rel = str(f.resolve().relative_to(root)).replace("\\", "/")
            config_paths.append(rel)

    return sorted(config_paths)


def parse_dependency_manifests(
    project_root: Path,
    discovered_files: List[Path],
) -> List[DependencyManifestInfo]:
    """Parse declared dependencies from standard manifest files."""
    manifests: List[DependencyManifestInfo] = []
    root = project_root.resolve()

    for f in discovered_files:
        rel = str(f.resolve().relative_to(root)).replace("\\", "/")
        name = f.name.lower()

        # pyproject.toml
        if name == "pyproject.toml":
            try:
                content = tomllib.loads(f.read_text(encoding="utf-8"))
                deps: List[str] = []
                dev_deps: List[str] = []

                # PEP 621 dependencies
                proj = content.get("project", {})
                for d in proj.get("dependencies", []):
                    deps.append(d.strip())
                for opt_group in proj.get("optional-dependencies", {}).values():
                    if isinstance(opt_group, list):
                        dev_deps.extend(str(item).strip() for item in opt_group)

                # Poetry dependencies
                poetry = content.get("tool", {}).get("poetry", {})
                for dep_name in poetry.get("dependencies", {}).keys():
                    if dep_name.lower() != "python":
                        deps.append(dep_name)
                for dep_name in poetry.get("group", {}).get("dev", {}).get("dependencies", {}).keys():
                    dev_deps.append(dep_name)

                manifests.append(
                    DependencyManifestInfo(
                        manifest_path=rel,
                        manifest_type="pyproject.toml",
                        dependencies=deps,
                        dev_dependencies=dev_deps,
                    )
                )
            except Exception as e:
                logger.debug("Failed to parse pyproject.toml at %s: %s", f, e)

        # requirements.txt
        elif name == "requirements.txt":
            try:
                deps = []
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        # Extract package name before ==, >=, etc.
                        pkg_match = re.match(r"^([A-Za-z0-9_.\-]+)", line)
                        if pkg_match:
                            deps.append(pkg_match.group(1))
                manifests.append(
                    DependencyManifestInfo(
                        manifest_path=rel,
                        manifest_type="requirements.txt",
                        dependencies=deps,
                    )
                )
            except Exception as e:
                logger.debug("Failed to parse requirements.txt: %s", e)

        # package.json
        elif name == "package.json":
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                deps = list(data.get("dependencies", {}).keys())
                dev_deps = list(data.get("devDependencies", {}).keys())
                manifests.append(
                    DependencyManifestInfo(
                        manifest_path=rel,
                        manifest_type="package.json",
                        dependencies=deps,
                        dev_dependencies=dev_deps,
                    )
                )
            except Exception as e:
                logger.debug("Failed to parse package.json: %s", e)

        # Cargo.toml
        elif name == "cargo.toml":
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
                deps = list(data.get("dependencies", {}).keys())
                dev_deps = list(data.get("dev-dependencies", {}).keys())
                manifests.append(
                    DependencyManifestInfo(
                        manifest_path=rel,
                        manifest_type="Cargo.toml",
                        dependencies=deps,
                        dev_dependencies=dev_deps,
                    )
                )
            except Exception as e:
                logger.debug("Failed to parse Cargo.toml: %s", e)

    return manifests
