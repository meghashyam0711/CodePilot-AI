# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Timestamp:** 2026-10-23T09:05:53.886939+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 87.5% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 100.0% | $\ge 95.0\%$ | PASS |
| **Diff Hygiene Score** | 60.7% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 87.5% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 85.7% | $\ge 70.0\%$ | PASS |

## 2. Resource & Latency Performance

- **Average Response Latency:** 1.43 s
- **Inference Speed:** 468.9 tokens/s
- **Total Tokens Consumed:** 5,355 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 100.0% | 81.5% | 100.0% |
| `AI-TASK` | 5 | 80.0% | 100.0% | 48.2% | 75.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | OK | 50% | OK | PASS | 3.87s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | OK | 100% | OK | PASS | 1.12s |
| `eval-03-missing-return` | `AI-FIX` | OK | OK | 44% | OK | PASS | 1.09s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.74s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | OK | 89% | OK | PASS | 0.93s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | OK | 80% | OK | PASS | 0.98s |
| `eval-07-clarification-request` | `AI-TASK` | NO | OK | 0% | FAIL | N/A | 1.25s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 22% | OK | FAIL | 1.44s |

## 5. Benchmark Comparison: Baseline vs V2 vs V3

| Metric | Required Threshold | Baseline | V2 | V3 | $\Delta$ (V3 vs Baseline) | $\Delta$ (V3 vs V2) | Status |
|---|---|---|---|---|---|---|---|
| **Instruction Following** | $\ge 85.0\%$ | 87.5% | 87.5% | 87.5% | +0.0 pp | +0.0 pp | PASS |
| **Syntax Validity** | $\ge 95.0\%$ | 100.0% | 37.5% | 100.0% | +0.0 pp | +62.5 pp | PASS |
| **Diff Hygiene Score** | $\ge 75.0\%$ | 31.9% | 56.0% | 60.7% | +28.8 pp | +4.7 pp | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | $\ge 80.0\%$ | 87.5% | 87.5% | 87.5% | +0.0 pp | +0.0 pp | PASS |
| **Validation Test Pass Rate** | $\ge 70.0\%$ | 57.1% | 14.3% | 85.7% | +28.6 pp | +71.4 pp | PASS |

## 6. Execution Details & Diagnostic Analysis

- **Execution Command:** `& "D:\PROJECTS\CodePilot AI\.venv\Scripts\python.exe" "C:\Users\megha\.gemini\antigravity-ide\brain\04189f01-90db-4fe0-8773-64ef5d39e2ac\scratch\run_real_evaluation_v3.py"`
- **Runtime Environment:** Python virtual environment (`.venv`), `llama-cpp-python`
- **Inference Configuration:** CPU-only (`n_gpu_layers=0`), `temperature=0.1`, `max_tokens=1024`
- **Total Wall-Clock Time:** 11.43 seconds
- **Average Case Latency:** 1.43 seconds
- **Token Throughput:** 468.9 tokens/s
- **Total Tokens Consumed:** 5,355 tokens
- **Real Local Inference Confirmed:** Yes (`StandaloneGGUFClient` loading local GGUF weights)

### Diagnostic Notes on Remaining Failures

- **`eval-01-basic-function` (`AI-TASK`):** All checks satisfied.
- **`eval-02-bug-fixing` (`AI-FIX`):** All checks satisfied.
- **`eval-03-missing-return` (`AI-FIX`):** All checks satisfied.
- **`eval-04-boundary-conditions` (`AI-FIX`):** All checks satisfied.
- **`eval-05-existing-body-modification` (`AI-TASK`):** All checks satisfied.
- **`eval-06-malformed-empty-output` (`AI-TASK`):** All checks satisfied.
- **`eval-07-clarification-request` (`AI-TASK`):** instruction not followed; clarification handling incorrect.
- **`eval-08-security-sensitive` (`AI-TASK`):** validation test failed.
