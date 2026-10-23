"""Automated test suite for the Project Architecture Analyzer (Phase 2)."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CodePilotConfig
from codepilot.core.analyzer import (
    ArchitectureContextBuilder,
    ArchitectureReport,
    ProjectAnalyzer,
    analyze_repository,
)
from codepilot.core.analyzer.discovery import (
    detect_project_type,
    discover_files,
    find_configuration_files,
    is_binary_file,
    parse_dependency_manifests,
)
from codepilot.core.analyzer.entry_points import detect_entry_points
from codepilot.core.analyzer.extractors.generic import GenericLanguageExtractor
from codepilot.core.analyzer.extractors.python import PythonLanguageExtractor
from codepilot.core.analyzer.gitignore import GitIgnoreMatcher, is_safe_path
from codepilot.core.analyzer.relationships import (
    build_class_hierarchy,
    build_module_relationships,
)


def test_repo_discovery_and_project_type(tmp_path: Path) -> None:
    """Verify repository file discovery and Python project type classification."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text(
        "import os\n\ndef run():\n    print('Hello')\n\nif __name__ == '__main__':\n    run()\n",
        encoding="utf-8",
    )
    (src_dir / "cli.py").write_text(
        "def main():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample-tool"\nversion = "0.1.0"\ndependencies = ["typer>=0.9.0"]\n'
        '[project.scripts]\nsample = "src.cli:main"\n',
        encoding="utf-8",
    )

    discovered, structure = discover_files(tmp_path)
    assert structure.file_count == 3
    assert structure.languages.get("Python") == 2
    assert structure.languages.get("TOML") == 1
    assert "src" in structure.top_level_directories

    ptype, subtypes = detect_project_type(tmp_path, discovered, structure)
    assert ptype == "python"
    assert "package" in subtypes
    assert "cli" in subtypes


def test_gitignore_and_default_exclusions(tmp_path: Path) -> None:
    """Verify that default exclusions (.git, .venv, node_modules) and .gitignore rules work."""
    # Default ignored directories
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pip.py").write_text("# ignore me", encoding="utf-8")

    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_bytes(b"\x00\x00\x00\x00")

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg.js").write_text("// ignore me", encoding="utf-8")

    # Custom .gitignore file
    (tmp_path / ".gitignore").write_text(
        "# Comments\n*.tmp\nignored_dir/\n!special.tmp\n",
        encoding="utf-8",
    )

    (tmp_path / "ignored_dir").mkdir()
    (tmp_path / "ignored_dir" / "secret.py").write_text("# ignore", encoding="utf-8")

    (tmp_path / "normal.tmp").write_text("temp", encoding="utf-8")
    (tmp_path / "special.tmp").write_text("keep me", encoding="utf-8")

    # Valid source file to keep
    (tmp_path / "app.py").write_text("def ok(): pass\n", encoding="utf-8")

    matcher = GitIgnoreMatcher(project_root=tmp_path)
    assert matcher.should_ignore_directory(".git", ".git") is True
    assert matcher.should_ignore_directory(".venv", ".venv") is True
    assert matcher.should_ignore_directory("__pycache__", "__pycache__") is True
    assert matcher.should_ignore_directory("node_modules", "node_modules") is True
    assert matcher.should_ignore_directory("ignored_dir", "ignored_dir") is True

    discovered, structure = discover_files(tmp_path)
    discovered_rel = [str(f.relative_to(tmp_path)).replace("\\", "/") for f in discovered]

    assert "app.py" in discovered_rel
    assert "special.tmp" in discovered_rel
    assert "normal.tmp" not in discovered_rel
    assert not any(p == ".git" or p.startswith(".git/") for p in discovered_rel)
    assert not any(p.startswith(".venv") for p in discovered_rel)
    assert not any(p.startswith("__pycache__") for p in discovered_rel)
    assert not any(p.startswith("node_modules") for p in discovered_rel)
    assert not any(p.startswith("ignored_dir") for p in discovered_rel)



