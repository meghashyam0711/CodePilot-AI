# Stage L: Diff Hygiene, Security, and Clarification Improvements — Completion Report

**Project Root:** `D:\PROJECTS\CodePilot AI`  
**Date:** October 23, 2026  
**Target Release:** v0.4.0  
**Status:** Engineering Improvements Implemented & Regression Verified (315 / 315 Tests Passing)  
**Real-Model Inference Run:** **NONE** (Awaiting separate explicit authorization)

---

## 1. Executive Summary

Stage L addressed the remaining weak points identified during the Stage J real-model evaluation (where Diff Hygiene scored 60.7% against the 75.0% threshold, `eval-08-security-sensitive` failed its path sanitization validation assertion, and `eval-07-clarification-request` emitted speculative code instead of asking a clarification question).

Without modifying canonical evaluation fixtures, thresholds, scoring formulas, or weakening any security checks, Stage L implemented targeted architectural improvements:
1. **Diff Hygiene Prompt De-conflicting:** Eliminated prompt instructions that previously encouraged models to inject unprompted defensive validation and type checks (`isinstance`); strictly instructed preservation of existing control flow, conditional structures, and branch order.
2. **Authoritative Filename Sanitization Utility:** Implemented a robust, standalone `sanitize_filename()` utility in `src/codepilot/core/security.py` that normalizes directory separators, isolates the basename component, and iteratively strips traversal tokens (`..`). Added system prompt guidance instructing models to isolate basenames before removing traversal sequences.
3. **Clarification Output Contract Hardening:** Resolved prompt rule ambiguity so that when a task lacks essential specifications (such as ambiguous authentication requirements), the model's entire output must be a single clarification question starting with `"Please clarify..."` or `"Could you please clarify..."` with no code fences or speculative code.
4. **Full Regression Test Verification:** Added 12 deterministic unit tests covering filename sanitization, clarification accuracy, diff hygiene characteristics, and prompt rules. All 315 tests passed across 24 test modules in 13.01s.

---

## 2. Root Cause of the Diff-Hygiene Issue

Analysis of the v3 raw completions revealed three distinct causes that pulled Diff Hygiene down to 60.7%:
1. **Conflicting Prompt Instruction on Defensive Validation:**
   - *Previous Prompt:* `SYSTEM_INSTRUCTION` Rule 1 stated: `"AI-FIX: Identify root cause and fix the bug with defensive validation."`
   - *Impact:* In `eval-01-basic-function`, the model injected 4 lines of unrequested `if not isinstance(a, int) or not isinstance(b, int): raise ValueError(...)` with comments, dropping diff hygiene on that case to 50.0%.
2. **Conditional Branch Inversion:**
   - In `eval-03-missing-return`, the model inverted `if count == 0: return 0.0` into `if count > 0: return total / count; else: return 0.0`. While functionally valid, this branch rearrangement received a sequence-matcher penalty (44.4%).
3. **Code Emission on Ambiguous Request:**
   - In `eval-07-clarification-request`, the model emitted 5 lines of placeholder code instead of a 1-line question, receiving a 0.0% diff hygiene score.
4. **Repetitive String Replacements on Security Fixture:**
   - In `eval-08-security-sensitive`, the model attempted chained `.replace()` calls across the whole path rather than extracting the basename, reducing hygiene to 22.2%.

---

## 3. Security-Sensitive Behavior and Implemented Correction

### Finding on `eval-08-security-sensitive`:
The v3 model generated:
```python
def sanitize_filename(filename: str) -> str:
    filename = filename.replace('..', '')
    filename = filename.replace('/', '').replace('\\', '')
    return filename
```
When evaluated against `assert sanitize_filename('../../etc/passwd') == 'passwd'`, `'../../etc/passwd'.replace('..', '')` produced `'//etc/passwd'`, and subsequent slash removal produced `'etcpasswd'`, failing the assertion.

