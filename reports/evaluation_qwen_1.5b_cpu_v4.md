# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Timestamp:** 2026-10-23T09:53:42.448513+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 12.5% | $\ge 85.0\%$ | NEEDS IMPROVEMENT |
| **Syntax Validity** | 12.5% | $\ge 95.0\%$ | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | 0.0% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 12.5% | $\ge 80.0\%$ | NEEDS IMPROVEMENT |
| **Validation Test Pass Rate** | 0.0% | $\ge 70.0\%$ | NEEDS IMPROVEMENT |

## 2. Resource & Latency Performance

- **Average Response Latency:** 0.67 s
- **Inference Speed:** 1295.2 tokens/s
- **Total Tokens Consumed:** 6,889 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 0.0% | 0.0% | 0.0% | 0.0% |
| `AI-TASK` | 5 | 20.0% | 20.0% | 0.0% | 0.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | NO | FAIL | 0% | FAIL | FAIL | 2.92s |
| `eval-02-bug-fixing` | `AI-FIX` | NO | FAIL | 0% | FAIL | FAIL | 0.35s |
| `eval-03-missing-return` | `AI-FIX` | NO | FAIL | 0% | FAIL | FAIL | 0.32s |
| `eval-04-boundary-conditions` | `AI-FIX` | NO | FAIL | 0% | FAIL | FAIL | 0.26s |
| `eval-05-existing-body-modification` | `AI-TASK` | NO | FAIL | 0% | FAIL | FAIL | 0.27s |
| `eval-06-malformed-empty-output` | `AI-TASK` | NO | FAIL | 0% | FAIL | FAIL | 0.36s |
| `eval-07-clarification-request` | `AI-TASK` | OK | OK | 0% | OK | N/A | 0.53s |
| `eval-08-security-sensitive` | `AI-TASK` | NO | FAIL | 0% | FAIL | FAIL | 0.31s |

## 5. Benchmark Comparison: Baseline vs V2 vs V3 vs V4

| Metric | Required Threshold | Baseline | V2 | V3 | V4 | $\Delta$ (V4 vs Baseline) | $\Delta$ (V4 vs V3) | Status |
|---|---|---|---|---|---|---|---|---|
| **Instruction Following** | $\ge 85.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | -75.0 pp | -75.0 pp | NEEDS IMPROVEMENT |
| **Syntax Validity** | $\ge 95.0\%$ | 100.0% | 37.5% | 100.0% | 12.5% | -87.5 pp | -87.5 pp | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | $\ge 75.0\%$ | 31.9% | 56.0% | 60.7% | 0.0% | -31.9 pp | -60.7 pp | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | $\ge 80.0\%$ | 87.5% | 87.5% | 87.5% | 12.5% | -75.0 pp | -75.0 pp | NEEDS IMPROVEMENT |
| **Validation Test Pass Rate** | $\ge 70.0\%$ | 57.1% | 14.3% | 85.7% | 0.0% | -57.1 pp | -85.7 pp | NEEDS IMPROVEMENT |

## 6. Execution Details & Diagnostic Analysis

- **Execution Command:** `& "D:\PROJECTS\CodePilot AI\.venv\Scripts\python.exe" "C:\Users\megha\.gemini\antigravity-ide\brain\04189f01-90db-4fe0-8773-64ef5d39e2ac\scratch\run_real_evaluation_v4.py"`
- **Runtime Environment:** Python virtual environment (`.venv`), `llama-cpp-python`
- **Inference Configuration:** CPU-only (`n_gpu_layers=0`), `temperature=0.1`, `max_tokens=1024`
- **Total Wall-Clock Time:** 5.33 seconds
- **Average Case Latency:** 0.67 seconds
- **Token Throughput:** 1295.2 tokens/s
- **Total Tokens Consumed:** 6,889 tokens
- **Real Local Inference Confirmed:** Yes (`StandaloneGGUFClient` loading local GGUF weights)

### Diagnostic Notes on Individual Cases

- **`eval-01-basic-function` (`AI-TASK`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 2.92s)
- **`eval-02-bug-fixing` (`AI-FIX`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.35s)
- **`eval-03-missing-return` (`AI-FIX`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.32s)
- **`eval-04-boundary-conditions` (`AI-FIX`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.26s)
- **`eval-05-existing-body-modification` (`AI-TASK`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.27s)
- **`eval-06-malformed-empty-output` (`AI-TASK`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.36s)
- **`eval-07-clarification-request` (`AI-TASK`):** All checks satisfied. (Diff hygiene: 0.0%, Latency: 0.53s)
- **`eval-08-security-sensitive` (`AI-TASK`):** instruction not followed; invalid Python syntax; validation test failed; clarification handling incorrect. (Diff hygiene: 0.0%, Latency: 0.31s)
