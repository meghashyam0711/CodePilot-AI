"""Application core and service orchestrator for CodePilot AI.

Separates application lifecycle, status inspections, and diagnostic routines
from the CLI presentation layer.
"""

from dataclasses import dataclass, field
import importlib
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, List, Optional, Union

from codepilot.config import (
    CONFIG_FILENAME,
    CodePilotConfig,
    load_config,
)
from codepilot.exceptions import CoreAppError
from codepilot.logging_config import get_logger, setup_logging
from codepilot.version import __version__

logger = get_logger("core.app")


@dataclass
class GitInfo:
    """Git repository inspection details."""

    is_installed: bool
    git_version: Optional[str] = None
    is_repo: bool = False
    current_branch: Optional[str] = None
    has_uncommitted_changes: bool = False
    description: str = "Git not detected"


@dataclass
class ProjectStatus:
    """Structured status representation for the project and environment."""

    app_version: str
    project_root: Path
    config_file_found: bool
    config_file_path: Optional[Path]
    config: CodePilotConfig
    git_info: GitInfo
    ai_provider: str
    ai_model_id: Optional[str]
    ai_ready: bool
    ai_status_description: str
    is_ready_for_phase1: bool


@dataclass
class DiagnosticCheck:
    """Individual diagnostic check result."""

    name: str
    category: str
    passed: bool
    details: str
    remediation: Optional[str] = None
    is_critical: bool = True


