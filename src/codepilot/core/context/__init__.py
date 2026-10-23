"""Project Context Engine package for CodePilot AI (Phase 4)."""

from codepilot.core.context.engine import ProjectContextEngine
from codepilot.core.context.models import (
    CodeSnippet,
    ProjectContextBundle,
    RelatedContext,
)
from codepilot.core.context.prompt import (
    CLARIFICATION_SYSTEM_INSTRUCTION,
    PROMPT_TEMPLATE_VERSION,
    SYSTEM_INSTRUCTION,
    build_clarification_prompt,
    build_model_request,
    build_system_prompt,
    build_user_prompt,
)
from codepilot.core.context.router import (
    TaskRoute,
    TaskRouter,
    TaskRoutingDecision,
)

__all__ = [
    "ProjectContextEngine",
    "ProjectContextBundle",
    "CodeSnippet",
    "RelatedContext",
    "PROMPT_TEMPLATE_VERSION",
    "SYSTEM_INSTRUCTION",
    "CLARIFICATION_SYSTEM_INSTRUCTION",
    "build_system_prompt",
    "build_clarification_prompt",
    "build_user_prompt",
    "build_model_request",
    "TaskRoute",
    "TaskRoutingDecision",
    "TaskRouter",
]
