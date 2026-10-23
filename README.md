# CodePilot AI

> **A Local, Privacy-First AI Coding Assistant for Unfamiliar Git Repositories**

CodePilot AI is an open-source, command-line coding assistant that brings local large language models directly into your development workflow. By adding standard instruction comments to your source code, you can generate implementation plans, inspect repository architectures, review unified Git diffs, execute test suites safely, and apply verified changes—with zero telemetry, zero cloud dependencies, and mandatory developer approval.

[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-391%20passed-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-local%20first-orange.svg)](ARCHITECTURE.md)

---

## Why CodePilot AI?

Modern coding assistants often require streaming proprietary codebases to remote cloud APIs, presenting compliance and intellectual property risks for privacy-conscious developers and air-gapped enterprise environments. Furthermore, tools that modify files autonomously without review frequently break tests, overwrite uncommitted work, or introduce subtle regressions.

CodePilot AI is built on four core principles:

1. **Local & Private by Default:** Inference runs completely on your machine using local model backends (Ollama or embedded standalone GGUF runtimes via `llama-cpp-python`). Your source code and credentials never leave your system.
2. **Deterministic Repository Context:** Before generating code, CodePilot statically analyzes your repository to discover entry points, module dependency graphs, class hierarchies, and AST symbols, giving local models accurate context without context bloat.
3. **Mandatory Developer Approval:** CodePilot never modifies working source files silently. Modifications are produced as standard unified Git diffs staged for inspection (`fix --preview`), and applied to disk only when you pass `--approved`.
4. **Reversible & Safe Operations:** Automatic pre-apply backup snapshots and non-destructive rollbacks guarantee that your working directory is protected at all times. Destructive commands (`git reset --hard`, `git clean`) are strictly forbidden.

---

## How It Works

```
 1. Declare Task       2. Discover & Map       3. Plan & Context       4. Preview Diff         5. Approve & Apply      6. Test & Verify
┌────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ # AI-FIX:      │──▶│ codepilot scan    │──▶│ codepilot plan    │──▶│ codepilot fix     │──▶│ codepilot apply   │──▶│ codepilot test    │
│ Handle empty   │   │ codepilot analyze │   │ codepilot explain │   │   --preview       │   │   --approved      │   │   --approved      │
│ lists safely   │   │ (AST & deps map)  │   │ (Bounded context) │   │ (Unified Git diff)│   │ (Pre-apply backup)│   │ (Safe execution)  │
└────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘   └───────────────────┘
```

1. **Inline Instructions:** Add task comments (`# AI-FIX: ...`, `# AI-TASK: ...`, `# AI-REFACTOR: ...`, `# AI-TEST: ...`, `# AI-DOCS: ...`) directly in your code.
2. **Static Discovery:** CodePilot scans the repository, resolving comments to surrounding functions, methods, and classes using standard AST parsing.
3. **Context Assembly:** Relevant code snippets, module imports, and sibling tests are assembled into a token-budgeted prompt with strict `<untrusted_repository_context>` isolation.
4. **Diff Preview:** The local model generates a minimal, context-grounded solution staged as a unified diff in `.codepilot/patches/pending_patch.json`. Source files remain 100% untouched.
5. **Approved Application:** You review the diff (`codepilot diff`) and apply it (`codepilot apply --approved`). CodePilot verifies target file checksums (preventing stale patch application) and creates an automatic backup snapshot.
6. **Safe Verification:** Run project tests (`codepilot test --approved`) under strict timeout, concurrency, and output bounds, with optional single-turn error correction (`codepilot test --approved --fix`).

---

## Key Features

- **Inline Instruction Parser:** Discovers comments across Python, JavaScript, TypeScript, Go, Rust, Java, C/C++, and SQL, filtering out string literals and docstrings.
- **Static Architecture Analyzer:** Inspects repository layouts, detects frameworks, maps import relationships, and ranks application entry points.
- **Two-Stage Code Modification:** Guarantees that code proposals are inspected via unified diffs prior to any disk write.
- **Automatic Recovery & Rollback:** Every patch application creates a pre-apply snapshot with cryptographic manifests. Revert changes instantly with `codepilot rollback`.
- **Protected File Enclaves:** Strictly blocks reading or modifying sensitive files (`.env`, credentials, private keys, `pyproject.toml`, `.git/`).
- **Sandboxed Test Runner:** Executes pytest, npm test, cargo test, or go test with shell-injection prevention, PID locking, and 64 KB bounded output capture.
- **Flexible Local Runtimes:** Connects to local Ollama instances or runs self-contained GGUF models directly via `llama-cpp-python` with cryptographic SHA256 integrity checks.

