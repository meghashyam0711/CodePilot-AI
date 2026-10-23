# Stage O — Controlled Real-Model Evaluation Review

**Project:** CodePilot AI v0.4.0  
**Evaluation Date:** October 23, 2026  
**Execution Runtime:** CPU-only (`llama-cpp-python` 0.3.36, `n_gpu_layers=0`)  
**Python Environment:** Python 3.13.12 (`D:\PROJECTS\CodePilot AI\.venv\Scripts\python.exe`)  
**Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` (1,065.56 MB)  
**Historical Reports Preserved:** All historical baselines (`v1`, `v2`, `v3`, `v4`, `release_readiness_audit_v0.4.0.md`, `stage_l_engineering_completion.md`, `stage_n_task_router_completion.md`) remain untouched and immutable.  

---

## 1. Executive Summary & Core Findings

Stage O executed a single, controlled real-model evaluation across all eight canonical benchmark cases to measure whether Stage N's `TaskRouter` and prompt separation resolved the catastrophic clarification mode collapse observed during Stage M v4.

### Key Conclusions:
1. **Clarification Mode Collapse is 100% Resolved:**
   - In Stage M v4, the compact 1.5B model collapsed onto the verbatim prompt trigger `"Please clarify..."` across 7 out of 8 benchmark cases, generating 3-token clarification responses for standard coding tasks and scoring 0.0% on validation tests and diff hygiene.
   - In Stage O v5, **zero coding tasks emitted `"Please clarify..."`**. Every single coding task (`eval-01` through `eval-06` and `eval-08`) was routed to `CODE_GENERATION` and received a pure, code-first system prompt devoid of clarification anchors.
   - The model generated full Python implementations in standard markdown code fences for all coding tasks.
2. **Benchmark Metric Recovery:**
   - **Validation Test Pass Rate:** Recovered from **0.0% to 85.7%** (+85.7 pp vs v4, matching the v3 reference baseline).
   - **Instruction Following Rate:** Recovered from **12.5% to 87.5%** (+75.0 pp vs v4, matching the v3 reference baseline).
   - **Clarification Accuracy:** Measured at **87.5%** (+75.0 pp vs v4, matching v3).
   - **Diff Hygiene Score:** Recovered from **0.0% to 60.0%** (+60.0 pp vs v4, closely tracking v3 at 60.7%).
   - **Syntax Validity:** Reached **87.5%** (+75.0 pp vs v4).
3. **Release Readiness Classification:**
   - Following Section 9 criteria, the evaluation outcome is **Improved but still limited**:
     - Clarification mode collapse is no longer observed on coding tasks.
     - Outputs are task-relevant across all categories.
     - Two metrics remain below canonical thresholds: Syntax Validity (87.5% vs $\ge 95.0\%$, impacted by `eval-07` metric string-marker accounting) and Diff Hygiene (60.0% vs $\ge 75.0\%$, reflecting the known structural verbosity of the 1.5B parameter base model).

---

## 2. Benchmark Scorecard & Historical Progression

All five canonical thresholds were evaluated using the unchanged scoring definitions:

| Metric | Target Threshold | Baseline (v1) | Stage H (v2) | Stage J (v3 Ref) | Stage M (v4) | Stage O (v5) | Status |
|---|---|---|---|---|---|---|---|
| **Instruction Following** | $\ge 85.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | **87.5%** | **PASS** |
| **Syntax Validity** | $\ge 95.0\%$ | 100.0% | 37.5% | 100.0% | 12.5% | **87.5%** | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | $\ge 75.0\%$ | 31.9% | 56.0% | 60.7% | 0.0% | **60.0%** | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | $\ge 80.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | **87.5%** | **PASS** |
| **Validation Test Pass Rate** | $\ge 70.0\%$ | 57.1% | 14.3% | 85.7% | 0.0% | **85.7%** | **PASS** |

### Deltas:
- **$\Delta$ (v5 vs v4):** Instruction Following **+75.0 pp**, Syntax Validity **+75.0 pp**, Diff Hygiene **+60.0 pp**, Clarification Accuracy **+75.0 pp**, Validation Test Pass Rate **+85.7 pp**.
- **$\Delta$ (v5 vs v3):** Instruction Following **0.0 pp**, Syntax Validity **-12.5 pp**, Diff Hygiene **-0.7 pp**, Clarification Accuracy **0.0 pp**, Validation Test Pass Rate **0.0 pp**.

---

## 3. Detailed Case-by-Case Analysis

| Case ID | Type | Routed To | Model Output Summary | Test Pass | Diff Hygiene | Notes |
|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | `CODE_GENERATION` | `def add(a: int, b: int) -> int: return a + b` | PASS | 100.0% | Clean, minimal implementation; identical to reference. |
| `eval-02-bug-fixing` | `AI-FIX` | `CODE_GENERATION` | `def scan_prices(prices: list) -> int: ...` | PASS | 100.0% | Correctly repaired negative price index scanner. |
| `eval-03-missing-return` | `AI-FIX` | `CODE_GENERATION` | `def compute_average(total: float, count: int) -> float: ...` | PASS | 50.0% | Repaired missing return; added zero guard. |
| `eval-04-boundary-conditions` | `AI-FIX` | `CODE_GENERATION` | `def get_first_item(items: list): ...` | PASS | 100.0% | Guarded empty list check and returned first element. |
| `eval-05-existing-body-modification` | `AI-TASK` | `CODE_GENERATION` | `def process_number(val: int) -> int: return val * 2 if val > 0 else 0` | PASS | 33.3% | Correct functional logic; ternary expression variation. |
| `eval-06-malformed-empty-output` | `AI-TASK` | `CODE_GENERATION` | `def parse_integer_safe(val: str, default: int = 0) -> int: ...` | PASS | 80.0% | Implemented try/except ValueError with default fallback. |
| `eval-07-clarification-request` | `AI-TASK` | `CLARIFICATION` | `"What specific criteria or behavior is required for the authentication logic to be considered better and the user verification flow to be updated?"` | N/A | 0.0% | Correctly routed; generated focused question without code fences. Scored False due to metric keyword matching (`clarif`, `please specify`). |
| `eval-08-security-sensitive` | `AI-TASK` | `CODE_GENERATION` | `def sanitize_filename(filename: str) -> str: ...` | FAIL | 16.7% | Routed to code gen; implemented traversal stripping; failed Windows backslash traversal in test. |

### Diagnostic Breakdown:

1. **Cases eval-01 through eval-06 (Standard Tasks):**
   - In Stage M v4, every one of these cases emitted `"Please clarify..."`.
   - In Stage O v5, all six cases generated working Python code and **passed 100% of their validation unit tests**.
   - Diff hygiene on standard tasks averaged **77.2%**, with three cases achieving a perfect 100.0%.

2. **Case eval-07 (Clarification Handling):**
   - The router successfully identified this task as missing essential specifications and routed it to `CLARIFICATION`.
   - The model received `CLARIFICATION_SYSTEM_INSTRUCTION` and responded with:
     > *"What specific criteria or behavior is required for the authentication logic to be considered better and the user verification flow to be updated?"*
   - The model followed all structural rules: it did not generate code fences, speculative code, or hallucinated implementations.
   - However, `check_clarification_accuracy()` in `metrics.py` checks for specific lexical substrings (`"clarif"`, `"could you specify"`, `"please specify"`, `"what should happen"`, etc.). Because the model phrased its question with `"What specific criteria or behavior is required..."`, the metric scored `clarification_correct = False`.
   - Under `runner.py`'s scoring logic for clarification tasks, `syntax_valid` and `instruction_followed` cascade from `clarif_ok`. Thus, this single string-matching discrepancy registered as 0% for syntax validity and instruction following on `eval-07`, pulling overall Syntax Validity down to 87.5%.

3. **Case eval-08 (Security-Sensitive Task):**
   - The task router's anti-keyword-bias defense succeeded: although the instruction mentioned `security` and `sanitize`, the presence of the concrete function signature (`sanitize_filename(...) -> str`) routed the task to `CODE_GENERATION`.
   - The model generated an in-memory implementation that safely stripped `..` traversal tokens and `/` separators.
   - However, the model executed `basename = filename.split('/')[-1]` before replacing Windows backslashes (`\\`). On the test input `..\\..\\windows\\system32`, it stripped `\\` after the split, yielding `windowssystem32` instead of `system32`. This caused the validation test to fail.

---

## 4. Resource & Throughput Measurements

- **Inference Hardware:** CPU-only (`n_gpu_layers=0`)
- **Total Execution Time:** 12.40 seconds
- **Average Case Latency:** 1.55 seconds
- **Token Generation Speed:** 432.2 tokens/second (prompt processing + completion throughput)
- **Total Tokens Consumed:** 5,357 tokens

---

## 5. Decision & Recommended Next Steps

### Release-Readiness Decision:
**Improved but still limited**
- The clarification mode collapse is definitively eliminated. The system prompt separation and `TaskRouter` design are empirically validated on real-model CPU inference.
- The 1.5B base model's limitations remain diff hygiene (60.0% vs 75.0% threshold) and occasional path normalization edge cases on Windows separators (`eval-08`).
- The project retains the verified status of **READY WITH LIMITATIONS** established in the Stage K Release Audit (`reports/release_readiness_audit_v0.4.0.md`).

### Recommended Next Steps:
1. **Maintain v3 & v5 as Co-Reference Baselines:** v3 demonstrated 85.7% test pass rate with 100% syntax validity using a monolithic prompt; v5 demonstrates the same 85.7% test pass rate while successfully eliminating prompt-mode-collapse via deterministic routing.
2. **Retain Stage N Architecture:** The `TaskRouter` and prompt separation in `src/codepilot/core/context/router.py` and `src/codepilot/core/context/prompt.py` should be kept as the production standard.
3. **Future QLoRA Fine-Tuning (TASK-EXT-01):** To bridge the gap in diff hygiene (60.0% $\rightarrow$ $\ge 75.0\%$) and canonical clarification phrasing, fine-tuning `Qwen2.5-Coder-1.5B-Instruct` on domain-specific diffs and clarification examples remains the optimal path forward.

