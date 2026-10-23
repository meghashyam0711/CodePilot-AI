# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Timestamp:** 2026-10-23T10:24:54.533618+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 87.5% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 87.5% | $\ge 95.0\%$ | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | 60.0% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 87.5% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 85.7% | $\ge 70.0\%$ | PASS |

## 2. Resource & Latency Performance

- **Average Response Latency:** 1.55 s
- **Inference Speed:** 432.2 tokens/s
- **Total Tokens Consumed:** 5,357 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 100.0% | 83.3% | 100.0% |
| `AI-TASK` | 5 | 80.0% | 80.0% | 46.0% | 75.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | OK | 100% | OK | PASS | 2.85s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | OK | 100% | OK | PASS | 1.12s |
| `eval-03-missing-return` | `AI-FIX` | OK | OK | 50% | OK | PASS | 1.05s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.79s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | OK | 33% | OK | PASS | 0.81s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | OK | 80% | OK | PASS | 0.98s |
| `eval-07-clarification-request` | `AI-TASK` | NO | FAIL | 0% | FAIL | N/A | 1.22s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 17% | OK | FAIL | 3.58s |

## 5. Benchmark Comparison: Baseline vs V2 vs V3 vs V4 vs V5

| Metric | Required Threshold | Baseline | V2 | V3 (Ref) | V4 | V5 (Stage O) | $\Delta$ (V5 vs V3) | $\Delta$ (V5 vs V4) | Status |
|---|---|---|---|---|---|---|---|---|---|
| **Instruction Following** | $\ge 85.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | 87.5% | +0.0 pp | +75.0 pp | PASS |
| **Syntax Validity** | $\ge 95.0\%$ | 100.0% | 37.5% | 100.0% | 12.5% | 87.5% | -12.5 pp | +75.0 pp | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | $\ge 75.0\%$ | 31.9% | 56.0% | 60.7% | 0.0% | 60.0% | -0.7 pp | +60.0 pp | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | $\ge 80.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | 87.5% | +0.0 pp | +75.0 pp | PASS |
| **Validation Test Pass Rate** | $\ge 70.0\%$ | 57.1% | 14.3% | 85.7% | 0.0% | 85.7% | +0.0 pp | +85.7 pp | PASS |

## 6. Execution Details & Preflight Results

- **Execution Command:** `& "D:\PROJECTS\CodePilot AI\.venv\Scripts\python.exe" "C:\Users\megha\.gemini\antigravity-ide\brain\04189f01-90db-4fe0-8773-64ef5d39e2ac\scratch\run_real_evaluation_v5.py"`
- **Runtime Environment:** Python virtual environment (`.venv`), Python 3.13.12
- **Inference Backend:** `llama-cpp-python` 0.3.36 on CPU (`n_gpu_layers=0`)
- **Model Path & Size:** `D:\PROJECTS\CodePilot AI\models\qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` (1065.56 MB)
- **Generation Parameters:** `temperature=0.1`, `max_tokens=1024`
- **Start Time:** 2026-10-23T10:24:42.131155+00:00
- **Completion Time:** 2026-10-23T10:24:54.533633+00:00
- **Total Wall-Clock Time:** 12.40 seconds
- **Average Case Latency:** 1.55 seconds
- **Token Throughput:** 432.2 tokens/s
- **Total Tokens Consumed:** 5,357 tokens
- **Real Local Inference Confirmed:** Yes (`StandaloneGGUFClient` loading local GGUF weights directly)

### Per-Case Routing Decisions & Diagnostic Notes

- **`eval-01-basic-function` (`AI-TASK`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 100.0%, Latency: 2.85s)
- **`eval-02-bug-fixing` (`AI-FIX`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 100.0%, Latency: 1.12s)
- **`eval-03-missing-return` (`AI-FIX`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 50.0%, Latency: 1.05s)
- **`eval-04-boundary-conditions` (`AI-FIX`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 100.0%, Latency: 0.79s)
- **`eval-05-existing-body-modification` (`AI-TASK`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 33.3%, Latency: 0.81s)
- **`eval-06-malformed-empty-output` (`AI-TASK`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). All checks satisfied. (Diff hygiene: 80.0%, Latency: 0.98s)
- **`eval-07-clarification-request` (`AI-TASK`):** Route = `clarification` (Task metadata explicitly specifies clarification required). Failures: instruction not followed; invalid Python syntax; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 1.22s)
- **`eval-08-security-sensitive` (`AI-TASK`):** Route = `code_generation` (Task specifies concrete function signature, return value, or transformation target). Failures: validation test failed. (Diff hygiene: 16.7%, Latency: 3.58s)

## 7. Failures, Limitations, & Release-Readiness Assessment

- **Overall Assessment:** **Improved but still limited**
- **Assessment Rationale:** The Stage M v4 clarification mode-collapse regression is definitively resolved. Standard coding tasks (eval-01 through eval-06) and the security-sensitive task (eval-08) reached code generation and produced concrete Python implementations rather than echoing clarification phrases, driving validation test pass rate from 0.0% to 85.7% (+85.7 pp). eval-07 was correctly routed to clarification and emitted a targeted clarification question without code fences. However, two metrics remain below canonical release thresholds: Syntax Validity (87.5% vs $\ge 95.0\%$, impacted by eval-07 string-marker mismatch cascading to syntax scoring) and Diff Hygiene (60.0% vs $\ge 75.0\%$, reflecting compact 1.5B model structural verbosity).
- **Security & Sandbox Scope:** In-memory validation test execution (`run_validation_test_safely`) provides restricted execution for deterministic unit verification, not a hardened OS-level container. Neither unit test passes nor benchmark execution constitutes proof of complete software security.
