"""Tests for R-1: risk screening of model-generated code modifications.

Screening is advisory — it annotates a patch so dangerous constructs are visible during
review, and never silently discards the patch. These tests pin both halves of that
contract: that real risks are reported, and that ordinary code is not.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codepilot.cli import app, _render_patch_risk_warning
from codepilot.config import load_config
from codepilot.core.fixer import generate_preview_patch
from codepilot.core.patch_risk import (
    RiskFinding,
    RiskSeverity,
    screen_file_change,
    screen_python_change,
    screen_text_change,
    summarize_risk,
)


@pytest.fixture()
def cli_runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


BENIGN = "def divide(a, b):\n    return a / b\n"


# ---------------------------------------------------------------------------
# Constructs that must be reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proposed,category,severity",
    [
        ("def f():\n    exec('x = 1')\n", "code_execution", "HIGH"),
        ("def f():\n    return eval('1 + 1')\n", "code_execution", "HIGH"),
        ("def f():\n    return compile('x', '<s>', 'exec')\n", "code_execution", "HIGH"),
        ("def f():\n    return __import__('os')\n", "code_execution", "HIGH"),
        ("import os\n\n\ndef f():\n    os.system('whoami')\n", "command_execution", "HIGH"),
        ("import os\n\n\ndef f():\n    os.popen('id')\n", "command_execution", "HIGH"),
        (
            "import subprocess\n\n\ndef f():\n    subprocess.run(['ls'])\n",
            "command_execution",
            "HIGH",
        ),
        (
            "import subprocess\n\n\ndef f():\n    subprocess.Popen(['ls'])\n",
            "command_execution",
            "HIGH",
        ),
        ("import pickle\n\n\ndef f():\n    return pickle.loads(b'')\n", "deserialization", "MEDIUM"),
        ("import yaml\n\n\ndef f():\n    return yaml.load('a: 1')\n", "deserialization", "MEDIUM"),
        (
            "import requests\n\n\ndef f():\n    return requests.post('http://x', data={})\n",
            "network_access",
            "MEDIUM",
        ),
        (
            "import socket\n\n\ndef f():\n    return socket.socket()\n",
            "network_access",
            "MEDIUM",
        ),
        (
            "import importlib\n\n\ndef f():\n    return importlib.import_module('os')\n",
            "dynamic_import",
            "MEDIUM",
        ),
        ("import shutil\n\n\ndef f():\n    shutil.rmtree('/tmp/x')\n", "destructive_filesystem", "LOW"),
        ("import os\n\n\ndef f():\n    os.remove('/tmp/x')\n", "destructive_filesystem", "LOW"),
    ],
)
def test_risky_constructs_are_reported(proposed: str, category: str, severity: str) -> None:
    findings = screen_file_change("m.py", "def f():\n    pass\n", proposed)

    assert findings, f"expected a finding for {category}"
    assert any(f.category == category and f.severity == severity for f in findings), (
        f"expected {category}/{severity}, got {[(f.category, f.severity) for f in findings]}"
    )


def test_severity_is_a_plain_string_not_an_enum_repr() -> None:
    """Findings are serialized into the patch bundle and rendered in a table."""
    findings = screen_file_change("m.py", "def f():\n    pass\n", "def f():\n    exec('x')\n")

    assert findings[0].severity == "HIGH"
    assert json.dumps(findings[0].to_dict())  # must be JSON-serializable
    assert "RiskSeverity" not in json.dumps(findings[0].to_dict())


def test_findings_are_ordered_high_severity_first() -> None:
    proposed = (
        "import os\nimport shutil\nimport requests\n\n\ndef f():\n"
        "    shutil.rmtree('/tmp/x')\n"
        "    requests.get('http://x')\n"
        "    os.system('id')\n"
    )

    findings = screen_file_change("m.py", "def f():\n    pass\n", proposed)

    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[s])
    assert severities[0] == "HIGH"


def test_finding_records_line_number_and_snippet() -> None:
    proposed = "import os\n\n\ndef f():\n    os.system('whoami')\n"

    finding = screen_file_change("m.py", "def f():\n    pass\n", proposed)[0]

    assert finding.line_number == 5
    assert "os.system" in finding.snippet


# ---------------------------------------------------------------------------
# Code that must NOT be reported (false-positive guards)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proposed",
    [
        # An ordinary guard clause — the single most common AI-FIX output
        "def divide(a, b):\n    if b == 0:\n        raise ValueError('zero')\n    return a / b\n",
        # 'eval' appearing only inside a string literal
        'def f():\n    return "never call eval(x) here"\n',
        # 'eval' appearing only in a comment
        "def f():\n    # do not use eval(x) for this\n    return 2\n",
        # A method named eval on an object is not the builtin
        "def f(model):\n    return model.eval()\n",
        # A local function named exec_plan is not the builtin exec
        "def exec_plan():\n    return 1\n\n\ndef f():\n    return exec_plan()\n",
        # Docstrings mentioning risky names
        'def f():\n    """Avoid os.system and subprocess here."""\n    return 1\n',
    ],
)
def test_benign_code_produces_no_findings(proposed: str) -> None:
    assert screen_file_change("m.py", "def f():\n    pass\n", proposed) == []


def test_construct_already_present_in_the_original_is_not_reported() -> None:
    """Only what the patch introduces is the patch's responsibility."""
    original = "import os\n\n\ndef f():\n    os.system('ls')\n"
    proposed = "import os\n\n\ndef f():\n    os.system('ls')\n    return 0\n"

    assert screen_file_change("m.py", original, proposed) == []


