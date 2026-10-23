"""Comprehensive tests for evaluation metrics, test runner, and scorecard generation."""

from pathlib import Path
import json
import textwrap
import pytest

from codepilot.config import CodePilotConfig

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
    EvaluationReport,
)
from codepilot.core.evaluation.runner import EvaluationRunner
from codepilot.core.models.base import ModelRequest
from codepilot.core.models.mock_client import MockModelClient


def test_extract_code_from_completion() -> None:
    """Verify code extraction from markdown blocks, diffs, and raw text."""
    # Python code block
    py_text = "Here is the fix:\n```python\ndef add(a, b):\n    return a + b\n```\nDone."
    assert extract_code_from_completion(py_text) == "def add(a, b):\n    return a + b"

    # Diff block
    diff_text = "```diff\n--- a.py\n+++ a.py\n@@ -1 +1 @@\n-old\n+new\n```"
    assert "--- a.py" in extract_code_from_completion(diff_text)

    # Raw text without code fences
    raw_text = "def mul(a, b): return a * b"
    assert extract_code_from_completion(raw_text) == raw_text


def test_syntax_validity_checker() -> None:
    """Verify syntax checking with ast.parse."""
    assert check_syntax_validity("def foo():\n    return 42", "python") is True
    # Indented snippet
    assert check_syntax_validity("    if x > 0:\n        return x", "python") is True
    # Syntax error
    assert check_syntax_validity("def invalid(:\n    pass", "python") is False


def test_instruction_followed_checker() -> None:
    """Verify classification of whether completion adheres to task type."""
    assert check_instruction_followed("AI-FIX", "This fixes the ZeroDivisionError by adding a check.") is True
    assert check_instruction_followed("AI-TASK", "def implement_feature(): return True") is True
    assert check_instruction_followed("AI-TEST", "def test_addition(): assert add(1, 2) == 3") is True
    assert check_instruction_followed("AI-DOCS", '"""Google style docstring.\nArgs: x\nReturns: y"""') is True
    assert check_instruction_followed("AI-FIX", "") is False


def test_clarification_accuracy_checker() -> None:
    """Verify clarification detection for ambiguous and clear prompts across natural inquiry patterns."""
    # When clarification is expected (positive cases)
    clarification_responses = [
        "Could you please clarify what authentication criteria should be checked?",
        "What specific criteria or behavior is required for the authentication logic to be considered better and the user verification flow to be updated?",
        "What criteria should be used to validate credentials?",
        "What requirements must be satisfied for the user verification flow?",
        "Which criteria or provider should be used for authentication?",
        "Can you specify the provider and protocol required?",
        "Could you provide more details regarding the verification steps?",
        "Please provide more information on the expected error handling.",
        "What behavior is required when token validation fails?",
        "What is the expected output for unauthenticated users?",
    ]
    for resp in clarification_responses:
        assert check_clarification_accuracy(should_request=True, completion=resp) is True, f"Failed on: {resp}"

    # When clarification is expected but model gave irrelevant/non-clarification responses (negative cases)
    irrelevant_responses = [
        "def authenticate(token): return True",
        "Why is the sky blue?",
        "I cannot do this task because it is too hard.",
        "Error: authentication module not found.",
        "Here is some information about security in Python.",
        "",
        "   ",
    ]
    for resp in irrelevant_responses:
        assert check_clarification_accuracy(should_request=True, completion=resp) is False, f"Should reject: {resp}"

    # When clarification is NOT expected (normal coding task)
    blind_guess_response = "def authenticate(token): return True"
    assert check_clarification_accuracy(should_request=False, completion=blind_guess_response) is True
    assert check_clarification_accuracy(should_request=False, completion=clarification_responses[0]) is False


