# Stage U: Second-File Cross-Project Documentation Validation Report

**Project:** CodePilot AI v0.4.0  
**Project Root:** `D:\PROJECTS\CodePilot AI`  
**Date:** October 23, 2026  
**Evaluator:** Standalone Local GGUF Engine (`StandaloneGGUFClient`)  
**Target File:** `S:\VOICE\SRISHTI_SHIKSHA\ai_engine\doc_retrieval\ranking\score_normalizer.py`  
**Final Verdict:** **PARTIAL** (Safety protections, AST invariance, unsafe patch rejection, rollback, and test suite succeeded; real-model documentation generation blocked by class-method extractor limitation)

---

## 1. Executive Summary

In Stage R and Stage T, CodePilot AI validated `# AI-DOCS` patch generation and AST safety against `grade_normalizer.py`, a module-level standalone function. Stage T achieved a **PASS** verdict by demonstrating that the Stage S AST-based documentation extractor isolated pure docstrings and discarded re-emitted function statements without executable body duplication.

Stage U was commissioned as an empirical validation experiment to test the Stage S/T pipeline against a second Python file with a **structurally distinct pattern**: an object-oriented class method (`ScoreNormalizer.normalize_scores`) with 2-level indentation and multiple control flow branches.

During the controlled validation experiment:
1. **Preflight and Isolation:** `score_normalizer.py` was inspected read-only and copied to an isolated directory `temp_stage_u_experiment/`. The external project `S:\VOICE\SRISHTI_SHIKSHA` was not modified.
2. **Real-Model Generation:** Exactly one real-model inference run was executed on CPU using the local `Qwen2.5-Coder-1.5B-Instruct` GGUF model.
3. **Model Output & Extractor Result:** The model emitted code starting with the outer class declaration (`class ScoreNormalizer:`) and method header (`def normalize_scores(...)`). 
4. **Failure Diagnosis:** CodePilot's Stage S extractor (`extract_documentation_content`) evaluated `len(tree.body) == 1` and extracted `tree.body[0].body`. Because `tree.body[0]` was `ClassDef("ScoreNormalizer")`, its immediate body was `[FunctionDef("normalize_scores")]` rather than an `ast.Expr` docstring. As a result, the extractor failed to descend into the method body, returning `(False, "", False)` and raising `FixError`.
5. **Safety Invariance & Rejection:** The safety pipeline prevented any corrupted or empty patch from touching the filesystem. Deterministic unit testing on `score_normalizer.py` verified that `apply_approved_patch` strictly rejects executable AST alterations with `PatchError`, and atomic rollback restores the file byte-for-byte.
6. **Regression Tests:** All 26 focused tests (`tests/test_fix.py`) and all 361 tests across the full regression suite passed with zero failures.
7. **Constraint Compliance:** In accordance with the constraint *"Do not repeat real-model generation if the first attempt fails; report the failure and stop"*, no secondary inference was attempted.

---

## 2. File Selection and Rationale

| Attribute | Value |
|---|---|
| **External Repository** | `SRISHTI_SHIKSHA` AI Engine (`S:\VOICE\SRISHTI_SHIKSHA\ai_engine`) |
| **Selected File** | `doc_retrieval/ranking/score_normalizer.py` |
| **Structure** | Class `ScoreNormalizer` with instance method `normalize_scores(self, results, score_key)` |
| **Size & Lines** | 55 lines, 1,084 bytes |
| **Indentation Depth** | 2 levels (4 spaces class, 8 spaces method body) |
| **Control Flow** | 3 branches: empty input guard (`if not results`), zero-variance guard (`if max_score == min_score`), and min-max normalization scaling loop |
| **Dependencies** | Pure Python; zero external imports; deterministic dictionary transformations |
| **Selection Rationale** | Structurally distinct from Stage R/T: tests class method scoping, 8-space indentation, and nested AST bodies. |