def test_removing_a_risky_construct_produces_no_findings() -> None:
    original = "import os\n\n\ndef f():\n    os.system('ls')\n"
    proposed = "def f():\n    return 0\n"

    assert screen_file_change("m.py", original, proposed) == []


def test_identical_content_is_not_screened() -> None:
    assert screen_file_change("m.py", BENIGN, BENIGN) == []


def test_unparseable_proposed_python_yields_no_findings() -> None:
    """Syntax validation in the fix engine already rejects this; screening stays silent."""
    assert screen_python_change("def f():\n    pass\n", "def f(:\n  !!!\n") == []


def test_bom_encoded_content_is_screened_correctly() -> None:
    proposed = "﻿import os\n\n\ndef f():\n    os.system('id')\n"

    findings = screen_python_change("def f():\n    pass\n", proposed)

    assert any(f.category == "command_execution" for f in findings)


# ---------------------------------------------------------------------------
# Non-Python fallback
# ---------------------------------------------------------------------------


def test_javascript_shell_execution_is_reported() -> None:
    findings = screen_file_change(
        "app.js",
        "function f(){ return 1 }\n",
        "const cp = require('child_process');\nfunction f(){ cp.execSync('whoami') }\n",
    )

    assert any(f.category == "command_execution" for f in findings)


def test_javascript_benign_change_is_not_reported() -> None:
    findings = screen_file_change(
        "app.js", "function f(){ return 1 }\n", "function f(){ return 2 }\n"
    )

    assert findings == []


def test_text_screening_ignores_unchanged_lines() -> None:
    original = "eval(payload)\nkeep()\n"
    proposed = "eval(payload)\nkeep()\nextra()\n"

    assert screen_text_change(original, proposed) == []


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


def test_summary_of_no_findings() -> None:
    assert summarize_risk([]) == "No risky constructs introduced."


def test_summary_counts_by_severity() -> None:
    findings = [
        RiskFinding("code_execution", RiskSeverity.HIGH, 1, "exec(x)", "msg"),
        RiskFinding("network_access", RiskSeverity.MEDIUM, 2, "requests.get(x)", "msg"),
        RiskFinding("destructive_filesystem", RiskSeverity.LOW, 3, "os.remove(x)", "msg"),
    ]

    summary = summarize_risk(findings)

    assert "3 risky construct(s)" in summary
    assert "1 HIGH" in summary and "1 MEDIUM" in summary and "1 LOW" in summary


# ---------------------------------------------------------------------------
# Integration: findings travel with the patch and reach the developer
# ---------------------------------------------------------------------------


