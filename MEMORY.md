# CodePilot AI — Technical Memory & Durable Decisions

> **Architectural Decisions, Technical Constraints, Implementation Lessons, and System Boundaries**

**Status:** Living Knowledge Base  
**Current Release:** v0.4.1  
**Maintainer:** CodePilot AI Team  

---

## 1. Architectural Decisions & Rationales

### DEC-001: Separate Task Discovery from In-Place Code Editing
- **Decision:** Parsing tasks and generating code proposals must never modify files directly on disk.
- **Rationale:** Developer trust is essential. Modifying code without review destroys uncommitted work and invalidates Git status. All proposals are staged as unified diffs in `.codepilot/patches/pending_patch.json` until explicitly approved.

### DEC-002: Token-Level Comment Extraction vs. Naive Regex
- **Decision:** Python source files are parsed with standard library `tokenize.tokenize`, filtering strictly on `tokenize.COMMENT`.
- **Rationale:** Naive regex searches match string assignments (e.g. `query = "SELECT # AI-FIX: false positive"`), multiline docstrings, and test data. Standard `tokenize` completely separates string literals from real code comments.

### DEC-003: Check File Hashes Before Applying Patches (Staleness Protection)
- **Decision:** During `codepilot apply --approved`, the target file's current SHA256 checksum is compared against the hash recorded when the diff was previewed.
- **Rationale:** If you edit a file between previewing a patch and applying it, applying the old diff would overwrite your fresh edits. Checking file hashes stops this immediately.

