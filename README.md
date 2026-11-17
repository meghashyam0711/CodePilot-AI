# CodePilot AI

> **A Local, Privacy-First, Command-Driven AI Coding Assistant for Unfamiliar Git Repositories**

CodePilot AI is an open-source, command-line coding assistant that brings local large language models directly into your terminal workflow. You can scan project health, inspect repository architectures, request command-driven fixes for specific files and issues, review unified Git diffs, execute test suites safely, and apply verified changes—with zero telemetry, zero cloud dependencies, and mandatory developer approval.

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-610%20passed-brightgreen.svg)](tests/)
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

Most AI coding tools stream your code to cloud APIs, which creates real intellectual property and compliance headaches if you work under NDAs, healthcare regulations, or strict company privacy policies. Worse, tools that edit files autonomously often introduce subtle bugs, break tests, or overwrite your uncommitted edits without warning.

CodePilot AI is built on four straightforward safety rules:

1. **100% Local and Private:** Everything runs on your machine using Ollama or standalone GGUF models via `llama-cpp-python`. Your code, comments, and files never leave your computer.
2. **Accurate Repository Context:** Before suggesting edits, CodePilot statically analyzes your project to find entry points, class hierarchies, imports, and symbols, so the model gets relevant context without bloated prompts.
3. **You Always Approve Changes:** CodePilot never touches your source files without your permission. It stages suggested edits as standard unified Git diffs so you can inspect them first (`codepilot diff`), applying them only when you pass `--approved`.
4. **Safe and Reversible:** Every approved change creates an automatic backup snapshot. If anything looks off, `codepilot rollback` restores your files instantly without running risky Git resets or clean commands.

---

## How It Works

```
 1. Command or Hint       2. Scan & Analyze       3. Investigate & Diff   4. Inspect Staged Diff  5. Approve & Apply      6. Test & Verify
┌────────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ codepilot fix      │──▶│ codepilot scan    │──▶│ codepilot fix     │──▶│ codepilot diff    │──▶│ codepilot apply   │──▶│ codepilot test    │
│ file.py --issue    │   │ codepilot analyze │   │ (Context & candidate  │   │ (Review candidate │   │   --approved      │   │   --approved      │
│ "handle zero div"  │   │ (AST & health map)│   │  diff preview)    │   │  unified patch)   │   │ (Pre-apply backup)│   │ (Safe execution)  │
└────────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘
```

1. **Start from the Terminal:** Run `codepilot fix <file> --issue "<description>"` or let CodePilot auto-target detected syntax errors. If you prefer annotating code directly, in-code comments (`# AI-FIX:`, etc.) work as optional hints.
2. **Scan the Codebase:** CodePilot checks project structure, file types, config files, test directories, and Python syntax health.
3. **Assemble Context & Investigate:** CodePilot isolates the relevant code lines, symbols, imports, and related tests, building a bounded prompt protected from prompt injection.
4. **Preview the Diff:** The model proposes a fix staged as a unified diff in `.codepilot/patches/pending_patch.json`. Your source files remain completely untouched.
5. **Review & Apply:** Check the diff with `codepilot diff`, then apply it with `codepilot apply --approved`. CodePilot checks file hashes to prevent overwriting recent edits and saves a backup snapshot.
6. **Verify with Tests:** Run your test suite (`codepilot test --approved`) under strict timeout and output bounds, with optional one-turn failure repair (`codepilot test --approved --fix`).

---

## Key Features

