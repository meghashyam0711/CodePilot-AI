# CodePilot AI — Current Work and Engineering Backlog

> **Active Project Status, Verified Capabilities, Open Issues, and Engineering Backlog**

**Current Release:** v0.4.1  
**Overall Status:** Healthy / All Systems Operational  
**Automated Test Suite:** 610 / 610 Tests Passing (100% Pass Rate across 29 Modules)  
**Last Verified:** November 2026  

---

## 1. Current Project Status

CodePilot AI v0.4.1 has completed its planned development cycle, release readiness audit, and command-driven refactor. The entire development workflow has been validated on isolated test repositories:

- **Command-Driven Workflows:** Scanning, architecture analysis, and fix generation operate completely independently of in-code comments. Commands like `codepilot scan`, `codepilot analyze`, and `codepilot fix` target files and issues directly from your terminal.
- **Optional In-Code Hints:** Comments like `# AI-FIX:`, `# AI-TASK:`, `# AI-REFACTOR:`, `# AI-TEST:`, and `# AI-DOCS:` remain supported as optional hints for developers who prefer annotating code while reading it.
- **Two-Stage Editing & Approval:** Proposed code changes are staged as unified Git diffs in `.codepilot/patches/pending_patch.json`. Source files remain completely untouched until you explicitly run `codepilot apply --approved`.
- **Pre-Apply Backups & Safe Rollback:** Every patch application creates a full snapshot in `.codepilot/backups/<id>/`. You can revert changes anytime with `codepilot rollback` without running destructive Git commands (`git reset --hard` and `git clean` are strictly forbidden).
- **Safe Test Runner:** Executes test suites (`pytest`, `npm test`, `cargo test`, `go test`) with process concurrency locking (`.codepilot/test.lock`), 64 KB output stream limits, configurable timeouts, and shell-injection prevention.
- **Flexible Model Backends:** Supports local Ollama daemons, standalone GGUF files (`llama-cpp-python`) running locally on CPU or GPU, and an offline mock client for fast CI testing.
- **Benchmark Evaluation:** An 8-category benchmark tests models on instruction following, syntax validity, diff hygiene, clarification handling, and safe in-memory test execution.

---

## 2. Implemented and Verified Capabilities

