"""Deterministic unit tests for TaskRouter and Prompt Separation (Stage N).

Validates that:
1. Well-specified tasks (including security-sensitive tasks) route to CODE_GENERATION.
2. Genuinely ambiguous or conflicting tasks route to CLARIFICATION.
3. Code-generation prompt does not impose a universal clarification response.
4. Clarification prompt is clean and dedicated without code fences.
5. All 8 canonical benchmark cases route to their intended task types without hardcoded IDs.
"""

from typing import List
import pytest

from codepilot.core.context.models import CodeSnippet, ProjectContextBundle
from codepilot.core.context.prompt import (
    CLARIFICATION_SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    build_clarification_prompt,
    build_model_request,
    build_system_prompt,
    build_user_prompt,
)
from codepilot.core.context.router import TaskRoute, TaskRouter, TaskRoutingDecision
from codepilot.core.evaluation.benchmark import get_default_benchmark_cases
from codepilot.core.evaluation.models import EvaluationCase
from codepilot.core.evaluation.runner import EvaluationRunner
from codepilot.core.models.mock_client import MockModelClient
from codepilot.core.parser.models import InstructionTask


class TestTaskRouterDecisions:
    """Tests verifying TaskRouter classification accuracy across diverse task shapes."""

    def setup_method(self) -> None:
        self.router = TaskRouter()

    def test_clearly_specified_implementation_task_routes_to_code_generation(self) -> None:
        prompt = "Please implement the function 'add(a: int, b: int) -> int' that returns the sum of a and b."
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CODE_GENERATION
        assert decision.is_code_generation is True
        assert decision.is_clarification is False

    def test_sufficiently_specified_code_modification_routes_to_code_generation(self) -> None:
        prompt = "Fix 'compute_average' by returning total / count when count > 0, otherwise return 0.0."
        source = "def compute_average(total: float, count: int) -> float:\n    pass\n"
        decision = self.router.route(prompt, source_code=source)
        assert decision.route == TaskRoute.CODE_GENERATION
        assert "concrete" in decision.reason.lower() or "sufficient" in decision.reason.lower()

    def test_sufficiently_specified_security_task_routes_to_code_generation(self) -> None:
        prompt = (
            "Implement 'sanitize_filename(filename: str) -> str' that safely extracts the basename "
            "and removes path traversal sequences ('..') and directory separators ('/' and '\\') without importing external modules."
        )
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CODE_GENERATION

    def test_genuinely_ambiguous_task_routes_to_clarification(self) -> None:
        prompt = "Make the authentication logic better and update the user verification flow accordingly."
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CLARIFICATION
        assert decision.is_clarification is True
        assert "essential specifications" in decision.reason.lower() or "criteria" in decision.reason.lower()

    def test_conflicting_requirements_route_to_clarification(self) -> None:
        prompt = "Always return True and return False depending on state."
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CLARIFICATION
        assert "conflicting" in decision.reason.lower()

    def test_optional_missing_info_does_not_force_clarification(self) -> None:
        prompt = "Implement 'format_name(first: str, last: str) -> str' returning 'first last'."
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CODE_GENERATION

    def test_security_keywords_alone_do_not_force_clarification(self) -> None:
        # A task mentioning authentication, security, or tokens with concrete specifications must NOT route to clarification
        prompt = "Implement 'validate_token(token: str) -> bool' returning True if token starts with 'Bearer '."
        decision = self.router.route(prompt)
        assert decision.route == TaskRoute.CODE_GENERATION

    def test_empty_and_placeholder_prompts_route_to_clarification(self) -> None:
        assert self.router.route("").route == TaskRoute.CLARIFICATION
        assert self.router.route("   \n\t").route == TaskRoute.CLARIFICATION
        assert self.router.route("TODO").route == TaskRoute.CLARIFICATION
        assert self.router.route("???").route == TaskRoute.CLARIFICATION

    def test_explicit_metadata_clarification_flag_routes_to_clarification(self) -> None:
        decision = self.router.route(
            prompt="Implement generic helper",
            should_request_clarification=True,
        )
        assert decision.route == TaskRoute.CLARIFICATION
        assert "explicit" in decision.reason.lower()

    def test_malformed_instruction_routes_to_clarification(self) -> None:
        decision = self.router.route(
            prompt="Implement helper",
            is_malformed=True,
        )
        assert decision.route == TaskRoute.CLARIFICATION
        assert "malformed" in decision.reason.lower()

    def test_routing_decisions_are_deterministic(self) -> None:
        prompt = "Fix off-by-one error in 'scan_prices'."
        decision_1 = self.router.route(prompt)
        decision_2 = self.router.route(prompt)
        assert decision_1.route == decision_2.route
        assert decision_1.reason == decision_2.reason
        assert decision_1.confidence == decision_2.confidence