- **Direct Terminal Commands:** Fix specific files and describe issues right from your shell (`codepilot fix calc.py --issue "handle division by zero"`).
- **Comment-Free Scanning:** `codepilot scan` checks file types, configurations, test folders, and syntax errors without needing any AI comments.
- **Independent Architecture Mapping:** `codepilot analyze` maps out modules, dependencies, and entry points purely through static analysis.
- **Two-Stage Editing:** Review proposed unified diffs first; files on disk are never modified without explicit approval.
- **One-Command Rollback:** Every patch creates an automatic snapshot with a manifest so you can revert changes immediately with `codepilot rollback`.
- **Protected File Enclaves:** Strictly blocks reading or modifying sensitive files (`.env`, credentials, private keys, `pyproject.toml`, `.git/`), and automatically masks credentials in source files before sending context to models.
- **Sandboxed Test Runner:** Runs pytest, npm test, cargo test, or go test with concurrency locking, configurable timeouts, 64 KB output bounds, and shell-injection protection.
- **Flexible Local Runtimes:** Connects to local Ollama instances or runs self-contained GGUF files directly using `llama-cpp-python` with SHA256 integrity verification.
- **Patch Risk Screening:** Automatically inspects candidate patches for risky calls (`exec`, `eval`, `os.system`, `subprocess`, network requests) and displays warnings by severity alongside the diff.

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

`codepilot doctor` runs 8 checks across the Python runtime, dependencies, Git, configuration, filesystem access, security boundaries and the model runtime. The model-runtime check **actually probes the configured backend** — it connects to the Ollama daemon and confirms the configured model is installed, or verifies the GGUF file — so a provider name alone is never reported as a working runtime. Non-critical problems are reported as `WARN` and summarized as *"Healthy (with warnings)"* rather than claiming the environment meets every requirement.

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

**Security Review of the Candidate Patch:**
Alongside the diff, CodePilot lists any risky construct the patch would *introduce* that was not in the original file — ranked by severity, with the exact line:

```
│ Severity │ File     │ Line │ Finding                          │
├──────────┼──────────┼──────┼──────────────────────────────────┤
│ HIGH     │ calc.py  │    6 │ 'os.system' runs a shell command │
```

`HIGH` covers code and command execution (`exec`, `eval`, `os.system`, `subprocess`); `MEDIUM` covers network access, `pickle`/`yaml` deserialization and dynamic imports; `LOW` covers destructive filesystem calls. Constructs already present in the file are not reported, since they are not the patch's doing. This is **advisory** — the patch is still staged, and nothing reaches disk until you run `apply --approved`.

**Partially Completed Runs:**
If one task's model output is unusable, that task is skipped rather than discarding the whole run. The successful work is staged, the run reports `partial_success`, and the skipped tasks are listed with the reason:

```
│ Task     │ File │ Reason                                            │
├──────────┼──────┼───────────────────────────────────────────────────┤
│ TASK-002 │ m.py │ Model output for AI-DOCS task ... does not        │
│          │      │ contain a valid docstring or comments             │
```

Re-run `codepilot fix` targeting a skipped task to retry it. Output-format failures are common with very small models and usually resolve with a larger one. If *every* task fails, no patch is staged and the command exits with an error.

---

### 4. Reviewing Proposed Diffs (`codepilot diff`)

Inspect the currently staged patch before authorizing any changes:

```bash
# Inspect the staged unified diff with terminal syntax highlighting
codepilot diff

# Inspect staged patch metadata and diff in JSON format
codepilot diff --json
```

The diff shows exact additions (`+`) and deletions (`-`), target file paths, and cryptographic SHA256 checksums. The security review and any skipped tasks are re-displayed here too, so reviewing a patch in a later session gives you the same warnings as when it was generated.

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

# Roll back even though you edited the file after applying the patch
codepilot rollback --force
```

Rollbacks restore files byte-for-byte from backup manifests without running destructive Git commands (`git reset --hard` and `git clean` are strictly forbidden).

**Protection for edits made after a patch was applied.** A snapshot records both the pre-patch content and the content the patch wrote. If you have since edited one of those files yourself, restoring the snapshot would silently discard that work, so CodePilot stops and names the affected files:

```
Rollback stopped to protect your changes.
These file(s) were modified after patch patch-b1a0364a11f2 was applied.
Restoring the backup would discard those edits:
  • src/calculator.py