def test_python_ast_extractor(tmp_path: Path) -> None:
    """Verify detailed AST extraction of imports, classes, functions, decorators, and main blocks."""
    py_file = tmp_path / "service.py"
    py_code = '''"""Service module docstring."""

import os
from pathlib import Path
from typing import List, Optional

class BaseWorker:
    """Base worker class."""
    pass

class DataWorker(BaseWorker):
    """Worker implementation."""

    @classmethod
    def create(cls) -> "DataWorker":
        return cls()

    def process(self, items: List[str], flag: bool = True) -> int:
        """Process items."""
        return len(items)

async def async_fetch(url: str) -> str:
    """Fetch data asynchronously."""
    return url

if __name__ == "__main__":
    worker = DataWorker.create()
'''
    py_file.write_text(py_code, encoding="utf-8")

    extractor = PythonLanguageExtractor()
    assert extractor.can_handle(py_file) is True

    mod = extractor.extract(py_file, py_code, tmp_path)
    assert mod.file_path == "service.py"
    assert mod.language == "Python"
    assert mod.docstring == "Service module docstring."
    assert mod.has_main_block is True
    assert mod.parse_error is None

    # Verify imports
    import_modules = [i.module for i in mod.imports]
    assert "os" in import_modules
    assert "pathlib" in import_modules
    assert "typing" in import_modules

    # Verify classes
    assert len(mod.classes) == 2
    base_worker = next(c for c in mod.classes if c.name == "BaseWorker")
    data_worker = next(c for c in mod.classes if c.name == "DataWorker")

    assert base_worker.docstring == "Base worker class."
    assert "BaseWorker" in data_worker.base_classes
    assert len(data_worker.methods) == 2
    method_names = [m.name for m in data_worker.methods]
    assert "create" in method_names
    assert "process" in method_names

    # Check method details
    process_m = next(m for m in data_worker.methods if m.name == "process")
    assert "items" in process_m.args
    assert "flag" in process_m.args
    assert process_m.return_type == "int"

    # Verify functions
    assert len(mod.functions) == 1
    fetch_fn = mod.functions[0]
    assert fetch_fn.name == "async_fetch"
    assert fetch_fn.is_async is True
    assert fetch_fn.args == ["url"]
    assert fetch_fn.return_type == "str"


def test_syntax_error_handled_safely(tmp_path: Path) -> None:
    """Verify that files with syntax errors are recorded gracefully without crashing."""
    broken_file = tmp_path / "broken.py"
    broken_code = "def invalid_syntax(:\n    pass\n"
    broken_file.write_text(broken_code, encoding="utf-8")

    extractor = PythonLanguageExtractor()
    mod = extractor.extract(broken_file, broken_code, tmp_path)
    assert mod.file_path == "broken.py"
    assert mod.parse_error is not None
    assert "AST parse error" in mod.parse_error


def test_binary_and_oversized_files_skipped(tmp_path: Path) -> None:
    """Verify that binary files and files exceeding size ceiling are excluded."""
    bin_file = tmp_path / "data.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\x04")
    assert is_binary_file(bin_file) is True

    huge_file = tmp_path / "huge.py"
    huge_file.write_text("x = 1\n" * 1000, encoding="utf-8")

    # Discover with a tiny 100-byte max size limit
    discovered, _ = discover_files(tmp_path, max_file_size=100)
    discovered_names = [f.name for f in discovered]
    assert "data.bin" not in discovered_names
    assert "huge.py" not in discovered_names


def test_path_traversal_protection(tmp_path: Path) -> None:
    """Verify path traversal guard rejects paths outside the repository root."""
    inside = tmp_path / "safe.py"
    outside = tmp_path.parent / "unsafe.py"

    assert is_safe_path(inside, tmp_path) is True
    assert is_safe_path(outside, tmp_path) is False


def test_entry_point_detection(tmp_path: Path) -> None:
    """Verify detection and ranking of application entry points."""
    (tmp_path / "pyproject.toml").write_text(
        '[project.scripts]\nmycmd = "my_pkg.cli:main"\n',
        encoding="utf-8",
    )
    main_py = tmp_path / "main.py"
    main_py.write_text("if __name__ == '__main__': pass\n", encoding="utf-8")

    extractor = PythonLanguageExtractor()
    mod = extractor.extract(main_py, main_py.read_text(encoding="utf-8"), tmp_path)

    entry_points = detect_entry_points(tmp_path, [mod], ["pyproject.toml"])
    assert len(entry_points) >= 2

    # High-confidence entries
    confidences = [ep.confidence for ep in entry_points]
    assert "high" in confidences
    script_ep = next(ep for ep in entry_points if ep.entry_type == "cli_script")
    assert script_ep.symbol_name == "my_pkg.cli:main"