### Source Code of Selected File (`score_normalizer.py`):
```python
# ============================================================
# SCORE NORMALIZER
# ============================================================

class ScoreNormalizer:

    def normalize_scores(
        self,
        results,
        score_key,
    ):

        if not results:
            return results

        scores = [
            result.get(
                score_key,
                0,
            )
            for result in results
        ]

        max_score = max(scores)

        min_score = min(scores)

        if max_score == min_score:

            for result in results:

                result[
                    f"{score_key}_normalized"
                ] = 1.0

            return results

        for result in results:

            value = result.get(
                score_key,
                0,
            )

            normalized = (
                value - min_score
            ) / (
                max_score - min_score
            )

            result[
                f"{score_key}_normalized"
            ] = normalized

        return results
```

---

## 3. Preflight Results

All preflight checks completed successfully prior to executing inference:
- **Target Report:** `reports/stage_u_second_file_validation.md` did not exist.
- **Model File:** `models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` verified present (1,117,320,768 bytes).
- **Inference Runtime:** `llama_cpp 0.3.36` loaded successfully in the Python environment.
- **Client Health:** `StandaloneGGUFClient` reported healthy: `Standalone GGUF runtime ready with 'qwen2.5-coder-1.5b-instruct-q4_k_m.gguf' (1065.6 MB)`.
- **Source File Verification:** Read-only verification of `S:\VOICE\SRISHTI_SHIKSHA\ai_engine\doc_retrieval\ranking\score_normalizer.py`.
- **Working Copy Isolation:** Created `temp_stage_u_experiment/` with reference copy and working copy containing target instruction.
- **External Repository Preservation:** Original file in `S:\VOICE\SRISHTI_SHIKSHA` was not modified.

---

## 4. Model Identity and Inference Configuration

- **Model:** `Qwen2.5-Coder-1.5B-Instruct` (Quantized GGUF Q4_K_M, 1,117,320,768 bytes)
- **Backend:** `StandaloneGGUFClient` via `llama-cpp-python` (0.3.36)
- **Execution Mode:** CPU-only (`n_gpu_layers=0`, `n_threads=16`)
- **Context Length Budget:** 2048 tokens
- **Temperature:** 0.1
- **Max Generation Tokens:** 1024
- **Single-Attempt Rule:** Exactly one generation executed.

---

## 5. Model Generation and Failure Root Cause Analysis

### 5.1 Raw Model Output Prefix
During execution of `generate_preview_patch`, the model emitted a completion containing:
```python
class ScoreNormalizer:
    def normalize_scores(
        self,
        results,
        score_key,
    ):
        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score n...
```

### 5.2 Exception Encountered
```
codepilot.exceptions.FixError: Model output for AI-DOCS task 'TASK-001' in 'score_normalizer.py' does not contain a valid docstring or comments: 'class ScoreNormalizer:\n    def normalize_scores(\n        self,\n        results,\n        score_key,\n    ):\n        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score n'
```

### 5.3 Root Cause Analysis in `extract_documentation_content`
Code inspection in `src/codepilot/core/fixer.py` (lines 237–269) revealed an architectural limitation in how Stage S handled nested class methods:

```python
    else:
        if len(tree.body) == 1 and isinstance(
            tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            target_body = tree.body[0].body
        elif target_symbol:
            for b in tree.body:
                if (
                    isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and b.name == target_symbol
                ):
                    target_body = b.body
                    break
        if target_body is None:
            target_body = tree.body

    if not target_body:
        return False, "", False

    first_stmt = target_body[0]
    if (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    ):
        ...
        return True, extracted_doc, True

    return False, "", False
```

#### Why it worked for `grade_normalizer.py` (Stage T):
1. `grade_normalizer.py` was a top-level function (`def normalize_grade`).
2. `tree.body[0]` was `FunctionDef("normalize_grade")`.
3. `target_body = tree.body[0].body` was the function's statements.
4. `first_stmt = target_body[0]` was the docstring `ast.Expr`.
5. Extraction succeeded.

