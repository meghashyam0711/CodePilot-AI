# Stage W: Real-Model Class-Method Revalidation Report

**Project:** CodePilot AI v0.4.0  
**Project Root:** `D:\PROJECTS\CodePilot AI`  
**Date:** October 23, 2026  
**Evaluator:** Standalone Local GGUF Engine (`StandaloneGGUFClient`)  
**Target File:** `S:\VOICE\SRISHTI_SHIKSHA\ai_engine\doc_retrieval\ranking\score_normalizer.py` (isolated copy in `temp_stage_w_experiment/`)  
**Target Class & Method:** `ScoreNormalizer.normalize_scores(self, results, score_key)`  
**Inference Engine:** `Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf` on CPU via `llama_cpp 0.3.36`  
**Automated Tests:** **369 / 369 Tests Passing** (100% pass rate across 25 modules in 11.20s)  
**Focused AI-DOCS Tests:** **34 / 34 Tests Passing** (100% pass rate in 0.64s)  
**Final Verdict:** **PASS** (100% compliant with all safety, extraction, AST invariance, rollback, and regression requirements)

---

## 1. Executive Summary

In Stage U (`reports/stage_u_second_file_validation.md`), CodePilot AI evaluated documentation extraction on a class method (`ScoreNormalizer.normalize_scores` from `SRISHTI_SHIKSHA`). The local Qwen 1.5B model re-emitted the outer `class ScoreNormalizer:` definition and method signature. The Stage S extractor evaluated only top-level nodes, failed to descend into the class body, and aborted with `FixError`, resulting in a **PARTIAL** verdict.

In Stage V (`reports/stage_v_class_method_extraction_fix.md`), CodePilot implemented recursive hierarchical AST symbol collection (`_collect_named_nodes`) and qualified/suffix target resolution, backed by 8 new deterministic unit tests.

**Stage W** executed a single, controlled real-model validation experiment using the actual local `Qwen2.5-Coder-1.5B-Instruct` GGUF model on CPU to revalidate class-method documentation extraction on `score_normalizer.py`.

### Key Outcomes:
1. **Successful Real-Model Generation:** The local Qwen 1.5B model generated a completion in 8.85s (1,254 prompt tokens, 206 completion tokens). The model re-emitted `class ScoreNormalizer:` and `def normalize_scores(...)`, followed by the docstring and the method body.
2. **Deterministic Extraction:** CodePilot's Stage V extractor successfully resolved `ScoreNormalizer.normalize_scores`, navigated into the nested method body, extracted exclusively the docstring, and discarded the re-emitted class definition, method signature, and executable statements.
3. **AST Invariance Confirmed:** The executable AST before and after patching was confirmed to be 100% invariant (`is_invariant=True`). Statement counts inside `normalize_scores` remained identical (7 original executable statements vs 7 proposed executable statements, with **zero duplication**).
4. **Clean Unified Diff:** The generated patch cleanly removed the `# AI-DOCS` comment and inserted the docstring at 8-space indentation below the multiline method signature.
5. **Pre-Write Rejection & Rollback:** A deliberately mutated candidate patch was rejected with `PatchError` before disk write. The valid patch was applied, verified against disk, and rolled back with byte-for-byte fidelity.
6. **Isolated Behavioral Equivalence:** Evaluated across 8 representative input test cases (empty results, uniform scores, min-max scaling, missing keys, floating scores), the patched code produced outputs 100% identical to the reference implementation.
7. **Regression Stability:** All 34 focused fixer tests and all 369 full-suite regression tests passed with zero failures.

---

## 2. Preflight and Environment Verification

All preflight checks executed cleanly prior to real-model inference:

| Check | Item | Value / Status | Verification Result |
|---|---|---|:---:|
| **Target Report Path** | `reports/stage_w_real_model_class_method_validation.md` | Non-existent | **PASSED** |
| **Model Weights File** | `models/qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` | 1,117,320,768 bytes | **PASSED** |
| **Inference Runtime** | `llama_cpp` | v0.3.36 | **PASSED** |
| **Client Health Check** | `StandaloneGGUFClient` | CPU runtime ready (1065.6 MB) | **PASSED** |
| **External Source File** | `S:\VOICE\SRISHTI_SHIKSHA\ai_engine\doc_retrieval\ranking\score_normalizer.py` | 1,138 bytes (read-only) | **PASSED** |
| **External Safety Guard** | External repository write access | Read-only; untouched | **PASSED** |
| **Isolated Working Directory** | `D:\PROJECTS\CodePilot AI\temp_stage_w_experiment` | Created fresh | **PASSED** |

---

## 3. Real-Model Inference Experiment