def test_instruction_followed_ai_docs_comments() -> None:
    """Verify AI-DOCS recognizes docstrings, line comments, and block comments as valid documentation."""
    assert check_instruction_followed("AI-DOCS", '"""Google style docstring.\nArgs: x\nReturns: y"""') is True
    assert check_instruction_followed("AI-DOCS", "# Compute and return the total sum of prices") is True
    assert check_instruction_followed("AI-DOCS", "// Calculate total tax based on subtotal") is True
    assert check_instruction_followed("AI-DOCS", "/* Process incoming request payload */") is True
    assert check_instruction_followed("AI-DOCS", "x = 42") is False


def test_runner_clarification_syntax_decoupling() -> None:
    """Verify runner does not penalize text-based clarification responses with invalid Python syntax."""
    # Mock client that returns a clean clarification question without code fences
    question_client = MockModelClient(
        default_response="What specific criteria or behavior is required for the authentication flow?"
    )
    runner = EvaluationRunner(question_client)
    case = EvaluationCase(
        id="eval-clarif-test",
        instruction_type="AI-TASK",
        description="Clarification request test",
        prompt="Make auth better",
        expected_output="What specific criteria or behavior is required?",
        should_request_clarification=True,
    )

    report = runner.evaluate_cases([case])
    assert report.total_cases == 1
    res = report.item_results[0]
    assert res.clarification_correct is True
    assert res.instruction_followed is True
    # Syntax must be valid because omitting code fences is the expected behavior for clarification
    assert res.syntax_valid is True
    assert res.error is None


def test_diff_hygiene_score() -> None:
    """Verify hygiene score measures focus and penalizes bloat."""
    expected = "def add(a, b):\n    return a + b"
    identical = "def add(a, b):\n    return a + b"
    bloated = "\n".join([f"# Bloat line {i}\nx = {i}" for i in range(50)])

    high_score = calculate_diff_hygiene(expected, identical)
    assert high_score == 1.0

    low_score = calculate_diff_hygiene(expected, bloated)
    assert low_score < 0.2


def test_run_validation_test_safely() -> None:
    """Verify in-memory execution of proposed code and unit test assertions."""
    # Passing test
    code = "def double(x):\n    return x * 2"
    test_code = "def test_double():\n    assert double(3) == 6"
    assert run_validation_test_safely(code, test_code) is True

    # Failing assertion
    bad_code = "def double(x):\n    return x + 1"
    assert run_validation_test_safely(bad_code, test_code) is False

    # Syntax error
    assert run_validation_test_safely("def broken(: pass", test_code) is False


def test_evaluation_runner_with_mock_client() -> None:
    """Verify EvaluationRunner executes cases deterministically with MockModelClient."""
    mock_client = MockModelClient()

    cases = [
        EvaluationCase(
            id="case-1",
            instruction_type="AI-FIX",
            description="Fix zero division",
            prompt="Please fix division by zero",
            expected_output="def div(a, b):\n    if b == 0: return 0.0\n    return a / b",
            validation_test="def test_div(): assert div(4, 2) == 2",
        ),
        EvaluationCase(
            id="case-2",
            instruction_type="AI-TASK",
            description="Ambiguous request",
            prompt="Make it better",
            expected_output="Could you clarify?",
            should_request_clarification=True,
            clarification_prompt="Could you clarify?",
        ),
    ]

    runner = EvaluationRunner(mock_client)
    report = runner.evaluate_cases(cases)

    assert report.total_cases == 2
    assert report.instruction_following_rate >= 0.0
    assert report.syntax_validity_rate >= 0.0
    assert len(report.item_results) == 2

    # Verify markdown rendering
    md = report.to_markdown()
    assert "# CodePilot AI — Base Model Evaluation Report" in md
    assert "Overall Scorecard" in md
    assert "Resource & Latency Performance" in md


# ---------------------------------------------------------------------------
# Phase 12: Robust Extraction & Eight-Category Deterministic Evaluation Tests
# ---------------------------------------------------------------------------

