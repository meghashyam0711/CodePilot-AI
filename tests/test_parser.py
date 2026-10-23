"""Automated tests for AI instruction comment parser (Phase 3)."""

import json
from pathlib import Path
from typer.testing import CliRunner

from codepilot.cli import app
from codepilot.core.parser import (
    InstructionCommentParser,
    InstructionParserEngine,
    InstructionTask,
    InstructionType,
    ModelTaskContext,
    ParseResult,
    TaskContextBuilder,
    parse_file,
    parse_repository,
)
from codepilot.core.parser.symbol_resolver import SymbolResolver


def test_supported_instruction_types(tmp_path: Path) -> None:
    """Verify parser recognizes all 5 supported instruction types: FIX, TASK, REFACTOR, TEST, DOCS."""
    code = """
# AI-FIX: Handle empty lists safely.
def calculate_average(numbers):
    return sum(numbers) / len(numbers)

# AI-TASK: Add input validation to this function.
def process_user(data):
    pass

# AI-REFACTOR: Extract repeated logic into helper.
def format_records(records):
    pass

# AI-TEST: Generate edge-case unit tests.
def parse_date(date_str):
    pass

# AI-DOCS: Add comprehensive docstring with examples.
def serialize_payload(obj):
    pass
"""
    py_file = tmp_path / "math_utils.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, warnings = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 5

    types = [t.instruction_type for t in tasks]
    assert "AI-FIX" in types
    assert "AI-TASK" in types
    assert "AI-REFACTOR" in types
    assert "AI-TEST" in types
    assert "AI-DOCS" in types

    fix_task = next(t for t in tasks if t.instruction_type == "AI-FIX")
    assert fix_task.description == "Handle empty lists safely."
    assert fix_task.target_symbol == "calculate_average"
    assert fix_task.target_symbol_type == "function"

    task_task = next(t for t in tasks if t.instruction_type == "AI-TASK")
    assert task_task.description == "Add input validation to this function."
    assert task_task.target_symbol == "process_user"


def test_case_insensitive_comment_matching(tmp_path: Path) -> None:
    """Verify that instruction matching works case-insensitively."""
    code = """
# ai-fix: lowercase prefix
def foo(): pass

# Ai-Task: capitalized prefix
def bar(): pass

// ai-docs: javascript style
function jsFunc() {}
"""
    py_file = tmp_path / "case.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) >= 2
    types = [t.instruction_type for t in tasks]
    assert "AI-FIX" in types
    assert "AI-TASK" in types


def test_symbol_association_functions_classes_methods(tmp_path: Path) -> None:
    """Verify symbol resolver maps comments to preceding and enclosing functions, methods, and classes."""
    code = """
# AI-TASK: Module level setup instruction

# AI-TASK: Class definition task
class DataProcessor:
    \"\"\"Docstring.\"\"\"
    def __init__(self):
        # AI-FIX: Initialize database pool safely
        self.db = None

    # AI-REFACTOR: Optimize data transformation
    def transform(self, items):
        # AI-DOCS: Document transform algorithm
        return [x * 2 for x in items]

# AI-TEST: Test isolated helper
def helper_func():
    pass
"""
    py_file = tmp_path / "processor.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 6

    # 1. Module-level comment
    mod_task = next(t for t in tasks if "Module level setup" in t.description)
    assert mod_task.target_symbol == "processor"
    assert mod_task.target_symbol_type == "module"

    # 2. Preceding class definition
    cls_task = next(t for t in tasks if "Class definition task" in t.description)
    assert cls_task.target_symbol == "DataProcessor"
    assert cls_task.target_symbol_type == "class"

    # 3. Inside method __init__
    init_task = next(t for t in tasks if "Initialize database pool" in t.description)
    assert init_task.target_symbol == "DataProcessor.__init__"
    assert init_task.target_symbol_type == "method"

    # 4. Preceding method transform
    trans_task = next(t for t in tasks if "Optimize data transformation" in t.description)
    assert trans_task.target_symbol == "DataProcessor.transform"
    assert trans_task.target_symbol_type == "method"

    # 5. Inside method transform
    doc_task = next(t for t in tasks if "Document transform algorithm" in t.description)
    assert doc_task.target_symbol == "DataProcessor.transform"
    assert doc_task.target_symbol_type == "method"

    # 6. Preceding helper_func
    helper_task = next(t for t in tasks if "Test isolated helper" in t.description)
    assert helper_task.target_symbol == "helper_func"
    assert helper_task.target_symbol_type == "function"