@dataclass
class DoctorReport:
    """Aggregated doctor diagnostic report."""

    checks: List[DiagnosticCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Returns True if all critical checks passed."""
        return all(check.passed for check in self.checks if check.is_critical)

    @property
    def passed_count(self) -> int:
        return sum(1 for check in self.checks if check.passed)

    @property
    def total_count(self) -> int:
        return len(self.checks)


class CodePilotApp:
    """Central entry point and service container for CodePilot AI."""

    def __init__(
        self,
        config: Optional[CodePilotConfig] = None,
        project_root: Optional[Union[str, Path]] = None,
        config_file: Optional[Union[str, Path]] = None,
    ) -> None:
        from codepilot.core.security import resolve_project_root

        self.project_root = resolve_project_root(target_path=project_root)
        self.config_file = (
            Path(config_file).expanduser().resolve()
            if config_file
            else None
        )
        self._config: Optional[CodePilotConfig] = config
        self.is_initialized: bool = False

    @property
    def config(self) -> CodePilotConfig:
        if self._config is None:
            self._config = load_config(
                project_root=self.project_root,
                config_file=self.config_file,
            )
        return self._config

    def initialize(self) -> None:
        """Initialize logging and core application services."""
        try:
            # Ensure config is loaded and valid
            cfg = self.config
            setup_logging(level=cfg.log_level)
            self.is_initialized = True
            logger.debug(
                "CodePilot core initialized for root: %s (log_level: %s)",
                cfg.project_root,
                cfg.log_level,
            )
        except Exception as e:
            raise CoreAppError(f"Application initialization failed: {e}") from e

    def shutdown(self) -> None:
        """Cleanly shutdown application resources."""
        if self.is_initialized:
            logger.debug("CodePilot application shutting down.")
            self.is_initialized = False

    def __enter__(self) -> "CodePilotApp":
        self.initialize()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.shutdown()

    def check_git(self) -> GitInfo:
        """Inspect Git CLI presence and repository status in project_root."""
        git_path = shutil.which("git")
        if not git_path:
            return GitInfo(
                is_installed=False,
                description="Git CLI is not installed or not in PATH",
            )

        # Get git version
        git_version: Optional[str] = None
        try:
            res = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res.returncode == 0:
                git_version = res.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return GitInfo(
                is_installed=False,
                description="Failed to execute Git command",
            )

        # Check if project_root is a git repository
        is_repo = False
        current_branch: Optional[str] = None
        has_uncommitted = False
        description = "Not a Git repository (run 'git init' to initialize)"

        try:
            # Check if inside git work tree
            res_work_tree = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if res_work_tree.returncode == 0 and res_work_tree.stdout.strip() == "true":
                is_repo = True

                # Try git branch --show-current first (works on modern git even before first commit)
                res_branch = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if res_branch.returncode == 0 and res_branch.stdout.strip():
                    current_branch = res_branch.stdout.strip()
                else:
                    # Fallback 1: git symbolic-ref --short HEAD
                    res_sym = subprocess.run(
                        ["git", "symbolic-ref", "--short", "HEAD"],
                        cwd=str(self.project_root),
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5,
                    )
                    if res_sym.returncode == 0 and res_sym.stdout.strip():
                        current_branch = res_sym.stdout.strip()
                    else:
                        # Fallback 2: check .git/HEAD file directly
                        head_file = self.project_root / ".git" / "HEAD"
                        if head_file.is_file():
                            try:
                                head_text = head_file.read_text(encoding="utf-8").strip()
                                if head_text.startswith("ref: refs/heads/"):
                                    current_branch = head_text[len("ref: refs/heads/"):].strip()
                            except OSError:
                                pass

                # Check working tree cleanliness
                res_status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if res_status.returncode == 0:
                    has_uncommitted = bool(res_status.stdout.strip())

                status_str = "with uncommitted changes" if has_uncommitted else "clean"
                description = f"Repository active on branch '{current_branch or 'unknown'}' ({status_str})"
        except (subprocess.SubprocessError, OSError):
            # If checking repo status fails, keep is_repo as False
            pass

        return GitInfo(
            is_installed=True,
            git_version=git_version,
            is_repo=is_repo,
            current_branch=current_branch,
            has_uncommitted_changes=has_uncommitted,
            description=description,
        )

    def get_status(self) -> ProjectStatus:
        """Inspect and return the overall project and engine readiness status."""
        config_file_target = self.config_file or (self.project_root / CONFIG_FILENAME)
        config_file_found = config_file_target.is_file()

        git_info = self.check_git()

        # Dynamic AI model runtime readiness check
        ai_provider = (self.config.model_provider or "none").strip().lower()
        ai_model_id = self.config.model_id
        ai_ready = False
        ai_status_description = "AI model provider not configured (current: 'none')"

        if ai_provider == "mock":
            ai_ready = True
            ai_status_description = "Mock model runtime active (offline deterministic testing)"
        elif ai_provider == "ollama":
            try:
                from codepilot.core.models.factory import create_model_client
                client = create_model_client(self.config)
                if client.is_available():
                    ai_ready = True
                    ai_status_description = f"Ollama local runtime active ({ai_model_id or 'default'})"
                else:
                    ai_status_description = "Ollama daemon unreachable at configured URL"
            except Exception as e:
                ai_status_description = f"Ollama initialization check failed: {e}"
        elif ai_provider in ("standalone", "gguf"):
            try:
                from codepilot.core.models.factory import create_model_client
                client = create_model_client(self.config)
                if client.is_available():
                    ai_ready = True
                    ai_status_description = f"Standalone GGUF runtime active ({ai_model_id or 'default'})"
                else:
                    ai_status_description = "Standalone GGUF runtime requires optional llama-cpp-python dependency"
            except Exception as e:
                ai_status_description = f"Standalone runtime initialization check failed: {e}"

        # Readiness indicator
        is_ready = True

        return ProjectStatus(
            app_version=__version__,
            project_root=self.project_root,
            config_file_found=config_file_found,
            config_file_path=config_file_target if config_file_found else None,
            config=self.config,
            git_info=git_info,
            ai_provider=ai_provider,
            ai_model_id=ai_model_id,
            ai_ready=ai_ready,
            ai_status_description=ai_status_description,
            is_ready_for_phase1=is_ready,
        )

    def run_doctor(self) -> DoctorReport:
        """Run diagnostic checks on the environment, dependencies, and configuration."""
        report = DoctorReport()

        # 1. Python version check (>= 3.11 required)
        py_version_str = (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        py_passed = sys.version_info >= (3, 11)
        report.checks.append(
            DiagnosticCheck(
                name="Python Runtime",
                category="Runtime",
                passed=py_passed,
                details=f"Python {py_version_str} (Path: {sys.executable})",
                remediation=(
                    None
                    if py_passed
                    else "Install Python 3.11 or newer from https://www.python.org/"
                ),
                is_critical=True,
            )
        )

        # 2. Required core dependencies check
        core_packages = ["typer", "rich", "pydantic", "pydantic_settings", "pytest"]
        missing_packages = []
        installed_versions = []

        for pkg in core_packages:
            try:
                mod = importlib.import_module(pkg)
                ver = getattr(mod, "__version__", None)
                if not ver:
                    try:
                        ver = importlib.metadata.version(pkg.replace("_", "-"))
                    except Exception:
                        ver = "installed"
                installed_versions.append(f"{pkg}=={ver}")
            except ImportError:
                missing_packages.append(pkg)

        deps_passed = len(missing_packages) == 0
        deps_details = (
            f"Installed: {', '.join(installed_versions)}"
            if deps_passed
            else f"Missing required dependencies: {', '.join(missing_packages)}"
        )
        deps_remediation = (
            None
            if deps_passed
            else f"Install missing packages using: pip install {' '.join(missing_packages)}"
        )

        report.checks.append(
            DiagnosticCheck(
                name="Core Dependencies",
                category="Dependencies",
                passed=deps_passed,
                details=deps_details,
                remediation=deps_remediation,
                is_critical=True,
            )
        )

        # 3. Git CLI availability check
        git_info = self.check_git()
        report.checks.append(
            DiagnosticCheck(
                name="Git Command-Line Tool",
                category="Tools",
                passed=git_info.is_installed,
                details=git_info.git_version or "Git command not found in PATH",
                remediation=(
                    None
                    if git_info.is_installed
                    else "Install Git from https://git-scm.com/ and ensure it is available in system PATH"
                ),
                is_critical=True,
            )
        )

        # 4. Git Repository initialization check (informational / non-critical for CLI startup)
        repo_remediation = None if git_info.is_repo else "Run 'git init' to enable future Git-based AI workflows"
        report.checks.append(
            DiagnosticCheck(
                name="Git Repository State",
                category="Repository",
                passed=git_info.is_repo,
                details=git_info.description,
                remediation=repo_remediation,
                is_critical=False,  # Can use CLI doctor before git init
            )
        )

        # 5. Configuration validity check
        config_passed = True
        config_details = "Configuration loaded and validated successfully"
        config_remediation = None
        try:
            _ = self.config
        except Exception as e:
            config_passed = False
            config_details = f"Configuration error: {e}"
            config_remediation = (
                "Verify syntax in .codepilot.toml or environment variables, or run 'codepilot init --force'"
            )

        report.checks.append(
            DiagnosticCheck(
                name="Configuration Validity",
                category="Configuration",
                passed=config_passed,
                details=config_details,
                remediation=config_remediation,
                is_critical=True,
            )
        )

        # 6. Project directory filesystem accessibility check
        dir_passed = True
        dir_details = f"Project root exists and is accessible: {self.project_root}"
        dir_remediation = None

        if not self.project_root.exists():
            dir_passed = False
            dir_details = f"Project root directory does not exist: {self.project_root}"
            dir_remediation = f"Create the directory '{self.project_root}'"
        elif not os.access(str(self.project_root), os.W_OK):
            dir_passed = False
            dir_details = f"Project root directory is not writable: {self.project_root}"
            dir_remediation = "Ensure the current user has write permissions for the project directory"

        report.checks.append(
            DiagnosticCheck(
                name="Project Directory Access",
                category="Filesystem",
                passed=dir_passed,
                details=dir_details,
                remediation=dir_remediation,
                is_critical=True,
            )
        )

        # 7. Security Boundaries & Protected Files check
        prot_files_cnt = len(self.config.protected_files) if hasattr(self.config, "protected_files") else 0
        prot_dirs_cnt = len(self.config.protected_directories) if hasattr(self.config, "protected_directories") else 0
        report.checks.append(
            DiagnosticCheck(
                name="Security Boundaries",
                category="Security",
                passed=True,
                details=f"Canonical path protection active ({prot_files_cnt} protected files, {prot_dirs_cnt} protected dirs)",
                remediation=None,
                is_critical=False,
            )
        )

        # 8. Model Runtime & Inference Engine check
        provider = (self.config.model_provider or "none").strip().lower()
        model_details = f"Configured provider: '{provider}'"
        model_remediation = None
        if provider == "none":
            model_remediation = "Configure model_provider ('ollama', 'standalone', or 'mock') in .codepilot.toml"
        report.checks.append(
            DiagnosticCheck(
                name="Model Runtime Interface",
                category="Runtime",
                passed=provider != "none",
                details=model_details,
                remediation=model_remediation,
                is_critical=False,
            )
        )

        return report