class TestCodeExtractionFencesAndPreambles:
    """Tests for code extraction robustness across closed, unclosed, and preambled completions."""

    def test_unclosed_python_fence_at_eof(self) -> None:
        """Extracts code block when model omits trailing triple backticks."""
        raw = "```python\ndef add(a, b):\n    return a + b"
        extracted = extract_code_from_completion(raw)
        assert extracted == "def add(a, b):\n    return a + b"

    def test_unclosed_python_fence_with_crlf_windows_newlines(self) -> None:
        """Handles Windows CRLF line endings in unclosed code fences."""
        raw = "```python\r\ndef add(a, b):\r\n    return a + b"
        extracted = extract_code_from_completion(raw)
        assert "def add(a, b):" in extracted
        assert "return a + b" in extracted

    def test_preamble_and_postscript_around_closed_fence(self) -> None:
        """Extracts code block while discarding conversational preamble and postscript."""
        raw = (
            "### Summary of Intent\n"
            "This fixes the issue by returning the sum.\n\n"
            "### Proposed Modification\n"
            "```python\n"
            "return a + b\n"
            "```\n\n"
            "### Assumptions\n"
            "Assumes a and b are integers."
        )
        extracted = extract_code_from_completion(raw)
        assert extracted == "return a + b"
        assert "Summary of Intent" not in extracted
        assert "Assumptions" not in extracted

    def test_preamble_before_unclosed_fence(self) -> None:
        """Extracts code even if preamble precedes an unclosed/truncated code fence."""
        raw = (
            "### Summary of Intent\n"
            "We will implement the addition function.\n\n"
            "### Proposed Code Modification\n"
            "```python\n"
            "def add_numbers(a: int, b: int) -> int:\n"
            "    return a + b"
        )
        extracted = extract_code_from_completion(raw)
        assert "def add_numbers" in extracted
        assert "return a + b" in extracted
        assert "Summary of Intent" not in extracted

    def test_unclosed_diff_fence(self) -> None:
        """Extracts unified diff from an unclosed diff code fence."""
        raw = "```diff\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-pass\n+return a + b"
        extracted = extract_code_from_completion(raw)
        assert "--- a/calc.py" in extracted
        assert "+return a + b" in extracted

    def test_conversational_text_without_fences_returns_empty(self) -> None:
        """Does NOT return explanatory conversational text when no code fences exist."""
        raw = (
            "### Summary of Intent\n"
            "The task is to implement the addition function in math_service.py.\n"
            "The function takes two integers as input and returns their sum."
        )
        # Must return empty string to prevent conversational prose from polluting code
        extracted = extract_code_from_completion(raw)
        assert extracted == ""

    def test_pure_raw_code_without_fences_is_extracted(self) -> None:
        """Extracts valid raw Python statements or functions when emitted without markdown fences."""
        assert extract_code_from_completion("return a + b") == "return a + b"
        assert extract_code_from_completion("def add(a, b):\n    return a + b") == "def add(a, b):\n    return a + b"

    def test_empty_and_whitespace_completion_returns_empty(self) -> None:
        """Empty or whitespace-only model completions return empty string."""
        assert extract_code_from_completion("") == ""
        assert extract_code_from_completion("   \n\t   ") == ""


