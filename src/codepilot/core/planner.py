"""Planner engine for CodePilot AI.

Analyzes discovered AI instruction comments, evaluates modification scope and risks,
and produces a structured, actionable implementation plan.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from codepilot.core.scanner import InstructionComment, ScanResult
from codepilot.exceptions import PlanError
from codepilot.logging_config import get_logger

logger = get_logger("core.planner")


@dataclass
class PlanTask:
    """An individual actionable task within the implementation plan."""

    task_id: str
    instruction_id: str
    target_file: str
    line_number: int
    instruction_type: str
    description: str
    risk_level: str  # LOW, MEDIUM, HIGH
    scope: str  # LINE, BLOCK, FUNCTION, MODULE
    steps: List[str] = field(default_factory=list)
    target_symbol: Optional[str] = None
    target_symbol_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanTask":
        valid_keys = {
            "task_id",
            "instruction_id",
            "target_file",
            "line_number",
            "instruction_type",
            "description",
            "risk_level",
            "scope",
            "steps",
            "target_symbol",
            "target_symbol_type",
        }
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


@dataclass
class Plan:
    """Structured implementation plan containing tasks, risk metrics, and targets."""

    plan_id: str
    project_root: str
    created_at: str
    tasks: List[PlanTask] = field(default_factory=list)

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    @property
    def risk_summary(self) -> Dict[str, int]:
        summary = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
        for task in self.tasks:
            summary[task.risk_level] = summary.get(task.risk_level, 0) + 1
        return summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "project_root": self.project_root,
            "created_at": self.created_at,
            "total_tasks": self.total_tasks,
            "risk_summary": self.risk_summary,
            "tasks": [task.to_dict() for task in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Plan":
        tasks = [PlanTask.from_dict(t) for t in data.get("tasks", [])]
        return cls(
            plan_id=data.get("plan_id", ""),
            project_root=data.get("project_root", ""),
            created_at=data.get("created_at", ""),
            tasks=tasks,
        )


def evaluate_risk_level(instruction: InstructionComment) -> str:
    """Assess modification risk based on file sensitivity and instruction type."""
    file_lower = instruction.file_path.lower()
    prompt_lower = instruction.prompt.lower()

    # High risk for security, authentication, payment, or database core logic
    high_risk_keywords = {"security", "auth", "token", "password", "crypto", "database", "migration", "pay"}
    if any(k in file_lower or k in prompt_lower for k in high_risk_keywords):
        return "HIGH"

    # Medium risk for refactoring, complex logic, or core modules
    if instruction.instruction_type.upper() in ("REFACTOR", "FIX", "AI-REFACTOR", "AI-FIX"):
        return "MEDIUM"

    return "LOW"


def estimate_scope(instruction: InstructionComment) -> str:
    """Determine the probable scope of code change based on context and instruction."""
    prompt_lower = instruction.prompt.lower()
    if any(k in prompt_lower for k in ["class", "module", "refactor all", "architecture"]):
        return "MODULE"
    if any(k in prompt_lower for k in ["function", "def ", "method", "endpoint"]):
        return "FUNCTION"
    if any(k in prompt_lower for k in ["loop", "if ", "try", "block", "handle"]):
        return "BLOCK"
    return "LINE"


def generate_plan(scan_result: ScanResult) -> Plan:
    """Create a structured implementation plan from scan results.

    Args:
        scan_result: Discovered AI instruction comments.

    Returns:
        Plan: Structured tasks with risk assessments and execution steps.

    Raises:
        PlanError: If planning fails or no instructions are present.
    """
    if not scan_result.instructions:
        findings = getattr(scan_result, "static_findings", [])
        if not findings:
            logger.info("Scan result contains no AI instruction comments or static findings.")
            return Plan(
                plan_id="plan-empty",
                project_root=scan_result.project_root,
                created_at=datetime.now(timezone.utc).isoformat(),
                tasks=[],
            )

        tasks: List[PlanTask] = []
        plan_id_hash = hashlib.sha256(
            f"{scan_result.project_root}:{len(findings)}:{datetime.now(timezone.utc).timestamp()}".encode("utf-8")
        ).hexdigest()[:10]

        for index, finding in enumerate(findings, start=1):
            task_id = f"FINDING-{index:03d}"
            f_file = finding.get("file", "")
            f_line = finding.get("line", 1)
            f_msg = finding.get("message", "Static issue detected")
            f_sev = finding.get("severity", "warning")
            risk = "HIGH" if f_sev == "error" else "MEDIUM"
            steps = [
                f"1. Inspect finding in '{f_file}' at line {f_line}",
                f"2. Formulate code correction for '{f_msg}'",
                f"3. Generate unified Git diff via 'codepilot fix {f_file}'",
                "4. Obtain explicit approval and apply patch via 'codepilot apply --approved'",
                "5. Execute test suite via 'codepilot test' to verify correctness",
            ]
            tasks.append(
                PlanTask(
                    task_id=task_id,
                    instruction_id=task_id,
                    target_file=f_file,
                    line_number=f_line,
                    instruction_type="AI-FIX",
                    description=f_msg,
                    risk_level=risk,
                    scope="LINE",
                    steps=steps,
                    target_symbol=None,
                    target_symbol_type=None,
                )
            )

        return Plan(
            plan_id=f"plan-{plan_id_hash}",
            project_root=scan_result.project_root,
            created_at=datetime.now(timezone.utc).isoformat(),
            tasks=tasks,
        )

    tasks: List[PlanTask] = []
    plan_id_hash = hashlib.sha256(
        f"{scan_result.project_root}:{len(scan_result.instructions)}:{datetime.now(timezone.utc).timestamp()}".encode("utf-8")
    ).hexdigest()[:10]

    for index, inst in enumerate(scan_result.instructions, start=1):
        task_id = f"TASK-{index:03d}"
        risk = evaluate_risk_level(inst)
        scope = estimate_scope(inst)

        # Standard step pipeline for code modification
        steps = [
            f"1. Analyze target file '{inst.file_path}' at line {inst.line_number}",
            f"2. Formulate code modification for '{inst.prompt}' (scope: {scope})",
            "3. Generate unified Git diff and inspect changes via 'codepilot fix --preview'",
            "4. Obtain explicit approval and apply patch via 'codepilot apply --approved'",
            "5. Execute test suite via 'codepilot test' to verify correctness",
        ]

        tasks.append(
            PlanTask(
                task_id=task_id,
                instruction_id=inst.id,
                target_file=inst.file_path,
                line_number=inst.line_number,
                instruction_type=inst.instruction_type,
                description=inst.prompt,
                risk_level=risk,
                scope=scope,
                steps=steps,
                target_symbol=inst.target_symbol,
                target_symbol_type=inst.target_symbol_type,
            )
        )

    return Plan(
        plan_id=f"plan-{plan_id_hash}",
        project_root=scan_result.project_root,
        created_at=datetime.now(timezone.utc).isoformat(),
        tasks=tasks,
    )


def save_plan(plan: Plan, target_file: Path) -> Path:
    """Persist generated plan to a JSON file."""
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        return target_file
    except OSError as e:
        raise PlanError(f"Failed to save plan to {target_file}: {e}") from e


def load_plan(source_file: Path) -> Optional[Plan]:
    """Load cached plan from a JSON file if present."""
    if not source_file.is_file():
        return None
    try:
        data = json.loads(source_file.read_text(encoding="utf-8"))
        return Plan.from_dict(data)
    except Exception as e:
        logger.warning("Could not read plan from %s: %s", source_file, e)
        return None
