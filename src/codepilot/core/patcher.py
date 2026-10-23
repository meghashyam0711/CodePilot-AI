"""Patch application engine for CodePilot AI.

Validates and safely applies approved code modifications while guarding
against stale, missing, or unreviewed patches, and generates pre-apply backups.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from codepilot.config import CodePilotConfig, load_config
from codepilot.core.fixer import (
    PENDING_PATCH_FILENAME,
    PatchBundle,
    compute_sha256,
    get_pending_patch_path,
    load_pending_patch,
)
from codepilot.core.recovery import create_patch_backup
from codepilot.core.security import (
    FileOperation,
    check_file_permission,
    validate_target_path,
)
from codepilot.exceptions import PatchError, SecurityError
from codepilot.logging_config import get_logger

logger = get_logger("core.patcher")


@dataclass
class ApplyResult:
    """Outcome of patch application."""

    patch_id: str
    applied_files: List[str]
    timestamp: str
    total_modified: int
    backup_dir: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "patch_id": self.patch_id,
            "applied_files": self.applied_files,
            "timestamp": self.timestamp,
            "total_modified": self.total_modified,
        }
        if self.backup_dir is not None:
            d["backup_dir"] = self.backup_dir
        return d


def apply_approved_patch(
    project_root: Path,
    approved: bool = False,
    config: Optional[CodePilotConfig] = None,
) -> ApplyResult:
    """Validate and apply a previously generated patch bundle.

    Args:
        project_root: Root directory of the repository.
        approved: Explicit developer approval flag.
        config: Optional CodePilot configuration. Loaded if None.

    Returns:
        ApplyResult: Details of applied files, timestamp, and backup location.

    Raises:
        PatchError: If approval flag is missing, no patch exists, or patch is stale/invalid.
    """
    # Requirement 4: Explicit approval required
    if not approved:
        raise PatchError(
            "Patch application was rejected: explicit approval is required.\n"
            "Review proposed modifications with 'codepilot fix --preview' and pass '--approved' to apply."
        )

    root = project_root.resolve()
    cfg = config or load_config(root)
    bundle = load_pending_patch(root)

    # Validate patch existence
    if bundle is None or not bundle.file_patches:
        raise PatchError(
            "No pending patch found. Run 'codepilot fix --preview' to generate a proposed modification patch first."
        )

    # Check if already applied
    if bundle.is_applied:
        raise PatchError(
            f"Patch '{bundle.patch_id}' has already been applied. Run 'codepilot scan' to find new tasks."
        )

    # Validate security boundaries, staleness, and file integrity
    for fp in bundle.file_patches:
        try:
            target_path = validate_target_path(fp.file_path, root, cfg)
        except SecurityError as e:
            raise PatchError(f"Security violation applying patch to '{fp.file_path}': {e}") from e

        # Validate file ownership policy permissions
        try:
            check_file_permission(
                target_path,
                FileOperation.MODIFY,
                project_root=root,
                config=cfg,
                approved=approved,
            )
        except SecurityError as e:
            raise PatchError(
                f"File ownership policy rejected patch modification for '{fp.file_path}': {e}"
            ) from e

        if not target_path.is_file():
            raise PatchError(
                f"Patch is invalid: target file '{fp.file_path}' no longer exists on disk."
            )

        try:
            current_content = target_path.read_text(encoding="utf-8")
        except OSError as e:
            raise PatchError(f"Cannot read target file '{fp.file_path}': {e}") from e

        current_hash = compute_sha256(current_content)
        if current_hash != fp.original_hash:
            raise PatchError(
                f"Patch is stale: target file '{fp.file_path}' was modified after the patch was generated.\n"
                "To prevent overwriting unreviewed changes, run 'codepilot fix --preview' again."
            )

        # Enforce structural executable AST invariance for AI-DOCS modifications
        is_docs_patch = (
            getattr(fp, "instruction_type", None) == "AI-DOCS"
            or bool(re.search(r"#\s*ai-docs?\b", fp.original_content, re.IGNORECASE))
        )
        if fp.file_path.endswith((".py", ".pyi")) and is_docs_patch:
            from codepilot.core.fixer import verify_executable_ast_invariance

            is_inv, reason = verify_executable_ast_invariance(
                fp.original_content, fp.proposed_content
            )
            if not is_inv:
                raise PatchError(
                    f"Structural validation failed for '{fp.file_path}': patch violates executable AST invariance: {reason}"
                )

    # Automatic pre-apply backup before modifying files on disk
    try:
        backup_dir = create_patch_backup(root, bundle.patch_id, bundle.file_patches)
    except Exception as e:
        raise PatchError(f"Could not create pre-apply backup before applying patch: {e}") from e

    # Safe atomic application of modified contents with automated rollback on write failure
    applied_files: List[str] = []
    try:
        for fp in bundle.file_patches:
            target_path = root / fp.file_path
            target_path.write_text(fp.proposed_content, encoding="utf-8")
            applied_files.append(fp.file_path)
            logger.info("Applied patch to '%s'", fp.file_path)
    except OSError as e:
        try:
            from codepilot.core.recovery import rollback_patch

            rollback_patch(root, bundle.patch_id)
        except Exception as rb_err:
            logger.error("Automated rollback failed during write abort: %s", rb_err)
        raise PatchError(f"Failed to write changes to '{fp.file_path}': {e}") from e

    bundle.is_approved = True
    bundle.is_applied = True

    # Archive applied patch and remove pending patch
    archive_file = (
        root
        / ".codepilot/patches"
        / f"applied_{bundle.patch_id}_{int(datetime.now(timezone.utc).timestamp())}.json"
    )
    try:
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_text(
            json.dumps(bundle.to_dict(), indent=2), encoding="utf-8"
        )
        pending_file = get_pending_patch_path(root)
        if pending_file.exists():
            pending_file.unlink()
    except OSError as e:
        logger.warning("Could not archive applied patch: %s", e)

    return ApplyResult(
        patch_id=bundle.patch_id,
        applied_files=applied_files,
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_modified=len(applied_files),
        backup_dir=str(backup_dir),
    )
