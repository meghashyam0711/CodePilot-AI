"""Evaluation execution runner for benchmarking local coding models."""

from collections import defaultdict
from pathlib import Path
import time
from typing import Dict, List, Optional

from codepilot.core.context.prompt import (
    build_clarification_prompt,
    build_system_prompt,
)
from codepilot.core.context.router import TaskRoute, TaskRouter
from codepilot.core.dataset.builder import load_jsonl_dataset
from codepilot.core.evaluation.metrics import (
    calculate_diff_hygiene,
    check_clarification_accuracy,
    check_instruction_followed,
    check_syntax_validity,
    extract_code_from_completion,
    run_validation_test_safely,
)
from codepilot.core.evaluation.models import (
    EvaluationCase,
    EvaluationItemResult,
    EvaluationReport,
)
from codepilot.core.models.base import BaseModelClient, ModelRequest


class EvaluationRunner:
    """Orchestrates deterministic evaluation of coding models against benchmark fixtures."""

    def __init__(
        self,
        client: BaseModelClient,
        router: Optional[TaskRouter] = None,
    ) -> None:
        self.client = client
        self.router = router or TaskRouter()

    def evaluate_cases(self, cases: List[EvaluationCase]) -> EvaluationReport:
        """Run all evaluation cases and compute comprehensive metrics."""
        results: List[EvaluationItemResult] = []
        total_latency = 0.0
        total_tokens = 0

        by_type_counts: Dict[str, int] = defaultdict(int)
        by_type_followed: Dict[str, int] = defaultdict(int)
        by_type_syntax: Dict[str, int] = defaultdict(int)
        by_type_hygiene_sum: Dict[str, float] = defaultdict(float)
        by_type_test_passed: Dict[str, int] = defaultdict(int)
        by_type_test_total: Dict[str, int] = defaultdict(int)

        for case in cases:
            itype = case.instruction_type
            by_type_counts[itype] += 1

            # Determine route and route-appropriate system and user prompts
            decision = self.router.route_case(case)
            case_system_prompt = build_system_prompt(route=decision.route)

            if decision.route == TaskRoute.CLARIFICATION:
                prompt_text = build_clarification_prompt(case.prompt, case.source_code)
            else:
                prompt_text = case.prompt
                if case.source_code and case.source_code.strip():
                    prompt_text = (
                        f"### Task Instruction\n"
                        f"{case.prompt}\n\n"
                        f"### Source Code to Modify\n"
                        f"<untrusted_repository_context>\n"
                        f"{case.source_code.strip()}\n"
                        f"</untrusted_repository_context>\n"
                    )

            request = ModelRequest(
                prompt=prompt_text,
                system_prompt=case_system_prompt,
                temperature=0.1,
                max_tokens=1024,
                task_type=case.instruction_type,
                metadata={
                    "case_id": case.id,
                    "route": decision.route.value,
                    "route_reason": decision.reason,
                },
            )

            start_t = time.perf_counter()
            error_msg: Optional[str] = None
            try:
                response = self.client.generate(request)
                completion = response.completion
                prompt_tokens = response.prompt_tokens or len(prompt_text) // 4
                comp_tokens = response.completion_tokens or len(completion) // 4
                latency = response.latency_seconds if response.latency_seconds > 0 else (time.perf_counter() - start_t)
            except Exception as e:
                completion = ""
                prompt_tokens = 0
                comp_tokens = 0
                latency = time.perf_counter() - start_t
                error_msg = str(e)

            total_latency += latency
            total_tokens += (prompt_tokens + comp_tokens)

            # Extract code and compute metrics
            extracted = extract_code_from_completion(completion)
            clarif_ok = check_clarification_accuracy(case.should_request_clarification, completion) if completion else False

            if case.should_request_clarification:
                # When clarification is expected, asking for clarification satisfies instruction following
                followed_ok = clarif_ok
                # If code was emitted, verify its syntax; otherwise for clarification tasks,
                # omitting code fences is the expected behavior and does not constitute a Python syntax error.
                syntax_ok = check_syntax_validity(extracted, case.language) if extracted else True
                # Diff hygiene compares response against expected clarification prompt
                hygiene = calculate_diff_hygiene(case.expected_output, completion) if (completion and clarif_ok) else 0.0
            else:
                followed_ok = check_instruction_followed(case.instruction_type, completion) if completion else False
                syntax_ok = check_syntax_validity(extracted, case.language) if completion else False
                hygiene = calculate_diff_hygiene(case.expected_output, extracted) if completion else 0.0

            test_ok: Optional[bool] = None
            if case.validation_test:
                by_type_test_total[itype] += 1
                if extracted:
                    test_ok = run_validation_test_safely(extracted, case.validation_test)
                    if test_ok:
                        by_type_test_passed[itype] += 1
                else:
                    test_ok = False

            if followed_ok:
                by_type_followed[itype] += 1
            if syntax_ok:
                by_type_syntax[itype] += 1
            by_type_hygiene_sum[itype] += hygiene

            results.append(
                EvaluationItemResult(
                    case_id=case.id,
                    instruction_type=case.instruction_type,
                    raw_completion=completion,
                    extracted_code=extracted,
                    instruction_followed=followed_ok,
                    syntax_valid=syntax_ok,
                    diff_hygiene_score=hygiene,
                    clarification_correct=clarif_ok,
                    test_passed=test_ok,
                    latency_seconds=round(latency, 3),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=comp_tokens,
                    error=error_msg,
                )
            )

        model_info = self.client.get_model_info()
        model_id = getattr(model_info, "name", getattr(model_info, "model_name", "unknown"))
        n = len(cases)

        if n == 0:
            return EvaluationReport(
                model_id=model_id,
                total_cases=0,
                instruction_following_rate=0.0,
                syntax_validity_rate=0.0,
                diff_hygiene_average=0.0,
                clarification_accuracy=0.0,
                test_pass_rate=0.0,
                average_latency_seconds=0.0,
                total_tokens_used=0,
                tokens_per_second=0.0,
            )

        # Aggregate metrics
        inst_rate = sum(1 for r in results if r.instruction_followed) / n
        syntax_rate = sum(1 for r in results if r.syntax_valid) / n
        hygiene_avg = sum(r.diff_hygiene_score for r in results) / n
        clarif_rate = sum(1 for r in results if r.clarification_correct) / n

        tested_results = [r for r in results if r.test_passed is not None]
        test_pass_rate = (sum(1 for r in tested_results if r.test_passed) / len(tested_results)) if tested_results else 0.0

        avg_latency = total_latency / n
        tps = (total_tokens / total_latency) if total_latency > 0 else 0.0

        # Build by_type breakdown
        by_type_breakdown: Dict[str, Dict[str, float]] = {}
        for cat, count in by_type_counts.items():
            by_type_breakdown[cat] = {
                "count": count,
                "instruction_followed": round(by_type_followed[cat] / count, 4),
                "syntax_valid": round(by_type_syntax[cat] / count, 4),
                "diff_hygiene": round(by_type_hygiene_sum[cat] / count, 4),
                "test_passed": (
                    round(by_type_test_passed[cat] / by_type_test_total[cat], 4)
                    if by_type_test_total[cat] > 0
                    else 0.0
                ),
            }

        model_info = self.client.get_model_info()
        model_id = getattr(model_info, "name", getattr(model_info, "model_name", "unknown"))

        return EvaluationReport(
            model_id=model_id,
            total_cases=n,
            instruction_following_rate=round(inst_rate, 4),
            syntax_validity_rate=round(syntax_rate, 4),
            diff_hygiene_average=round(hygiene_avg, 4),
            clarification_accuracy=round(clarif_rate, 4),
            test_pass_rate=round(test_pass_rate, 4),
            average_latency_seconds=round(avg_latency, 3),
            total_tokens_used=total_tokens,
            tokens_per_second=round(tps, 2),
            item_results=results,
            by_type_breakdown=by_type_breakdown,
        )


def evaluate_dataset(dataset_path: Path, client: BaseModelClient) -> EvaluationReport:
    """Load JSONL dataset and evaluate all cases using the provided model client."""
    examples = load_jsonl_dataset(dataset_path)
    cases = [EvaluationCase.from_dataset_example(ex) for ex in examples]
    runner = EvaluationRunner(client)
    return runner.evaluate_cases(cases)
