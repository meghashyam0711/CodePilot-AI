"""AI Instruction Comment Parser package for CodePilot AI (Phase 3)."""

from codepilot.core.parser.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    BaseLanguageAdapter,
    GenericLanguageAdapter,
    LanguageAdapterRegistry,
    PythonLanguageAdapter,
)
from codepilot.core.parser.comment_parser import InstructionCommentParser
from codepilot.core.parser.engine import (
    InstructionParserEngine,
    parse_file,
    parse_repository,
)
from codepilot.core.parser.llm_interface import (
    ModelTaskContext,
    TaskContextBuilder,
)
from codepilot.core.parser.models import (
    INSTRUCTION_TYPE_ALIASES,
    SUPPORTED_INSTRUCTION_TYPES,
    InstructionTask,
    InstructionType,
    ParseResult,
    RawInstructionMatch,
)
from codepilot.core.parser.symbol_resolver import ResolvedSymbol, SymbolResolver

__all__ = [
    "InstructionType",
    "SUPPORTED_INSTRUCTION_TYPES",
    "INSTRUCTION_TYPE_ALIASES",
    "InstructionTask",
    "RawInstructionMatch",
    "ParseResult",
    "InstructionCommentParser",
    "SymbolResolver",
    "ResolvedSymbol",
    "InstructionParserEngine",
    "parse_repository",
    "parse_file",
    "ModelTaskContext",
    "TaskContextBuilder",
    "BaseLanguageAdapter",
    "PythonLanguageAdapter",
    "GenericLanguageAdapter",
    "LanguageAdapterRegistry",
    "DEFAULT_ADAPTER_REGISTRY",
]