class TestPhase12EightCategoryBenchmark:
    """Deterministic evaluation benchmark covering the 8 required quality categories without code execution."""

    def test_category_1_basic_function_implementation(self) -> None:
        """Category 1: Basic function implementation syntax and diff hygiene."""
        expected = "return a + b"
        model_out = "```python\nreturn a + b\n```"
        extracted = extract_code_from_completion(model_out)
        assert check_syntax_validity(f"def add(a, b):\n    {extracted}") is True
        assert calculate_diff_hygiene(expected, extracted) == 1.0

    def test_category_2_bug_fixing(self) -> None:
        """Category 2: Bug fixing off-by-one boundary condition."""
        expected = "for i in range(len(prices)):"
        model_out = "```python\nfor i in range(len(prices)):\n```"
        extracted = extract_code_from_completion(model_out)
        assert check_syntax_validity(f"def scan(prices):\n    {extracted}\n        pass") is True
        assert calculate_diff_hygiene(expected, extracted) == 1.0

    def test_category_3_missing_return_statements(self) -> None:
        """Category 3: Missing return statement addition."""
        expected = "return total / count"
        model_out = "```python\nreturn total / count\n```"
        extracted = extract_code_from_completion(model_out)
        assert check_syntax_validity(f"def avg(total, count):\n    {extracted}") is True
        assert calculate_diff_hygiene(expected, extracted) == 1.0

    def test_category_4_boundary_conditions(self) -> None:
        """Category 4: Boundary conditions (empty list check)."""
        expected = "if not items:\n    return None\nreturn items[0]"
        model_out = "```python\nif not items:\n    return None\nreturn items[0]\n```"
        extracted = extract_code_from_completion(model_out)
        indented = textwrap.indent(extracted, "    ")
        assert check_syntax_validity(f"def first(items):\n{indented}") is True
        assert calculate_diff_hygiene(expected, extracted) >= 0.95

    def test_category_5_existing_function_body_modification(self) -> None:
        """Category 5: Modifying existing body with nested indentation."""
        expected = "if val > 0:\n    return val * 2\nreturn 0"
        model_out = (
            "```python\n"
            "def process(val):\n"
            "    if val > 0:\n"
            "        return val * 2\n"
            "    return 0\n"
            "```"
        )
        extracted = extract_code_from_completion(model_out)
        from codepilot.core.fixer import unwrap_nested_function_body
        unwrapped = unwrap_nested_function_body(extracted, target_symbol="process")
        assert unwrapped == "if val > 0:\n    return val * 2\nreturn 0"
        indented = textwrap.indent(unwrapped, "    ")
        assert check_syntax_validity(f"def process(val):\n{indented}") is True

    def test_category_6_malformed_or_empty_model_output(self) -> None:
        """Category 6: Malformed or empty output is rejected safely."""
        assert extract_code_from_completion("") == ""
        assert extract_code_from_completion("```python\ndef broken(:\n```") == "def broken(:"
        assert check_syntax_validity(extract_code_from_completion("```python\ndef broken(:\n```")) is False

    def test_category_7_unclear_or_conflicting_instructions(self) -> None:
        """Category 7: Unclear instructions trigger clarification detection."""
        ambiguous_completion = "Could you please clarify what authentication criteria should be checked?"
        assert check_clarification_accuracy(should_request=True, completion=ambiguous_completion) is True
        clear_completion = "```python\nreturn user.is_authenticated\n```"
        assert check_clarification_accuracy(should_request=False, completion=clear_completion) is True

    def test_category_8_security_sensitive_and_unauthorized_changes(self, tmp_path: Path) -> None:
        """Category 8: Security-sensitive files and path traversal are rejected."""
        from codepilot.core.security import validate_target_path, SecurityError
        cfg = CodePilotConfig(project_root=tmp_path)

        # 1. Reject path traversal
        with pytest.raises(SecurityError, match="Path traversal detected"):
            validate_target_path("../../outside.py", tmp_path, cfg)

        # 2. Reject protected file enclave (.env)
        with pytest.raises(SecurityError, match="matches protected file pattern"):
            validate_target_path(".env", tmp_path, cfg)


