"""Unit tests for CodePilot AI project-root detection and centralized file ownership policy layer."""

import os
from pathlib import Path
import subprocess
import pytest

from codepilot.config import CodePilotConfig
from codepilot.core.security import (
    FileOperation,
    FileOwnershipCategory,
    check_file_permission,
    classify_file_ownership,
    resolve_project_root,
)
from codepilot.exceptions import SecurityError


# ==============================================================================
# 1. Project-Root Detection Tests
# ==============================================================================

def test_resolve_project_root_explicit_valid_directory(tmp_path: Path) -> None:
    """Explicitly supplied existing directory resolves to canonical path."""
    resolved = resolve_project_root(target_path=tmp_path)
    assert resolved == tmp_path.resolve()


def test_resolve_project_root_nonexistent_path_raises(tmp_path: Path) -> None:
    """Non-existent path raises SecurityError."""
    fake_dir = tmp_path / "does_not_exist"
    with pytest.raises(SecurityError, match="does not exist"):
        resolve_project_root(target_path=fake_dir)


def test_resolve_project_root_file_instead_of_dir_raises(tmp_path: Path) -> None:
    """Specifying a file path instead of a directory raises SecurityError."""
    file_path = tmp_path / "regular_file.txt"
    file_path.write_text("content", encoding="utf-8")
    with pytest.raises(SecurityError, match="not a directory"):
        resolve_project_root(target_path=file_path)


def test_resolve_project_root_refuses_home_directory() -> None:
    """Refuses to treat user home directory as project root without authorization."""
    home = Path.home()
    with pytest.raises(SecurityError, match="Refusing to treat user home directory"):
        resolve_project_root(target_path=home, allow_home_or_root=False)


def test_resolve_project_root_allows_home_directory_when_explicitly_permitted() -> None:
    """Permits user home directory when allow_home_or_root=True."""
    home = Path.home()
    resolved = resolve_project_root(target_path=home, allow_home_or_root=True)
    assert resolved == home.resolve()


def test_resolve_project_root_from_git_metadata(tmp_path: Path) -> None:
    """Detects Git repository root even when invoked from deep nested subdirectories."""
    # Init git repo
    try:
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    except (subprocess.SubprocessError, OSError):
        pytest.skip("Git binary not available in environment")

    nested_dir = tmp_path / "src" / "pkg" / "nested"
    nested_dir.mkdir(parents=True, exist_ok=True)

    # When start_path is None and cwd is nested_dir
    resolved = resolve_project_root(target_path=None, cwd=nested_dir)
    assert resolved == tmp_path.resolve()