class _FixedOutputClient:
    def __init__(self, completion: str) -> None:
        self._completion = completion

    def health_check(self):
        return True, "stub runtime ready"

    def generate(self, request, model_name=None):
        from codepilot.core.models.base import ModelResponse

        return ModelResponse(
            content=self._completion,
            model_name="stub",
            latency_seconds=0.0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            finish_reason="stop",
        )


@pytest.fixture()
def stub_model(monkeypatch):
    def _install(completion: str):
        import codepilot.core.models.factory as factory

        monkeypatch.setattr(
            factory, "create_model_client", lambda **kwargs: _FixedOutputClient(completion)
        )

    return _install


def test_risk_findings_are_recorded_on_the_generated_patch(tmp_path: Path, stub_model) -> None:
    _write(tmp_path / "m.py", "def run():\n    # AI-FIX: run the health check\n    return 0\n")
    stub_model("```python\nimport os\nos.system('whoami')\nreturn 0\n```")

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    findings = bundle.file_patches[0].risk_findings
    assert findings, "risky construct should be recorded on the patch"
    assert findings[0]["severity"] == "HIGH"
    assert findings[0]["category"] == "command_execution"


def test_risk_findings_persist_to_the_pending_patch_file(tmp_path: Path, stub_model) -> None:
    """'diff' must be able to re-surface the warning in a later invocation."""
    _write(tmp_path / "m.py", "def run():\n    # AI-FIX: run the health check\n    return 0\n")
    stub_model("```python\nimport os\nos.system('whoami')\nreturn 0\n```")

    generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    saved = json.loads(
        (tmp_path / ".codepilot" / "patches" / "pending_patch.json").read_text(encoding="utf-8")
    )
    assert saved["file_patches"][0]["risk_findings"][0]["category"] == "command_execution"


def test_benign_generated_patch_records_no_findings(tmp_path: Path, stub_model) -> None:
    _write(tmp_path / "m.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    stub_model("```python\nif b == 0:\n    raise ValueError('zero')\nreturn a / b\n```")

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert bundle.file_patches[0].risk_findings == []


def test_screening_annotates_but_never_blocks_the_patch(tmp_path: Path, stub_model) -> None:
    """The developer stays the decision maker: a risky patch is still staged for review."""
    _write(tmp_path / "m.py", "def run():\n    # AI-FIX: run the health check\n    return 0\n")
    stub_model("```python\nimport os\nos.system('whoami')\nreturn 0\n```")

    bundle = generate_preview_patch(tmp_path, load_config(tmp_path, model_provider="ollama"))

    assert bundle.status == "success"
    assert bundle.file_patches


def test_cli_fix_displays_the_security_review_table(
    cli_runner: CliRunner, tmp_path: Path, stub_model
) -> None:
    _write(tmp_path / "m.py", "def run():\n    # AI-FIX: run the health check\n    return 0\n")
    _write(tmp_path / ".codepilot.toml", 'model_provider = "ollama"\n')
    stub_model("```python\nimport os\nos.system('whoami')\nreturn 0\n```")

    result = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Security Review" in result.output
    assert "Review Carefully Before Approving" in result.output


def test_cli_fix_stays_quiet_for_a_benign_patch(
    cli_runner: CliRunner, tmp_path: Path, stub_model
) -> None:
    _write(tmp_path / "m.py", "def divide(a, b):\n    # AI-FIX: guard zero\n    return a / b\n")
    _write(tmp_path / ".codepilot.toml", 'model_provider = "ollama"\n')
    stub_model("```python\nif b == 0:\n    raise ValueError('zero')\nreturn a / b\n```")

    result = cli_runner.invoke(app, ["fix", "--directory", str(tmp_path)])

    assert result.exit_code == 0
    assert "Security Review" not in result.output


def test_render_helper_reports_count_and_handles_empty() -> None:
    class _Patch:
        file_path = "m.py"
        risk_findings = [
            {"category": "code_execution", "severity": "HIGH", "line_number": 2,
             "snippet": "exec(x)", "message": "'exec' executes arbitrary Python source"}
        ]

    class _Bundle:
        file_patches = [_Patch()]

    class _Empty:
        file_patches = []

    assert _render_patch_risk_warning(_Bundle()) == 1
    assert _render_patch_risk_warning(_Empty()) == 0
