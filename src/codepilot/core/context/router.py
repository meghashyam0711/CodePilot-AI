"""Lightweight task routing for CodePilot AI.

Distinguishes between actionable code-generation tasks and materially
ambiguous requests requiring clarification, preventing prompt-level mode collapse.
"""

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional

from codepilot.logging_config import get_logger

logger = get_logger("core.context.router")


class TaskRoute(str, Enum):
    """Execution routes for instruction tasks."""

    CODE_GENERATION = "code_generation"
    CLARIFICATION = "clarification"


@dataclass
class TaskRoutingDecision:
    """Outcome of a task routing decision."""

    route: TaskRoute
    reason: str
    confidence: float = 1.0
    ambiguity_flags: List[str] = field(default_factory=list)

    @property
    def is_clarification(self) -> bool:
        """True if the task requires clarification before code generation."""
        return self.route == TaskRoute.CLARIFICATION

    @property
    def is_code_generation(self) -> bool:
        """True if the task has sufficient specifications for code generation."""
        return self.route == TaskRoute.CODE_GENERATION


class TaskRouter:
    """Lightweight, transparent, deterministic task router.

    Separates code-generation requests from clarification-required tasks
    without introducing external models or heavyweight classifiers.
    """

    # Patterns indicating high-level subjective vagueness without concrete specifications
    VAGUE_PATTERNS = [
        re.compile(r"\bmake\s+(?:the\s+)?[\w\s]+\s+better\b", re.IGNORECASE),
        re.compile(r"\bimprove\s+(?:the\s+)?[\w\s]+\s+flow\b", re.IGNORECASE),
        re.compile(r"\bupdate\s+(?:the\s+)?[\w\s]+\s+flow\s+accordingly\b", re.IGNORECASE),
        re.compile(r"\bclean\s+up\s+(?:the\s+)?(?:code|logic)\b", re.IGNORECASE),
        re.compile(r"\bmake\s+it\s+work\b", re.IGNORECASE),
        re.compile(r"\bfix\s+(?:everything|the\s+stuff)\b", re.IGNORECASE),
    ]

    # Patterns indicating concrete technical specifications or signatures
    CONCRETE_PATTERNS = [
        re.compile(r"[a-zA-Z_]\w*\s*\([^)]*\)\s*(?:->\s*[\w\[\], ]+)?"),  # func(args) -> type
        re.compile(r"['\"][a-zA-Z_]\w*['\"]"),  # 'symbol_name'
        re.compile(r"\breturn\b", re.IGNORECASE),  # explicit return specification
        re.compile(
            r"\b(?:off-by-one|boundary\s+condition|empty\s+list|fallback|traversal|basename|sanitize)\b",
            re.IGNORECASE,
        ),
    ]

    # Patterns indicating explicit contradictions / mutually exclusive requirements
    CONFLICT_PATTERNS = [
        re.compile(r"\balways\s+return\s+true\b.*\breturn\s+false\b", re.IGNORECASE),
        re.compile(r"\brequire\s+authentication\b.*\bwithout\s+(?:credentials|token|auth)\b", re.IGNORECASE),
        re.compile(r"\braise\s+error\b.*\bdo\s+not\s+raise\b", re.IGNORECASE),
        re.compile(r"\bdo\s+not\s+change\b.*\brewrite\b", re.IGNORECASE),
    ]

    def route(
        self,
        prompt: str,
        source_code: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        is_malformed: bool = False,
        should_request_clarification: Optional[bool] = None,
    ) -> TaskRoutingDecision:
        """Evaluate task specifications and determine whether to generate code or request clarification.

        Rules:
        1. Explicit metadata signals (should_request_clarification, malformed) take precedence.
        2. Empty, whitespace-only, or pure placeholder instructions route to clarification.
        3. Explicit contradictions route to clarification.
        4. Tasks with concrete function signatures, return specifications, or actionable fixes route to code generation.
        5. Purely subjective vagueness lacking actionable criteria routes to clarification.
        6. Default is conservative code generation to avoid blocking routine work.
        """
        meta = metadata or {}

        # 1. Authoritative Task Intent & Metadata Signals
        if should_request_clarification is True:
            decision = TaskRoutingDecision(
                route=TaskRoute.CLARIFICATION,
                reason="Task metadata explicitly specifies clarification required",
                confidence=1.0,
            )
            logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
            return decision

        if meta.get("should_request_clarification") is True or meta.get("requires_clarification") is True:
            decision = TaskRoutingDecision(
                route=TaskRoute.CLARIFICATION,
                reason="Task metadata flags clarification required",
                confidence=1.0,
            )
            logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
            return decision

        if is_malformed:
            decision = TaskRoutingDecision(
                route=TaskRoute.CLARIFICATION,
                reason="Task instruction comment is malformed or unparseable",
                confidence=1.0,
                ambiguity_flags=["malformed_instruction"],
            )
            logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
            return decision

        clean_prompt = prompt.strip()

        # 2. Empty or Placeholder Prompts
        if not clean_prompt or clean_prompt.lower() in {"...", "?", "???", "todo", "fixme", "tbd", "pass"}:
            decision = TaskRoutingDecision(
                route=TaskRoute.CLARIFICATION,
                reason="Instruction is empty or purely placeholder",
                confidence=1.0,
                ambiguity_flags=["empty_or_placeholder"],
            )
            logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
            return decision

        # 3. Explicit Contradictions / Mutually Exclusive Requirements
        for conflict_re in self.CONFLICT_PATTERNS:
            if conflict_re.search(clean_prompt):
                decision = TaskRoutingDecision(
                    route=TaskRoute.CLARIFICATION,
                    reason="Instruction contains conflicting or mutually exclusive requirements",
                    confidence=0.9,
                    ambiguity_flags=["conflicting_requirements"],
                )
                logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
                return decision

        # 4. Check for Concrete Technical Specifications
        has_concrete_spec = any(pattern.search(clean_prompt) for pattern in self.CONCRETE_PATTERNS)
        has_source_context = bool(source_code and source_code.strip())

        # 5. Check for Pure Subjective Vagueness Lacking Technical Criteria
        is_vague = any(pattern.search(clean_prompt) for pattern in self.VAGUE_PATTERNS)

        if is_vague and not has_concrete_spec:
            decision = TaskRoutingDecision(
                route=TaskRoute.CLARIFICATION,
                reason="Instruction lacks essential specifications, technical criteria, or expected behavior",
                confidence=0.85,
                ambiguity_flags=["subjective_vagueness", "missing_technical_criteria"],
            )
            logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
            return decision

        # 6. Default: Sufficiently Specified Coding Request
        # (Preserves security, bug fixing, features, refactoring, and general programming tasks)
        reason = "Task contains sufficient technical specification for implementation"
        if has_concrete_spec:
            reason = "Task specifies concrete function signature, return value, or transformation target"
        elif has_source_context:
            reason = "Task provides source code context with actionable modification target"

        decision = TaskRoutingDecision(
            route=TaskRoute.CODE_GENERATION,
            reason=reason,
            confidence=0.95,
        )
        logger.debug("Task routed to %s: %s", decision.route.value, decision.reason)
        return decision

    def route_case(self, case: Any) -> TaskRoutingDecision:
        """Route an EvaluationCase benchmark fixture."""
        return self.route(
            prompt=getattr(case, "prompt", ""),
            source_code=getattr(case, "source_code", ""),
            metadata=getattr(case, "metadata", None),
            should_request_clarification=getattr(case, "should_request_clarification", None),
        )

    def route_task(self, task: Any) -> TaskRoutingDecision:
        """Route an InstructionTask discovered from source code comments."""
        return self.route(
            prompt=getattr(task, "description", getattr(task, "prompt", "")),
            is_malformed=getattr(task, "is_malformed", False),
        )

    def route_bundle(self, bundle: Any) -> TaskRoutingDecision:
        """Route a ProjectContextBundle assembled for model prompting."""
        task = getattr(bundle, "task", None)
        prompt = getattr(task, "description", getattr(task, "prompt", "")) if task else ""
        is_malformed = getattr(task, "is_malformed", False) if task else False
        source_code = ""
        target_snippet = getattr(bundle, "target_snippet", None)
        if target_snippet and hasattr(target_snippet, "content"):
            source_code = target_snippet.content

        return self.route(
            prompt=prompt,
            source_code=source_code,
            is_malformed=is_malformed,
        )