def test_resolve_project_root_from_marker_discovery(tmp_path: Path) -> None:
    """Discovers project root upward via pyproject.toml marker without git."""
    root_dir = tmp_path / "my_project"
    root_dir.mkdir(parents=True, exist_ok=True)
    (root_dir / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")

    sub_dir = root_dir / "modules" / "sub"
    sub_dir.mkdir(parents=True, exist_ok=True)

    resolved = resolve_project_root(target_path=None, cwd=sub_dir)
    assert resolved == root_dir.resolve()


# ==============================================================================
# 2. File Ownership Classification Tests
# ==============================================================================

def test_classify_file_ownership_categories(tmp_path: Path) -> None:
    """Classifies files across all 5 ownership categories."""
    config = CodePilotConfig(project_root=tmp_path)

    # 1. External
    external_file = tmp_path.parent / "unrelated.py"
    assert classify_file_ownership(external_file, tmp_path, config) == FileOwnershipCategory.EXTERNAL
    traversal_file = "../../escape.py"
    assert classify_file_ownership(traversal_file, tmp_path, config) == FileOwnershipCategory.EXTERNAL

    # 2. Protected
    assert classify_file_ownership(".env", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership(".env.production", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership("credentials.json", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership("server.key", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership("cert.pem", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership("pyproject.toml", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership(".git/config", tmp_path, config) == FileOwnershipCategory.PROTECTED
    assert classify_file_ownership(".venv/lib/site.py", tmp_path, config) == FileOwnershipCategory.PROTECTED

    # 3. Project-managed (.codepilot/*)
    assert classify_file_ownership(".codepilot/patches/pending_patch.json", tmp_path, config) == FileOwnershipCategory.PROJECT_MANAGED
    assert classify_file_ownership(".codepilot/backups/patch-1/manifest.json", tmp_path, config) == FileOwnershipCategory.PROJECT_MANAGED
    assert classify_file_ownership(".codepilot/test.lock", tmp_path, config) == FileOwnershipCategory.PROJECT_MANAGED

    # 4. Generated (caches, build, test artifacts)
    assert classify_file_ownership(".pytest_cache/v/cache/nodeids", tmp_path, config) == FileOwnershipCategory.GENERATED
    assert classify_file_ownership(".coverage", tmp_path, config) == FileOwnershipCategory.GENERATED
    assert classify_file_ownership("build/lib/mod.py", tmp_path, config) == FileOwnershipCategory.GENERATED
    assert classify_file_ownership("dist/package.whl", tmp_path, config) == FileOwnershipCategory.GENERATED
    assert classify_file_ownership("pkg/__pycache__/mod.cpython-313.pyc", tmp_path, config) == FileOwnershipCategory.GENERATED

    # 5. User-owned
    assert classify_file_ownership("src/codepilot/cli.py", tmp_path, config) == FileOwnershipCategory.USER_OWNED
    assert classify_file_ownership("tests/test_cli.py", tmp_path, config) == FileOwnershipCategory.USER_OWNED
    assert classify_file_ownership("README.md", tmp_path, config) == FileOwnershipCategory.USER_OWNED
    assert classify_file_ownership("docs/index.md", tmp_path, config) == FileOwnershipCategory.USER_OWNED


# ==============================================================================
# 3. Permission Enforcement Tests
# ==============================================================================

def test_check_file_permission_denies_external_operations(tmp_path: Path) -> None:
    """External files cannot be read, modified, or deleted."""
    config = CodePilotConfig(project_root=tmp_path)
    external_path = tmp_path.parent / "escape.py"

    for op in [FileOperation.READ, FileOperation.MODIFY, FileOperation.DELETE, FileOperation.OVERWRITE]:
        with pytest.raises(SecurityError, match="outside project root"):
            check_file_permission(external_path, op, tmp_path, config)


def test_check_file_permission_protected_files(tmp_path: Path) -> None:
    """Protected files cannot be modified or deleted, and credentials cannot be read."""
    config = CodePilotConfig(project_root=tmp_path)

    # Modifications are denied
    for op in [FileOperation.MODIFY, FileOperation.DELETE, FileOperation.OVERWRITE, FileOperation.RENAME]:
        with pytest.raises(SecurityError, match="classified as PROTECTED"):
            check_file_permission(".env", op, tmp_path, config)
        with pytest.raises(SecurityError, match="classified as PROTECTED"):
            check_file_permission("pyproject.toml", op, tmp_path, config)

    # Reading secret credential files is denied
    with pytest.raises(SecurityError, match="reading protected credential file"):
        check_file_permission(".env", FileOperation.READ, tmp_path, config)
    with pytest.raises(SecurityError, match="reading protected credential file"):
        check_file_permission("credentials.json", FileOperation.READ, tmp_path, config)
    with pytest.raises(SecurityError, match="reading protected credential file"):
        check_file_permission("key.pem", FileOperation.READ, tmp_path, config)


def test_check_file_permission_user_owned_requires_approval(tmp_path: Path) -> None:
    """User-owned files require explicit approval before modification, overwrite, or deletion."""
    config = CodePilotConfig(project_root=tmp_path)
    user_file = "src/main.py"

    # Without approval: operations raise SecurityError
    for op in [FileOperation.MODIFY, FileOperation.DELETE, FileOperation.OVERWRITE, FileOperation.RENAME]:
        with pytest.raises(SecurityError, match="requires explicit user approval"):
            check_file_permission(user_file, op, tmp_path, config, approved=False)

    # With approval: operations succeed without raising
    for op in [FileOperation.MODIFY, FileOperation.DELETE, FileOperation.OVERWRITE, FileOperation.RENAME]:
        check_file_permission(user_file, op, tmp_path, config, approved=True)

    # Reading user-owned files is always permitted
    check_file_permission(user_file, FileOperation.READ, tmp_path, config, approved=False)
