# CodePilot AI — Base Model Evaluation Report Template

## 1. Executive Summary
- **Evaluation Date:** YYYY-MM-DD
- **Target Model:** `[Model ID / Quantization]` (e.g. `qwen2.5-coder:1.5b` or `qwen2.5:1.5b-instruct-q4_K_M`)
- **Evaluation Framework:** CodePilot AI Test Suite v1.0.0
- **Total Test Cases:** `[Count]`
- **Overall Verdict:** `[READY / REQUIRES FINE-TUNING / BLOCKED]`

---

## 2. Core Evaluation Metrics

| Metric | Measured Baseline | Target Benchmark | Outcome |
|---|---|---|---|
| **Instruction-Following Accuracy** | `0.0%` | $\ge 85.0\%$ | `[PASS / FAIL]` |
| **Syntax Validity Rate** | `0.0%` | $\ge 95.0\%$ | `[PASS / FAIL]` |
| **Diff Hygiene Score** (Minimal Scope) | `0.0%` | $\ge 75.0\%$ | `[PASS / FAIL]` |
| **Clarification Accuracy** (Ambiguous Prompts) | `0.0%` | $\ge 80.0\%$ | `[PASS / FAIL]` |
| **Validation Test Pass Rate** | `0.0%` | $\ge 70.0\%$ | `[PASS / FAIL]` |

---

## 3. Resource Usage & Hardware Profiling

| Resource Metric | Measured Value | Reference GPU Budget (8 GB VRAM) | Headroom |
|---|---|---|---|
| **Peak GPU VRAM Usage** | `~1.2 GB` | $\le 6.0\text{ GB}$ (8 GB reference) | $\ge 2.0\text{ GB}$ |
| **Host System RAM Working Set** | `~450 MB` | $\le 1.0\text{ GB}$ (16–32 GB host) | $\ge 15.0\text{ GB}$ |
| **Average Response Latency** | `0.0s` | $\le 15.0\text{s}$ per task | `[PASS]` |
| **Generation Speed (Tokens/s)** | `0.0 tps` | $\ge 25.0\text{ tokens/s}$ | `[PASS]` |

---

## 4. Breakdown by Instruction Category

| Category | Cases | Instruction Followed | Syntax Valid | Diff Hygiene | Test Pass Rate |
|---|---|---|---|---|---|
| `AI-FIX` | `[N]` | `0.0%` | `0.0%` | `0.0%` | `0.0%` |
| `AI-TASK` | `[N]` | `0.0%` | `0.0%` | `0.0%` | `0.0%` |
| `AI-REFACTOR` | `[N]` | `0.0%` | `0.0%` | `0.0%` | `0.0%` |
| `AI-TEST` | `[N]` | `0.0%` | `0.0%` | `0.0%` | `0.0%` |
| `AI-DOCS` | `[N]` | `0.0%` | `0.0%` | `0.0%` | `0.0%` |

---

## 5. Failure Analysis & Observed Deviations
- **Hallucinations & Bloat:** (e.g. Did the model invent unrelated helper functions or rewrite surrounding code?)
- **Syntax Errors:** (e.g. Unclosed brackets, improper indentation, or invalid imports)
- **Clarification Failures:** (e.g. Did the model guess blindly on underspecified instructions instead of asking?)

---

## 6. Comparison with Fine-Tuned Checkpoints
*(This section will be populated once model fine-tuning is completed to measure direct delta improvements over this baseline).*