#### Why it failed for `score_normalizer.py` (Stage U):
1. The model re-emitted the enclosing class: `class ScoreNormalizer:`.
2. `tree.body[0]` was `ClassDef("ScoreNormalizer")`.
3. `len(tree.body) == 1` matched `ClassDef`, assigning `target_body = tree.body[0].body`.
4. `target_body` was the *class* body: `[FunctionDef("normalize_scores")]`.
5. `first_stmt = target_body[0]` was `FunctionDef`, **not** `ast.Expr` (since the class itself has no docstring).
6. The `elif target_symbol:` branch was never entered because `len(tree.body) == 1` matched first. Even if entered, `for b in tree.body:` only inspected top-level nodes in `tree.body`, not inner methods of `ClassDef`.
7. `extract_documentation_content` returned `(False, "", False)`.
8. `generate_preview_patch` correctly halted with `FixError`, preventing an unverified patch.

---

## 6. Safety Verification: Rejection and Rollback

Although live real-model patch application was halted by the extractor, CodePilot's Stage S safety and recovery mechanisms were independently validated on `score_normalizer.py`:

### 6.1 Unsafe Patch Rejection Test
A deliberately constructed unsafe patch modified the executable logic of `score_normalizer.py` (changing `1.0` to `2.0` in the zero-variance branch):
- **AST Invariance Check:** `verify_executable_ast_invariance(orig, mutated)` returned `False` (`Executable AST differs between original and proposed code.`).
- **Patcher Behavior:** `apply_approved_patch` caught the violation before touching disk.
- **Exception:** `PatchError: Structural validation failed for 'score_normalizer.py': patch violates executable AST invariance: Executable AST differs between original and proposed code.`
- **Filesystem Integrity:** `target_file.read_bytes() == original_bytes` (Verified untouched).

### 6.2 Rollback Verification
A valid docstring patch was applied to `score_normalizer.py`, and `rollback_patch` was invoked:
- **Pre-Apply Backup:** Verified snapshot created in `.codepilot/backups/`.
- **Rollback Invocation:** `rollback_patch(temp_dir, patch_id="patch-safe-stage-u")`.
- **Restoration Status:** Restored `['score_normalizer.py']`.
- **Byte-for-Byte Check:** `post_rollback_bytes == original_bytes` (**True**, 1,303 bytes matched identically).

---

## 7. Regression Test Results

Both focused and full test suites were executed using the configured Python environment:

### 7.1 Focused Tests
```powershell
python -m pytest -o pythonpath=src tests/test_fix.py
```
- **Result:** **26 passed in 0.48s** (100% pass rate)

### 7.2 Full Regression Suite
```powershell
python -m pytest -o pythonpath=src tests/
```
- **Result:** **361 passed in 10.19s** across 25 modules (0 failures, 0 regressions)

---

## 8. Limitations Identified

1. **Class-Method Scope Resolution in AST Extractor:** `extract_documentation_content()` assumes either a top-level function, a class-level docstring, or a flat list of statements. When a 1.5B model wraps a method inside its enclosing `class` definition, the extractor checks only the class-level body and fails to locate the method-level docstring.
2. **Model Re-emission Tendency:** The Qwen2.5-Coder-1.5B model exhibits a strong tendency to emit enclosing class definitions when instructed to document indented methods, rather than outputting solely the docstring or solely the method.

---

## 9. Final Acceptance Verdict

| Criterion | Requirement | Result | Evidence |
|---|---|:---:|---|
| Preflight Verification | Model file, runtime, and external file verified | **PASS** | Section 3 |
| Single Real-Model Attempt | Exactly one run on CPU; no retries | **PASS** | Section 4 |
| Filesystem Safety | External project unmodified; bad patch halted | **PASS** | Section 1 & 5 |
| Unsafe Patch Rejection | Rejects executable AST mutation with `PatchError` | **PASS** | Section 6.1 |
| Byte-for-Byte Rollback | Rollback restores original pre-patch file | **PASS** | Section 6.2 |
| Full Regression Suite | 361/361 tests pass cleanly | **PASS** | Section 7 |
| End-to-End Real Model Docs | Docstring extracted and applied from real model | **UNVERIFIED** | Section 5 (Extractor class-method limitation) |

**Overall Verdict: PARTIAL**

*In accordance with the constraints of Stage U, execution stopped after this single controlled experiment to report findings for review.*