| Subsystem | Verified Capabilities | Test Evidence |
|---|---|---|
| **CLI & Runtime Diagnostics** | Typer CLI with 14 subcommands, dual human/JSON outputs, cascading configuration (`.codepilot.toml`), and environment doctor (`codepilot doctor`) | `tests/test_cli.py`, `tests/test_app.py`, `tests/test_doctor.py` |
| **Command-Driven Intelligence** | Direct file and issue targeting (`codepilot fix calc.py --issue "..."`), comment-free scanning, syntax error auto-targeting, and timeout overrides | `tests/test_command_driven.py` |
| **Static Architecture Analyzer** | Directory traversal honoring `.gitignore`, Python AST symbol extraction, entry point ranking, dependency graphs, and architecture reporting (`codepilot analyze`) | `tests/test_analyzer.py` |
| **Instruction Comment Parser** | Token-level Python comment parser (excludes strings/docstrings), multi-language adapters, 5 instruction types, multiline continuations, and AST symbol mapping | `tests/test_parser.py`, `tests/test_scan.py` |
| **Context & Prompt Engine** | Line-numbered snippet extraction, test pairing, token budgeting ($\le 2048$ tokens), nested symbol extraction (`Outer.Inner.method`), and `<untrusted_repository_context>` isolation | `tests/test_context_engine.py`, `tests/test_task_router.py` |
| **Modular Model Runtimes** | Pluggable runtime interface (`BaseModelClient`), standard library HTTP client for Ollama, standalone GGUF runtime (`llama-cpp-python`) with dynamic `n_ctx` sizing, and offline mock client | `tests/test_models.py` |
| **Two-Stage Patch Modification** | Preview diff generation (`codepilot fix --preview`), unified diff inspector (`codepilot diff`), mandatory approval gate (`codepilot apply --approved`), and SHA256 staleness defense | `tests/test_fix.py`, `tests/test_apply.py` |
| **Reversible Recovery & Rollback** | Pre-apply backup snapshots in `.codepilot/backups/<id>/` with relative directory mirroring, `manifest.json`, and non-destructive rollback (`codepilot rollback`) | `tests/test_phase9_security_recovery.py` |
| **Safe Test Execution & Fix** | Safe test discovery, preview mode (`codepilot test --preview`), concurrency locking (`.codepilot/test.lock`), 64 KB capture, shell injection blocking, and live-model test repair staging (`codepilot test --approved --fix`) | `tests/test_test.py`, `tests/test_phase9_security_recovery.py` |
| **Security & File Ownership** | Authoritative root resolution (`resolve_project_root`), 5-tier ownership classification, permission checks, and path traversal guards (`validate_target_path`) | `tests/test_ownership_root.py`, `tests/test_phase9_security_recovery.py` |
| **Secret Redaction & Protection** | Masks API keys, tokens, and passwords in source files while preserving line numbering; rejects patches that echo redaction placeholders | `tests/test_secret_redaction.py` |
| **Patch Risk Screening** | Advisory scanning of candidate patches for risky calls (`exec`, `eval`, `os.system`, `subprocess`, network access, deserialization) | `tests/test_patch_risk.py` |
| **Partial Patch Handling** | Staging usable work when multi-task runs encounter an error, reporting `partial_success` and listing skipped tasks | `tests/test_partial_patches.py` |
| **Concurrency & Resource Limits** | Process locking, stale lock recovery, timeout enforcement, and memory boundaries | `tests/test_concurrency_and_limits.py` |
| **Benchmark Evaluation** | 8-category evaluation runner testing instruction following, syntax validity, diff hygiene, clarification accuracy, and in-memory test safety | `tests/test_dataset.py`, `tests/test_evaluation.py`, `tests/test_cli_evaluate.py` |
| **End-to-End Integration** | Complete lifecycle validation across isolated repositories: routing, preview, approval gate, backup snapshots, rollback, and security boundaries | `tests/test_phase10_integration.py` |

---

## 3. Active Engineering Backlog & Future Tasks

### Long-Term Enhancements & Packaging

- [ ] **TASK-EXT-01: Dedicated QLoRA Fine-Tuning Execution**
  - **Description:** Run fine-tuning for `Qwen/Qwen2.5-Coder-1.5B-Instruct` using the verified `training/data/train.jsonl` dataset and `training/qlora_config.yaml` when dedicated training compute is allocated.
  - **Goal:** Export trained quantized GGUF weights specialized for minimal unified diffs and precise clarification handling.

- [ ] **TASK-EXT-02: Multi-Language Tree-sitter AST Extractors**
  - **Description:** Implement Tree-sitter parsers for TypeScript, JavaScript, Go, Rust, and Java to provide symbol resolution matching Python's native AST extractor.
  - **Goal:** Extract functions, classes, interfaces, and methods for TypeScript and Go, associating tasks with qualified symbols.

- [ ] **TASK-EXT-03: Multi-Turn Interactive Test Repair Loop**
  - **Description:** Extend `codepilot test --fix` to support an interactive, multi-turn repair loop where the developer reviews each candidate fix step-by-step.
  - **Goal:** Bound attempts to a maximum (e.g., 3 turns), asking for user confirmation between iterations and stopping as soon as tests pass.

- [ ] **TASK-EXT-04: Packaging and PyPI Distribution Pipeline**
  - **Description:** Prepare GitHub Actions workflows for automated test runs on Linux, macOS, and Windows runners, and build distribution wheels (`.whl`) and source tarballs (`.tar.gz`).
  - **Goal:** Ensure `python -m build` passes cleanly, produces valid wheels, and passes `twine check`.

---

## 4. Open Issues and Blockers

- **Blockers:** *None.* All core capabilities, security controls, and routing mechanisms are implemented and verified with 610 passing automated tests across 29 modules.
- **Latent Audit Issues:** *None.* All 9 findings from the internal architecture audit have been resolved with passing regression tests.

---

## 5. Summary & Handover

- **Current Status:** All planned engineering phases completed and verified.
- **Readiness:** CodePilot AI v0.4.1 is stable, fully tested, documented, and ready for local developer use or release tagging.