class TestEvaluationRunnerContextAndValidationDenominator:
    """Tests for EvaluationRunner prompt context construction and validation test denominator handling."""

    def test_runner_supplies_source_code_in_untrusted_context(self) -> None:
        """Verify runner formats prompt with <untrusted_repository_context> when source_code is provided."""
        captured_requests = []

        def recording_handler(req: ModelRequest) -> str:
            captured_requests.append(req)
            return "```python\ndef add(a, b): return a + b\n```"

        client = MockModelClient(response_handler=recording_handler)
        case = EvaluationCase(
            id="test-ctx-1",
            instruction_type="AI-FIX",
            description="Fix addition",
            prompt="Return sum of a and b",
            source_code="def add(a, b):\n    pass\n",
            expected_output="def add(a, b):\n    return a + b",
            validation_test="def test_add(): assert add(1, 2) == 3",
        )

        runner = EvaluationRunner(client)
        report = runner.evaluate_cases([case])

        assert len(captured_requests) == 1
        sent_prompt = captured_requests[0].prompt
        assert "### Task Instruction" in sent_prompt
        assert "Return sum of a and b" in sent_prompt
        assert "### Source Code to Modify" in sent_prompt
        assert "<untrusted_repository_context>" in sent_prompt
        assert "def add(a, b):\n    pass" in sent_prompt
        assert "</untrusted_repository_context>" in sent_prompt
        assert report.test_pass_rate == 1.0

    def test_runner_empty_source_code_uses_raw_prompt(self) -> None:
        """Verify runner falls back to case.prompt when source_code is empty or whitespace."""
        captured_requests = []

        def recording_handler(req: ModelRequest) -> str:
            captured_requests.append(req)
            return "```python\ndef f(): return 1\n```"

        client = MockModelClient(response_handler=recording_handler)
        case = EvaluationCase(
            id="test-ctx-2",
            instruction_type="AI-TASK",
            description="Implement f",
            prompt="Implement function f",
            source_code="",
            expected_output="def f(): return 1",
        )

        runner = EvaluationRunner(client)
        runner.evaluate_cases([case])

        assert len(captured_requests) == 1
        assert captured_requests[0].prompt == "Implement function f"

    def test_runner_empty_extraction_counts_as_test_failure_in_denominator(self) -> None:
        """Verify that when extraction produces no code, test_passed is False and included in denominator."""
        client = MockModelClient(default_response="### Conversational prose without code\nNo code here.")
        case_with_test = EvaluationCase(
            id="test-denom-fail",
            instruction_type="AI-TASK",
            description="Task with test",
            prompt="Implement feature",
            expected_output="def f(): return 42",
            validation_test="def test_f(): assert f() == 42",
        )
        case_without_test = EvaluationCase(
            id="test-denom-na",
            instruction_type="AI-TASK",
            description="Task without test",
            prompt="Implement docs",
            expected_output='"""docstring"""',
            validation_test=None,
        )

        runner = EvaluationRunner(client)
        report = runner.evaluate_cases([case_with_test, case_without_test])

        res_with_test = next(r for r in report.item_results if r.case_id == "test-denom-fail")
        res_without_test = next(r for r in report.item_results if r.case_id == "test-denom-na")

        # Case with test must be recorded as False, not None
        assert res_with_test.extracted_code == ""
        assert res_with_test.test_passed is False

        # Case without test must remain None (N/A)
        assert res_without_test.test_passed is None

        # Denominator must be exactly 1 (the case with test), and pass rate 0.0
        assert report.test_pass_rate == 0.0
        assert report.by_type_breakdown["AI-TASK"]["test_passed"] == 0.0