def test_strings_and_docstrings_ignored_without_false_positives(tmp_path: Path) -> None:
    """Verify that AI instruction markers inside strings and docstrings are strictly ignored."""
    code = '''
# AI-FIX: Real instruction 1
query = "# AI-FIX: Do not parse this string literal"
raw_data = """
# AI-TASK: Do not parse multiline string
def fake(): pass
"""

def valid_func():
    """
    Example usage:
    # AI-REFACTOR: Do not parse docstring comment
    """
    # AI-DOCS: Real instruction 2
    return 42
'''
    py_file = tmp_path / "strings.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    descriptions = [t.description for t in tasks]

    assert len(tasks) == 2
    assert "Real instruction 1" in descriptions
    assert "Real instruction 2" in descriptions
    assert not any("Do not parse" in d for d in descriptions)


def test_malformed_and_missing_descriptions(tmp_path: Path) -> None:
    """Verify handling of missing descriptions and malformed comments."""
    code = """
# AI-FIX:
def empty_fix(): pass

# AI-TASK: Valid task
def valid_task(): pass
"""
    py_file = tmp_path / "malformed.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, warnings = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 2

    empty_t = next(t for t in tasks if t.line_number == 2)
    assert empty_t.is_malformed is True
    assert empty_t.warning is not None
    assert "Missing description" in empty_t.warning


def test_multiline_continuation_comments(tmp_path: Path) -> None:
    """Verify that consecutive comment lines starting with bullet points or indentation are captured."""
    code = """
# AI-TASK: Implement user authentication workflow.
# - Validate email and password inputs
# - Hash password using bcrypt
# - Return signed JWT token
def login(username, password):
    pass
"""
    py_file = tmp_path / "auth.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 1

    task = tasks[0]
    assert task.target_symbol == "login"
    assert len(task.multiline_lines) == 3
    assert "Validate email" in task.description
    assert "Return signed JWT" in task.description


def test_duplicate_instructions_deduplication(tmp_path: Path) -> None:
    """Verify duplicate instructions at same line/content are not duplicated."""
    code = """
# AI-FIX: Fix error
# AI-FIX: Fix error
def broken(): pass
"""
    py_file = tmp_path / "dup.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 2  # Different line numbers are distinct


def test_cross_language_comments(tmp_path: Path) -> None:
    """Verify comment parsing across JS, TS, HTML, and C/C++ style comments."""
    js_code = """
// AI-FIX: Validate request payload
function handleRequest(req, res) {}

/* AI-REFACTOR: Extract router */
const router = {};
"""
    (tmp_path / "server.js").write_text(js_code, encoding="utf-8")

    html_code = """
<!-- AI-DOCS: Add SEO metadata description -->
<meta name="description" content="CodePilot">
"""
    (tmp_path / "index.html").write_text(html_code, encoding="utf-8")

    result = parse_repository(tmp_path)
    assert result.scanned_files_count == 2
    assert len(result.tasks) == 3

    types = [t.instruction_type for t in result.tasks]
    assert "AI-FIX" in types
    assert "AI-REFACTOR" in types
    assert "AI-DOCS" in types