class TestPromptSeparation:
    """Tests verifying clean separation of code-generation and clarification prompts."""

    def test_code_generation_prompt_has_no_clarification_phrase(self) -> None:
        sys_prompt = build_system_prompt(route=TaskRoute.CODE_GENERATION)
        # Must NOT contain the verbatim phrase that caused mode collapse in Stage M
        assert "Please clarify..." not in sys_prompt
        assert "Could you please clarify..." not in sys_prompt
        assert "your ENTIRE response must be a single concise clarification question" not in sys_prompt

    def test_code_generation_prompt_retains_security_and_delimiter_rules(self) -> None:
        sys_prompt = build_system_prompt(route=TaskRoute.CODE_GENERATION)
        assert "<untrusted_repository_context>" in sys_prompt
        assert "passive data" in sys_prompt.lower()
        assert "prompt-only boundary delimiters" in sys_prompt.lower()
        assert "isolate the basename component" in sys_prompt.lower()
        assert "MINIMAL SCOPE & DIFF HYGIENE" in sys_prompt

    def test_clarification_prompt_does_not_contain_code_fences_rule(self) -> None:
        clarif_prompt = build_system_prompt(route=TaskRoute.CLARIFICATION)
        assert "CLARIFICATION REQUEST" in clarif_prompt
        assert "concise, targeted clarification question" in clarif_prompt
        assert "Do NOT emit markdown code fences" in clarif_prompt
        assert "```python" not in clarif_prompt

    def test_build_system_prompt_backward_compatible(self) -> None:
        # Default with no arguments must return the code-generation prompt
        default_prompt = build_system_prompt()
        assert default_prompt == SYSTEM_INSTRUCTION.strip()
        assert "MINIMAL SCOPE & DIFF HYGIENE" in default_prompt

    def test_build_clarification_prompt_structure(self) -> None:
        prompt = build_clarification_prompt("Make auth better", "def auth(): pass")
        assert "### Ambiguous Task Instruction" in prompt
        assert "Make auth better" in prompt
        assert "<untrusted_repository_context>" in prompt
        assert "def auth(): pass" in prompt
        assert "ask a single concise, targeted question" in prompt.lower()

    def test_build_model_request_attaches_route_metadata(self) -> None:
        task = InstructionTask(
            id="task-clear",
            file_path="src/calc.py",
            line_number=5,
            instruction_type="AI-TASK",
            description="Implement 'add(a: int, b: int) -> int' returning a + b",
        )
        bundle = ProjectContextBundle(task=task)
        req = build_model_request(bundle)
        assert req.metadata["route"] == TaskRoute.CODE_GENERATION.value
        assert "route_reason" in req.metadata


class TestBenchmarkRoutingCoverage:
    """Tests verifying that all eight canonical benchmark cases route to their expected task types."""

    def setup_method(self) -> None:
        self.router = TaskRouter()
        self.cases = get_default_benchmark_cases()

    def test_canonical_benchmark_cases_count(self) -> None:
        assert len(self.cases) == 8

    def test_eval_01_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-01-basic-function")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_02_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-02-bug-fixing")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_03_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-03-missing-return")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_04_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-04-boundary-conditions")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_05_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-05-existing-body-modification")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_06_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-06-malformed-empty-output")
        assert self.router.route_case(case).route == TaskRoute.CODE_GENERATION

    def test_eval_07_routes_to_clarification(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-07-clarification-request")
        decision = self.router.route_case(case)
        assert decision.route == TaskRoute.CLARIFICATION
        assert decision.is_clarification is True

    def test_eval_08_routes_to_code_generation(self) -> None:
        case = next(c for c in self.cases if c.id == "eval-08-security-sensitive")
        decision = self.router.route_case(case)
        assert decision.route == TaskRoute.CODE_GENERATION
        assert decision.is_code_generation is True

    def test_router_does_not_depend_on_case_ids(self) -> None:
        # Verify that routing logic works without case.id
        for case in self.cases:
            anonymized_case = EvaluationCase(
                id="anonymized-id",
                instruction_type=case.instruction_type,
                description=case.description,
                prompt=case.prompt,
                expected_output=case.expected_output,
                source_code=case.source_code,
                should_request_clarification=case.should_request_clarification,
            )
            decision = self.router.route_case(anonymized_case)
            expected_route = (
                TaskRoute.CLARIFICATION
                if case.should_request_clarification
                else TaskRoute.CODE_GENERATION
            )
            assert decision.route == expected_route, f"Mismatch for case {case.id}"


class TestEvaluationRunnerRouterIntegration:
    """Tests verifying EvaluationRunner interacts correctly with TaskRouter."""

    def test_runner_routes_cases_and_dispatches_correct_prompts(self) -> None:
        captured_requests = []

        class SpyClient(MockModelClient):
            def generate(self, request, model_name=None):
                captured_requests.append(request)
                if request.metadata.get("route") == TaskRoute.CLARIFICATION.value:
                    from codepilot.core.models.base import ModelResponse
                    return ModelResponse(
                        content="Could you please clarify what authentication criteria should be used?",
                        model_name="mock-spy",
                        latency_seconds=0.01,
                    )
                return super().generate(request, model_name=model_name)

        client = SpyClient()
        runner = EvaluationRunner(client)
        cases = get_default_benchmark_cases()
        report = runner.evaluate_cases(cases)

        assert len(captured_requests) == 8

        # 7 coding cases must receive CODE_GENERATION route and SYSTEM_INSTRUCTION
        code_requests = [r for r in captured_requests if r.metadata.get("case_id") != "eval-07-clarification-request"]
        assert len(code_requests) == 7
        for req in code_requests:
            assert req.metadata["route"] == TaskRoute.CODE_GENERATION.value
            assert "Please clarify..." not in req.system_prompt
            assert "MINIMAL SCOPE & DIFF HYGIENE" in req.system_prompt
            assert "<untrusted_repository_context>" in req.prompt or "<untrusted_repository_context>" in req.system_prompt

        # eval-07 must receive CLARIFICATION route and CLARIFICATION_SYSTEM_INSTRUCTION
        clarif_request = next(r for r in captured_requests if r.metadata.get("case_id") == "eval-07-clarification-request")
        assert clarif_request.metadata["route"] == TaskRoute.CLARIFICATION.value
        assert "CLARIFICATION REQUEST" in clarif_request.system_prompt
        assert "Do NOT emit markdown code fences" in clarif_request.system_prompt

        # Clarification accuracy for eval-07 must be 1.0 (correct)
        eval_07_result = next(r for r in report.item_results if r.case_id == "eval-07-clarification-request")
        assert eval_07_result.clarification_correct is True
        assert eval_07_result.instruction_followed is True
