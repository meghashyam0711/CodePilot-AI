# CodePilot AI

> **A Local, Privacy-First, Command-Driven AI Coding Assistant for Unfamiliar Git Repositories**

CodePilot AI is an open-source, command-line coding assistant that brings local large language models directly into your terminal workflow. You can scan project health, inspect repository architectures, request command-driven fixes for specific files and issues, review unified Git diffs, execute test suites safely, and apply verified changes—with zero telemetry, zero cloud dependencies, and mandatory developer approval.

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-404%20passed-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-local%20first-orange.svg)](ARCHITECTURE.md)

---

## Table of Contents

- [Why CodePilot AI?](#why-codepilot-ai)
- [How It Works](#how-it-works)
- [Key Features](#key-features)
- [Installation & Setup](#installation--setup)
- [Project Initialization & Selection](#project-initialization--selection)
- [Command Reference & Workflows](#command-reference--workflows)
  - [1. Comment-Free Project Scanning (`codepilot scan`)](#1-comment-free-project-scanning-codepilot-scan)
  - [2. Independent Architecture Analysis (`codepilot analyze`)](#2-independent-architecture-analysis-codepilot-analyze)
  - [3. Command-Driven Fixes & Issue Targeting (`codepilot fix`)](#3-command-driven-fixes--issue-targeting-codepilot-fix)
  - [4. Reviewing Proposed Diffs (`codepilot diff`)](#4-reviewing-proposed-diffs-codepilot-diff)
  - [5. Approved Patch Application (`codepilot apply`)](#5-approved-patch-application-codepilot-apply)
  - [6. Pre-Apply Snapshots & Safe Rollback (`codepilot rollback`)](#6-pre-apply-snapshots--safe-rollback-codepilot-rollback)
  - [7. Safe Test Execution & Failure Repair (`codepilot test`)](#7-safe-test-execution--failure-repair-codepilot-test)
  - [8. Model Benchmark Evaluation (`codepilot evaluate`)](#8-model-benchmark-evaluation-codepilot-evaluate)
  - [9. Optional In-Code Task Hints (`tasks`, `plan`, `explain`)](#9-optional-in-code-task-hints-tasks-plan-explain)
- [Configuration (`.codepilot.toml`)](#configuration-codepilottoml)
- [Safety Boundaries & Guardrails](#safety-boundaries--guardrails)
- [Supported Languages & File Types](#supported-languages--file-types)
- [Troubleshooting & FAQ](#troubleshooting--faq)
- [Running Automated Tests](#running-automated-tests)
- [Documentation Index](#documentation-index)
- [License](#license)

---

## Why CodePilot AI?

Modern coding assistants frequently require streaming proprietary codebases to remote cloud APIs, presenting intellectual property risks for privacy-conscious developers and air-gapped enterprise environments. Furthermore, autonomous agents that modify files without review often introduce subtle regressions, break test suites, or overwrite uncommitted local work.

CodePilot AI is engineered around four core safety principles:

1. **Local & Private by Default:** Inference runs completely on your machine using local model backends (Ollama or embedded standalone GGUF runtimes via `llama-cpp-python`). Your source code and credentials never leave your system.
2. **Deterministic Repository Context:** Before proposing changes, CodePilot statically analyzes your repository to discover entry points, module dependency graphs, class hierarchies, and AST symbols, giving local models accurate context without context bloat.
3. **Mandatory Developer Approval:** CodePilot never modifies working source files silently. Modifications are produced as standard unified Git diffs staged for inspection (`fix --preview`), and applied to disk only when you pass `--approved`.
4. **Reversible & Safe Operations:** Automatic pre-apply backup snapshots and non-destructive rollbacks guarantee that your working directory is protected at all times. Destructive commands (`git reset --hard`, `git clean`) are strictly forbidden.

---

## How It Works

```
 1. Command or Hint       2. Scan & Analyze       3. Investigate & Diff   4. Inspect Staged Diff  5. Approve & Apply      6. Test & Verify
┌────────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ codepilot fix      │──▶│ codepilot scan    │──▶│ codepilot fix     │──▶│ codepilot diff    │──▶│ codepilot apply   │──▶│ codepilot test    │
│ file.py --issue    │   │ codepilot analyze │   │ (AST & Context    │   │ (Review candidate │   │   --approved      │   │   --approved      │
│ "handle zero div"  │   │ (AST & health map)│   │  diff preview)    │   │  unified patch)   │   │ (Pre-apply backup)│   │ (Safe execution)  │
└────────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘
```

1. **Command-Driven Initiation:** Run `codepilot fix <file> --issue "<description>"` or let CodePilot auto-target detected static code defects. Special in-code comments (`# AI-FIX:`, etc.) remain fully supported as optional hints.
2. **Static Discovery:** CodePilot inspects the repository structure, file extensions, configuration files, test suites, and Python AST syntax health.
3. **Investigation & Context Assembly:** CodePilot isolates the relevant code span, enclosed symbols, module imports, and sibling tests, assembling a token-budgeted prompt with strict `<untrusted_repository_context>` isolation.
4. **Diff Preview:** The model generates a candidate solution staged as a unified diff in `.codepilot/patches/pending_patch.json`. Source files remain 100% untouched.
5. **Approved Application:** You review the diff (`codepilot diff`) and apply it (`codepilot apply --approved`). CodePilot verifies target file checksums (preventing stale patch application) and creates an automatic backup snapshot.
6. **Safe Verification:** Run project tests (`codepilot test --approved`) under strict timeout, concurrency, and output bounds, with optional single-turn error correction (`codepilot test --approved --fix`).

---

## Key Features

- **Command-Driven Operation:** Target specific files and issues directly from your terminal (`codepilot fix calculator.py --issue "handle division by zero"`).
- **Comment-Free Scanning & Health Checks:** `codepilot scan` inspects repository structure, file distributions, test setups, and syntax errors without requiring special AI comments.
- **Independent Architecture Analysis:** `codepilot analyze` maps classes, dependencies, entry points, and static defects without LLM dependency.
- **Two-Stage Code Modification:** Guarantees that code proposals are inspected via unified diffs prior to any disk write.
- **Automatic Recovery & Rollback:** Every patch application creates a pre-apply snapshot with cryptographic manifests. Revert changes instantly with `codepilot rollback`.
- **Protected File Enclaves:** Strictly blocks reading or modifying sensitive files (`.env`, credentials, private keys, `pyproject.toml`, `.git/`).
- **Sandboxed Test Runner:** Executes pytest, npm test, cargo test, or go test with shell-injection prevention, PID locking, configurable timeouts, and 64 KB bounded output capture.
- **Flexible Local Runtimes:** Connects to local Ollama instances or runs self-contained GGUF models directly via `llama-cpp-python` with cryptographic SHA256 integrity checks.

---

## Installation & Setup

### Prerequisites

- Python 3.11 or higher
- Git 2.20 or higher
- Minimum 16 GB system RAM (32 GB recommended for multi-turn local model inference)
- Modern multi-core CPU; optional NVIDIA GPU ($\ge 8\text{ GB}$ VRAM) for accelerated inference

### 1. Standard Installation

Clone the repository and install the package:

```bash
git clone https://github.com/meghashyam0711/CodePilot-AI.git
cd CodePilot-AI

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install in development editable mode
pip install -e .
```

### 2. Standalone Local GGUF Runtime (Optional)

To run local inference directly against GGUF model files without an external Ollama daemon, install the standalone extra:

```bash
pip install -e ".[standalone]"
```

### 3. Verify Installation

Run the built-in diagnostic doctor to verify your environment:

```bash
codepilot doctor
codepilot status
```

---

## Project Initialization & Selection

You can run CodePilot AI directly inside any project directory or target external directories using the `--directory` / `-d` flag.

### Initialize Configuration

To generate a `.codepilot.toml` configuration file with safe defaults in your project:

```bash
# Initialize in current working directory
codepilot init

# Initialize in a specific project directory
codepilot init --directory /path/to/project
```

### Working Across Directories

Every CodePilot CLI command accepts `--directory` / `-d`:

```bash
codepilot scan -d ../my-other-project
codepilot analyze -d ../my-other-project
codepilot fix calculator.py --issue "fix bounds check" -d ../my-other-project
```

---

## Command Reference & Workflows

### 1. Comment-Free Project Scanning (`codepilot scan`)

Inspect the target project's file inventory, extension distribution, configuration files, test directories, skipped files, and static code defects without requiring any AI comments:

```bash
# Scan repository and display project structure, file types, and code health
codepilot scan

# Scan an external directory
codepilot scan --directory /path/to/project

# Output structured JSON for automation or IDE integration
codepilot scan --json
```

**What `codepilot scan` reports:**
- **Scanned Source Files:** Count and listing of discovered source files.
- **Supported File Types:** Breakdown of source file extensions (`.py`, `.js`, `.ts`, etc.).
- **Config Files Discovered:** Detected configuration files (`pyproject.toml`, `package.json`, etc.).
- **Test Directories:** Discovered test suites (`tests/`, `__tests__/`, etc.).
- **Skipped Files:** Files skipped due to size limits, binary content, or protected enclave rules.
- **Static Findings:** Static defects (such as AST syntax errors and empty files) that are immediately actionable via `codepilot fix`.
- **Optional Comment Hints:** Any optional `# AI-*` comments found in the project.

---

### 2. Independent Architecture Analysis (`codepilot analyze`)

Generate comprehensive architectural maps, module dependencies, and symbol hierarchies via deterministic static analysis:

```bash
# Analyze project structure and language distribution
codepilot analyze

# Detailed view of module dependencies and class inheritance
codepilot analyze --detailed

# Preview token-budgeted prompt context for local LLMs
codepilot analyze --context

# Output raw JSON architecture report
codepilot analyze --json

# Save report directly to a file
codepilot analyze -o architecture_report.json
```

`codepilot analyze` operates completely independently of in-code comments, reporting code quality concerns and static defects alongside module architectures.

---

### 3. Command-Driven Fixes & Issue Targeting (`codepilot fix`)

Generate proposed fixes for specific files, explicit issue descriptions, or automatically detected static findings:

```bash
# Fix a specific file with an explicit issue description
codepilot fix src/calculator.py --issue "handle division by zero"

# Fix an issue where the file name is embedded in the prompt
codepilot fix "fix division by zero in src/calculator.py"

# Auto-target a detected static finding (e.g. syntax error) when no target is provided
codepilot fix

# Override model provider and model for fix generation
codepilot fix src/utils.py --issue "fix NoneType error" --provider ollama --model qwen2.5-coder:1.5b

# Output proposed patch bundle as JSON
codepilot fix src/calculator.py --issue "handle division by zero" --json
```

**Investigation Explanation:**
Whenever a fix is requested, CodePilot presents an **Investigation & Proposed Fix** panel displaying:
- **Target:** Resolved target file path and line number.
- **Suspected Problem:** Concrete issue or requirement being addressed.
- **Evidence:** File location, AST symbol name, or static analysis message.
- **Likely Cause:** Technical rationale for the issue.
- **Proposed Correction:** Summary of candidate modification.

**Safety Guarantee:** `codepilot fix` **never modifies source files on disk**. It generates a candidate unified Git diff and stages it in `.codepilot/patches/pending_patch.json`.

---

### 4. Reviewing Proposed Diffs (`codepilot diff`)

Inspect the currently staged patch before authorizing any changes:

```bash
# Inspect the staged unified diff with terminal syntax highlighting
codepilot diff

# Inspect staged patch metadata and diff in JSON format
codepilot diff --json
```

The diff shows exact additions (`+`) and deletions (`-`), target file paths, and cryptographic SHA256 checksums.

---

### 5. Approved Patch Application (`codepilot apply`)

Apply a previously generated, staged, and reviewed patch:

```bash
# Apply the staged patch with explicit developer authorization
codepilot apply --approved

# Target a specific repository directory
codepilot apply --approved --directory /path/to/project
```

**Anti-Staleness Check:** CodePilot compares the current target file's SHA256 hash against the hash recorded when the diff was previewed. If you edited the file between preview and apply, CodePilot rejects the patch to prevent overwriting your work.

**Automatic Backup:** Before touching any file, CodePilot saves a full pre-apply snapshot to `.codepilot/backups/<patch_id>/`.

---

### 6. Pre-Apply Snapshots & Safe Rollback (`codepilot rollback`)

Restore your codebase non-destructively to its pre-patch state:

```bash
# List all available pre-apply backup snapshots
codepilot rollback --list

# Roll back the most recently applied patch
codepilot rollback

# Roll back a specific patch by ID
codepilot rollback --patch-id patch-b1a0364a11f2
```

Rollbacks restore files byte-for-byte from backup manifests without running destructive Git commands (`git reset --hard` and `git clean` are strictly forbidden).

---

### 7. Safe Test Execution & Failure Repair (`codepilot test`)

Execute project test suites under strict concurrency locks and bounded capture:

```bash
# Preview discovered test runner and command without executing
codepilot test --preview

# Execute test suite with explicit developer approval
codepilot test --approved

# Run tests with a custom timeout override (in seconds)
codepilot test --approved --timeout 30.0

# Run a custom test command safely
codepilot test --approved --command "pytest tests/test_calc.py -v"

# Automated failure repair: analyze test failures and stage a candidate fix
codepilot test --approved --fix
```

**Safety Controls:**
- **Concurrency Locking:** Acquires `.codepilot/test.lock` with PID tracking to prevent overlapping test executions.
- **Stream Bounds:** Captures up to 64 KB of output to prevent terminal freeze or buffer overflows.
- **Shell Injection Prevention:** Rejects chained operators (`;`, `&&`, `||`, `|`, `$()`).

---

### 8. Model Benchmark Evaluation (`codepilot evaluate`)

Evaluate local LLMs against the 8 canonical coding and safety benchmarks measuring instruction following, syntax validity, diff hygiene, clarification accuracy, and in-memory validation test execution:

```bash
# Run benchmark against configured provider
codepilot evaluate

# Run benchmark against local standalone GGUF model via CPU inference
codepilot evaluate --provider standalone --model models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf

# Export markdown report
codepilot evaluate --output reports/evaluation.md --format markdown

# Export structured JSON report with strict threshold enforcement
codepilot evaluate --strict --output reports/evaluation.json --format json
```

> [!NOTE]
> **Evaluation Safety Boundary:** In-memory validation test execution in the evaluation runner restricts dangerous builtins (`__import__`, `open`, `eval`, `exec`, `breakpoint`, `input`) to prevent unintended disk or network operations during benchmark runs. This in-memory restriction is defense-in-depth and does not constitute a hardened OS-level sandbox.

---

### 9. Optional In-Code Task Hints (`tasks`, `plan`, `explain`)

While CodePilot is command-driven, you can still embed task comments directly in your source code as optional task hints:

```python
# calculator.py
def divide(a, b):
    # AI-FIX: handle division by zero safely
    return a / b
```

**Supported Hint Types:**
- `AI-FIX`: Bug fixes and edge-case handling.
- `AI-TASK`: Feature implementations.
- `AI-REFACTOR`: Code clean-up and structural improvements.
- `AI-TEST`: Unit tests, fixtures, and assertions.
- `AI-DOCS`: Docstrings and documentation (strictly verified for AST invariance).

**Commands for Comment Hints:**
```bash
# List all in-code task hints with symbols and locations
codepilot tasks

# Filter tasks by type
codepilot tasks --type AI-FIX

# Generate structured implementation plan from tasks
codepilot plan

# Explain a task using local LLM context
codepilot explain <TASK_ID>
```

---

## Configuration (`.codepilot.toml`)

CodePilot loads configuration with cascading precedence: safe defaults $\rightarrow$ `.codepilot.toml` $\rightarrow$ environment variables (`CODEPILOT_*`) $\rightarrow$ CLI arguments.

```toml
# .codepilot.toml
project_root = "."
log_level = "INFO"

# Provider: 'none', 'mock', 'ollama', 'standalone'
model_provider = "ollama"
model_id = "qwen2.5-coder:1.5b"

request_timeout = 30.0
max_context_tokens = 2048
max_input_size = 100000
require_approval = true

# Protected file patterns (never scanned or modified)
protected_files = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials.json",
    "secrets.*",
    "pyproject.toml"
]

# Protected directory names
protected_directories = [
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".idea",
    ".vscode"
]

# Safe test execution command (optional, defaults to auto-detection)
# test_command = "pytest tests -v"
```

---

## Safety Boundaries & Guardrails

1. **Two-Stage Code Modification:** Candidate code is always staged as a diff in `.codepilot/patches/pending_patch.json`. Source files are never modified without the `--approved` flag.
2. **Read-Only Guarantees:** `codepilot scan` and `codepilot analyze` are strictly read-only and never modify files.
3. **Protected File Enclaves:** Reading or modifying credentials, private keys, `.env`, `.git/`, or build configs is rejected with `SecurityError`.
4. **Path Traversal Protection:** Canonical absolute paths are verified via `pathlib.Path.resolve()`, strictly blocking directory traversal (`../../`) and symlink escapes.
5. **No Shell Execution of LLM Code:** Local models cannot run arbitrary shell commands.
6. **AST Invariance for Documentation Tasks:** For `AI-DOCS` tasks, candidate patches are stripped of docstrings and compared against the original AST. If executable statements, control flow, or signatures differ, the patch is rejected.

---

## Supported Languages & File Types

| Language | Extension | Static Discovery | Symbol Extraction | Comment Hint Parser |
|---|---|---|---|---|
| **Python** | `.py` | Full | Full AST (classes, functions, methods) | Full Token-Level (`#`) |
| **JavaScript / TypeScript** | `.js`, `.ts`, `.jsx`, `.tsx` | Full | Regex Heuristics | Full Adapter (`//`, `/* */`) |
| **Go** | `.go` | Full | Regex Heuristics | Full Adapter (`//`, `/* */`) |
| **Rust** | `.rs` | Full | Regex Heuristics | Full Adapter (`//`, `/* */`) |
| **Java** | `.java` | Full | Regex Heuristics | Full Adapter (`//`, `/* */`) |
| **C / C++** | `.c`, `.cpp`, `.h`, `.hpp` | Full | Regex Heuristics | Full Adapter (`//`, `/* */`) |
| **HTML / XML** | `.html`, `.xml` | Full | Structural | Full Adapter (`<!-- -->`) |
| **SQL** | `.sql` | Full | Structural | Full Adapter (`--`) |

> [!NOTE]
> Python source code receives full AST syntax error checking, class/method extraction, and docstring invariance checks via standard library `ast`. Non-Python languages receive full file discovery, dependency indicators, and comment adapter parsing, but do not receive native deep semantic AST analysis.

---

## Troubleshooting & FAQ

### Model Provider Not Configured
If you see `AI model provider is not configured (current: 'none')`:
- In `.codepilot.toml`, set `model_provider = "ollama"` or `model_provider = "standalone"`.
- Alternatively, set the environment variable: `export CODEPILOT_MODEL_PROVIDER="ollama"`.
- For offline testing or CI pipelines, use `model_provider = "mock"` or pass `--provider mock`.

### Test Runner Detection Fails
If `codepilot test` reports `Could not detect test framework in project root`:
- Ensure standard test files exist (e.g. `tests/`, `pytest.ini`, `pyproject.toml`, `test_*.py`).
- Provide an explicit safe command: `codepilot test --approved --command "pytest tests -v"`.
- Or configure `test_command` in `.codepilot.toml`.

### Stale Patch Error
If `codepilot apply --approved` rejects a patch with a stale checksum error:
- The target file was modified after `codepilot fix` generated the candidate diff.
- Run `codepilot fix` again to generate a fresh diff against the updated file content.

---

## Running Automated Tests

Run the complete regression test suite (404 tests across 26 modules):

```bash
pytest -v
```

Run specific test modules:

```bash
pytest tests/test_command_driven.py -v
pytest tests/test_task_router.py -v
pytest tests/test_phase10_integration.py -v
pytest tests/test_ownership_root.py -v
pytest tests/test_phase9_security_recovery.py -v
pytest tests/test_context_engine.py -v
```

---

## Documentation Index

For in-depth technical documentation, refer to:

- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture, data flow, and component design.
- [PRD.md](PRD.md) — Product requirements, target personas, and functional specifications.
- [DESIGN.md](DESIGN.md) — Product design, CLI ergonomics, comment grammar, and recovery patterns.
- [RULES.md](RULES.md) — Mandatory safety principles, file ownership, and execution bounds.
- [TASK.md](TASK.md) — Active engineering tasks, project status, and backlog.
- [MEMORY.md](MEMORY.md) — Durable technical decisions and architectural rationales.
- [CHANGELOG.md](CHANGELOG.md) — Versioned release notes and history.

---

## License

This project is licensed under the [MIT License](LICENSE).
