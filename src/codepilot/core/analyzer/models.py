"""Data models for project architecture representation and analysis."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ImportInfo:
    """Represents an imported module or symbol in a source file."""

    module: str
    names: List[str] = field(default_factory=list)
    alias: Optional[str] = None
    is_from: bool = False
    line_number: int = 1
    is_local: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImportInfo":
        return cls(**data)


@dataclass
class FunctionInfo:
    """Represents a function or method defined in a source file."""

    name: str
    line_number: int
    end_line_number: int
    args: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    is_async: bool = False
    calls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FunctionInfo":
        return cls(**data)


@dataclass
class ClassInfo:
    """Represents a class defined in a source file."""

    name: str
    line_number: int
    end_line_number: int
    base_classes: List[str] = field(default_factory=list)
    methods: List[FunctionInfo] = field(default_factory=list)
    docstring: Optional[str] = None
    decorators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "line_number": self.line_number,
            "end_line_number": self.end_line_number,
            "base_classes": self.base_classes,
            "methods": [m.to_dict() for m in self.methods],
            "docstring": self.docstring,
            "decorators": self.decorators,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClassInfo":
        methods = [FunctionInfo.from_dict(m) for m in data.get("methods", [])]
        return cls(
            name=data.get("name", ""),
            line_number=data.get("line_number", 1),
            end_line_number=data.get("end_line_number", 1),
            base_classes=data.get("base_classes", []),
            methods=methods,
            docstring=data.get("docstring"),
            decorators=data.get("decorators", []),
        )


@dataclass
class ModuleInfo:
    """Represents extracted architectural details of a single source file."""

    file_path: str
    language: str
    imports: List[ImportInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    has_main_block: bool = False
    line_count: int = 0
    docstring: Optional[str] = None
    parse_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "language": self.language,
            "imports": [imp.to_dict() for imp in self.imports],
            "classes": [cls.to_dict() for cls in self.classes],
            "functions": [fn.to_dict() for fn in self.functions],
            "has_main_block": self.has_main_block,
            "line_count": self.line_count,
            "docstring": self.docstring,
            "parse_error": self.parse_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModuleInfo":
        imports = [ImportInfo.from_dict(i) for i in data.get("imports", [])]
        classes = [ClassInfo.from_dict(c) for c in data.get("classes", [])]
        functions = [FunctionInfo.from_dict(f) for f in data.get("functions", [])]
        return cls(
            file_path=data.get("file_path", ""),
            language=data.get("language", "Unknown"),
            imports=imports,
            classes=classes,
            functions=functions,
            has_main_block=data.get("has_main_block", False),
            line_count=data.get("line_count", 0),
            docstring=data.get("docstring"),
            parse_error=data.get("parse_error"),
        )


@dataclass
class EntryPointInfo:
    """Represents an identified entry point of the project."""

    file_path: str
    entry_type: str
    symbol_name: Optional[str] = None
    confidence: str = "medium"  # "high", "medium", "low"
    is_confirmed: bool = False  # True if confirmed by manifest/spec, False if heuristic guess
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EntryPointInfo":
        return cls(**data)


@dataclass
class DependencyManifestInfo:
    """Represents a discovered package/dependency manifest file."""

    manifest_path: str
    manifest_type: str
    dependencies: List[str] = field(default_factory=list)
    dev_dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DependencyManifestInfo":
        return cls(**data)


@dataclass
class ProjectStructure:
    """Metrics and overview of the project directory structure."""

    directory_count: int = 0
    file_count: int = 0
    total_lines_of_code: int = 0
    excluded_count: int = 0
    symlinks_count: int = 0
    languages: Dict[str, int] = field(default_factory=dict)
    language_lines: Dict[str, int] = field(default_factory=dict)
    top_level_directories: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectStructure":
        return cls(**data)


@dataclass
class ArchitectureReport:
    """Comprehensive, structured architecture report for an unfamiliar repository."""

    project_name: str
    project_root: str
    project_type: str
    project_subtypes: List[str] = field(default_factory=list)
    git_branch: Optional[str] = None
    structure: ProjectStructure = field(default_factory=ProjectStructure)
    entry_points: List[EntryPointInfo] = field(default_factory=list)
    config_files: List[str] = field(default_factory=list)
    dependency_manifests: List[DependencyManifestInfo] = field(default_factory=list)
    test_directories: List[str] = field(default_factory=list)
    test_configuration: Dict[str, Any] = field(default_factory=dict)
    modules: List[ModuleInfo] = field(default_factory=list)
    relationships: Dict[str, List[str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    summary: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_root": self.project_root,
            "project_type": self.project_type,
            "project_subtypes": self.project_subtypes,
            "git_branch": self.git_branch,
            "structure": self.structure.to_dict(),
            "entry_points": [ep.to_dict() for ep in self.entry_points],
            "config_files": self.config_files,
            "dependency_manifests": [dm.to_dict() for dm in self.dependency_manifests],
            "test_directories": self.test_directories,
            "test_configuration": self.test_configuration,
            "modules": [m.to_dict() for m in self.modules],
            "relationships": self.relationships,
            "warnings": self.warnings,
            "limitations": self.limitations,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchitectureReport":
        structure = ProjectStructure.from_dict(data.get("structure", {}))
        entry_points = [
            EntryPointInfo.from_dict(ep) for ep in data.get("entry_points", [])
        ]
        dependency_manifests = [
            DependencyManifestInfo.from_dict(dm)
            for dm in data.get("dependency_manifests", [])
        ]
        modules = [ModuleInfo.from_dict(m) for m in data.get("modules", [])]
        return cls(
            project_name=data.get("project_name", ""),
            project_root=data.get("project_root", ""),
            project_type=data.get("project_type", "unknown"),
            project_subtypes=data.get("project_subtypes", []),
            git_branch=data.get("git_branch"),
            structure=structure,
            entry_points=entry_points,
            config_files=data.get("config_files", []),
            dependency_manifests=dependency_manifests,
            test_directories=data.get("test_directories", []),
            test_configuration=data.get("test_configuration", {}),
            modules=modules,
            relationships=data.get("relationships", {}),
            warnings=data.get("warnings", []),
            limitations=data.get("limitations", []),
            summary=data.get("summary", ""),
            timestamp=data.get("timestamp", ""),
        )