### 3.1 Model Configuration
- **Model:** `Qwen2.5-Coder-1.5B-Instruct-Q4_K_M.gguf`
- **Provider:** Standalone GGUF (`StandaloneGGUFClient`)
- **Hardware Mode:** CPU inference (`n_gpu_layers=0`)
- **Context Limit:** 2,048 tokens
- **Inference Latency:** 8.85 seconds
- **Prompt Tokens:** 1,254
- **Completion Tokens:** 206
- **Raw Completion Size:** 1,088 characters

### 3.2 Target Instruction
Placed inside `normalize_scores` at indentation level 2 (8 spaces):
```python
        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score normalization, empty result handling, and dictionary score key updates
```

### 3.3 Raw Model Completion
```python
### Proposed Code Modification
```python
class ScoreNormalizer:
    def normalize_scores(
        self,
        results,
        score_key,
    ):
        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score normalization, empty result handling, and dictionary score key updates
        """
        Normalize the scores in the given results dictionary.

        Args:
            results (dict): A dictionary containing the results to be normalized.
            score_key (str): The key in the results dictionary that contains the scores to be normalized.

        Returns:
            dict: The normalized results dictionary.
        """
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
                result[f"{score_key}_normalized"] = 1.0
            return results
```

### 3.4 Extracted Documentation Content
The Stage V `extract_documentation_content` engine cleanly resolved the method scope, isolated the docstring, and pruned all enclosing classes, signatures, and duplicate statements:
```python
"""
Normalize the scores in the given results dictionary.

Args:
    results (dict): A dictionary containing the results to be normalized.
    score_key (str): The key in the results dictionary that contains the scores to be normalized.

Returns:
    dict: The normalized results dictionary.
"""
```

### 3.5 Generated Unified Diff
```diff
--- a/score_normalizer.py
+++ b/score_normalizer.py
@@ -9,7 +9,16 @@
         results,
         score_key,
     ):
-        # AI-DOCS: Add comprehensive docstring for normalize_scores explaining min-max score normalization, empty result handling, and dictionary score key updates
+        """
+        Normalize the scores in the given results dictionary.
+
+        Args:
+            results (dict): A dictionary containing the results to be normalized.
+            score_key (str): The key in the results dictionary that contains the scores to be normalized.
+
+        Returns:
+            dict: The normalized results dictionary.
+        """
 
         if not results:
             return results
```

---

## 4. Safety Validation & AST Invariance

| Safety Check | Empirical Observation | Status |
|---|---|:---:|
| **Target Method Resolution** | Correctly resolved `normalize_scores` inside `ScoreNormalizer` class scope | **PASSED** |
| **Python Syntax Validity** | `ast.parse()` succeeded on both original and proposed files | **PASSED** |
| **Executable AST Invariance** | `verify_executable_ast_invariance()` confirmed identical executable AST | **PASSED** |
| **Executable Statement Count** | Original: 7 statements, Proposed: 7 statements (Zero duplication) | **PASSED** |
| **Method Signature Invariance** | `def normalize_scores(self, results, score_key)` fully preserved | **PASSED** |
| **Diff Content Safety** | Diff contains strictly documentation lines; zero executable statements added | **PASSED** |
| **Unsafe Patch Rejection** | Deliberately mutated logic patch rejected with `PatchError` before write | **PASSED** |
| **Pre-Apply Snapshot** | Backup snapshot created in `.codepilot/backups/patch-01809605a44b` | **PASSED** |
| **Byte-for-Byte Rollback** | `rollback_patch()` restored exact byte-for-byte original file state | **PASSED** |
| **External Repository Safety** | `S:\VOICE\SRISHTI_SHIKSHA` untouched | **PASSED** |

---

## 5. Isolated Behavioral Validation

Both the reference file and the patched file were executed in isolated namespaces against 8 representative test cases:

| Case ID | Input Description | Expected Behavior | Patched Behavior | Match |
|---|---|---|---|:---:|
| `empty_list` | `results = []` | `[]` | `[]` | **TRUE** |
| `single_item` | `[{'score': 10}]` | Uniform 1.0 normalization | Uniform 1.0 normalization | **TRUE** |
| `equal_scores` | `[{'score': 5}, {'score': 5}]` | Uniform 1.0 normalization | Uniform 1.0 normalization | **TRUE** |
| `distinct_scores` | `[{'score': 0}, {'score': 50}, {'score': 100}]` | Min-max scaled (0.0, 0.5, 1.0) | Min-max scaled (0.0, 0.5, 1.0) | **TRUE** |
| `negative_scores` | `[{'score': -10}, {'score': 0}, {'score': 10}]` | Min-max scaled (0.0, 0.5, 1.0) | Min-max scaled (0.0, 0.5, 1.0) | **TRUE** |
| `missing_key_fallback` | `[{'score': 10}, {}]` (defaults to 0) | Missing key treated as 0 | Missing key treated as 0 | **TRUE** |
| `floating_scores` | `[{'score': 1.5}, {'score': 3.0}, {'score': 4.5}]` | Min-max scaled (0.0, 0.5, 1.0) | Min-max scaled (0.0, 0.5, 1.0) | **TRUE** |
| `custom_key` | `[{'val': 20}, {'val': 40}]` | Custom key normalized | Custom key normalized | **TRUE** |

**Summary:** 8 / 8 test cases produced 100% identical outputs.

---

## 6. Regression Test Suite Execution

### 6.1 Focused AI-DOCS Test Suite
```powershell
python -m pytest -o pythonpath=src tests/test_fix.py
```
```
============================= test session starts =============================
platform win32 -- Python 3.13.12, pytest-9.1.1, pluggy-1.6.0
collected 34 items

