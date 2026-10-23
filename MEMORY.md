# CodePilot AI — Technical Memory & Durable Decisions

> **Architectural Decisions, Technical Constraints, Implementation Lessons, and System Boundaries**

**Status:** Living Knowledge Base  
**Current Release:** v0.4.1  
**Maintainer:** CodePilot AI Team  

---

## 1. Durable Technical Decisions & Rationales

### DEC-001: Separation of Comment Discovery and In-Place Code Editing
- **Decision:** Parsing comments and generating code proposals must never modify source files directly.
- **Rationale:** Developer trust is the primary adoption factor. Auto-modifying code without review destroys uncommitted work and invalidates Git working trees. All proposals are stored as unified diffs in `.codepilot/patches/pending_patch.json` until explicitly approved.

### DEC-002: Token-Level Comment Extraction vs. Naive Regex
- **Decision:** Python source files are parsed via `tokenize.tokenize`, filtering strictly on `tokenize.COMMENT`.
- **Rationale:** Naive regex searches match string assignments (e.g., `query = "SELECT # AI-FIX: false positive"`), multiline docstrings, and test fixtures. Standard `tokenize` completely separates string literals from real source code comments.

### DEC-003: Cryptographic Stale Patch Protection (SHA256)
- **Decision:** In `codepilot apply --approved`, the target file's current SHA256 checksum is compared against the `original_sha256` recorded at the time of patch generation (`codepilot fix --preview`).
- **Rationale:** If a developer edits the file between the preview and apply steps, applying the patch would produce conflicting or corrupted edits. Staleness checks protect developer modifications.