def test_cli_tasks_command(cli_runner: CliRunner, tmp_path: Path) -> None:
    """Verify CLI 'codepilot tasks' displays Rich table with counts and symbols."""
    code = """
# AI-FIX: Handle divide by zero
def divide(a, b):
    return a / b

# AI-TASK: Add multiplication function
def multiply(a, b):
    pass
"""
    (tmp_path / "math_service.py").write_text(code, encoding="utf-8")

    # Standard visual table
    res = cli_runner.invoke(app, ["tasks", "--directory", str(tmp_path)])
    assert res.exit_code == 0
    assert "Tasks Overview" in res.output
    assert "math_service.py" in res.output
    assert "divide" in res.output
    assert "multiply" in res.output
    assert "AI-FIX" in res.output
    assert "AI-TASK" in res.output
    assert "Instruction Counts by Type" in res.output

    # JSON output
    res_json = cli_runner.invoke(app, ["tasks", "--directory", str(tmp_path), "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert "tasks" in data
    assert len(data["tasks"]) == 2
    assert data["total_tasks"] == 2
    assert "counts_by_type" in data
    assert data["counts_by_type"].get("AI-FIX") == 1
    assert data["counts_by_type"].get("AI-TASK") == 1

    # Filtered by type
    res_filtered = cli_runner.invoke(app, ["tasks", "--directory", str(tmp_path), "--type", "AI-FIX"])
    assert res_filtered.exit_code == 0
    assert "AI-FIX" in res_filtered.output
    assert "multiply" not in res_filtered.output


def test_task_context_builder_llm_interface(tmp_path: Path) -> None:
    """Verify TaskContextBuilder generates structured prompt context without invoking models."""
    code = """
# AI-FIX: Prevent index out of bounds
def get_first_element(items):
    return items[0]
"""
    py_file = tmp_path / "array_ops.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, _ = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 1
    task = tasks[0]

    context = TaskContextBuilder.build_task_context(
        task=task,
        project_root=tmp_path,
        token_budget=1500,
    )

    assert isinstance(context, ModelTaskContext)
    assert context.task.id == task.id
    assert context.file_path == "array_ops.py"
    assert context.target_symbol == "get_first_element"
    assert context.is_safe is True
    assert "Line    3: def get_first_element(items):" in context.target_code_snippet
    assert "# CodePilot AI — Modification Task [AI-FIX]" in context.prompt_text
    assert context.estimated_tokens > 0
    assert context.estimated_tokens <= 1500


def test_task_context_builder_excludes_sensitive_files(tmp_path: Path) -> None:
    """Verify TaskContextBuilder refuses to embed secret files into LLM prompt contexts."""
    task = InstructionTask(
        id="task-secret",
        instruction_type="AI-TASK",
        description="Update database password",
        file_path=".env",
        line_number=1,
    )
    (tmp_path / ".env").write_text("DB_PASSWORD=supersecret\n", encoding="utf-8")

    context = TaskContextBuilder.build_task_context(
        task=task,
        project_root=tmp_path,
    )

    assert context.is_safe is False
    assert "sensitive credential" in (context.safety_warning or "")
    assert "supersecret" not in context.target_code_snippet
    assert "supersecret" not in context.prompt_text


def test_instruction_task_data_model_defaults() -> None:
    """Verify InstructionTask can be initialized with spec example kwargs and auto-generates id."""
    task = InstructionTask(
        instruction_type="AI-FIX",
        description="Handle empty lists safely.",
        file_path="src/math_utils.py",
        line_number=2,
        target_symbol="calculate_average",
    )
    assert task.instruction_type == "AI-FIX"
    assert task.description == "Handle empty lists safely."
    assert task.prompt == "Handle empty lists safely."
    assert task.file_path == "src/math_utils.py"
    assert task.line_number == 2
    assert task.target_symbol == "calculate_average"
    assert task.id.startswith("task-")


def test_comment_inside_function_body(tmp_path: Path) -> None:
    """Verify parser correctly associates instruction comment placed INSIDE function body."""
    code = """def calculate_average(numbers):
    # AI-FIX: Handle empty lists safely.
    return sum(numbers) / len(numbers)
"""
    py_file = tmp_path / "math_utils.py"
    py_file.write_text(code, encoding="utf-8")

    tasks, warnings = parse_file(py_file, project_root=tmp_path)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.instruction_type == "AI-FIX"
    assert task.description == "Handle empty lists safely."
    assert task.target_symbol == "calculate_average"
    assert task.target_symbol_type == "function"
    assert task.line_number == 2


def test_language_adapter_registry_and_custom_adapter() -> None:
    """Verify LanguageAdapterRegistry routes extensions to adapters and allows custom extension."""
    from codepilot.core.parser.adapters import (
        DEFAULT_ADAPTER_REGISTRY,
        BaseLanguageAdapter,
        GenericLanguageAdapter,
        LanguageAdapterRegistry,
        PythonLanguageAdapter,
    )
    from codepilot.core.parser.models import RawInstructionMatch
    from codepilot.core.parser.symbol_resolver import ResolvedSymbol

    registry = LanguageAdapterRegistry()
    py_adapter = registry.get_adapter(Path("module.py"))
    assert isinstance(py_adapter, PythonLanguageAdapter)
    assert py_adapter.language_id == "python"

    js_adapter = registry.get_adapter(Path("script.js"))
    assert isinstance(js_adapter, GenericLanguageAdapter)

    # Custom adapter registration
    class RubyLanguageAdapter(BaseLanguageAdapter):
        @property
        def language_id(self) -> str:
            return "ruby"

        @property
        def supported_extensions(self):
            return {".rb"}

        def extract_comments(self, lines, content, rel_path):
            return [], []

        def resolve_symbol(self, file_path, line_number, content, end_line_number=None):
            return ResolvedSymbol(symbol_name="ruby_mod", symbol_type="module", confidence="heuristic")

    registry.register(RubyLanguageAdapter())
    rb_adapter = registry.get_adapter(Path("script.rb"))
    assert rb_adapter.language_id == "ruby"

