# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `mock-qwen2.5-coder:1.5b`
- **Timestamp:** 2026-10-27T03:58:54.266177+00:00
- **Total Benchmark Cases:** 8

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 100.0% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 100.0% | $\ge 95.0\%$ | PASS |
| **Diff Hygiene Score** | 100.0% | $\ge 75.0\%$ | PASS |
| **Clarification Accuracy** | 100.0% | $\ge 80.0\%$ | PASS |
| **Validation Test Pass Rate** | 100.0% | $\ge 70.0\%$ | PASS |

## 2. Resource & Latency Performance

- **Average Response Latency:** 0.01 s
- **Inference Speed:** 13212.5 tokens/s
- **Total Tokens Consumed:** 1,057 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 3 | 100.0% | 100.0% | 100.0% | 100.0% |
| `AI-TASK` | 5 | 100.0% | 100.0% | 100.0% | 100.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `eval-01-basic-function` | `AI-TASK` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-02-bug-fixing` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-03-missing-return` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-04-boundary-conditions` | `AI-FIX` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-05-existing-body-modification` | `AI-TASK` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-06-malformed-empty-output` | `AI-TASK` | OK | OK | 100% | OK | PASS | 0.01s |
| `eval-07-clarification-request` | `AI-TASK` | OK | OK | 100% | OK | N/A | 0.01s |
| `eval-08-security-sensitive` | `AI-TASK` | OK | OK | 100% | OK | PASS | 0.01s |