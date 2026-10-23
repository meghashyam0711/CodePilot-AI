"""Relationship graph analysis between project modules and classes."""

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set

from codepilot.core.analyzer.models import ModuleInfo


def build_module_relationships(modules: List[ModuleInfo]) -> Dict[str, List[str]]:
    """Build a directed dependency graph of local module imports.

    Returns:
        Dict mapping source module path -> list of target local module paths it imports.
    """
    # Create lookup map of module identifiers to file paths
    # e.g., "codepilot.core.app" or "app" -> "src/codepilot/core/app.py"
    module_lookup: Dict[str, str] = {}
    for mod in modules:
        path = mod.file_path
        stem = Path(path).stem
        module_lookup[stem] = path

        # Dotted module path from file path
        clean_path = path.replace("\\", "/").rstrip(".py")
        if clean_path.startswith("src/"):
            dotted = clean_path[4:].replace("/", ".")
            module_lookup[dotted] = path
        else:
            dotted = clean_path.replace("/", ".")
            module_lookup[dotted] = path

    graph: Dict[str, Set[str]] = defaultdict(set)

    for mod in modules:
        src_path = mod.file_path
        for imp in mod.imports:
            # Check if this import matches a known local module
            target_path = None
            if imp.module in module_lookup:
                target_path = module_lookup[imp.module]
            else:
                # Check stem or dotted suffix
                parts = imp.module.split(".")
                for i in range(len(parts)):
                    sub = ".".join(parts[i:])
                    if sub in module_lookup:
                        target_path = module_lookup[sub]
                        break

            if target_path and target_path != src_path:
                graph[src_path].add(target_path)

    # Convert sets to sorted lists for deterministic serialization
    return {k: sorted(v) for k, v in sorted(graph.items())}


def build_class_hierarchy(modules: List[ModuleInfo]) -> Dict[str, List[str]]:
    """Map base class names to list of child class names across all modules."""
    hierarchy: Dict[str, List[str]] = defaultdict(list)
    for mod in modules:
        for cls_info in mod.classes:
            for base in cls_info.base_classes:
                if base not in ("object", "None"):
                    hierarchy[base].append(f"{mod.file_path}::{cls_info.name}")
    return {k: sorted(v) for k, v in sorted(hierarchy.items())}
