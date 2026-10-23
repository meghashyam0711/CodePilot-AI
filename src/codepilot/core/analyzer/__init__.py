"""Architecture analysis package for CodePilot AI."""

from codepilot.core.analyzer.context_builder import ArchitectureContextBuilder
from codepilot.core.analyzer.engine import ProjectAnalyzer, analyze_repository
from codepilot.core.analyzer.models import (
    ArchitectureReport,
    ClassInfo,
    DependencyManifestInfo,
    EntryPointInfo,
    FunctionInfo,
    ImportInfo,
    ModuleInfo,
    ProjectStructure,
)

__all__ = [
    "ArchitectureContextBuilder",
    "ArchitectureReport",
    "ClassInfo",
    "DependencyManifestInfo",
    "EntryPointInfo",
    "FunctionInfo",
    "ImportInfo",
    "ModuleInfo",
    "ProjectAnalyzer",
    "ProjectStructure",
    "analyze_repository",
]