tests/test_fix.py ..................................                     [100%]

============================= 34 passed in 0.64s ==============================
```

### 6.2 Full Regression Test Suite
```powershell
python -m pytest -o pythonpath=src tests/
```
```
============================= test session starts =============================
platform win32 -- Python 3.13.12, pytest-9.1.1, pluggy-1.6.0
collected 369 items

tests/test_analyzer.py ................                                  [  4%]
tests/test_app.py ....................                                   [  9%]
tests/test_apply.py .............                                        [ 13%]
tests/test_cli.py .................                                      [ 17%]
tests/test_cli_evaluate.py ....                                          [ 18%]
tests/test_config.py ............                                        [ 22%]
tests/test_context_engine.py .........                                   [ 24%]
tests/test_dataset.py ................                                   [ 28%]
tests/test_doctor.py ...........                                         [ 31%]
tests/test_evaluation.py ........................                        [ 38%]
tests/test_fix.py ..................................                     [ 47%]
tests/test_models.py ......................                              [ 53%]
tests/test_ownership_root.py ............                                [ 56%]
tests/test_parser.py .......................                             [ 62%]
tests/test_phase10_integration.py ............                           [ 66%]
tests/test_phase11_gguf_integration.py .........                         [ 68%]
tests/test_phase9_security_recovery.py ................                  [ 72%]
tests/test_predict.py ...........                                        [ 75%]
tests/test_recovery.py ..........                                        [ 78%]
tests/test_scan.py .................                                     [ 83%]
tests/test_status.py ............                                        [ 86%]
tests/test_task_router.py ............................                   [ 94%]
tests/test_tasks.py ............                                         [ 97%]
tests/test_test.py ...........                                           [100%]

============================ 369 passed in 11.20s =============================
```

---

## 7. Comparative Assessment (Stage U vs Stage W)

| Evaluation Dimension | Stage U (Before Fix) | Stage W (After Fix) | Delta / Improvement |
|---|---|---|---|
| **Real Model Execution** | Completed (CPU, 10.45s) | Completed (CPU, 8.85s) | Identical real GGUF model |
| **Model Completion Pattern** | Emitted outer `class` + method + body | Emitted outer `class` + method + body | Consistent 1.5B model behavior |
| **Documentation Extractor** | Failed (`(False, "", False)`) | Succeeded (`valid=True, is_doc=True`) | **FIXED** (Hierarchical AST traversal) |
| **Patch Generation** | Aborted with `FixError` | Generated clean unified diff | **FIXED** |
| **Statement Duplication** | N/A (no patch generated) | 0 duplicate statements (7 original vs 7 proposed) | **VERIFIED** |
| **AST Invariance** | Not tested on generated patch | 100% verified (`is_invariant=True`) | **VERIFIED** |
| **Unsafe Patch Rejection** | Verified | Verified with `PatchError` | Maintained |
| **Byte-for-Byte Rollback** | Verified | Verified | Maintained |
| **Behavioral Tests** | 8 / 8 matched | 8 / 8 matched | 100% parity |
| **Verdict** | **PARTIAL** | **PASS** | **RESOLVED** |

---

## 8. Final Verdict

**Verdict: PASS**

The Stage V class-method extraction fix has been empirically validated against real inference from the local `Qwen2.5-Coder-1.5B-Instruct` GGUF model. CodePilot AI safely extracts docstrings from completions containing nested classes and methods, completely discards re-emitted executable logic, enforces strict AST executable invariance, rejects unsafe modifications, guarantees byte-for-byte rollback, and maintains 100% test pass rates across the full regression suite.