---

## Repository Architecture

```
CodePilot AI/
├── pyproject.toml              # Build specification, dependencies & entrypoints
├── README.md                   # Project overview, installation & user guide
├── ARCHITECTURE.md             # System architecture, data flow & component design
├── PRD.md                      # Product requirements, personas & acceptance criteria
├── DESIGN.md                   # CLI design, comment grammar & interaction patterns
├── RULES.md                    # Core safety rules, file ownership & permissions
├── TASK.md                     # Current status, active tasks & backlog
├── MEMORY.md                   # Durable technical decisions & lessons learned
├── CHANGELOG.md                # Release history and versioned changes
├── src/
│   └── codepilot/
│       ├── __init__.py         # Package exports & version info
│       ├── cli.py              # Typer CLI application (14 subcommands)
│       ├── config.py           # Pydantic v2 configuration schema
│       ├── exceptions.py       # Custom exception hierarchy
│       ├── logging_config.py   # Secret-masking logger configuration
│       └── core/
│           ├── app.py          # Application coordinator & system health
│           ├── security.py     # Root detection, path guards & ownership policy
│           ├── recovery.py     # Pre-apply snapshots, manifests & rollback
│           ├── scanner.py      # Instruction comment scanner engine
│           ├── planner.py      # Implementation planning engine
│           ├── fixer.py        # Proposed patch generation & diff preview
│           ├── patcher.py      # Approved patch application engine
│           ├── tester.py       # Safe test runner & concurrency locking
│           ├── test_analyzer.py# Test failure analysis & error correction
│           ├── analyzer/       # Static architecture analysis engine
│           ├── parser/         # Multi-language comment parser & AST resolver
│           ├── context/        # Context engine & prompt construction
│           ├── dataset/        # Training dataset schemas & validator
│           ├── evaluation/     # Benchmark evaluation runner & metrics
│           └── models/         # Modular runtime interfaces (Ollama, Standalone, Mock)
└── tests/
    ├── conftest.py             # Test fixtures and shared mocks
    ├── test_analyzer.py        # Architecture analysis tests
    ├── test_cli.py             # CLI command behavior & exit code tests
    ├── test_config.py          # Configuration validation tests
    ├── test_context_engine.py  # Context retrieval & prompt tests
    ├── test_models.py          # Model client & runtime factory tests
    ├── test_parser.py          # Comment parsing & symbol resolution tests
    ├── test_fix.py             # Patch generation & diff staging tests
    ├── test_apply.py           # Approved patch application & staleness tests
    ├── test_test.py            # Test execution & injection prevention tests
    ├── test_ownership_root.py  # Root resolution & ownership policy tests
    ├── test_phase9_security_recovery.py # Security boundaries & rollback tests
    └── test_phase10_integration.py      # End-to-end integration test suite
```

---

## Technology Stack