class TestDiffHygieneCharacteristics:
    """Audit diff hygiene score response to comments, defensive checks, signatures, and bloat."""

    def test_diff_hygiene_identical_code_scores_perfect(self) -> None:
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        assert calculate_diff_hygiene(code, code) == 1.0

    def test_diff_hygiene_with_extra_comments_and_docstrings(self) -> None:
        expected = "def add(a: int, b: int) -> int:\n    return a + b\n"
        with_comments = (
            "def add(a: int, b: int) -> int:\n"
            "    \"\"\"Add two integers together.\"\"\"\n"
            "    # Calculate sum\n"
            "    return a + b\n"
        )
        score = calculate_diff_hygiene(expected, with_comments)
        # Ratio drops due to extra lines, but remains positive (> 0.4 and < 1.0)
        assert 0.4 <= score < 1.0

    def test_diff_hygiene_with_harmless_defensive_type_checks(self) -> None:
        expected = "def add(a: int, b: int) -> int:\n    return a + b\n"
        with_checks = (
            "def add(a: int, b: int) -> int:\n"
            "    if not isinstance(a, int) or not isinstance(b, int):\n"
            "        raise TypeError('Both inputs must be integers')\n"
            "    return a + b\n"
        )
        score = calculate_diff_hygiene(expected, with_checks)
        assert 0.4 <= score < 0.8

    def test_diff_hygiene_with_function_signature_changes(self) -> None:
        expected = "def scan_prices(prices: list) -> int:\n    return -1\n"
        altered_sig = "def scan_prices(prices):\n    return -1\n"
        score = calculate_diff_hygiene(expected, altered_sig)
        # Signature mismatch lowers line ratio
        assert score == 0.5

    def test_diff_hygiene_with_excessive_rewrites_and_bloat(self) -> None:
        expected = "def add(a: int, b: int) -> int:\n    return a + b\n"
        bloated_rewrite = (
            "def add(a: int, b: int) -> int:\n"
            + "\n".join(f"    # Step {i}\n    temp_{i} = {i}" for i in range(25))
            + "\n    return a + b\n"
        )
        score = calculate_diff_hygiene(expected, bloated_rewrite)
        # Bloat penalty should heavily depress score
        assert score < 0.15

    def test_diff_hygiene_empty_or_whitespace_returns_zero(self) -> None:
        assert calculate_diff_hygiene("", "def f(): pass") == 0.0
        assert calculate_diff_hygiene("def f(): pass", "") == 0.0
        assert calculate_diff_hygiene("   ", "   ") == 0.0


class TestClarificationBehavior:
    """Audit clarification decision logic and detection for clear vs ambiguous prompts."""

    def test_clear_implementation_request_proceeds(self) -> None:
        completion = "def add(a: int, b: int) -> int:\n    return a + b"
        # Should NOT request clarification on clear tasks
        assert check_clarification_accuracy(should_request=False, completion=completion) is True

    def test_materially_ambiguous_request_requests_clarification(self) -> None:
        completion = "Could you please clarify what authentication criteria or provider should be used?"
        # Should request clarification on ambiguous tasks
        assert check_clarification_accuracy(should_request=True, completion=completion) is True

    def test_request_with_sufficient_information_proceeds(self) -> None:
        completion = "```python\ndef get_first_item(items: list):\n    if not items:\n        return None\n    return items[0]\n```"
        assert check_clarification_accuracy(should_request=False, completion=completion) is True

    def test_safe_explicit_assumption_proceeds_without_unneeded_loop(self) -> None:
        completion = (
            "# Assumption: default encoding is UTF-8\n"
            "def read_file(path: str) -> str:\n"
            "    return open_file(path, encoding='utf-8')\n"
        )
        # Stating safe assumption without asking clarification satisfies task completion
        assert check_clarification_accuracy(should_request=False, completion=completion) is True


class TestValidationExecutionSecurity:
    """Verify security restrictions and error handling in run_validation_test_safely."""

    def test_restricted_builtin_open_blocked(self) -> None:
        code = "def malicious():\n    return open('test.txt', 'w')"
        test_code = "def test_malicious():\n    malicious()"
        assert run_validation_test_safely(code, test_code) is False

    def test_restricted_builtin_import_blocked(self) -> None:
        code = "def malicious():\n    os = __import__('os')\n    return os.listdir('.')"
        test_code = "def test_malicious():\n    malicious()"
        assert run_validation_test_safely(code, test_code) is False

    def test_restricted_builtin_eval_blocked(self) -> None:
        code = "def dynamic_exec():\n    return eval('1 + 1')"
        test_code = "def test_dynamic():\n    assert dynamic_exec() == 2"
        assert run_validation_test_safely(code, test_code) is False

    def test_runtime_exception_handled_safely(self) -> None:
        code = "def crash():\n    return 1 / 0"
        test_code = "def test_crash():\n    crash()"
        assert run_validation_test_safely(code, test_code) is False


