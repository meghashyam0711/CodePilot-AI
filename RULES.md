# CodePilot AI — Development & Safety Standards

> **Permanent Operational Rules, Safety Boundaries, File Ownership Policies, and Contribution Standards**

**Status:** Active & Mandatory  
**Scope:** Repository-wide  
**Enforcement:** Mandatory for all human contributors, maintainers, and automated agents.

---

## 1. Core Safety Principles

1. **Preserve Existing Functionality and User Changes:**
   - Never overwrite, delete, or reformat working code without an explicit, documented reason.
   - Respect user configurations, untracked files, and existing Git branches. Untracked files must never be assumed disposable.
   - Verify all existing tests before proposing or applying modifications. If a change causes regressions, revert or fix immediately.

2. **Inspect Before Modifying:**
   - Always inspect files, dependencies, ASTs, and existing project conventions before proposing or applying changes.
   - Never guess module layouts, import paths, or API signatures. Verify them directly from source code.

3. **No Destructive Operations Without Explicit Approval:**
   - Destructive operations (deleting files, resetting Git history, overwriting uncommitted edits) are strictly prohibited without explicit user approval.
   - Destructive Git commands (`git reset --hard`, `git clean -fd`, automatic commits, forced pushes) are **strictly forbidden** under all circumstances.

4. **Truthfulness and Empirical Evidence Standards:**
   - **Never claim a test passed unless it actually executed and returned exit code 0.**
   - **Never claim a feature is implemented without inspecting verified, working code.**
   - Include exact command outputs, test counts, and execution durations in status reports.

---

## 2. Code Generation and Patch Safety

1. **Zero Execution of Unvalidated Model Output:**
   - Local or remote models must **never** be permitted to execute arbitrary shell commands directly.
   - Model-generated code modifications must always be produced as standard unified Git diffs and staged in `.codepilot/patches/pending_patch.json` for developer review.

2. **Mandatory Two-Stage Patch Review Flow:**
   - *Stage 1 (Preview):* `codepilot fix --preview` generates proposed modifications and displays the unified diff with syntax highlighting. Source files **remain untouched**.
   - *Stage 2 (Approved Apply):* `codepilot apply --approved` applies the patch **only when the developer explicitly supplies the `--approved` flag**.
   - If target source files have changed between Stage 1 and Stage 2 (verified via SHA256 hashes), the patch is flagged as stale and rejected immediately.

3. **Unified Diff Integrity:**
   - Patches must adhere to standard unified diff format (`diff -u`).
   - Patches must never touch files matching `protected_files` or directories in `protected_directories`.

---

## 3. Hardware Resource & System Safety

1. **Host Environment & Resource Boundaries:**
   - Routine CLI commands (`scan`, `tasks`, `analyze`, `plan`) must run entirely on the CPU with **zero GPU/VRAM allocation**.
   - Maximum resident memory during static analysis must not exceed 1 GB RAM.
   - When local model inference is enabled, quantization (e.g., 4-bit GGUF) must fit within configured limits, preserving at least 1.5–2 GB VRAM headroom for display and operating system stability.

2. **Prohibited System Modifications:**
   - Do **NOT** modify hardware drivers, GPU settings, BIOS/UEFI firmware, power plans, or OS security policies.
   - Do **NOT** alter global environment variables, system PATH entries, or registry keys without interactive user confirmation.
   - Do **NOT** download model weights, start background daemon processes, or initiate model training without separate, explicit user authorization.

---

## 4. Test Execution & Command Security

1. **Strict Test Command Whitelisting:**
   - Test execution (`codepilot test`) is restricted to verified test runners:
     - Python: `pytest`, `python -m pytest`, `python -m unittest`
     - Node: `npm test`, `npx vitest`, `npx jest`
     - Rust: `cargo test`
     - Go: `go test`
   - Test commands must be verified against an approved runner registry before spawning subprocesses.