- **Runtime:** Python 3.11+ (verified on Python 3.11, 3.12, and 3.13)
- **CLI Framework:** [Typer](https://typer.tiangolo.com/) with [Rich](https://github.com/Textualize/rich) formatting
- **Configuration:** [Pydantic v2](https://docs.pydantic.dev/) and `tomllib` (TOML)
- **Diff & Patching:** Python standard library `difflib` (standard unified Git diffs)
- **Testing:** [pytest](https://docs.pytest.org/)
- **VCS Tooling:** Git CLI integration

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
git clone https://github.com/your-org/codepilot-ai.git
cd codepilot-ai

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

## CLI Command Reference

### Summary of Commands

| Command | Description |
|---|---|
| `codepilot init` | Initialize `.codepilot.toml` configuration in the project directory. |
| `codepilot status` | Inspect workspace, Git status, architecture metrics, and runtime health. |
| `codepilot doctor` | Run environment diagnostics on Python, Git, dependencies, and security boundaries. |
| `codepilot scan` | Scan the repository and display discovered AI instruction comments by type. |
| `codepilot tasks` | List parsed instructions with AST symbol associations, line numbers, and task IDs. |
| `codepilot analyze` | Generate a comprehensive project architecture report via static analysis. |
| `codepilot plan` | Generate a structured implementation plan with risk assessments and step breakdowns. |
| `codepilot explain [ID]` | Explain a task and preview context via local LLM without modifying files. |
| `codepilot fix --preview` | Generate proposed changes and stage a unified Git diff without modifying files. |
| `codepilot diff` | Inspect the currently staged unified patch with syntax highlighting or raw JSON. |
| `codepilot apply --approved` | Apply a previously generated, validated patch after explicit approval. |
| `codepilot rollback` | Restore modified files from pre-apply snapshot backups non-destructively. |
| `codepilot test` | Execute test suites through a safe, concurrency-locked runner with failure analysis. |
| `codepilot evaluate` | Evaluate local models against the 8 canonical quality and safety benchmarks. |

---

### Command Workflows & Examples

#### 1. Discover & Inspect Tasks (`scan` & `tasks`)

Scan your repository for supported instruction comments:

```bash
# Scan repository and display instruction counts by category
codepilot scan

# List tasks with target symbols, file locations, and task IDs
codepilot tasks

# Filter by instruction category
codepilot tasks --type AI-FIX
codepilot tasks --type AI-TASK

# Output machine-readable JSON for scripting or tooling
codepilot tasks --json
```

**Supported Instruction Types:**
- `AI-FIX`: Bug fixes and edge-case handling.
- `AI-TASK`: Feature implementations.
- `AI-REFACTOR`: Code clean-up and structural improvements preserving behavior.
- `AI-TEST`: Unit tests, fixtures, and assertions.
- `AI-DOCS`: Docstrings, type annotations, and documentation.

#### 2. Analyze Codebase Architecture (`analyze`)

Generate deterministic architectural maps without loading an LLM:

```bash
# Analyze project structure and language distribution
codepilot analyze

# Detailed view of module dependencies and class inheritance
codepilot analyze --detailed

# Preview token-budgeted prompt context for local LLMs
codepilot analyze --context

# Output raw JSON architecture report
codepilot analyze --json
```

#### 3. Plan & Explain (`plan` & `explain`)

Structure implementation workflows and query local models safely:

```bash
# Generate implementation plan with risk scoring
codepilot plan

# Verify implementation plan against local model
codepilot plan --llm

# Explain a specific task and inspect assembled context
codepilot explain task-06ecb2a39e

# Preview prompt context offline without executing model inference
codepilot explain task-06ecb2a39e --context-only
```

#### 4. Preview, Review & Apply Patches (`fix`, `diff`, `apply`, `rollback`)

Two-stage patch application guarantees complete developer oversight:

```bash
# Step 1: Generate proposed fix as a unified diff (modifies 0 bytes of source files)
codepilot fix --preview --task-id task-06ecb2a39e

# Step 2: Inspect staged unified diff in terminal with syntax highlighting
codepilot diff

# Step 3: Apply the reviewed patch (requires explicit --approved flag)
codepilot apply --approved

# Step 4: If needed, roll back immediately from the automatic pre-apply backup
codepilot rollback --list
codepilot rollback --patch-id patch-1729580400
```

#### 5. Safe Test Runner & Error Correction (`test`)

Execute tests under concurrency locks and bounded capture:

```bash
# Preview discovered test runner without executing subprocesses
codepilot test --preview

# Execute test suite with explicit approval
codepilot test --approved

# Single-turn error correction: analyze test failures and propose a candidate fix
codepilot test --approved --fix
```

#### 6. Local Model Benchmark Evaluation (`evaluate`)

Evaluate local LLMs against the 8 canonical benchmark categories measuring instruction following, syntax validity, diff hygiene, clarification accuracy, and in-memory validation test execution:

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

# Default test command
test_command = "pytest tests -v"
```

---

## Running Automated Tests

Run the complete regression test suite (391 tests across 25 modules):

```bash
pytest -v
```

Run specific test modules:

```bash
pytest tests/test_task_router.py -v
pytest tests/test_phase10_integration.py -v
pytest tests/test_ownership_root.py -v
pytest tests/test_phase9_security_recovery.py -v
pytest tests/test_context_engine.py -v
```

---

## Documentation Directory

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
