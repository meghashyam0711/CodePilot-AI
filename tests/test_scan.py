"""Automated tests for the 'codepilot scan' command and scanner engine."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.config import CodePilotConfig
from codepilot.core.scanner import (
    is_binary_file,
    is_protected_file,
    scan_file,
    scan_repository,
    should_skip_directory,
)


def test_scan_comments_across_languages(tmp_path: Path) -> None:
    """Verify that scanner detects instruction comments in Python, JS, and HTML."""
    py_file = tmp_path / "service.py"
    py_file.write_text(
        "# Regular comment\n"
        "# AI: implement user login function\n"
        "def login():\n"
        "    pass\n"
        "# AI-FIX: handle division by zero\n"
        "def divide(a, b):\n"
        "    return a / b\n",
        encoding="utf-8",
    )

    js_file = tmp_path / "app.js"
    js_file.write_text(
        "// Regular comment\n"
        "// AI-TODO: add validation middleware\n"
        "const express = require('express');\n"
        "/* AI-REFACTOR: optimize database query */\n",
        encoding="utf-8",
    )

    html_file = tmp_path / "index.html"
    html_file.write_text(
        "<!-- Regular HTML comment -->\n"
        "<!-- AI: add accessible navigation bar -->\n"
        "<nav></nav>\n",
        encoding="utf-8",
    )

    config = CodePilotConfig(project_root=tmp_path)
    result = scan_repository(tmp_path, config)

    assert result.scanned_files_count == 3
    assert len(result.instructions) == 5

    prompts = [inst.prompt for inst in result.instructions]
    assert "implement user login function" in prompts
    assert "handle division by zero" in prompts
    assert "add validation middleware" in prompts
    assert "optimize database query" in prompts
    assert "add accessible navigation bar" in prompts


def test_scanner_skips_binary_files(tmp_path: Path) -> None:
    """Verify that binary files are safely skipped."""
    bin_file = tmp_path / "image.png"
    bin_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR# AI: do not scan this")

    assert is_binary_file(bin_file) is True


def test_scanner_skips_protected_dirs_and_files(tmp_path: Path) -> None:
    """Verify that protected directories (.git, node_modules) and files (.env) are skipped."""
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg.js").write_text("// AI: ignore me", encoding="utf-8")

    env_file = tmp_path / ".env"
    env_file.write_text("# AI: ignore secret", encoding="utf-8")

    src_file = tmp_path / "main.py"
    src_file.write_text("# AI: keep me", encoding="utf-8")

    config = CodePilotConfig(project_root=tmp_path)
    result = scan_repository(tmp_path, config)

    assert len(result.instructions) == 1
    assert result.instructions[0].prompt == "keep me"


def test_cli_scan_empty_repo(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI scan output when no comments are present."""
    result = cli_runner.invoke(app, ["scan", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "No AI instruction comments found" in result.output


def test_cli_scan_discovers_instructions(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI scan discovers instructions and reports them in table."""
    test_file = tmp_path / "calc.py"
    test_file.write_text("# AI: return true if n is even\ndef is_even(n):\n    pass\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["scan", "--directory", str(tmp_path)])
    assert result.exit_code == 0
    assert "Discovered AI Instruction Comments" in result.output
    assert "calc.py" in result.output
    assert "return true" in result.output


def test_cli_scan_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI scan --json produces valid JSON output."""
    test_file = tmp_path / "utils.py"
    test_file.write_text("# AI-FIX: sanitize user query\n", encoding="utf-8")

    result = cli_runner.invoke(app, ["scan", "--directory", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "instructions" in data
    assert len(data["instructions"]) == 1
    assert data["instructions"][0]["instruction_type"] in ("FIX", "AI-FIX")
    assert data["instructions"][0]["prompt"] == "sanitize user query"