```

Commit or save your work first, or pass `--force` to overwrite it deliberately.

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
- **Concurrency Locking:** Acquires `.codepilot/test.lock` with PID tracking to prevent overlapping test executions. A lock left behind by a crashed or killed run is detected (its owner process is no longer alive) and reclaimed automatically, so an interrupted run never blocks the repository permanently.
- **Timeout:** Bounded by `test_timeout` (default **900 s**), or `--timeout` for a one-off override. This is deliberately *separate* from `request_timeout`, which bounds model inference only — a test suite routinely runs far longer than a model call.
- **Process-Tree Termination:** On timeout the runner is killed together with everything it spawned (test workers, servers started by tests), so no orphaned processes survive.
- **Stream Bounds:** Captures up to 64 KB of output to prevent terminal freeze or buffer overflows.
- **Shell Injection Prevention:** Rejects chained operators (`;`, `&&`, `||`, `|`, `$()`), and runs the command without a shell. Approved runner names must match at a token boundary, so an executable merely *beginning* with an approved name (e.g. `pytest_something`) is rejected.

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

# Timeout for a single model inference call, in seconds
request_timeout = 30.0

# Timeout for executing the project's test suite, in seconds.
# Separate from 'request_timeout' above: test suites routinely run far longer than
# a model call. Defaults to 900 (15 minutes) when unset.
# test_timeout = 900

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
6. **AST Invariance for Documentation Tasks:** For files whose tasks are *all* `AI-DOCS`, candidate patches are stripped of docstrings and compared against the original AST. If executable statements, control flow, or signatures differ, the patch is rejected. Files that also carry `AI-FIX`/`AI-TASK` work are exempt, since they are expected to change executable code.
7. **Secret Redaction Before Inference:** Credential *files* are never read, and hardcoded credentials found inside ordinary source files are redacted before the content is placed in a prompt. Redaction preserves line numbering and leaves placeholders, `os.environ[...]` indirections and short values untouched, so the model still receives workable code. If a model echoes a redaction placeholder back, the patch is rejected rather than overwriting your real credential with mask text.
8. **Risk Screening of Generated Patches:** Candidate patches are inspected for constructs they would *introduce* — `exec`, `eval`, `os.system`, the `subprocess` family, network calls, `pickle` deserialization, recursive deletes — and these are listed by severity alongside the diff. Screening is advisory: it never blocks a patch, because you remain the decision maker.
9. **Partial Results Instead of Total Loss:** If one task's model output is unusable, that task is skipped and reported while the remaining tasks are staged normally. A run reports `partial_success` and names every skipped task, so a patch never silently covers less than you asked for.

### Local-Only Inference

Inference runs against a local runtime: an Ollama daemon on `localhost` or an embedded GGUF model. If you point `ollama_base_url` at a non-loopback address, CodePilot **permits it but warns**, in the log and in `codepilot doctor`, because repository source code in prompts would then leave your machine. It is not silently blocked, so remote runtimes remain available by deliberate choice.

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

### Test Suite Times Out
If `codepilot test --approved` reports a timeout before your suite finishes:
- Raise `test_timeout` in `.codepilot.toml` (default 900 s), or pass `--timeout <seconds>` for one run.
- Do **not** raise `request_timeout` — that setting bounds model inference only and has no effect on test execution.

### Rollback Stopped To Protect Changes
If `codepilot rollback` refuses and lists files:
- You edited those files after the patch was applied, and restoring the snapshot would discard that work.
- Commit or save the edits, then roll back; or pass `--force` to overwrite them deliberately.

### Tasks Were Skipped
If `codepilot fix` reports `partial_success` and lists skipped tasks:
- The staged patch is valid but covers only the tasks that succeeded.
- Read the stated reason per task. Output-format failures (for example an `AI-DOCS` task that returned code instead of a docstring) are common with very small models and usually resolve with a larger one.
- Re-run `codepilot fix --task-id <TASK-ID>` to retry an individual task.

### Secret Redaction Placeholder Rejected
If `codepilot fix` reports that model output contains a secret-redaction placeholder:
- The target file holds a hardcoded credential, which was masked before being sent to the model, and the model echoed the mask back.
- Applying it would overwrite your real credential with placeholder text, so the patch was refused.
- Move the credential into an environment variable and retry.

---

## Running Automated Tests

Run the complete regression test suite (610 tests across 29 modules):

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
pytest tests/test_secret_redaction.py -v
pytest tests/test_patch_risk.py -v
pytest tests/test_partial_patches.py -v
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
