# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Timestamp:** 2026-10-23T07:10:08.636908+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 87.5% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 37.5% | $\ge 95.0\%$ | NEEDS IMPROVEMENT |
| **Diff Hygiene Score** | 56.0% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 87.5% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 14.3% | $\ge 70.0\%$ | NEEDS IMPROVEMENT |

## 2. Resource & Latency Performance

- **Average Response Latency:** 1.48 s
- **Inference Speed:** 398.7 tokens/s
- **Total Tokens Consumed:** 4,715 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 33.3% | 73.2% | 33.3% |
| `AI-TASK` | 5 | 80.0% | 40.0% | 45.7% | 0.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | FAIL | 67% | OK | FAIL | 2.85s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | FAIL | 83% | OK | FAIL | 1.39s |
| `eval-03-missing-return` | `AI-FIX` | OK | FAIL | 36% | OK | FAIL | 1.59s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.86s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | FAIL | 73% | OK | FAIL | 1.21s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | FAIL | 67% | OK | FAIL | 1.25s |
| `eval-07-clarification-request` | `AI-TASK` | NO | OK | 0% | FAIL | N/A | 1.20s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 22% | OK | FAIL | 1.48s |