### DEC-004: Strict Shell Injection Defense on Test Runner
- **Decision:** `codepilot test` restricts execution to an approved whitelist of test binaries (`pytest`, `python -m pytest`, `npm test`, `cargo test`, `go test`) and strictly forbids shell control operators (`;`, `&&`, `||`, `|`, `&`, `>`, `` ` ``, `$()`).
- **Rationale:** If an LLM or untrusted comment injected malicious commands into `test_command`, executing via `shell=True` would cause remote code execution or data destruction.

### DEC-005: AST-Based Symbol Association
- **Decision:** Instructions are linked to functions, classes, and qualified methods (`Class.method`) using standard library Python AST node ranges and distance checks ($\le 2$ lines above def/class, accounting for decorators).
- **Rationale:** Provides rich semantic context (`target_symbol`, `target_symbol_type`) to downstream LLMs without requiring external language servers or heavyweight language models.

### DEC-006: Extensible Language Adapter Interface Pattern
- **Decision:** Extracted language comment parsing and symbol resolution into a formal `BaseLanguageAdapter` contract and `LanguageAdapterRegistry`.
- **Rationale:** Separating Python tokenization/AST logic from generic regex parsing decouples the core parsing engine from language specifics, allowing clean addition of future language adapters (e.g., Tree-sitter or TypeScript compilers) without altering `InstructionCommentParser`.

### DEC-007: Standard Library HTTP Client for Ollama Integration
- **Decision:** Built `OllamaClient` entirely on Python standard library `urllib.request` and `json` rather than importing `requests` or `httpx`.
- **Rationale:** Eliminates external runtime dependencies, protects against package version conflicts (such as `urllib3` vs `requests` warnings), and guarantees lightweight, portable execution across standard Python 3.11+ environments.

### DEC-008: Untrusted Repository Context Isolation (`<untrusted_repository_context>`)
- **Decision:** Wrapped all extracted code snippets, line numbers, and comments in explicit `<untrusted_repository_context>` blocks in prompt templates, paired with strict system instructions that enclosed code is passive data.
- **Rationale:** Prevents prompt injection attacks embedded inside repository files or instruction comments from hijacking system instructions or model behavior.

### DEC-009: Strict Defensive Secret Exclusion
- **Decision:** `ProjectContextEngine` automatically flags and refuses context generation for sensitive files (`.env`, `credentials.json`, `*.pem`, `*.key`, `id_rsa`), setting `bundle.is_safe = False`.
- **Rationale:** Developer credentials and secrets must never be transmitted to model contexts, even on local models.

### DEC-010: Model Name Resolution with Coder Model Priority
- **Decision:** In `OllamaClient.resolve_model_name()`, if the user has not specified a model override, the client dynamically inspects `/api/tags` and prefers `qwen2.5-coder:1.5b` over `qwen2.5:1.5b-instruct-q4_K_M`.
- **Rationale:** Coding-specialized models produce significantly superior code completions and syntax compliance while sharing identical 1.5B parameters and ~1.2 GB VRAM footprint.

### DEC-011: Standardized JSONL Training Dataset Schema with In-Line Test Verification
- **Decision:** Established `DatasetExample` schema capturing instruction types (`AI-FIX`, `AI-TASK`, `AI-REFACTOR`, `AI-TEST`, `AI-DOCS`), prompt templates adhering to template v1.0.0, and embedded unit tests (`validation_test`).
- **Rationale:** Pairing examples directly with executable validation tests allows automated post-generation verification, preventing hallucinated solutions from entering downstream fine-tuning.

### DEC-012: Defense-in-Depth Quality & Secret Scrubbing in Training Datasets
- **Decision:** Implemented `DatasetValidator` scanning for OpenAI API keys, GitHub tokens, AWS keys, and private keys, as well as enforcing Python AST syntax validation and permissive licensing.
- **Rationale:** Models must never be trained on proprietary credentials or invalid syntax, and open-source compliance requires explicit license tracking (MIT/Apache-2.0).

### DEC-013: Stratified Split Allocation and Cross-Split Leakage Blocking
- **Decision:** Partitioned seed datasets into `train`, `validation`, and `test` splits using stratified distribution by instruction type, backed by content-hash collision checks that raise hard errors on any cross-split leakage.
- **Rationale:** Data leakage between training and test sets corrupts generalization metrics. Automated leakage detection guarantees that evaluation benchmarks remain completely unbiased.

### DEC-014: Diff Hygiene and Clarification Evaluation Metrics
- **Decision:** Evaluated models not just on syntax or task presence, but specifically on diff hygiene (penalizing unrelated bloat) and clarification accuracy (testing if the model politely requests clarification on ambiguous instructions rather than guessing blindly).
- **Rationale:** Real-world developer trust requires concise, focused diffs and humility when requirements are underspecified.

### DEC-015: Pre-Apply Backup Snapshots for Non-Destructive Recovery
- **Decision:** All file modifications in `codepilot apply --approved` create timestamped file copies in `.codepilot/backups/<patch_id>/` along with a cryptographic `manifest.json`.
- **Rationale:** Destructive Git commands (`git reset --hard`, `git clean`) discard uncommitted and untracked work. Reversible backup snapshots allow exact recovery via `codepilot rollback` without altering repository Git status or uncommitted files.

### DEC-016: Canonical Path Traversal & Protected Enclave Defense
- **Decision:** Every target file in patch generation and application is resolved to a canonical path via `pathlib.Path.resolve()` and validated to be inside `project_root`. Access or modification to protected credential extensions (`.env`, `.pem`, `.key`), protected files (`credentials.json`, `pyproject.toml`), and protected directories (`.git`, `.venv`) is strictly rejected with `SecurityError`.
- **Rationale:** AI instruction comments or model-generated paths must never escape the repository boundary or compromise critical build/credential files.

### DEC-017: Test Concurrency Lock and Bounded Capture
- **Decision:** Test runs acquire an atomic lock file `.codepilot/test.lock` with PID tracking, enforce process timeouts with tree termination, and truncate captured output to 64 KB.
- **Rationale:** Prevents race conditions from simultaneous test executions, guards against hanging test processes or infinite loops, and stops memory/terminal buffer exhaustion.

### DEC-018: Single-Turn Bounded AI Error Correction
- **Decision:** `codepilot test --fix` only proposes candidate patches staged in `.codepilot/patches/pending_patch.json` and never loops or applies changes autonomously.
- **Rationale:** Prevents unbounded model hallucination spirals, runaway test runs, or autonomous destruction of working code. Developer review remains the mandatory enforcement boundary.

### DEC-019: Standalone GGUF Model Runtime Decoupled from Ollama
- **Decision:** `StandaloneGGUFClient` integrates with `llama-cpp-python` as an optional dependency, validating GGUF checksums via `compute_file_sha256` before inference.
- **Rationale:** Provides true standalone local model distribution without requiring an external daemon service like Ollama, while maintaining clean failure handling when optional dependencies are not yet installed.

### DEC-020: Centralized Project-Root Detection and File Ownership Policy Layer
- **Decision:** Implemented `resolve_project_root()`, `classify_file_ownership()`, and `check_file_permission()` in `src/codepilot/core/security.py`. All path operations are checked against 5 distinct ownership tiers (`PROJECT_MANAGED`, `USER_OWNED`, `GENERATED`, `PROTECTED`, `EXTERNAL`) and 6 operation types (`READ`, `CREATE`, `MODIFY`, `DELETE`, `RENAME`, `OVERWRITE`).
- **Rationale:** Ensures CodePilot AI never treats user home directories or filesystem roots as project roots, prevents unintended writes outside the repository boundary, and strictly protects user-owned source code and secrets against automatic modifications or deletion.

### DEC-021: Centralized Permission Integration in Patch Application
- **Decision:** Integrated `check_file_permission()` directly inside `apply_approved_patch` in `patcher.py`, catching `SecurityError` and re-raising descriptive `PatchError` on unauthorized file attempts.
- **Rationale:** Centralizes policy enforcement so no file write can bypass ownership authorization, even if called outside the CLI dispatcher.

### DEC-022: Dynamic Runtime Status Inspection
- **Decision:** Updated `get_status()` in `src/codepilot/core/app.py` to evaluate model provider health dynamically using `client.is_available()`, eliminating all hardcoded status strings.
- **Rationale:** Reflects real-time environment readiness accurately across `mock`, `ollama`, `standalone`, and disabled runtimes.

### DEC-023: Explicit Forwarding of Standalone Model Overrides
- **Decision:** In `create_model_client()`, resolve and forward explicit `override_model` to `StandaloneGGUFClient` as `model_path`, anchoring relative paths to `project_root`.
- **Rationale:** Ensures CLI arguments like `codepilot evaluate --provider standalone --model <path>` load the user's intended GGUF model rather than silently reverting to default paths.

### DEC-024: Untrusted Repository Context in Benchmark Request Generation
- **Decision:** `EvaluationRunner.evaluate_cases()` supplies `case.source_code` inside `<untrusted_repository_context>`, clearly separating task instructions from repository code without leaking expected answers or modifying production `fix` flows.
- **Rationale:** Models instructed not to alter function signatures or rewrite surrounding code must receive the source code to observe the target function and parameters.

### DEC-025: Truthful Validation Test Denominator Accounting
- **Decision:** Cases defining a `validation_test` must be counted in the test pass denominator, and cases where code extraction returns empty must be scored as `test_passed = False` rather than `None`.
- **Rationale:** Prevents extraction failures from artificially inflating test pass rates by omitting failed cases from the denominator.

### DEC-026: In-Memory Validation Test Defense-in-Depth and Sandbox Scope
- **Decision:** `run_validation_test_safely()` restricts builtins (`__import__`, `open`, `breakpoint`, `input`, `eval`, `exec`, `quit`, `exit`) to avoid unintended side effects during benchmark runs.
- **Rationale:** While restricting builtins protects against accidental file modifications, in-memory execution is not an OS-level sandbox. Truly adversarial code requires external containerization.

### DEC-027: Prompt-Boundary Echo Filtering in Code Extraction
- **Decision:** In `extract_code_from_completion()`, standalone wrapper lines matching `r"^\s*(?:#\s*)?</?untrusted_repository_context>\s*$"` are stripped from extracted code blocks via `_strip_context_delimiter_lines()`.
- **Rationale:** Compact local coding LLMs (such as 1.5B parameter models) frequently echo surrounding XML/demarcation tags inside their markdown code fences. Stripping only standalone wrapper delimiter lines restores valid code syntax without altering legitimate variable assignments, strings, or comments containing angle brackets.

### DEC-028: Benchmark Recovery and Validation Verification via Stage I Delimiter Hardening
- **Decision:** Executed real-model evaluation v3 against canonical 8-category benchmark using local Qwen2.5-Coder-1.5B-Instruct on CPU. Produced immutable reports `reports/evaluation_qwen_1.5b_cpu_v3.md` and `.json`.
- **Rationale:** Empirically validated that Stage I prompt boundaries and narrow delimiter stripping completely restored syntax validity from 37.5% to 100.0% (+62.5 pp) and elevated validation test pass rate to 85.7% (+71.4 pp vs v2, +28.6 pp vs baseline) without altering benchmark thresholds, evaluation cases, or existing functionality.

### DEC-029: Release Readiness Classification for v0.4.0 (Ready With Limitations)
- **Decision:** Classified CodePilot AI v0.4.0 as READY WITH LIMITATIONS in `reports/release_readiness_audit_v0.4.0.md`.
- **Rationale:** All core systems and safety mechanisms are 100% verified (303/303 tests passing). On the real local 1.5B model, 4 of 5 benchmark metrics meet release thresholds (Syntax 100.0%, Tests 85.7%, Instruction 87.5%, Clarification 87.5%). Diff Hygiene achieved 60.7% (below 75.0% threshold) due to small-model defensive assertions and branch inversions. Because mandatory approval gates and pre-apply backups strictly prevent unreviewed disk modifications, release is safe under this documented accepted limitation.

### DEC-030: Diff Hygiene, Filename Sanitization, and Clarification Hardening
- **Decision:** De-conflicted `SYSTEM_INSTRUCTION` (Rules 1, 2, 4, 6, 7) to instruct models to make the smallest changes that satisfy requested tasks, explicitly forbidding unprompted defensive `isinstance` checks, unrequested argument assertions, and explanatory comments. Implemented string-level `sanitize_filename()` in `src/codepilot/core/security.py` using safe basename extraction and traversal sequence stripping while maintaining strict separation from filesystem security checks (`validate_target_path()`). Hardened clarification output contract so ambiguous tasks trigger a concise question without code fences.
- **Rationale:** Root cause analysis of Stage J benchmark weaknesses identified that small models interpreted "defensive validation" prompts as a mandate to inject unprompted type assertions, chained `.replace()` calls on full path strings, and emitted speculative code fences on ambiguous tasks. De-conflicting prompt instructions and adding a dedicated filename sanitization utility addresses all three failure modes cleanly without altering benchmark fixtures, metric formulas, or existing safety boundaries.

### DEC-031: Task Router Architecture and Clarification Prompt Separation
- **Decision:** Implement a deterministic pre-inference `TaskRouter` in `src/codepilot/core/context/router.py` to route tasks between `CODE_GENERATION` and `CLARIFICATION`. Purge the primary `SYSTEM_INSTRUCTION` of all universal clarification phrases ("Please clarify...", "Could you please clarify..."), and encapsulate clarification requirements in a separate, dedicated `CLARIFICATION_SYSTEM_INSTRUCTION`.
- **Rationale:** Empirical evaluation in Stage M v4 proved that compact 1.5B coding LLMs cannot reliably navigate complex multi-clause conditional directives ("implement the task, but if ambiguous ask a question") when a dominant lexical trigger is present in the prompt. By routing deterministically ahead of inference and dispatching pure, single-purpose system prompts, the model is never presented with conflicting output goals. Guarding against keyword bias ensures security-sensitive implementation tasks (such as `eval-08`) remain routed to code generation.

### DEC-032: Clarification Decoupling and Domain-General Inquiry Pattern Expansion
- **Decision:** In `EvaluationRunner._evaluate_case()`, decouple syntax validation from non-code clarification responses by evaluating `syntax_ok = check_syntax_validity(extracted, case.language) if extracted else True` when a task routes to `TaskRoute.CLARIFICATION`. Expand `check_clarification_accuracy()` in `metrics.py` to match natural domain-general inquiry phrasing (`"what specific criteria"`, `"what requirements"`, `"can you specify"`, `"could you provide"`) while strictly rejecting non-questions, filler, and code fences.
- **Rationale:** Clarification tasks intentionally produce questions rather than executable code fences. Passing an empty extracted code block to AST syntax parsers produced misleading syntax failures (0.0% syntax validity on a text question) which corrupted benchmark composite metrics. Natural clarification questions vary grammatically and should not be penalized because they use standard English question structures instead of one narrow phrase.

### DEC-033: Dual-Separator Basename Normalization and Sanitization Defense
- **Decision:** Hardened `sanitize_filename()` in `src/codepilot/core/security.py` to filter null bytes (`\x00`), ASCII control characters (`0x00-0x1f`, `0x7f`), and Windows drive letters (`C:`), normalize both `/` and `\` directory separators, and iteratively strip path-traversal tokens (`..`). Updated Rule 5 in `SYSTEM_INSTRUCTION` to instruct models to normalize separators before extracting basenames.
- **Rationale:** On Windows or when processing cross-platform path inputs containing backslashes on POSIX runtimes, naive split or basename operations on `/` treat `..\\..\\windows\\system32` as a legal filename. Explicit normalization of both separator styles ensures path traversal guards function identically across all runtime environments.

### DEC-034: Comment-Generation Non-Destructive Invariance Protocol
- **Decision:** Verified and documented CodePilot's comment-generation capability (`AI-DOCS`). The system replaces comment placeholder tokens (`# AI-DOCS`) with valid docstrings or inline comments while strictly preserving all surrounding executable statements, function signatures, imports, variable names, and control-flow structure.
- **Rationale:** Comment generation must never alter program logic or introduce syntax regressions. Verifying AST node invariance guarantees that documentation additions can be safely generated and applied to unfamiliar repositories with zero risk of breaking working functionality.

### DEC-035: Pre-Apply Backup Relative Subdirectory Hierarchy Preservation
- **Decision:** In `src/codepilot/core/recovery.py`, `create_patch_backup()` mirrors the relative directory structure of each modified file under `.codepilot/backups/<patch_id>/` and appends an incremental collision counter. `rollback_patch()` resolves restore destinations directly from the recorded backup path, ensuring full backward compatibility with legacy flat archives.
- **Rationale:** When a patch modifies identically named files across distinct subdirectories (e.g., `pkg_a/service.py` and `pkg_b/service.py`), flattening the backup path causes silent file overwrites during backup creation and data loss upon rollback. Preserving the relative folder structure guarantees independent snapshotting and byte-for-byte fidelity during rollback.

### DEC-036: Safe Staging for Live-Model AI Test Failure Auto-Repair
- **Decision:** In `src/codepilot/core/test_analyzer.py`, `analyze_test_failure()` parses failure tracebacks and source contexts to construct targeted repair prompts for live model providers (`ollama`, `standalone`), extracts repaired code blocks, verifies syntax, generates unified diffs, and stages the candidate patch into `.codepilot/patches/pending_patch.json`. Fixes are never applied autonomously to source files.
- **Rationale:** Auto-repair workflows must enforce the exact same two-stage safety model and user approval gate (`codepilot apply --approved`) as instruction-based fixes (`codepilot fix`), preventing unverified code mutations from entering the working tree.

### DEC-037: Windows Subprocess Python Executable Path Quoting and Normalization
- **Decision:** In `src/codepilot/core/tester.py`, `detect_safe_test_command()` wraps `sys.executable` in double quotes when spaces are detected. `validate_test_command()` accepts quoted executable paths while strictly forbidding shell injection operators (`;`, `&&`, `||`, `|`, `&`, `>`, `` ` ``, `$()`). `run_project_tests()` strips outer double quotes from split argument tokens before passing to `subprocess.Popen`.
- **Rationale:** On Windows, python installations commonly reside in paths with spaces (`C:\Program Files\Python313\python.exe`). Passing literal outer quotes in `argv[0]` causes Windows `CreateProcessW` to fail with `FileNotFoundError: [WinError 2] The system cannot find the file specified`. Normalizing outer quotes from the argument vector resolves the binary cleanly while preserving paths with spaces.

### DEC-038: Standalone GGUF Dynamic Context Sizing and Overflow Defense
- **Decision:** In `src/codepilot/core/models/standalone_client.py`, `_get_or_load_model()` initializes `n_ctx` to `max_context_tokens` (default 2048 or configured) rather than clamping to `2 * max_tokens`. In `generate()`, input prompt tokens are estimated (`len(prompt) // 4`); if prompt tokens exceed `n_ctx`, a descriptive `RuntimeError` is raised. If `prompt_tokens + max_tokens > n_ctx`, `max_tokens` is safely clamped to `max(1, n_ctx - prompt_tokens)` with an informational log.
- **Rationale:** Prevents native crashes in `llama-cpp-python` when prompt lengths exceed narrow context allocations while ensuring models utilize their full allocated context window.

### DEC-039: Preservation of Target Symbol Metadata Across Task Planning
- **Decision:** In `src/codepilot/core/planner.py`, `PlanTask` explicitly includes `target_symbol` and `target_symbol_type` attributes with backward-compatible deserialization in `PlanTask.from_dict()`. `generate_plan()` forwards these attributes directly from `InstructionComment`.
- **Rationale:** Preserves semantic symbol grounding across the planning and execution stages, eliminating the risk of mis-targeting code modifications or falling back to error-prone line-proximity heuristics.

### DEC-040: Recursive Hierarchical AST Symbol Span and Method Resolution
- **Decision:** In `src/codepilot/core/context/engine.py`, `_find_ast_symbol_span()` uses a recursive `_match_path()` helper to resolve symbol spans through arbitrary nesting levels (e.g. `Outer.Inner.method`). In `src/codepilot/core/parser/symbol_resolver.py`, `_find_enclosing_python_symbol()` and `_find_preceding_python_symbol()` recursively traverse class and function hierarchies to construct fully qualified names (`Outer.Inner.method`).
- **Rationale:** Deeply nested classes, namespaces, and inner methods must have their AST boundaries correctly extracted and attributed so downstream context building and docstring extraction target the exact method scope.

### DEC-041: Context Token Budgeting with System Prompt and Wrapper Overhead Accounting
- **Decision:** In `src/codepilot/core/context/engine.py`, `DEFAULT_SYSTEM_OVERHEAD_TOKENS = 400` is defined and incorporated into `_estimate_tokens(..., include_overhead=True)`. Context budget evaluation (`estimated_tokens > token_budget`) reserves this overhead buffer before assembling auxiliary related code context.
- **Rationale:** Prevents context window overflows at inference time by ensuring the context engine reserves adequate token budget for system instructions, instruction prompts, and `<untrusted_repository_context>` wrapper delimiters.

---

## 2. Confirmed Technical Constraints

- **Platform Support:** Linux, macOS, and Windows 10/11.
- **Python Version:** Python 3.11+ supported.
- **System Memory:** 16 GB RAM minimum, 32 GB recommended. Routine CLI execution constrained to $< 1\text{ GB}$ working set.
- **Inference Hardware:** Modern multi-core CPU. Optional NVIDIA GPU ($\ge 8\text{ GB}$ VRAM) for local model acceleration.
- **GPU Usage Rule:** 0 GPU usage during static analysis and unit tests (`MockModelClient` CPU-only). Local model inference with 1.5B Q4 models consumes $\approx 1.2\text{--}1.5\text{ GB}$ VRAM, well within safety threshold.
- **Local Model Backends:** Compatible with local Ollama daemon (`http://localhost:11434`) and standalone embedded GGUF (`llama-cpp-python`).
- **Git Integration:** Standard Git CLI available on system `PATH`.

---

## 3. Implementation Lessons Learned

### LESSON-001: Terminal Table Width in 80-Column Displays
- **Symptom:** Rich tables with 6+ wide columns wrap awkwardly or truncate titles in standard 80-column terminal environments.
- **Remedy:** Keep tables to 4–5 compact columns (`Task ID`, `Type`, `Location`, `Target Symbol`, `Description`), constrain column widths, and provide `--detailed` or `--json` flags for wide inspectable fields.

### LESSON-002: Decorator Line Offsets in Python AST
- **Symptom:** In Python's AST, `FunctionDef.lineno` points to the `def` keyword, but preceding decorators (`@decorator`) appear lines above. A comment placed above `@app.route` was failing a distance threshold check against `lineno`.
- **Remedy:** In `symbol_resolver.py`, `_get_node_start_line` checks `node.decorator_list[0].lineno` first if decorators are present, properly measuring comment-to-definition distance.

### LESSON-003: Subprocess Execution Aliases
- **Symptom:** Windows execution aliases for `python.exe` point to Microsoft Store stub wrappers which fail with `Access is denied` inside non-elevated sandbox subprocesses.
- **Remedy:** Always query the real resolved executable path (`sys.executable`) rather than relying on unquoted `python` on PATH.

### LESSON-004: In-Memory Mock Model Client for Fast CI/CD
- **Symptom:** Running tests that depend on a live Ollama daemon causes CI failures when the daemon is offline or models are not installed.
- **Remedy:** Provided `MockModelClient` implementing `BaseModelClient` interface. All automated tests default to `MockModelClient` or mocked HTTP endpoints, running in $< 10\text{ seconds}$ without network or GPU access.

### LESSON-005: Computed Property Fields in Dataclass Instantiation
- **Symptom:** Passing `total_tasks=1` into `Plan(...)` raised `TypeError: Plan.__init__() got an unexpected keyword argument 'total_tasks'` because `total_tasks` is defined as a computed property (`@property def total_tasks(self) -> int: return len(self.tasks)`).
- **Remedy:** Dataclass constructors only accept initialized fields. Computed properties must never be passed as keyword arguments in tests or fixtures.

### LESSON-006: Capturing Group Requirements for Regex Backreferences
- **Symptom:** Replacing regex patterns with `\1` in secret masking raised `re.error: invalid group reference 1` when the regex pattern lacked capturing parentheses `(...)`.
- **Remedy:** Ensure capturing groups `(...)` are explicitly defined in the search pattern when using numeric backreferences in replacement strings.

### LESSON-007: Empty Extraction Handling in Metric Denominators
- **Symptom:** When a model response contains commentary but no extractable code fence, `extract_code_from_completion()` returns an empty string. If `test_passed` is left as `None`, the case drops out of the validation pass-rate denominator.
- **Remedy:** If a benchmark case defines a `validation_test`, increment the test total denominator unconditionally, and record `test_passed = False` when code extraction yields an empty string.

### LESSON-008: Preservation of Baseline Evaluation Reports
- **Symptom:** Overwriting baseline reports destroys historical tracking and comparisons against improved model runs.
- **Remedy:** Always version evaluation reports (e.g., `_v2`) and keep canonical historical baselines (`evaluation_qwen_1.5b_cpu.md`) immutable.

### LESSON-009: Small Local LLM Delimiter Echoing
- **Symptom:** Real-model evaluation v2 against Qwen2.5-Coder-1.5B showed syntax validity drop to 37.5% because the model wrapped its output in `<untrusted_repository_context>` inside markdown code fences.
- **Remedy:** Harden both prompt instructions (instructing the model that tags are prompt-only and must never be echoed in output) and the extraction parser (`_strip_context_delimiter_lines`) to strip standalone wrapper delimiter lines while strictly preserving legitimate code, strings, and comments containing angle brackets.

### LESSON-010: Synergistic Prompt and Extractor Hardening for Compact LLMs
- **Symptom:** Relying solely on prompt instructions can still leave edge cases if small models occasionally hallucinate tags, while relying solely on post-processing risks stripping legitimate domain code if regexes are too broad.
- **Remedy:** Pair an explicit negative prompt constraint ("do not echo tags") with an ultra-narrow line-level filter targeting only exact standalone wrapper delimiter lines (`r"^\s*(?:#\s*)?</?untrusted_repository_context>\s*$"`). In v3 evaluation, the model completely ceased emitting delimiter tags, and the extractor guaranteed complete syntax parsing integrity (100.0% valid) and elevated test pass rate to 85.7%.

### LESSON-011: Small Models Are Highly Sensitive to Contradictory Directives
- **Symptom:** Small 1.5B parameter models are prone to over-reacting to competing prompt directives. When a system prompt states "Write clean code with defensive validation" while concurrently demanding minimal diffs, the model adds multiple unprompted `isinstance` checks and `assert` statements, degrading diff hygiene scores (from 100% down to 50%). Similarly, instructing "always respond in code fences" conflicts with "ask clarification on ambiguous tasks", causing the model to emit speculative code fences instead of a clean question.
- **Remedy:** Ensure system prompt rules are strictly complementary. Explicitly instruct models to avoid unrequested defensive checks, preserve existing control flow and conditional structures, and specify that ambiguous tasks must be answered with a question only (with no code fences).

### LESSON-012: Extreme Prompt Sensitivity and Clarification Attractor Hijacking in 1.5B Models
- **Symptom:** In real-model evaluation v4 against Qwen2.5-Coder-1.5B, adding verbatim prompt guidance in Rule 4 (`your ENTIRE response must be a single concise clarification question starting with "Please clarify..." or "Could you please clarify..."`) successfully resolved `eval-07` (producing a perfect question without code fences), but triggered a catastrophic mode collapse across all other 7 cases (`eval-01` through `eval-06`, `eval-08`), where the model emitted `"Please clarify..."` (3 tokens) instead of writing code.
- **Remedy:** Compact 1.5B parameter models lack the capacity to balance complex conditional prompt logic with verbatim lexical triggers. For small models, clarification handling must NOT be embedded as a high-salience lexical option in a monolithic system prompt; instead, clarification requirements should be handled via a fast pre-generation classification/routing step, or acquired via dedicated parameter-efficient fine-tuning (e.g. QLoRA).

### LESSON-013: Deterministic Pre-Routing Outperforms Monolithic Multi-Behavior System Prompts for Compact LLMs
- **Symptom:** In real-model evaluation v4, introducing an explicit clarification rule into a monolithic system prompt caused the 1.5B model to collapse into emitting clarification phrases across 7 out of 8 benchmark cases, including fully specified implementation tasks.
- **Remedy:** Do not burden compact models with task classification via meta-instructions inside the prompt. Instead, perform lightweight, deterministic pre-prompt classification (`TaskRouter`), inspecting explicit task metadata, intent signals, structural completeness, and technical specificity. When the task is ready for implementation, send a dedicated, concise code-generation prompt. When essential requirements are missing or contradictory, send a dedicated clarification prompt asking for one focused question without code fences.

### LESSON-014: Decoupling Natural-Language Clarification Questions from Code Syntax Fences
- **Symptom:** In real-model evaluation v5, the model correctly asked a natural-language clarification question on an ambiguous task without code fences. However, the evaluation runner extracted code fences from the completion (which was empty) and passed the empty string to `check_syntax_validity()`, causing the question to be marked as having invalid Python syntax and dropping syntax validity from 100% to 87.5%.
- **Remedy:** Pure text responses to clarification tasks do not represent code syntax failures. When a case routes to clarification, if no code fence is generated, syntax validity must default to valid (`True`). Only if the clarification response includes code fences should those fences be validated for syntax.

### LESSON-015: Small-Model Inline Explanatory Commenting Depresses Diff Hygiene
- **Symptom:** In benchmark evaluations, small 1.5B parameter models routinely insert step-by-step explanatory comments (`# Step 1: parse input`, `# Step 2: calculate sum`) inside generated functions, reducing diff hygiene scores from 100% to 50-60%.
- **Remedy:** Explicitly forbid gratuitous inline comments and docstrings inside code generation responses unless the task explicitly requests documentation (e.g. `AI-DOCS`). Keep the prompt focused on emitting only minimal, clean, task-targeted executable code.

### DEC-019: Structural AST Invariance Verification for AI-DOCS Tasks
- **Decision:** For all `AI-DOCS` tasks on Python files, proposed modifications must undergo `verify_executable_ast_invariance()`. Docstrings are stripped from both original and proposed ASTs, and the remaining AST dumps are compared for exact structural equivalence before creating diffs or applying patches.
- **Rationale:** Documentation-only tasks must never alter runtime behavior, control flow, signatures, variables, or expressions. Enforcing AST invariance prevents models from inadvertently mutating logic or duplicating function statements.

### LESSON-016: Preserving Executable Code Integrity During Documentation Patching
- **Symptom:** In Stage R cross-project testing, when prompted to add a docstring, the local model generated the docstring and re-emitted the function body. Because the engine assumed only placeholder lines (`pass`, `...`) needed popping, the re-emitted statements were inserted above the original body, causing duplicate code and expanding statement counts from 9 to 17.
- **Remedy:** Isolate docstrings explicitly using `extract_documentation_content()`, discard trailing code bodies, position the docstring cleanly under `def`/`class` (handling `# AI-DOCS` placed above or inside), and reject any patch that alters the normalized executable AST via `verify_executable_ast_invariance()`.

### LESSON-017: Empirical Validation of Real-Model Docstring Isolation
- **Symptom:** As confirmed in Stage T, small 1.5B coding models systematically re-emit the enclosing function declaration and body statements when asked to provide a docstring (emitting 514 completion tokens for a 20-line docstring).
- **Remedy:** Real-model validation proved that AST-level docstring extraction (`extract_documentation_content`) paired with structural invariance checks (`verify_executable_ast_invariance`) reliably strips the trailing re-emitted statements, preserving statement counts (9 before, 9 after) and eliminating duplicate dead code with 100% AST equivalence.

### LESSON-018: Class-Method Scope Resolution in AST Documentation Extraction
- **Symptom:** In Stage U cross-project validation on a class method (`ScoreNormalizer.normalize_scores`), the model re-emitted the outer `class` definition along with the method. `extract_documentation_content` evaluated `len(tree.body) == 1` and took `tree.body[0].body` (the class body containing `FunctionDef`), expecting an immediate `ast.Expr` docstring. It did not traverse into the nested method's body to find the method docstring, resulting in extraction failure and raising `FixError`.
- **Remedy:** When inspecting model output for documentation tasks targeting class methods, AST traversal must inspect nested function definitions within class bodies matching `target_symbol` rather than only checking the immediate class body. Pre-write AST invariance checks and patch rejection correctly prevented invalid file writes.

### LESSON-019: Real-Model Validation of Hierarchical Class-Method Documentation Extraction
- **Symptom:** Small coding models (Qwen2.5-Coder-1.5B) predictably re-emit outer classes, enclosing method signatures, comments, and implementation statements when prompted to generate a docstring for a nested class method (emitting 1,088 characters across 206 tokens for a 12-line docstring).
- **Remedy:** Empirical validation in Stage W proved that recursive AST symbol collection (`_collect_named_nodes`) combined with qualified/suffix target resolution and parenthesis-depth tracking for multiline headers isolates exclusively the docstring with 100% precision. The pipeline produced identical executable ASTs (7 statements before, 7 after, zero duplication), rejected unsafe logic mutations with `PatchError`, and preserved byte-for-byte state on rollback.

### LESSON-020: Mirroring Subdirectory Paths in Pre-Apply Backups Prevents Multi-File Collision
- **Symptom:** Flattening backup file paths using only `rel_path.name` causes identical filenames in different subdirectories to overwrite each other inside `.codepilot/backups/<patch_id>/`, resulting in data corruption during rollback.
- **Remedy:** Mirror `rel_path.parent` inside the backup directory structure (`backup_dir / rel_path.parent / f"{rel_path.name}_{ts}.bak"`) and add an incremental collision loop, ensuring every distinct file has an isolated, uncollidable backup destination.

### LESSON-021: Non-Destructive Clarification Route Gating in Diff Generation
- **Symptom:** When a task is routed to `TaskRoute.CLARIFICATION`, the model returns a natural-language question without code blocks. Attempting code extraction threw an unhandled `FixError("No valid code block found")`, aborting preview generation.
- **Remedy:** Explicitly check `task_route == TaskRoute.CLARIFICATION` in `generate_preview_patch()`, capture the question into `PatchBundle.clarification_question`, set `status="clarification_requested"`, bypass code extraction and disk staging, and present the question cleanly via a dedicated CLI panel.

### LESSON-022: Windows CreateProcessW Fails on Outer Quotes in argv[0]
- **Symptom:** Wrapping an executable with spaces in quotes (`f'"{sys.executable}"'`) causes `shlex.split(cmd, posix=False)` to preserve literal quote characters in the token (`['"C:\\Program Files\\...\\python.exe"', ...]`). When passed as a list to `subprocess.Popen`, Windows `CreateProcessW` treats the quote character as part of the filename itself and raises `[WinError 2] The system cannot find the file specified`.
- **Remedy:** In subprocess callers using tokenized argument lists on Windows, strip enclosing outer quotes (`token[1:-1] if token.startswith('"') and token.endswith('"')`) from each element before dispatching to `subprocess.Popen`.

### LESSON-023: GGUF Context Truncation from Narrow max_tokens Clamping
- **Symptom:** Initializing `n_ctx = min(2 * max_tokens, max_context_tokens)` with `max_tokens = 512` allocated only 1024 context tokens. Large prompts with detailed context (>800 tokens) caused llama.cpp to overflow its KV cache or crash with out-of-bounds context exceptions.
- **Remedy:** Initialize `n_ctx` directly to `max_context_tokens` (default 2048), dynamically inspect prompt token load, reject requests that exceed `n_ctx`, and clamp generation tokens to the remaining headroom.

### LESSON-024: Symbol Resolution Divergence Between Comment Parsing and Docstring Extraction
- **Symptom:** During Phase 15 regression testing of nested class methods (`class Outer: class Inner: def compute:`), `PlanTask` forwarded `target_symbol = "Outer"` because `SymbolResolver` only checked the immediate class body. Downstream, `extract_documentation_content` expected qualified method paths (`Outer.Inner.compute`), resulting in target mismatch.
- **Remedy:** Synchronize symbol resolution across all subsystems: make `SymbolResolver` traverse nested classes recursively to construct the fully qualified symbol name (`Outer.Inner.compute`), aligning it with `_find_ast_symbol_span` and `extract_documentation_content`.

---

## 4. Unresolved Technical Questions for Future Architecture

1. **Multi-Language AST Depth:**
   - *Status:* Non-Python languages currently use regex pattern matching for functions/classes.
   - *Question:* Should Tree-sitter bindings be introduced to construct full AST symbol hierarchies for TypeScript, Rust, and Go?
2. **Context Window Optimization for Large Single Files:**
   - *Status:* `ProjectContextEngine` trims auxiliary related context first when exceeding `max_context_tokens`.
   - *Question:* For massive single files (>2,000 lines), should AST-based code folding (replacing unrelated function bodies with `...`) be introduced?
3. **Multi-File Coordinated Patches:**
   - *Status:* `InstructionTask` associates with a single primary symbol and file.
   - *Question:* How should multi-file tasks (e.g., modifying an API route in `views.py` and simultaneously updating a schema in `models.py`) be coordinated in patch staging? (Currently grouped into separate tasks during `codepilot plan`).
