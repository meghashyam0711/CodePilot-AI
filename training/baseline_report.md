# CodePilot AI — Base Model Evaluation Report

- **Evaluated Model:** `mock-qwen2.5-coder:1.5b`
- **Timestamp:** 2026-10-22T04:07:43.436233+00:00
- **Total Benchmark Cases:** 3

## 1. Overall Scorecard

| Metric | Result | Target Benchmark | Status |
|---|---|---|---|
| **Instruction Following** | 100.0% | $\ge 85.0\%$ | PASS |
| **Syntax Validity** | 100.0% | $\ge 95.0\%$ | PASS |
| **Diff Hygiene Score** | 0.0% | $\ge 75.0\%$ | NEEDS IMPROVEMENT |
| **Clarification Accuracy** | 66.7% | $\ge 80.0\%$ | NEEDS IMPROVEMENT |
| **Validation Test Pass Rate** | 0.0% | $\ge 70.0\%$ | NEEDS IMPROVEMENT |

## 2. Resource & Latency Performance

- **Average Response Latency:** 0.01 s
- **Inference Speed:** 33500.0 tokens/s
- **Total Tokens Consumed:** 1,005 tokens

## 3. Results by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | 1 | 100.0% | 100.0% | 0.0% | 0.0% |
| `AI-REFACTOR` | 1 | 100.0% | 100.0% | 0.0% | 0.0% |
| `AI-TASK` | 1 | 100.0% | 100.0% | 0.0% | 0.0% |

## 4. Case-by-Case Breakdown

| Case ID | Type | Followed | Syntax | Hygiene | Clarification | Test | Latency |
|---|---|---|---|---|---|---|---|
| `ex-clarif-002` | `AI-FIX` | OK | OK | 0% | FAIL | N/A | 0.01s |
| `ex-refactor-001` | `AI-REFACTOR` | OK | OK | 0% | OK | FAIL | 0.01s |
| `ex-task-002` | `AI-TASK` | OK | OK | 0% | OK | FAIL | 0.01s |