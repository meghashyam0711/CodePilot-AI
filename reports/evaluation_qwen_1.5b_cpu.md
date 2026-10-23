# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf`
- **Timestamp:** 2026-10-23T06:35:58.187604+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 87.5% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 100.0% | $\ge 95.0\%$ | PASS |
| **Diff Hygiene Score** | 31.9% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 87.5% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 57.1% | $\ge 70.0\%$ | NEEDS IMPROVEMENT |

## 2. Resource & Latency Performance

- **Average Response Latency:** 2.68 s
- **Inference Speed:** 181.3 tokens/s
- **Total Tokens Consumed:** 3,883 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 100.0% | 25.1% | 33.3% |
| `AI-TASK` | 5 | 80.0% | 100.0% | 36.0% | 75.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | OK | 50% | OK | PASS | 3.08s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | OK | 0% | OK | FAIL | 1.92s |
| `eval-03-missing-return` | `AI-FIX` | OK | OK | 15% | OK | FAIL | 1.84s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 60% | OK | PASS | 0.98s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | OK | 50% | OK | PASS | 1.32s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | OK | 80% | OK | PASS | 0.88s |
| `eval-07-clarification-request` | `AI-TASK` | NO | OK | 0% | FAIL | N/A | 8.49s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 2.91s |