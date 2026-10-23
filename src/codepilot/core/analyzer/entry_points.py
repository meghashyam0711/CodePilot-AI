"""Identification and ranking of project application entry points."""

from pathlib import Path
import sys
from typing import List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore

from codepilot.core.analyzer.models import EntryPointInfo, ModuleInfo


def detect_entry_points(
    project_root: Path,
    modules: List[ModuleInfo],
    config_files: List[str],
) -> List[EntryPointInfo]:
    """Detect and rank application entry points across discovered modules and manifests."""
    entry_points: List[EntryPointInfo] = []
    root = project_root.resolve()
    seen_paths = set()

    # 1. Inspect pyproject.toml [project.scripts] and [tool.poetry.scripts]
    pyproject_path = root / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
            scripts = data.get("project", {}).get("scripts", {})
            for name, target in scripts.items():
                target_str = str(target)
                file_hint = target_str.split(":")[0].replace(".", "/") + ".py"
                entry_points.append(
                    EntryPointInfo(
                        file_path=file_hint,
                        entry_type="cli_script",
                        symbol_name=target_str,
                        confidence="high",
                        is_confirmed=True,
                        description=f"CLI executable script '{name}' configured in pyproject.toml",
                    )
                )
                seen_paths.add(file_hint)
        except Exception:
            pass

    # 2. Inspect modules for main blocks and standard entry point filenames
    for mod in modules:
        path = mod.file_path
        filename = Path(path).name.lower()

        # __main__.py
        if filename == "__main__.py":
            entry_points.append(
                EntryPointInfo(
                    file_path=path,
                    entry_type="package_main",
                    symbol_name="__main__",
                    confidence="high",
                    is_confirmed=True,
                    description="Standard Python package execution entry point (__main__.py)",
                )
            )
            seen_paths.add(path)

        # main.py or app.py with main block
        elif filename in ("main.py", "app.py", "server.py") and mod.has_main_block:
            entry_points.append(
                EntryPointInfo(
                    file_path=path,
                    entry_type="application_main",
                    symbol_name="__main__",
                    confidence="high",
                    is_confirmed=False,
                    description=f"Primary application file '{filename}' with __main__ execution guard",
                )
            )
            seen_paths.add(path)

        # cli.py
        elif filename == "cli.py":
            has_cli_app = any("command" in d for fn in mod.functions for d in fn.decorators)
            entry_points.append(
                EntryPointInfo(
                    file_path=path,
                    entry_type="cli_entry",
                    symbol_name="main",
                    confidence="high" if has_cli_app else "medium",
                    is_confirmed=False,
                    description="CLI entry point module (cli.py)",
                )
            )
            seen_paths.add(path)

        # Any other file with if __name__ == '__main__':
        elif mod.has_main_block and path not in seen_paths:
            entry_points.append(
                EntryPointInfo(
                    file_path=path,
                    entry_type="standalone_executable",
                    symbol_name="__main__",
                    confidence="medium",
                    is_confirmed=False,
                    description=f"File with __main__ block: {path}",
                )
            )
            seen_paths.add(path)

        # JS / TS entry conventions
        elif filename in ("index.js", "index.ts", "server.js", "main.go", "main.rs"):
            entry_points.append(
                EntryPointInfo(
                    file_path=path,
                    entry_type="standard_entry",
                    symbol_name=filename,
                    confidence="high",
                    is_confirmed=False,
                    description=f"Standard language entry point: {filename}",
                )
            )
            seen_paths.add(path)


    # Sort entry points by confidence (high before medium before low)
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    entry_points.sort(key=lambda ep: confidence_order.get(ep.confidence, 3))

    return entry_points