2. **Shell Injection Prevention:**
   - Dangerous shell chaining and redirection operators are strictly blocked:
     - Semicolons (`;`), logical operators (`&&`, `||`), pipes (`|`), background operators (`&`), redirections (`>`, `<`), backticks (`` ` ``), and subshell expansions (`$()`).
   - Arguments must be split and executed without `shell=True` wherever the platform allows.

3. **Concurrency Locking & Bounded Capture:**
   - Test runs acquire an exclusive file lock (`.codepilot/test.lock`) recording the active PID to prevent overlapping executions.
   - Output streams are bounded to 64 KB (65,536 characters) to protect terminal buffers from runaway log loops.
   - Processes must enforce timeouts with graceful termination before hard kills.

---

## 5. Security Enclaves & Privacy Boundaries

1. **Secret and Credential Protection:**
   - Files matching `.env`, `.env.*`, `credentials.json`, `*.pem`, `*.key`, `secrets.*`, and `.git/` must **never** be scanned, logged, parsed, or included in LLM context bundles.
   - Loggers must run through the `SecretMaskingFormatter` to strip API keys, Bearer tokens, and credential hashes before writing to disk or terminal.

2. **Boundary Containment & Path Traversal Guards:**
   - Canonical absolute paths are resolved via `pathlib.Path.resolve()`.
   - Directory traversals (`../`) outside the selected repository root are strictly forbidden. Symlinks pointing outside the repository root must be rejected immediately.

---

## 6. Project-Root Detection & File Ownership Policy

### 6.1 Project-Root Detection Rules
1. **Structural Marker Detection:** Detect project roots using verified structural markers: `.git`, `pyproject.toml`, and `.codepilot.toml`.
2. **Git Metadata Resolution:** If executed within a Git repository, resolve the canonical repository root via `git rev-parse --show-toplevel`.
3. **Explicit Root Validation:** An explicitly supplied `--directory` argument is canonicalized and validated before any operation.
4. **Boundary Escape Prevention:**
   - Never treat the user's home directory (`~`) or filesystem root (`/`, `C:\`) as the project root without explicit authorization.
   - Stop and report ambiguity if multiple conflicting roots exist or if no valid root can be established.
5. **Symlink Resolution:** Symbolic links and relative paths must be resolved to their canonical targets to prevent boundary escapes.

### 6.2 File Ownership Categories
All files are classified into five explicit ownership categories before any operation:
- **`PROJECT_MANAGED`:** Internal state, logs, locks, manifests, and patches created by CodePilot AI (located within `.codepilot/*`). CodePilot AI has explicit authorization to create, read, update, and maintain these files.
- **`USER_OWNED`:** Application source code, tests, documentation, and user configurations. These remain user-owned at all times. CodePilot AI may read them and propose changes, but may never modify them without explicit approval (`--approved`).
- **`GENERATED`:** Build outputs, test caches, coverage data, and compiler artifacts (`.pytest_cache`, `.coverage`, `dist`, `build`, `__pycache__`). These are regenerated by tooling rather than patched directly.
- **`PROTECTED`:** Sensitive credentials, environment variables, private keys, VCS internals, and critical package configs (`.env*`, `credentials.json`, `*.pem`, `*.key`, `id_rsa`, `pyproject.toml`, `.git/*`, `.venv/*`). Modifications and deletions are strictly blocked. Reading credential files is strictly forbidden.
- **`EXTERNAL`:** Any path resolving outside the canonical project root. All operations are denied by default.

### 6.3 Read, Write, and Delete Permissions Matrix

| Category | Read | Create | Modify / Overwrite | Delete / Rename |
|---|---|---|---|---|
| `PROJECT_MANAGED` | Allowed | Allowed (internal state) | Allowed | Allowed (managed cleanup) |
| `USER_OWNED` | Allowed | Requires approval | Preview first; Requires `--approved` | Strictly forbidden automatically; Requires explicit confirmation |
| `GENERATED` | Allowed | Tool-driven | Handled via build/test tools | Allowed with clear cleanup scope |
| `PROTECTED` | Denied for secrets; Allowed for non-secret configs | Denied | **Denied** | **Denied** |
| `EXTERNAL` | **Denied** | **Denied** | **Denied** | **Denied** |

---

## 7. Model, Dataset & Artifact Handling Rules

1. **No Large Weights in Version Control:**
   - Model weights (`.bin`, `.gguf`, `.safetensors`), training datasets, and checkpoints must remain outside Git tracking (managed via `.gitignore`).
2. **Preserve Existing Model Assets:**
   - Existing model weights or downloaded assets must never be overwritten, modified, or deleted without interactive user confirmation.
3. **Training Data Compliance:**
   - Any dataset curated for CodePilot AI must be validated for permissive licensing (MIT, Apache 2.0, BSD), scrubbed of proprietary secrets, and verified against Python AST syntax errors.

---

## 8. Dependency Management & Testing Standards

1. **Standard Library First:**
   - Favor the Python standard library (`urllib.request`, `tokenize`, `ast`, `difflib`, `tomllib`) over adding third-party dependencies.
2. **Optional Extras for Heavy Packages:**
   - Heavy dependencies (such as `llama-cpp-python` for standalone GGUF execution) must be configured as optional dependencies in `pyproject.toml` (`[project.optional-dependencies]`), preserving a lightweight core.
3. **100% Automated Test Pass Requirement:**
   - All proposed code changes must pass 100% of the automated test suite without regressions.
   - CodePilot AI tests must run offline by default using `MockModelClient`, requiring zero network or GPU resources for standard CI.

---

## 9. Documentation Standards

1. **Project-Focused Documentation:**
   - All documentation must describe CodePilot AI itself—its architecture, requirements, design, rules, and usage.
   - Do NOT include personal hardware specifications, personal usernames, or machine-specific local directories.
   - Do NOT embed phase-by-phase implementation prompts or development conversation logs into product documentation.
2. **Documentation Maintenance Policy:**
   - Any architectural change or added capability must be reflected across the documentation suite: `README.md`, `ARCHITECTURE.md`, `PRD.md`, `DESIGN.md`, `RULES.md`, `TASK.md`, `MEMORY.md`, and `CHANGELOG.md`.
