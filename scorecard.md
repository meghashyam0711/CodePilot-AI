# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `mock-qwen2.5-coder:1.5b`
- **Timestamp:** 2026-10-23T05:25:36.701853+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 100.0% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 100.0% | $\ge 95.0\%$ | PASS |
| **Diff Hygiene Score** | 3.6% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 87.5% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 0.0% | $\ge 70.0\%$ | NEEDS IMPROVEMENT |

## 2. Resource & Latency Performance

- **Average Response Latency:** 0.01 s
- **Inference Speed:** 9950.0 tokens/s
- **Total Tokens Consumed:** 796 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 100.0% | 9.5% | 0.0% |
| `AI-TASK` | 5 | 100.0% | 100.0% | 0.0% | 0.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 0.01s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | OK | 0% | OK | FAIL | 0.01s |
| `eval-03-missing-return` | `AI-FIX` | OK | OK | 29% | OK | FAIL | 0.01s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 0% | OK | FAIL | 0.01s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 0.01s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 0.01s |
| `eval-07-clarification-request` | `AI-TASK` | OK | OK | 0% | FAIL | N/A | 0.01s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 0.01s |