### DEC-004: Strict Shell Injection Defense on Test Runner
- **Decision:** `codepilot test` restricts execution to an approved list of test binaries (`pytest`, `python -m pytest`, `npm test`, `cargo test`, `go test`) and strictly forbids shell operators (`;`, `&&`, `||`, `|`, `&`, `>`, `` ` ``, `$()`).
- **Rationale:** If an LLM or untrusted input placed malicious shell commands in a test string, running it with `shell=True` would execute arbitrary code. Commands run as tokenized argument lists without a shell.

### DEC-005: AST-Based Symbol Association
- **Decision:** Instructions are linked to functions, classes, and methods (`Class.method`) using Python standard library AST node ranges and distance checks ($\le 2$ lines above `def`/`class`, taking decorators into account).
- **Rationale:** Gives downstream models precise context (`target_symbol`, `target_symbol_type`) without needing heavy external language servers.

### DEC-006: Extensible Language Adapter Interface Pattern
- **Decision:** Extracted language comment parsing and symbol resolution into a formal `BaseLanguageAdapter` contract and `LanguageAdapterRegistry`.
- **Rationale:** Keeps Python tokenization/AST logic cleanly separated from generic regex adapters, making it easy to add future language extractors (like Tree-sitter) without touching the core parser.

### DEC-007: Standard Library HTTP Client for Ollama Integration
- **Decision:** Built `OllamaClient` entirely with Python's standard library `urllib.request` and `json`, avoiding external dependencies like `requests` or `httpx`.
- **Rationale:** Avoids dependency conflicts, reduces installation overhead, and keeps CodePilot lightweight and portable across Python 3.11+ environments.

### DEC-008: Untrusted Repository Context Isolation
- **Decision:** Wrapped all extracted code snippets, line numbers, and comments in explicit `<untrusted_repository_context>` blocks in prompt templates, with system instructions treating enclosed code strictly as passive data.
- **Rationale:** Prevents prompt injection attacks hidden inside repository files or comments from hijacking system instructions or model behavior.

### DEC-009: Defensive Secret Protection
- **Decision:** `ProjectContextEngine` automatically refuses context generation for sensitive files (`.env`, `credentials.json`, `*.pem`, `*.key`, `id_rsa`), setting `bundle.is_safe = False`.
- **Rationale:** Developer credentials and secrets must never be passed to model prompts, even on local models.

### DEC-010: Model Name Resolution with Coder Model Priority
- **Decision:** When the user has not specified a model override, `OllamaClient.resolve_model_name()` checks installed models and prefers `qwen2.5-coder:1.5b` over general chat variants like `qwen2.5:1.5b-instruct-q4_K_M`.
- **Rationale:** Coding-specialized models produce significantly better syntax and diffs while fitting comfortably within the same ~1.2 GB VRAM footprint.

### DEC-011: JSONL Training Dataset Schema with In-Line Test Verification
- **Decision:** Built a `DatasetExample` schema capturing instruction types (`AI-FIX`, `AI-TASK`, etc.), prompt templates, and embedded unit tests (`validation_test`).
- **Rationale:** Pairing examples directly with executable validation tests allows automated verification, ensuring only working code enters fine-tuning datasets.

### DEC-012: Secret Scrubbing and Syntax Validation for Training Data
- **Decision:** Implemented `DatasetValidator` to scan training examples for API keys, tokens, and private keys, as well as verifying Python AST syntax and license terms.
- **Rationale:** Models should never be trained on secrets or invalid syntax, and open-source datasets must track permissive licenses (MIT/Apache-2.0).

### DEC-013: Stratified Dataset Split and Leakage Checks
- **Decision:** Partitioned seed datasets into `train`, `validation`, and `test` splits using stratified distribution by instruction type, backed by content-hash collision checks.
- **Rationale:** Data leakage between training and test sets invalidates benchmark scores. Automated collision checks guarantee evaluation benchmarks remain unbiased.

### DEC-014: Diff Hygiene and Clarification Evaluation Metrics
- **Decision:** Benchmark evaluation scores not just syntax and test pass rates, but also diff hygiene (penalizing unrelated edits) and clarification accuracy (checking whether models ask a question on ambiguous tasks rather than guessing blindly).
- **Rationale:** Practical developer trust requires clean, focused diffs and the humility to ask for clarification when requirements are unclear.

### DEC-015: Pre-Apply Backup Snapshots for Safe Recovery
- **Decision:** Every patch applied via `codepilot apply --approved` creates timestamped file copies in `.codepilot/backups/<patch_id>/` with a cryptographic `manifest.json`.
- **Rationale:** Destructive Git commands (`git reset --hard`, `git clean`) discard uncommitted and untracked work. Reversible backup snapshots allow exact recovery via `codepilot rollback` without altering repository Git history.

### DEC-016: Canonical Path Validation & Protected Enclaves
- **Decision:** Target paths are resolved to canonical absolute paths via `pathlib.Path.resolve()` and verified to reside inside `project_root`. Protected extensions (`.env`, `*.pem`, `*.key`) and directories (`.git`, `.venv`) are strictly blocked.
- **Rationale:** Model-generated paths or comments must never escape the repository boundary or touch sensitive system files.

### DEC-017: Test Concurrency Lock and Bounded Output
- **Decision:** Test runs acquire an atomic lock file `.codepilot/test.lock` with PID tracking, enforce process timeouts, and cap output capture at 64 KB.
- **Rationale:** Prevents race conditions from overlapping test runs, terminates hanging processes cleanly, and stops runaways from exhausting memory or terminal buffers.

### DEC-018: Single-Turn Bounded AI Error Correction
- **Decision:** `codepilot test --fix` only proposes candidate patches staged in `.codepilot/patches/pending_patch.json` and never loops or applies changes autonomously.
- **Rationale:** Prevents hallucination spirals or runaway test execution. The developer always inspects and approves changes.

### DEC-019: Standalone GGUF Model Runtime Decoupled from Ollama
- **Decision:** Integrated `StandaloneGGUFClient` with `llama-cpp-python` as an optional dependency, validating GGUF checksums via `compute_file_sha256` before inference.
- **Rationale:** Enables self-contained local model execution without requiring an external daemon like Ollama, while failing cleanly when optional packages are not installed.

### DEC-020: Centralized Project-Root Detection and File Ownership Tiers
- **Decision:** Implemented `resolve_project_root()`, `classify_file_ownership()`, and `check_file_permission()` in `src/codepilot/core/security.py`, defining 5 tiers (`PROJECT_MANAGED`, `USER_OWNED`, `GENERATED`, `PROTECTED`, `EXTERNAL`) and 6 operations (`READ`, `CREATE`, `MODIFY`, `DELETE`, `RENAME`, `OVERWRITE`).
- **Rationale:** Ensures CodePilot never treats home or root directories as project roots, prevents unintended writes outside the project, and protects user source files from unauthorized modifications.

### DEC-021: Enforce File Permissions Inside Patch Application
- **Decision:** Integrated `check_file_permission()` directly inside `apply_approved_patch` in `patcher.py`, converting `SecurityError` into a descriptive `PatchError` on unauthorized file writes.
- **Rationale:** Guarantees that no file write can bypass ownership authorization, even if called outside the CLI dispatcher.

### DEC-022: Dynamic Runtime Status Reporting
- **Decision:** Updated `get_status()` in `src/codepilot/core/app.py` to check model health dynamically via `client.is_available()`, eliminating hardcoded status strings.
- **Rationale:** Accurately reflects environment readiness across `mock`, `ollama`, `standalone`, and unconfigured runtimes.

### DEC-023: Forward Standalone Model Path Overrides
- **Decision:** In `create_model_client()`, resolve and forward explicit `override_model` to `StandaloneGGUFClient` as `model_path`, anchoring relative paths to `project_root`.
- **Rationale:** Ensures CLI arguments like `codepilot evaluate --provider standalone --model <path>` load the user's specified GGUF file rather than falling back to default paths.

### DEC-024: Untrusted Repository Context in Benchmark Runs
- **Decision:** `EvaluationRunner.evaluate_cases()` supplies `case.source_code` inside `<untrusted_repository_context>`, clearly separating task instructions from repository code without leaking expected answers.
- **Rationale:** Models instructed not to alter function signatures or rewrite surrounding code must receive the source code to observe the target function and parameters.

### DEC-025: Accurate Validation Test Denominator Accounting
- **Decision:** Benchmark cases defining a `validation_test` must be counted in the test pass denominator, and cases where code extraction returns empty must be scored as `test_passed = False` rather than `None`.
- **Rationale:** Prevents extraction failures from artificially inflating test pass rates by omitting failed cases from the denominator.

### DEC-026: Restrict Builtins During In-Memory Validation Tests
- **Decision:** `run_validation_test_safely()` restricts builtins (`__import__`, `open`, `breakpoint`, `input`, `eval`, `exec`, `quit`, `exit`) during benchmark runs.
- **Rationale:** Protects local environments from accidental file modifications or execution loops during unit test validation.

### DEC-027: Strip Echoed Wrapper Delimiters in Code Extraction
- **Decision:** In `extract_code_from_completion()`, standalone wrapper lines matching `r"^\s*(?:#\s*)?</?untrusted_repository_context>\s*$"` are stripped from extracted code blocks via `_strip_context_delimiter_lines()`.
- **Rationale:** Compact 1.5B coding LLMs sometimes echo surrounding XML demarcation tags inside their markdown code fences. Stripping standalone wrapper delimiter lines restores valid code syntax without altering legitimate code containing angle brackets.

### DEC-028: Benchmark Recovery via Delimiter Hardening
- **Decision:** Executed real-model evaluation v3 against canonical 8-category benchmark using local Qwen2.5-Coder-1.5B-Instruct on CPU. Produced immutable reports `reports/evaluation_qwen_1.5b_cpu_v3.md` and `.json`.
- **Rationale:** Validated that prompt boundaries and narrow delimiter stripping completely restored syntax validity from 37.5% to 100.0% and elevated validation test pass rate to 85.7% without altering benchmark thresholds or existing functionality.

### DEC-029: Release Readiness Classification for v0.4.0
- **Decision:** Classified CodePilot AI v0.4.0 as READY WITH LIMITATIONS in `reports/release_readiness_audit_v0.4.0.md`.
- **Rationale:** All core systems and safety mechanisms are 100% verified (303/303 tests passing). On the real local 1.5B model, 4 of 5 benchmark metrics met release thresholds (Syntax 100.0%, Tests 85.7%, Instruction 87.5%, Clarification 87.5%). Diff Hygiene achieved 60.7% (below 75.0% threshold) due to small-model defensive assertions. Because mandatory approval gates and pre-apply backups strictly prevent unreviewed disk modifications, release was safe under this documented accepted limitation.

### DEC-030: Diff Hygiene, Filename Sanitization, and Clarification Hardening
- **Decision:** De-conflicted `SYSTEM_INSTRUCTION` (Rules 1, 2, 4, 6, 7) to instruct models to make minimal changes, forbidding unprompted `isinstance` checks, unrequested assertions, and explanatory comments. Implemented string-level `sanitize_filename()` in `security.py` using safe basename extraction and traversal stripping. Hardened clarification output contract so ambiguous tasks trigger a concise question without code fences.
- **Rationale:** Small models interpreted "defensive validation" prompts as a mandate to inject type assertions and emitted speculative code fences on ambiguous tasks. De-conflicting instructions and adding filename sanitization resolved these issues cleanly.

### DEC-031: Task Router Architecture and Clarification Prompt Separation
- **Decision:** Implemented a deterministic pre-inference `TaskRouter` in `src/codepilot/core/context/router.py` to route tasks between `CODE_GENERATION` and `CLARIFICATION`. Removed clarification phrases from the main `SYSTEM_INSTRUCTION` and placed clarification rules in a separate `CLARIFICATION_SYSTEM_INSTRUCTION`.
- **Rationale:** Compact 1.5B coding models cannot reliably balance complex conditional prompt logic with verbatim lexical triggers. Pre-routing tasks deterministically ensures the model receives a focused, single-purpose prompt.

### DEC-032: Clarification Decoupling and Natural-Language Inquiry Patterns
- **Decision:** In `EvaluationRunner._evaluate_case()`, decouple syntax validation from non-code clarification responses by evaluating syntax only when code fences are present. Expanded `check_clarification_accuracy()` in `metrics.py` to match natural domain-general inquiry phrasing (`"what specific criteria"`, `"what requirements"`, `"can you specify"`).
- **Rationale:** Clarification tasks intentionally produce questions rather than code fences. Passing an empty code block to AST syntax parsers produced misleading syntax failures, corrupting composite metrics.

### DEC-033: Cross-Platform Separator Normalization in Filename Sanitization
- **Decision:** Hardened `sanitize_filename()` in `src/codepilot/core/security.py` to filter null bytes, ASCII control characters, and Windows drive letters, normalizing both `/` and `\` separators and iteratively stripping path-traversal tokens (`..`).
- **Rationale:** On Windows or when processing cross-platform paths with backslashes on POSIX runtimes, naive splitting on `/` treats `..\\..\\windows\\system32` as a legal filename. Explicit normalization ensures path traversal guards function identically across all operating systems.

### DEC-034: Structural AST Invariance Verification for AI-DOCS Tasks
- **Decision:** For all `AI-DOCS` tasks on Python files, proposed modifications undergo `verify_executable_ast_invariance()`. Docstrings are stripped from both original and proposed ASTs, and the remaining AST dumps are compared for exact structural equivalence before creating diffs or applying patches.
- **Rationale:** Documentation-only tasks must never alter runtime behavior, control flow, signatures, variables, or expressions. Enforcing AST invariance prevents models from inadvertently mutating logic or duplicating function statements.

### DEC-035: Mirror Subdirectory Hierarchy in Pre-Apply Backups
- **Decision:** In `src/codepilot/core/recovery.py`, `create_patch_backup()` mirrors the relative directory structure of each modified file under `.codepilot/backups/<patch_id>/` and appends an incremental collision counter.
- **Rationale:** When a patch modifies identically named files in different subdirectories (e.g., `pkg_a/service.py` and `pkg_b/service.py`), flattening the backup path causes silent file overwrites during backup and data loss during rollback. Preserving relative folders guarantees independent, uncollidable backups.

### DEC-036: Safe Staging for Live-Model AI Test Failure Auto-Repair
- **Decision:** In `src/codepilot/core/test_analyzer.py`, `analyze_test_failure()` parses failure tracebacks to construct repair prompts for live models, extracts repaired code, verifies syntax, generates unified diffs, and stages the candidate patch into `.codepilot/patches/pending_patch.json`. Fixes are never applied autonomously.
- **Rationale:** Auto-repair workflows must follow the exact same two-stage safety model and user approval gate (`codepilot apply --approved`) as standard fixes, preventing unverified code mutations from entering the working tree.

### DEC-037: Windows Subprocess Python Executable Path Quoting and Normalization
- **Decision:** In `src/codepilot/core/tester.py`, `detect_safe_test_command()` wraps `sys.executable` in double quotes when spaces are detected. `validate_test_command()` accepts quoted executable paths while strictly blocking shell injection operators. `run_project_tests()` strips outer double quotes from split argument tokens before passing to `subprocess.Popen`.
- **Rationale:** On Windows, python installations often reside in paths with spaces (`C:\Program Files\Python313\python.exe`). Passing literal outer quotes in `argv[0]` causes Windows `CreateProcessW` to fail with `FileNotFoundError`. Stripping outer quotes from tokenized arguments resolves the binary cleanly.

### DEC-038: Dynamic GGUF Context Sizing and Overflow Defense
- **Decision:** In `src/codepilot/core/models/standalone_client.py`, `_get_or_load_model()` initializes `n_ctx` to `max_context_tokens` (default 2048 or configured) rather than clamping to `2 * max_tokens`. In `generate()`, input prompt tokens are estimated; if prompt tokens exceed `n_ctx`, a clear `RuntimeError` is raised. If `prompt_tokens + max_tokens > n_ctx`, `max_tokens` is safely clamped with an informational log.
- **Rationale:** Prevents native crashes in `llama-cpp-python` when prompt lengths exceed narrow context allocations while ensuring models utilize their full allocated context window.

### DEC-039: Retain Target Symbol Metadata in Task Planning
- **Decision:** In `src/codepilot/core/planner.py`, `PlanTask` explicitly includes `target_symbol` and `target_symbol_type` attributes with backward-compatible deserialization in `PlanTask.from_dict()`. `generate_plan()` forwards these attributes directly from `InstructionComment`.
- **Rationale:** Preserves semantic symbol grounding across planning and execution, eliminating the risk of mis-targeting code modifications or falling back to error-prone line-proximity heuristics.

### DEC-040: Recursive Hierarchical AST Symbol Span Extraction
- **Decision:** In `src/codepilot/core/context/engine.py`, `_find_ast_symbol_span()` uses a recursive path-matching helper to resolve symbol spans through arbitrary nesting levels (e.g. `Outer.Inner.method`). In `SymbolResolver`, class and function hierarchies are traversed recursively to construct fully qualified names (`Outer.Inner.method`).
- **Rationale:** Deeply nested classes, namespaces, and inner methods must have their AST boundaries correctly extracted and attributed so downstream context building and docstring extraction target the exact method scope.

### DEC-041: Context Token Budgeting with System Overhead Buffer
- **Decision:** In `src/codepilot/core/context/engine.py`, `DEFAULT_SYSTEM_OVERHEAD_TOKENS = 400` is defined and incorporated into `_estimate_tokens(..., include_overhead=True)`. Context budget evaluation reserves this overhead buffer before assembling auxiliary related code context.
- **Rationale:** Prevents context window overflows at inference time by ensuring the context engine reserves adequate token budget for system instructions, instruction prompts, and wrapper delimiters.

### DEC-042: Command-Driven Intelligence and In-Code Comment Decoupling
- **Decision:** Decoupled `codepilot scan`, `analyze`, and `fix` workflows from in-code comments. `codepilot scan` discovers project structure, file types, config files, test suites, skipped files, and AST static defects; `codepilot analyze` operates independently of comments; `codepilot fix` accepts explicit target files and issue descriptions directly (`codepilot fix calc.py --issue "handle zero div"`), auto-targets single clear static findings, and displays structured investigation panels.
- **Rationale:** Requiring users to manually edit source code to insert special comments (`# AI-FIX:`) before using an AI assistant creates friction and pollutes source files. In-code comments are retained as optional task hints.

### DEC-043: Preserve Function Signatures in Fix Replacements
- **Decision:** In `generate_mock_file_fix()` and symbol target line resolution, when targeting a function definition, CodePilot targets the first statement of the function body (`node.body[0].lineno`) or preserves the `def`/`async def` header and indents replacement statements underneath rather than overwriting the header.
- **Rationale:** Overwriting a function header with replacement body statements destroys the function signature, breaks indentation of succeeding lines, and produces syntax errors. Preserving headers ensures generated fixes remain structurally and syntactically valid Python code.

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

### LESSON-001: Keep Terminal Tables Compact on 80-Column Displays
- **Symptom:** Rich tables with 6+ wide columns wrap awkwardly or truncate titles in standard 80-column terminal environments.
- **Remedy:** Keep tables to 4–5 compact columns (`Task ID`, `Type`, `Location`, `Target Symbol`, `Description`), constrain column widths, and provide `--detailed` or `--json` flags for wide inspectable fields.

### LESSON-002: Decorator Line Offsets in Python AST
- **Symptom:** In Python's AST, `FunctionDef.lineno` points to the `def` keyword, but preceding decorators (`@decorator`) appear lines above. A comment placed above `@app.route` was failing a distance threshold check against `lineno`.
- **Remedy:** In `symbol_resolver.py`, `_get_node_start_line` checks `node.decorator_list[0].lineno` first if decorators are present, properly measuring comment-to-definition distance.

### LESSON-003: Subprocess Execution Aliases on Windows
- **Symptom:** Windows execution aliases for `python.exe` point to Microsoft Store stub wrappers which fail with `Access is denied` inside non-elevated subprocesses.
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
- **Remedy:** Harden both prompt instructions (instructing the model that tags are prompt-only and must never be echoed in output) and the extraction parser (`_strip_context_delimiter_lines`) to strip standalone wrapper delimiter lines while strictly preserving legitimate code containing angle brackets.

### LESSON-010: Synergistic Prompt and Extractor Hardening for Compact LLMs
- **Symptom:** Relying solely on prompt instructions leaves edge cases if small models occasionally hallucinate tags, while relying solely on post-processing risks stripping legitimate code if regexes are too broad.
- **Remedy:** Pair an explicit negative prompt constraint ("do not echo tags") with an ultra-narrow line-level filter targeting only exact standalone wrapper delimiter lines (`r"^\s*(?:#\s*)?</?untrusted_repository_context>\s*$"`).

### LESSON-011: Small Models Are Highly Sensitive to Contradictory Directives
- **Symptom:** Small 1.5B parameter models are prone to over-reacting to competing prompt directives. When a system prompt states "Write clean code with defensive validation" while concurrently demanding minimal diffs, the model adds multiple unprompted `isinstance` checks and `assert` statements, degrading diff hygiene scores (from 100% down to 50%).
- **Remedy:** Ensure system prompt rules are strictly complementary. Explicitly instruct models to avoid unrequested defensive checks, preserve existing control flow and conditional structures, and specify that ambiguous tasks must be answered with a question only (with no code fences).

### LESSON-012: Extreme Prompt Sensitivity and Clarification Attractor Hijacking in 1.5B Models
- **Symptom:** In real-model evaluation v4 against Qwen2.5-Coder-1.5B, adding verbatim prompt guidance in Rule 4 (`your ENTIRE response must be a single concise clarification question starting with "Please clarify..." or "Could you please clarify..."`) successfully resolved `eval-07`, but triggered a catastrophic mode collapse across all other 7 cases, where the model emitted `"Please clarify..."` instead of writing code.
- **Remedy:** Compact 1.5B parameter models lack the capacity to balance complex conditional prompt logic with verbatim lexical triggers. Clarification handling must not be embedded as a high-salience lexical option in a monolithic system prompt; instead, clarification requirements should be handled via a fast pre-generation classification/routing step.

### LESSON-013: Deterministic Pre-Routing Outperforms Monolithic System Prompts for Compact LLMs
- **Symptom:** In real-model evaluation v4, introducing an explicit clarification rule into a monolithic system prompt caused the 1.5B model to collapse into emitting clarification phrases across 7 out of 8 benchmark cases, including fully specified implementation tasks.
- **Remedy:** Do not burden compact models with task classification via meta-instructions inside the prompt. Instead, perform lightweight, deterministic pre-prompt classification (`TaskRouter`), inspecting explicit task metadata, intent signals, structural completeness, and technical specificity. Dispatch a dedicated, concise code-generation prompt when ready, or a dedicated clarification prompt when requirements are missing.

### LESSON-014: Decouple Natural-Language Clarification Questions from Code Syntax Fences
- **Symptom:** In real-model evaluation v5, the model correctly asked a natural-language clarification question on an ambiguous task without code fences. However, the evaluation runner extracted code fences from the completion (which was empty) and passed the empty string to `check_syntax_validity()`, causing the question to be marked as invalid Python syntax.
- **Remedy:** Pure text responses to clarification tasks do not represent code syntax failures. When a case routes to clarification, if no code fence is generated, syntax validity defaults to valid (`True`). Only if the clarification response includes code fences should those fences be validated for syntax.

### LESSON-015: Small-Model Inline Explanatory Commenting Depresses Diff Hygiene
- **Symptom:** Small 1.5B parameter models routinely insert step-by-step explanatory comments (`# Step 1: parse input`, `# Step 2: calculate sum`) inside generated functions, reducing diff hygiene scores from 100% to 50-60%.
- **Remedy:** Explicitly forbid gratuitous inline comments and docstrings inside code generation responses unless the task explicitly requests documentation (e.g. `AI-DOCS`). Keep the prompt focused on emitting only minimal, clean, task-targeted executable code.

### LESSON-016: Preserve Executable Code Integrity During Documentation Patching
- **Symptom:** When prompted to add a docstring, local models sometimes generate the docstring and re-emit the function body. Because the engine assumed only placeholder lines (`pass`, `...`) needed replacing, the re-emitted statements were inserted above the original body, causing duplicate code and expanding statement counts.
- **Remedy:** Isolate docstrings explicitly using `extract_documentation_content()`, discard trailing code bodies, position the docstring cleanly under `def`/`class` (handling `# AI-DOCS` placed above or inside), and reject any patch that alters the normalized executable AST via `verify_executable_ast_invariance()`.

### LESSON-017: Empirical Validation of Real-Model Docstring Isolation
- **Symptom:** Small 1.5B coding models systematically re-emit the enclosing function declaration and body statements when asked to provide a docstring (emitting 514 completion tokens for a 20-line docstring).
- **Remedy:** Real-model validation proved that AST-level docstring extraction (`extract_documentation_content`) paired with structural invariance checks (`verify_executable_ast_invariance`) reliably strips trailing re-emitted statements, preserving statement counts and eliminating duplicate dead code with 100% AST equivalence.

### LESSON-018: Class-Method Scope Resolution in AST Documentation Extraction
- **Symptom:** In cross-project validation on a class method (`ScoreNormalizer.normalize_scores`), the model re-emitted the outer `class` definition along with the method. The extractor took `tree.body[0].body` (the class body), expecting an immediate docstring, rather than traversing into the nested method's body, resulting in extraction failure.
- **Remedy:** When inspecting model output for documentation tasks targeting class methods, AST traversal must inspect nested function definitions within class bodies matching `target_symbol` rather than only checking the immediate class body.

### LESSON-019: Recursive Symbol Matching for Class-Method Documentation
- **Symptom:** Small coding models predictably re-emit outer classes, enclosing method signatures, comments, and implementation statements when prompted to generate a docstring for a nested class method.
- **Remedy:** Recursive AST symbol collection (`_collect_named_nodes`) combined with qualified/suffix target resolution and parenthesis-depth tracking for multiline headers isolates exclusively the docstring with 100% precision. The pipeline produces identical executable ASTs (zero duplication) and rejects unsafe mutations.

### LESSON-020: Mirroring Subdirectory Paths in Pre-Apply Backups Prevents Collisions
- **Symptom:** Flattening backup file paths using only `rel_path.name` causes identical filenames in different subdirectories to overwrite each other inside `.codepilot/backups/<patch_id>/`, resulting in data corruption during rollback.
- **Remedy:** Mirror `rel_path.parent` inside the backup directory structure (`backup_dir / rel_path.parent / f"{rel_path.name}_{ts}.bak"`) and add an incremental collision loop, ensuring every distinct file has an isolated, uncollidable backup destination.

### LESSON-021: Non-Destructive Clarification Route Gating in Diff Generation
- **Symptom:** When a task is routed to `TaskRoute.CLARIFICATION`, the model returns a natural-language question without code blocks. Attempting code extraction threw an unhandled `FixError("No valid code block found")`, aborting preview generation.
- **Remedy:** Explicitly check `task_route == TaskRoute.CLARIFICATION` in `generate_preview_patch()`, capture the question into `PatchBundle.clarification_question`, set `status="clarification_requested"`, bypass code extraction and disk staging, and present the question cleanly via a dedicated CLI panel.

### LESSON-022: Windows CreateProcessW Fails on Outer Quotes in argv[0]
- **Symptom:** Wrapping an executable with spaces in quotes (`f'"{sys.executable}"'`) causes `shlex.split(cmd, posix=False)` to preserve literal quote characters in the token. When passed as a list to `subprocess.Popen`, Windows `CreateProcessW` treats the quote character as part of the filename itself and raises `[WinError 2] The system cannot find the file specified`.
- **Remedy:** In subprocess callers using tokenized argument lists on Windows, strip enclosing outer quotes from each element before dispatching to `subprocess.Popen`.

### LESSON-023: GGUF Context Truncation from Narrow max_tokens Clamping
- **Symptom:** Initializing `n_ctx = min(2 * max_tokens, max_context_tokens)` with `max_tokens = 512` allocated only 1024 context tokens. Large prompts with detailed context (>800 tokens) caused llama.cpp to overflow its KV cache or crash with out-of-bounds context exceptions.
- **Remedy:** Initialize `n_ctx` directly to `max_context_tokens` (default 2048), dynamically inspect prompt token load, reject requests that exceed `n_ctx`, and clamp generation tokens to the remaining headroom.

### LESSON-024: Symbol Resolution Divergence Between Comment Parsing and Docstring Extraction
- **Symptom:** When inspecting nested class methods (`class Outer: class Inner: def compute:`), `PlanTask` forwarded `target_symbol = "Outer"` because `SymbolResolver` only checked the immediate class body. Downstream, `extract_documentation_content` expected qualified method paths (`Outer.Inner.compute`), resulting in target mismatch.
- **Remedy:** Synchronize symbol resolution across all subsystems: make `SymbolResolver` traverse nested classes recursively to construct the fully qualified symbol name (`Outer.Inner.compute`), aligning it with `_find_ast_symbol_span` and `extract_documentation_content`.

### LESSON-025: Target Line Resolution Must Anchor to Function Bodies to Avoid Indentation Collisions
- **Symptom:** When targeting an entire function via `codepilot fix <file> --issue "<issue>"`, AST symbol resolution initially assigned `target_line = node.lineno` (the `def` statement header line). Substituting replacement code onto line 1 destroyed the function header, leaving the original body indented and triggering `IndentationError: unexpected indent`.
- **Remedy:** When an enclosing function symbol is detected, anchor `target_line` to the first statement of the body (`node.body[0].lineno`). In addition, guard `generate_mock_file_fix()` so that if `target_line` begins with `def ` or `async def `, it automatically indents the insertion under the header.

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