class TestContextDelimiterStrippingAndSyntaxValidity:
    """Verify narrow prompt delimiter tag stripping and syntax validity preservation."""

    def test_valid_python_without_tags_remains_unchanged(self) -> None:
        raw = "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```"
        extracted = extract_code_from_completion(raw)
        assert extracted == "def add(a: int, b: int) -> int:\n    return a + b"
        assert check_syntax_validity(extracted) is True

    def test_opening_context_tag_echoed_inside_fence_handled(self) -> None:
        raw = "```python\n<untrusted_repository_context>\ndef add(a: int, b: int) -> int:\n    return a + b\n```"
        extracted = extract_code_from_completion(raw)
        assert extracted == "def add(a: int, b: int) -> int:\n    return a + b"
        assert check_syntax_validity(extracted) is True

    def test_closing_context_tag_echoed_inside_fence_handled(self) -> None:
        raw = "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n</untrusted_repository_context>\n```"
        extracted = extract_code_from_completion(raw)
        assert extracted == "def add(a: int, b: int) -> int:\n    return a + b"
        assert check_syntax_validity(extracted) is True

    def test_both_tags_echoed_around_valid_python_handled(self) -> None:
        raw = (
            "```python\n"
            "<untrusted_repository_context>\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
            "</untrusted_repository_context>\n"
            "```"
        )
        extracted = extract_code_from_completion(raw)
        assert extracted == "def add(a: int, b: int) -> int:\n    return a + b"
        assert check_syntax_validity(extracted) is True

    def test_commented_tags_echoed_inside_fence_handled(self) -> None:
        raw = (
            "```python\n"
            "# <untrusted_repository_context>\n"
            "def authenticate_user(token: str) -> bool:\n"
            "    return True\n"
            "# </untrusted_repository_context>\n"
            "```"
        )
        extracted = extract_code_from_completion(raw)
        assert extracted == "def authenticate_user(token: str) -> bool:\n    return True"
        assert check_syntax_validity(extracted) is True

    def test_ordinary_python_strings_and_comments_with_angle_brackets_preserved(self) -> None:
        raw = (
            "```python\n"
            "def check_tags(val: str) -> bool:\n"
            '    expected = "<untrusted_repository_context>"\n'
            "    # Compare if a < b and b > c\n"
            "    return len(val) < 10\n"
            "```"
        )
        extracted = extract_code_from_completion(raw)
        assert 'expected = "<untrusted_repository_context>"' in extracted
        assert "# Compare if a < b and b > c" in extracted
        assert "return len(val) < 10" in extracted
        assert check_syntax_validity(extracted) is True

    def test_malformed_python_unrelated_to_tags_remains_invalid(self) -> None:
        raw = (
            "```python\n"
            "<untrusted_repository_context>\n"
            "def invalid(:\n"
            "    pass\n"
            "</untrusted_repository_context>\n"
            "```"
        )
        extracted = extract_code_from_completion(raw)
        assert extracted == "def invalid(:\n    pass"
        # Genuinely malformed Python must remain invalid
        assert check_syntax_validity(extracted) is False

    def test_existing_markdown_code_fence_extraction_preserved(self) -> None:
        raw_unclosed = "```python\ndef multiply(x: int) -> int:\n    return x * 2"
        extracted = extract_code_from_completion(raw_unclosed)
        assert extracted == "def multiply(x: int) -> int:\n    return x * 2"
        assert check_syntax_validity(extracted) is True

        # Conversational text without code still returns empty string
        conversational = "I suggest creating a function that adds two numbers."
        assert extract_code_from_completion(conversational) == ""

    def test_repository_context_continues_to_reach_model_request(self) -> None:
        captured_requests = []

        class SpyClient(MockModelClient):
            def generate(self, req, model_name=None):
                captured_requests.append(req)
                return super().generate(req, model_name)

        client = SpyClient()
        runner = EvaluationRunner(client)
        case = EvaluationCase(
            id="test-spy",
            instruction_type="AI-TASK",
            description="Test spy",
            prompt="Implement f",
            source_code="def f(): pass",
            expected_output="def f(): return 1",
        )
        runner.evaluate_cases([case])

        assert len(captured_requests) == 1
        assert "<untrusted_repository_context>" in captured_requests[0].prompt
        assert "def f(): pass" in captured_requests[0].prompt

    def test_security_sensitive_and_clarification_behavior_not_regressed(self) -> None:
        # Clarification accuracy logic preserved
        assert check_clarification_accuracy(should_request=True, completion="Could you please clarify?") is True
        assert check_clarification_accuracy(should_request=False, completion="def f(): pass") is True

        # Restricted execution blocked
        assert run_validation_test_safely("import os; os.listdir('.')", "assert True") is False
        assert run_validation_test_safely("open('secret.txt')", "assert True") is False

    def test_v2_real_model_raw_completions_pass_syntax_after_fix(self) -> None:
        """Verify that the actual v2 model completions pass syntax checking with the narrow delimiter stripping fix."""
        v2_json_path = Path("reports/evaluation_qwen_1.5b_cpu_v2.json")
        if not v2_json_path.exists():
            return
        data = json.loads(v2_json_path.read_text(encoding="utf-8"))
        for item in data.get("item_results", []):
            cid = item["case_id"]
            if cid in [
                "eval-01-basic-function",
                "eval-02-bug-fixing",
                "eval-03-missing-return",
                "eval-04-boundary-conditions",
                "eval-05-existing-body-modification",
                "eval-06-malformed-empty-output",
                "eval-08-security-sensitive",
            ]:
                extracted = extract_code_from_completion(item["raw_completion"])
                assert check_syntax_validity(extracted) is True, f"Failed syntax for {cid}: {extracted}"