### Implemented Corrections:
1. **Core Utility in `src/codepilot/core/security.py`:**
   Added `sanitize_filename(filename: str) -> str` which:
   - Strips leading/trailing whitespace; returns `""` for empty or whitespace-only inputs.
   - Normalizes Windows backslashes (`\`) to Unix forward slashes (`/`).
   - Extracts the basename component (the last non-empty segment).
   - Iteratively removes residual path traversal tokens (`..`) from the basename until none remain.
   - Handles Unicode, multiple extensions, and hidden dotfiles deterministically.
2. **System Prompt Guidance (Rule 6):**
   Explicitly instructs models that when implementing path traversal defense or filename sanitization, they must isolate the basename component before removing traversal tokens (`..`), rather than naively deleting characters across the full path string.
3. **Separation from Core Boundary Checks:**
   Verified that `sanitize_filename()` is an in-memory string utility and does not replace canonical filesystem boundary verification performed by `validate_target_path()`. Core path traversal defense in `security.py` remains untouched and verified.

---

## 4. Clarification Improvements

### Finding on `eval-07-clarification-request`:
On ambiguous requests like *"Make the authentication logic better and update the user verification flow accordingly"*, the model previously emitted speculative code (`if not token: raise ValueError(...)`) because Rule 7 previously instructed models to always return Markdown code fences with code implementations.

### Implemented Corrections:
1. **Prompt Contract De-conflicting (Rule 4 & Rule 7):**
   - Rule 4 now explicitly states: *"When a task lacks essential specifications, authentication rules, verification logic, or domain requirements... your ENTIRE response must be a single concise clarification question starting with 'Please clarify...' or 'Could you please clarify...'. Do NOT emit code fences, placeholder logic, or speculative code when asking for clarification."*
   - Rule 7 explicitly distinguishes code tasks (which use Markdown code fences) from clarification requests (which return only the clarification question without code fences).
2. **Evaluator Contract:**
   - Verified that `check_clarification_accuracy(should_request=True, ...)` correctly accepts standard questions starting with `"Could you please clarify..."` and rejects speculative code implementations.

---

## 5. Files Modified

| File | Changes Made |
| :--- | :--- |
| `src/codepilot/core/security.py` | Implemented `sanitize_filename()` to safely extract basenames, normalize separators, and iteratively strip traversal tokens. |
| `src/codepilot/core/__init__.py` | Exported `sanitize_filename` in module namespace and `__all__`. |
| `src/codepilot/core/context/prompt.py` | Updated `SYSTEM_INSTRUCTION` (Rules 1, 2, 4, 6, 7) to remove defensive-validation prompts, forbid unrequested `isinstance` checks, instruct preservation of control flow, clarify ambiguous auth handling, and guide basename isolation. |
| `tests/test_phase9_security_recovery.py` | Added `TestFilenameSanitization` with 8 deterministic test cases covering path traversal, Windows separators, nested separators, empty/whitespace inputs, normal files, repeated dots, Unicode, and core boundary isolation. |
| `tests/test_context_engine.py` | Added prompt construction verification tests for diff hygiene, clarification, and basename isolation rules. |
| `tests/test_evaluation.py` | Added `TestStageLDiffHygieneAndClarificationStrengthening` testing clarification accuracy on ambiguous vs well-specified requests and diff hygiene scoring on minimal vs bloated code. |
| `README.md` | Updated test badge and regression suite count to 315 tests across 24 modules. |
| `TASK.md` | Recorded Stage L engineering completion, 315 passing tests, and updated backlog. |
| `CHANGELOG.md` | Documented Stage L additions and test suite expansion. |
| `MEMORY.md` | Recorded architectural decision DEC-030 (Diff Hygiene, Security, and Clarification Hardening). |

---

## 6. New Tests Added (12 New Tests)

1. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_traversal_paths`
2. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_windows_separators`
3. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_nested_separators`
4. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_empty_and_whitespace`
5. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_normal_files`
6. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_repeated_dots_and_traversal`
7. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_sanitize_filename_unicode_and_extensions`
8. `tests/test_phase9_security_recovery.py::TestFilenameSanitization::test_core_boundary_checks_remain_unaffected`
9. `tests/test_context_engine.py::TestPromptConstruction::test_system_prompt_diff_hygiene_and_clarification_rules`
10. `tests/test_evaluation.py::TestStageLDiffHygieneAndClarificationStrengthening::test_clarification_accuracy_rejects_speculative_code_on_ambiguous_request`
11. `tests/test_evaluation.py::TestStageLDiffHygieneAndClarificationStrengthening::test_clarification_accuracy_accepts_proper_question`
12. `tests/test_evaluation.py::TestStageLDiffHygieneAndClarificationStrengthening::test_diff_hygiene_minimal_implementation_scores_higher_than_defensive_checks`

---

## 7. Exact Test Commands and Actual Results

### A. Security & Recovery Module
```bash
python -m pytest tests/test_phase9_security_recovery.py -v
```
**Result:** **28 passed** in 2.84s (Exit Code: 0).

### B. Context Engine Module
```bash
python -m pytest tests/test_context_engine.py -v
```
**Result:** **18 passed** in 0.29s (Exit Code: 0).

### C. Evaluation Module
```bash
python -m pytest tests/test_evaluation.py -v
```
**Result:** **54 passed** in 0.10s (Exit Code: 0).

### D. Full Project Regression Suite
```bash
python -m pytest -q
```
**Result:** **315 passed out of 315 tests** across 24 modules in 13.01s (Exit Code: 0).

---

## 8. Remaining Risks and Known Limitations

1. **Stochastic Nature of Compact LLMs:** Prompt engineering significantly steers 1.5B parameter models toward minimal diffs and clarification questions, but small models without dedicated instruction fine-tuning may still occasionally produce extraneous comments or speculative code on certain prompts.
2. **Benchmark Fixtures Unchanged:** The canonical benchmark fixtures and thresholds ($\ge 75\%$ Diff Hygiene, $\ge 85\%$ Instruction Following, $\ge 95\%$ Syntax Validity, $\ge 80\%$ Clarification, $\ge 70\%$ Test Pass Rate) remain strict and unmodified.
3. **In-Memory Validation Environment:** `run_validation_test_safely()` executes code in a restricted in-memory namespace (forbidding `open`, `__import__`, `eval`, `exec`), which serves as defense-in-depth rather than an OS-level sandbox.

---

## 9. Historical Reports and Baselines Preserved

All six historical evaluation reports remain completely intact:
- `reports/evaluation_qwen_1.5b_cpu.md` (1,856 bytes)
- `reports/evaluation_qwen_1.5b_cpu.json` (12,685 bytes)
- `reports/evaluation_qwen_1.5b_cpu_v2.md` (1,878 bytes)
- `reports/evaluation_qwen_1.5b_cpu_v2.json` (7,436 bytes)
- `reports/evaluation_qwen_1.5b_cpu_v3.md` (3,903 bytes)
- `reports/evaluation_qwen_1.5b_cpu_v3.json` (8,776 bytes)
- `reports/release_readiness_audit_v0.4.0.md` (17,607 bytes)

No report was modified, deleted, or overwritten.

---

## 10. Real-Model Inference Status

> **Explicit Confirmation:** No real-model inference, evaluation benchmarks, or package installations were run during Stage L. All improvements were verified strictly through deterministic offline unit and regression tests.

---

## 11. Benchmark Impact Notice

> **Important Notice:** The impact of these engineering changes on the real Qwen2.5-Coder-1.5B model's benchmark scores remains **unverified** until a separately authorized evaluation run (e.g. Stage M / v4) is explicitly approved by the user. Unit test passes alone do not establish benchmark acceptance.