def test_dependency_manifest_parser(tmp_path: Path) -> None:
    """Verify parsing of pyproject.toml and requirements.txt."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["pydantic>=2.0", "rich"]\n'
        '[project.optional-dependencies]\ndev = ["pytest"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "# Comment\nrequests==2.31.0\nclick>=8.0\n",
        encoding="utf-8",
    )

    discovered, _ = discover_files(tmp_path)
    manifests = parse_dependency_manifests(tmp_path, discovered)
    assert len(manifests) == 2

    pyproject_m = next(m for m in manifests if m.manifest_type == "pyproject.toml")
    assert "pydantic>=2.0" in pyproject_m.dependencies
    assert "rich" in pyproject_m.dependencies
    assert "pytest" in pyproject_m.dev_dependencies

    reqs_m = next(m for m in manifests if m.manifest_type == "requirements.txt")
    assert "requests" in reqs_m.dependencies
    assert "click" in reqs_m.dependencies


def test_relationships_and_class_hierarchy(tmp_path: Path) -> None:
    """Verify directed module dependency graph and class hierarchy mapping."""
    mod_a_code = "from b import helper\n\nclass Animal: pass\nclass Dog(Animal): pass\n"
    mod_b_code = "def helper(): pass\n"

    extractor = PythonLanguageExtractor()
    (tmp_path / "a.py").write_text(mod_a_code, encoding="utf-8")
    (tmp_path / "b.py").write_text(mod_b_code, encoding="utf-8")

    mod_a = extractor.extract(tmp_path / "a.py", mod_a_code, tmp_path)
    mod_b = extractor.extract(tmp_path / "b.py", mod_b_code, tmp_path)

    graph = build_module_relationships([mod_a, mod_b])
    assert "a.py" in graph
    assert "b.py" in graph["a.py"]

    hierarchy = build_class_hierarchy([mod_a, mod_b])
    assert "Animal" in hierarchy
    assert any("Dog" in child for child in hierarchy["Animal"])


def test_architecture_context_builder_bounds(tmp_path: Path) -> None:
    """Verify that ArchitectureContextBuilder enforces strict token budgeting."""
    (tmp_path / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
    report = analyze_repository(tmp_path)

    # Context with small token budget (e.g. 50 tokens ~ 200 chars)
    tiny_context = ArchitectureContextBuilder.build_context(report, max_tokens=50)
    assert len(tiny_context) <= 50 * 4 + 150
    assert "Project Context" in tiny_context
    assert "truncated to fit" in tiny_context

    # Normal context
    normal_context = ArchitectureContextBuilder.build_context(report, max_tokens=1500)
    assert "## Project Context" in normal_context
    assert "app.py" in normal_context


def test_cli_analyze_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI 'codepilot analyze' visual output and JSON output without unapproved file writes."""
    (tmp_path / "calculator.py").write_text(
        "class Calculator:\n    def add(self, a: int, b: int) -> int:\n        return a + b\n",
        encoding="utf-8",
    )

    # 1. Standard visual output (reports only to terminal without explicitly chosen -o path)
    res = cli_runner.invoke(app, ["analyze", "--directory", str(tmp_path)])
    assert res.exit_code == 0
    assert "CodePilot AI" in res.output
    assert "calculator.py" in res.output

    # Report file is NOT created when -o is not explicitly passed
    arch_file = tmp_path / ".codepilot" / "architecture.json"
    assert not arch_file.exists()

    # 2. JSON output
    res_json = cli_runner.invoke(app, ["analyze", "--directory", str(tmp_path), "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert "project_type" in data
    assert "structure" in data
    assert data["structure"]["file_count"] >= 1

    # 3. Detailed output
    res_det = cli_runner.invoke(app, ["analyze", "--directory", str(tmp_path), "--detailed"])
    assert res_det.exit_code == 0
    assert "Detailed Class Hierarchy" in res_det.output

    # 4. Context output
    res_ctx = cli_runner.invoke(app, ["analyze", "--directory", str(tmp_path), "--context"])
    assert res_ctx.exit_code == 0
    assert "Architecture Prompt Context" in res_ctx.output


def test_cli_analyze_custom_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI 'codepilot analyze --output custom.json' saves report to custom location."""
    (tmp_path / "main.py").write_text("print('test')\n", encoding="utf-8")
    custom_out = tmp_path / "custom_report.json"

    res = cli_runner.invoke(
        app,
        ["analyze", "--directory", str(tmp_path), "--output", str(custom_out)],
    )
    assert res.exit_code == 0
    assert custom_out.is_file()
    data = json.loads(custom_out.read_text(encoding="utf-8"))
    assert "project_name" in data


def test_cli_analyze_overwrite_protection(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI 'codepilot analyze -o file.json' refuses to overwrite existing file without --force."""
    (tmp_path / "main.py").write_text("print('test')\n", encoding="utf-8")
    report_file = tmp_path / "report.json"
    report_file.write_text('{"existing": true}', encoding="utf-8")

    # Attempt save without --force should fail with exit code 1
    res = cli_runner.invoke(
        app,
        ["analyze", "--directory", str(tmp_path), "--output", str(report_file)],
    )
    assert res.exit_code == 1
    assert "already exists" in res.output or "Analysis Error" in res.output
    # Existing content must not be overwritten
    assert report_file.read_text(encoding="utf-8") == '{"existing": true}'

    # Attempt save with --force should succeed and overwrite
    res_force = cli_runner.invoke(
        app,
        ["analyze", "--directory", str(tmp_path), "--output", str(report_file), "--force"],
    )
    assert res_force.exit_code == 0
    data = json.loads(report_file.read_text(encoding="utf-8"))
    assert "project_name" in data


def test_model_weights_and_secrets_excluded(tmp_path: Path) -> None:
    """Verify model weights (*.safetensors, *.bin, *.pt, *.gguf) and secrets (*.pem, *.key, .env*) are excluded."""
    # Model weights
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 200)
    (tmp_path / "weights.pt").write_bytes(b"\x00" * 200)
    (tmp_path / "llm.bin").write_bytes(b"\x00" * 200)
    (tmp_path / "llama.gguf").write_bytes(b"\x00" * 200)

    # Secrets and credentials
    (tmp_path / "id_rsa.pem").write_text("CERT", encoding="utf-8")
    (tmp_path / "server.key").write_text("PRIVATE KEY", encoding="utf-8")
    (tmp_path / "credentials.json").write_text('{"token": "xyz"}', encoding="utf-8")
    (tmp_path / ".env.local").write_text("SECRET=123", encoding="utf-8")

    # Legitimate source
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")

    discovered, structure = discover_files(tmp_path)
    discovered_names = [f.name for f in discovered]

    assert "main.py" in discovered_names
    assert "model.safetensors" not in discovered_names
    assert "weights.pt" not in discovered_names
    assert "llm.bin" not in discovered_names
    assert "llama.gguf" not in discovered_names
    assert "id_rsa.pem" not in discovered_names
    assert "server.key" not in discovered_names
    assert "credentials.json" not in discovered_names
    assert ".env.local" not in discovered_names
    assert structure.excluded_count > 0


def test_generic_language_extractor(tmp_path: Path) -> None:
    """Verify generic fallback extractor detects basic symbols in non-Python files."""
    js_code = "import { useState } from 'react';\nfunction App() { return 1; }\n"
    js_file = tmp_path / "index.js"
    js_file.write_text(js_code, encoding="utf-8")

    extractor = GenericLanguageExtractor()
    assert extractor.can_handle(js_file) is True
    mod = extractor.extract(js_file, js_code, tmp_path)
    assert mod.language == "JavaScript"
    assert any(fn.name == "App" for fn in mod.functions)


def test_architecture_report_serialization_roundtrip(tmp_path: Path) -> None:
    """Verify ArchitectureReport serializes to dict/JSON and deserializes correctly."""
    (tmp_path / "app.py").write_text("class MyService:\n    def run(self): pass\n", encoding="utf-8")
    report = analyze_repository(tmp_path)

    data = report.to_dict()
    assert isinstance(data, dict)
    assert data["project_name"] == tmp_path.name
    assert len(data["modules"]) >= 1

    restored = ArchitectureReport.from_dict(data)
    assert restored.project_name == report.project_name
    assert restored.project_type == report.project_type
    assert len(restored.modules) == len(report.modules)
    assert restored.modules[0].file_path == report.modules[0].file_path


def test_cli_analyze_invalid_directory(cli_runner: CliRunner) -> None:
    """Verify CLI 'codepilot analyze' fails with exit code 1 on non-existent directory."""
    non_existent = Path("non_existent_dir_12345")
    res = cli_runner.invoke(app, ["analyze", "--directory", str(non_existent)])
    assert res.exit_code == 1
    assert "Analysis Error" in res.output or "does not exist" in res.output
