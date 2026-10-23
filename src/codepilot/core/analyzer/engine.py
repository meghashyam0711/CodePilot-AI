"""Main ProjectAnalyzer engine orchestrating repository architecture analysis."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, List, Optional, Union

from codepilot.config import CodePilotConfig, load_config
from codepilot.core.analyzer.discovery import (
    MAX_DEFAULT_FILE_SIZE,
    MAX_DEFAULT_FILES,
    detect_git_info,
    detect_project_type,
    detect_test_suites,
    discover_files,
    find_configuration_files,
    parse_dependency_manifests,
)
from codepilot.core.analyzer.entry_points import detect_entry_points
from codepilot.core.analyzer.extractors import get_extractor_for_file
from codepilot.core.analyzer.models import (
    ArchitectureReport,
    ModuleInfo,
)
from codepilot.core.analyzer.relationships import build_module_relationships
from codepilot.exceptions import AnalysisError
from codepilot.logging_config import get_logger

logger = get_logger("core.analyzer.engine")


class ProjectAnalyzer:
    """Static analysis engine that inspects unfamiliar Git repositories."""

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

    def analyze(
        self,
        max_files: Optional[int] = None,
        max_file_size: Optional[int] = None,
        max_depth: Optional[int] = None,
    ) -> ArchitectureReport:
        """Execute safe static analysis of the repository.

        Args:
            max_files: Maximum number of files to inspect.
            max_file_size: Maximum individual file size in bytes to read.
            max_depth: Maximum directory traversal depth.

        Returns:
            ArchitectureReport: Complete, structured architecture report.

        Raises:
            AnalysisError: If repository discovery or analysis fails.
        """
        root = self.project_root
        if not root.is_dir():
            raise AnalysisError(f"Target directory does not exist or is not a directory: {root}")

        file_limit = max_files or MAX_DEFAULT_FILES
        size_limit = max_file_size or getattr(self.config, "max_input_size", MAX_DEFAULT_FILE_SIZE)
        depth_limit = max_depth or 10

        try:
            # 1. Discover repository files
            # Filter manifest/config files from ignore patterns so they can be read for architecture analysis
            ignore_patterns = [
                p
                for p in (self.config.protected_files or [])
                if not any(
                    cfg in p.lower()
                    for cfg in ("pyproject.toml", "package.json", "cargo.toml", "go.mod")
                )
            ]
            discovered_files, structure = discover_files(
                project_root=root,
                max_files=file_limit,
                max_file_size=size_limit,
                max_depth=depth_limit,
                custom_ignore_dirs=self.config.protected_directories,
                custom_ignore_patterns=ignore_patterns,
            )

            # 2. Detect Git active branch
            _, git_branch = detect_git_info(root)

            # 3. Detect project type
            project_type, subtypes = detect_project_type(root, discovered_files, structure)

            # 4. Find key config files and dependency manifests
            config_files = find_configuration_files(root, discovered_files)
            manifests = parse_dependency_manifests(root, discovered_files)

            # 5. Detect test directories and test configuration
            test_dirs, test_config = detect_test_suites(root, discovered_files)

            # 6. Extract architectural symbols from source files
            modules: List[ModuleInfo] = []
            parse_warnings: List[str] = []
            for file_path in discovered_files:
                extractor = get_extractor_for_file(file_path)
                if not extractor:
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    mod_info = extractor.extract(file_path, content, root)
                    modules.append(mod_info)
                    if mod_info.parse_error:
                        parse_warnings.append(f"{mod_info.file_path}: {mod_info.parse_error}")
                except Exception as e:
                    parse_warnings.append(f"{file_path.name}: Failed to read/extract: {e}")
                    logger.debug("Failed to extract symbols from %s: %s", file_path, e)

            # 7. Detect and rank application entry points
            entry_points = detect_entry_points(root, modules, config_files)

            # 8. Build module relationships
            relationships = build_module_relationships(modules)

            # 9. Warnings and limitations
            warnings: List[str] = list(parse_warnings)
            if structure.excluded_count > 0:
                warnings.append(
                    f"{structure.excluded_count} item(s) safely excluded via .gitignore, size limits, or sensitive patterns."
                )

            limitations: List[str] = [
                "Python source files are analyzed with complete AST static extraction.",
                "Non-Python source files use heuristic extraction; language-specific AST engines will be added in future phases.",
                "Dynamic runtime imports (e.g. importlib) are not statically evaluated.",
            ]

            # 10. Generate concise summary
            summary = self._generate_summary(
                project_name=root.name,
                project_type=project_type,
                subtypes=subtypes,
                structure=structure,
                entry_points=entry_points,
                manifests=manifests,
            )

            return ArchitectureReport(
                project_name=root.name,
                project_root=str(root),
                project_type=project_type,
                project_subtypes=subtypes,
                git_branch=git_branch,
                structure=structure,
                entry_points=entry_points,
                config_files=config_files,
                dependency_manifests=manifests,
                test_directories=test_dirs,
                test_configuration=test_config,
                modules=modules,
                relationships=relationships,
                warnings=warnings,
                limitations=limitations,
                summary=summary,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        except AnalysisError:
            raise
        except Exception as e:
            raise AnalysisError(f"Architecture analysis failed unexpectedly: {e}") from e

    def _generate_summary(
        self,
        project_name: str,
        project_type: str,
        subtypes: List[str],
        structure: Any,
        entry_points: Any,
        manifests: Any,
    ) -> str:
        """Generate a concise narrative summary of the project architecture."""
        type_desc = project_type.capitalize()
        if subtypes:
            type_desc += f" ({', '.join(subtypes)})"

        main_langs = [f"{lang} ({count})" for lang, count in list(structure.languages.items())[:3]]
        lang_str = ", ".join(main_langs) if main_langs else "undetermined languages"

        ep_desc = "no explicit entry point found"
        if entry_points:
            best_ep = entry_points[0]
            status_tag = "confirmed" if best_ep.is_confirmed else "heuristic"
            ep_desc = f"entry point '{best_ep.file_path}' ({status_tag})"

        dep_count = sum(len(m.dependencies) for m in manifests)
        dep_str = f"with {dep_count} declared dependencies" if dep_count else "with zero declared external dependencies"

        return (
            f"'{project_name}' is a {type_desc} codebase comprising {structure.file_count} files "
            f"({structure.total_lines_of_code} total lines) written primarily in {lang_str}, "
            f"centered on {ep_desc}, {dep_str}."
        )

    @staticmethod
    def save_report(
        report: ArchitectureReport,
        target_file: Path,
        force: bool = False,
    ) -> Path:
        """Persist the architecture report to a JSON file, refusing to overwrite without force."""
        if target_file.is_file() and not force:
            raise AnalysisError(
                f"Target file already exists at {target_file}. Pass force=True (or CLI --force) to overwrite."
            )
        try:
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(
                json.dumps(report.to_dict(), indent=2), encoding="utf-8"
            )
            return target_file
        except OSError as e:
            raise AnalysisError(f"Failed to save architecture report to {target_file}: {e}") from e

    @staticmethod
    def load_report(source_file: Path) -> Optional[ArchitectureReport]:
        """Load a persisted architecture report from a JSON file."""
        if not source_file.is_file():
            return None
        try:
            data = json.loads(source_file.read_text(encoding="utf-8"))
            return ArchitectureReport.from_dict(data)
        except Exception as e:
            logger.warning("Could not read architecture report from %s: %s", source_file, e)
            return None


def analyze_repository(
    project_root: Path,
    config: Optional[CodePilotConfig] = None,
    max_files: Optional[int] = None,
    max_file_size: Optional[int] = None,
    max_depth: Optional[int] = None,
) -> ArchitectureReport:
    """Convenience function to analyze a repository and return its architecture report."""
    analyzer = ProjectAnalyzer(project_root=project_root, config=config)
    return analyzer.analyze(
        max_files=max_files,
        max_file_size=max_file_size,
        max_depth=max_depth,
    )
