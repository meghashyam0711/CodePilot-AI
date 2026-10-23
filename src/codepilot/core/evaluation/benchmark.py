"""Reusable default benchmark test cases for CodePilot AI model evaluation.

Defines the eight canonical evaluation cases corresponding to the Phase 12
benchmark categories, providing deterministic test fixtures for local coding models.
"""

from typing import List

from codepilot.core.evaluation.models import EvaluationCase


def get_default_benchmark_cases() -> List[EvaluationCase]:
    """Return eight deterministic benchmark fixtures covering canonical evaluation categories."""
    return [
        EvaluationCase(
            id="eval-01-basic-function",
            instruction_type="AI-TASK",
            description="Basic function implementation: add two numbers.",
            prompt=(
                "Please implement the function 'add(a: int, b: int) -> int' "
                "that returns the sum of a and b."
            ),
            source_code="def add(a: int, b: int) -> int:\n    pass\n",
            expected_output="def add(a: int, b: int) -> int:\n    return a + b\n",
            validation_test="def test_add():\n    assert add(2, 3) == 5\n    assert add(-1, 1) == 0\n",
            should_request_clarification=False,
            metadata={"category": "basic_function_implementation", "benchmark_index": 1},
        ),
        EvaluationCase(
            id="eval-02-bug-fixing",
            instruction_type="AI-FIX",
            description="Bug fixing: correct off-by-one boundary condition in list scan.",
            prompt=(
                "Fix the off-by-one error in 'scan_prices' so it iterates over the "
                "entire list without skipping the final element."
            ),
            source_code=(
                "def scan_prices(prices: list) -> int:\n"
                "    for i in range(len(prices) - 1):\n"
                "        if prices[i] < 0:\n"
                "            return i\n"
                "    return -1\n"
            ),
            expected_output=(
                "def scan_prices(prices: list) -> int:\n"
                "    for i in range(len(prices)):\n"
                "        if prices[i] < 0:\n"
                "            return i\n"
                "    return -1\n"
            ),
            validation_test=(
                "def test_scan():\n"
                "    assert scan_prices([10, 20, -5]) == 2\n"
                "    assert scan_prices([10, 20, 30]) == -1\n"
            ),
            should_request_clarification=False,
            metadata={"category": "bug_fixing", "benchmark_index": 2},
        ),
        EvaluationCase(
            id="eval-03-missing-return",
            instruction_type="AI-FIX",
            description="Missing return statement: ensure computed average is returned.",
            prompt=(
                "Fix 'compute_average' by returning total / count when count > 0, "
                "otherwise return 0.0."
            ),
            source_code=(
                "def compute_average(total: float, count: int) -> float:\n"
                "    if count == 0:\n"
                "        return 0.0\n"
                "    result = total / count\n"
            ),
            expected_output=(
                "def compute_average(total: float, count: int) -> float:\n"
                "    if count == 0:\n"
                "        return 0.0\n"
                "    return total / count\n"
            ),
            validation_test=(
                "def test_avg():\n"
                "    assert compute_average(10.0, 2) == 5.0\n"
                "    assert compute_average(0.0, 0) == 0.0\n"
            ),
            should_request_clarification=False,
            metadata={"category": "missing_return_statements", "benchmark_index": 3},
        ),
        EvaluationCase(
            id="eval-04-boundary-conditions",
            instruction_type="AI-FIX",
            description="Boundary conditions: safely handle empty collections returning None.",
            prompt=(
                "Handle the empty list boundary condition in 'get_first_item' "
                "by returning None when the list is empty."
            ),
            source_code=(
                "def get_first_item(items: list):\n"
                "    return items[0]\n"
            ),
            expected_output=(
                "def get_first_item(items: list):\n"
                "    if not items:\n"
                "        return None\n"
                "    return items[0]\n"
            ),
            validation_test=(
                "def test_first():\n"
                "    assert get_first_item([]) is None\n"
                "    assert get_first_item([42]) == 42\n"
            ),
            should_request_clarification=False,
            metadata={"category": "boundary_conditions", "benchmark_index": 4},
        ),
        EvaluationCase(
            id="eval-05-existing-body-modification",
            instruction_type="AI-TASK",
            description="Existing function body modification: double positive values, return 0 otherwise.",
            prompt=(
                "Modify the body of 'process_number' to return val * 2 if val > 0, "
                "otherwise return 0."
            ),
            source_code=(
                "def process_number(val: int) -> int:\n"
                "    return val\n"
            ),
            expected_output=(
                "def process_number(val: int) -> int:\n"
                "    if val > 0:\n"
                "        return val * 2\n"
                "    return 0\n"
            ),
            validation_test=(
                "def test_process():\n"
                "    assert process_number(5) == 10\n"
                "    assert process_number(-3) == 0\n"
                "    assert process_number(0) == 0\n"
            ),
            should_request_clarification=False,
            metadata={"category": "existing_function_body_modification", "benchmark_index": 5},
        ),
        EvaluationCase(
            id="eval-06-malformed-empty-output",
            instruction_type="AI-TASK",
            description="Malformed/empty output resilience: implement safe integer conversion with fallback.",
            prompt=(
                "Implement 'parse_integer_safe(val: str, default: int = 0) -> int' "
                "returning default on error."
            ),
            source_code="def parse_integer_safe(val: str, default: int = 0) -> int:\n    pass\n",
            expected_output=(
                "def parse_integer_safe(val: str, default: int = 0) -> int:\n"
                "    try:\n"
                "        return int(val)\n"
                "    except (ValueError, TypeError):\n"
                "        return default\n"
            ),
            validation_test=(
                "def test_parse():\n"
                "    assert parse_integer_safe('123') == 123\n"
                "    assert parse_integer_safe('invalid', -1) == -1\n"
            ),
            should_request_clarification=False,
            metadata={"category": "malformed_or_empty_model_output", "benchmark_index": 6},
        ),
        EvaluationCase(
            id="eval-07-clarification-request",
            instruction_type="AI-TASK",
            description="Clarification request handling: detect ambiguous instructions requiring clarification.",
            prompt="Make the authentication logic better and update the user verification flow accordingly.",
            source_code="def authenticate_user(token: str) -> bool:\n    return True\n",
            expected_output="Could you please clarify what authentication criteria or provider should be used?",
            validation_test=None,
            should_request_clarification=True,
            clarification_prompt="Could you please clarify what authentication criteria or provider should be used?",
            metadata={"category": "unclear_or_conflicting_instructions", "benchmark_index": 7},
        ),
        EvaluationCase(
            id="eval-08-security-sensitive",
            instruction_type="AI-TASK",
            description="Security-sensitive changes: sanitize filename to prevent directory traversal.",
            prompt=(
                "Implement 'sanitize_filename(filename: str) -> str' that safely extracts the basename "
                "and removes path traversal sequences ('..') and directory separators ('/' and '\\') without importing external modules."
            ),
            source_code="def sanitize_filename(filename: str) -> str:\n    pass\n",
            expected_output=(
                "def sanitize_filename(filename: str) -> str:\n"
                "    clean = filename.replace('\\\\', '/').split('/')[-1]\n"
                "    return clean.replace('..', '')\n"
            ),
            validation_test=(
                "def test_sanitize():\n"
                "    assert sanitize_filename('../secret.txt') == 'secret.txt'\n"
                "    assert sanitize_filename('..\\\\secret.txt') == 'secret.txt'\n"
                "    assert sanitize_filename('../../etc/passwd') == 'passwd'\n"
                "    assert sanitize_filename('..\\\\..\\\\windows\\\\system32') == 'system32'\n"
                "    assert '..' not in sanitize_filename('../..')\n"
            ),
            should_request_clarification=False,
            metadata={"category": "security_sensitive_and_unauthorized_changes", "benchmark_index": 8},
        ),

    ]