class TestStageLDiffHygieneAndClarificationStrengthening:
    """Tests verifying diff hygiene scoring and clarification routing improvements."""

    def test_clarification_accuracy_rejects_speculative_code_on_ambiguous_request(self) -> None:
        """Ambiguous auth task: speculative code implementation fails clarification accuracy."""
        speculative_code = (
            "```python\n"
            "def authenticate_user(token: str) -> bool:\n"
            '    if not token: raise ValueError("Token cannot be empty")\n'
            "    return True\n"
            "```"
        )
        assert check_clarification_accuracy(should_request=True, completion=speculative_code) is False

    def test_clarification_accuracy_accepts_proper_question(self) -> None:
        """Ambiguous auth task: explicit clarification question passes clarification accuracy."""
        question = "Could you please clarify what authentication criteria or provider should be used?"
        assert check_clarification_accuracy(should_request=True, completion=question) is True

    def test_diff_hygiene_minimal_implementation_scores_higher_than_defensive_checks(self) -> None:
        """Verify that a minimal implementation scores higher diff hygiene than unrequested defensive type checks."""
        expected = "def add(a: int, b: int) -> int:\n    return a + b"
        minimal = "def add(a: int, b: int) -> int:\n    return a + b"
        bloated = (
            "def add(a: int, b: int) -> int:\n"
            "    # Defensive validation to ensure inputs are integers\n"
            "    if not isinstance(a, int) or not isinstance(b, int):\n"
            '        raise ValueError("Both arguments must be integers")\n'
            "    # Calculate the sum\n"
            "    return a + b"
        )
        minimal_score = calculate_diff_hygiene(expected, minimal)
        bloated_score = calculate_diff_hygiene(expected, bloated)

        assert minimal_score == 1.0
        assert bloated_score <= 0.6
        assert minimal_score > bloated_score






