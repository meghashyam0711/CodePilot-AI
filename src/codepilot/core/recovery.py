"""Recovery and rollback engine for CodePilot AI.

Manages automated pre-apply backups, rollback manifests, and safe restoration
of modified files without destructive Git operations or loss of untracked files.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from codepilot.exceptions import RecoveryError
from codepilot.logging_config import get_logger

logger = get_logger("core.recovery")

BACKUPS_DIR_NAME = ".codepilot/backups"
MANIFEST_FILENAME = "manifest.json"


@dataclass
class BackupEntry:
    """Record of a single backed-up file."""

    file_path: str
    backup_file: str
    original_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackupEntry":
        return cls(**data)


@dataclass
class BackupManifest:
    """Manifest describing a pre-apply backup snapshot."""

    patch_id: str
    created_at: str
    project_root: str
    files: List[BackupEntry]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "created_at": self.created_at,
            "project_root": self.project_root,
            "files": [f.to_dict() for f in self.files],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BackupManifest":
        files = [BackupEntry.from_dict(f) for f in data.get("files", [])]
        return cls(
            patch_id=data.get("patch_id", ""),
            created_at=data.get("created_at", ""),
            project_root=data.get("project_root", ""),
            files=files,
        )


@dataclass
class RecoveryResult:
    """Outcome of a rollback recovery operation."""

    patch_id: str
    restored_files: List[str]
    timestamp: str
    backup_dir: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_backups_dir(project_root: Path) -> Path:
    """Return path to backups directory."""
    return project_root.resolve() / BACKUPS_DIR_NAME


def create_patch_backup(
    project_root: Path,
    patch_id: str,
    file_patches: List[Any],
) -> Path:
    """Create a verified pre-apply backup of all target files before modification.

    Args:
        project_root: Repository root.
        patch_id: Identifier of the patch being applied.
        file_patches: List of FilePatch objects.

    Returns:
        Path: Path to the created backup snapshot directory.

    Raises:
        RecoveryError: If copying backup files or saving manifest fails.
    """
    root = project_root.resolve()
    backup_dir = get_backups_dir(root) / patch_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    entries: List[BackupEntry] = []

    for fp in file_patches:
        target_path = root / fp.file_path
        if not target_path.is_file():
            continue

        rel_path = Path(fp.file_path.replace("\\", "/"))
        ts = int(datetime.now(timezone.utc).timestamp())
        # Preserve directory structure to prevent basename collisions across subdirectories
        if rel_path.parent == Path("."):
            candidate_rel = f"{rel_path.name}_{ts}.bak"
        else:
            candidate_rel = (rel_path.parent / f"{rel_path.name}_{ts}.bak").as_posix()

        dest_file = backup_dir / candidate_rel
        counter = 1
        while dest_file.exists():
            counter += 1
            if rel_path.parent == Path("."):
                candidate_rel = f"{rel_path.name}_{ts}_{counter}.bak"
            else:
                candidate_rel = (rel_path.parent / f"{rel_path.name}_{ts}_{counter}.bak").as_posix()
            dest_file = backup_dir / candidate_rel

        dest_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(target_path, dest_file)
            entries.append(
                BackupEntry(
                    file_path=fp.file_path,
                    backup_file=candidate_rel,
                    original_hash=fp.original_hash,
                )
            )
            logger.info("Created backup for '%s' at '%s'", fp.file_path, dest_file)
        except OSError as e:
            raise RecoveryError(f"Failed to create backup copy for '{fp.file_path}': {e}") from e

    manifest = BackupManifest(
        patch_id=patch_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        project_root=str(root),
        files=entries,
    )

    manifest_file = backup_dir / MANIFEST_FILENAME
    try:
        manifest_file.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    except OSError as e:
        raise RecoveryError(f"Failed to write backup manifest at '{manifest_file}': {e}") from e

    return backup_dir


def list_backups(project_root: Path) -> List[Dict[str, Any]]:
    """List all available backup snapshots in descending chronological order."""
    root = project_root.resolve()
    backups_dir = get_backups_dir(root)
    if not backups_dir.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for entry in backups_dir.iterdir():
        if entry.is_dir():
            manifest_file = entry / MANIFEST_FILENAME
            if manifest_file.is_file():
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    results.append(data)
                except Exception as e:
                    logger.warning("Could not read backup manifest in %s: %s", entry, e)

    # Sort newest first
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results


def rollback_patch(
    project_root: Path,
    patch_id: Optional[str] = None,
) -> RecoveryResult:
    """Restore target files from a previously created pre-apply backup snapshot.

    Args:
        project_root: Repository root.
        patch_id: Optional specific patch ID to restore. If None, restores the latest backup.

    Returns:
        RecoveryResult: Restored file list and timestamp.

    Raises:
        RecoveryError: If no backups exist, manifest is missing, or restore write fails.
    """
    root = project_root.resolve()
    backups = list_backups(root)

    if not backups:
        raise RecoveryError("No backup snapshots found in '.codepilot/backups'. Cannot rollback.")

    target_backup_data: Optional[Dict[str, Any]] = None
    if patch_id:
        clean_id = patch_id.strip()
        for b in backups:
            if b.get("patch_id") == clean_id or clean_id in b.get("patch_id", ""):
                target_backup_data = b
                break
        if not target_backup_data:
            raise RecoveryError(f"Backup snapshot for patch ID '{patch_id}' not found.")
    else:
        target_backup_data = backups[0]

    manifest = BackupManifest.from_dict(target_backup_data)
    backup_dir = get_backups_dir(root) / manifest.patch_id

    restored_files: List[str] = []
    for entry in manifest.files:
        src_bak = backup_dir / entry.backup_file
        if not src_bak.is_file():
            raise RecoveryError(f"Backup file '{entry.backup_file}' missing from snapshot folder.")

        target_dest = root / entry.file_path
        try:
            target_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_bak, target_dest)
            restored_files.append(entry.file_path)
            logger.info("Restored '%s' from backup '%s'", entry.file_path, src_bak)
        except OSError as e:
            raise RecoveryError(f"Failed to restore file '{entry.file_path}': {e}") from e

    return RecoveryResult(
        patch_id=manifest.patch_id,
        restored_files=restored_files,
        timestamp=datetime.now(timezone.utc).isoformat(),
        backup_dir=str(backup_dir),
    )
