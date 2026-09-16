"""Filesystem locations for skills, workspaces, and attachments."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_ROOT = SRC_DIR.parent
#: Built-in skill library shipped with the framework. User packages installed
#: through the Skills UI live in ``uploads/maf-skills`` and shadow these.
SKILLS_DIR = PACKAGE_DIR / "skills"
#: Optional sample content (demo agents, bundled MCP tool servers, fixtures).
#: Nothing in the framework imports it: deleting the directory must leave a
#: running platform untouched.
SAMPLES_DIR = APP_ROOT / "platform_samples"
SAMPLE_SERVICE_DIR = SAMPLES_DIR / "sample_service"


def app_root() -> Path:
    return APP_ROOT


def python_executable() -> str:
    return sys.executable


def pythonpath_env(existing: str | None = None) -> str:
    extra = existing if existing is not None else os.environ.get("PYTHONPATH", "")
    parts = [str(APP_ROOT)]
    if extra:
        parts.append(extra)
    return os.pathsep.join(parts)


def uploads_dir() -> Path:
    try:
        from config.config import UPLOADS_DIR
        return Path(UPLOADS_DIR)
    except Exception:
        path = APP_ROOT / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path


def workspace_root() -> Path:
    path = uploads_dir() / "agent-workspace"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_dir_for(run_or_conversation_id: str) -> Path:
    path = workspace_root() / str(run_or_conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_workspace_dir(run_id: int | str, conversation_id: int | str | None = None) -> Path:
    if conversation_id not in (None, "", 0, "0"):
        return workspace_dir_for(f"conv-{conversation_id}")
    return workspace_dir_for(f"run-{run_id}")


def conversation_workspace_dir(conversation_id: int | str) -> Path:
    """Directory shared by every run in a conversation (where agents write files)."""
    return workspace_dir_for(f"conv-{conversation_id}")


def attachments_root() -> Path:
    path = uploads_dir() / "agent-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def checkpoint_dir_for(namespace: str) -> Path:
    """Directory for a LangGraph thread's checkpoint+store files.

    ``namespace`` MUST be a stable, globally-unique, non-reused identifier such as
    ``conv-<public_id>`` or ``run-<public_id>`` (see
    ``runtime.checkpointing.checkpoint_namespace``). Using a recycled
    auto-increment integer id here lets a new session land on an orphaned
    ``checkpoints.db`` left by a prior database state and resume another
    conversation's history -- which is exactly the cross-session leak this
    function is designed to prevent.
    """
    path = uploads_dir() / "agent-checkpoints" / namespace
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_skills_dir() -> Path:
    path = uploads_dir() / "maf-skills"
    path.mkdir(parents=True, exist_ok=True)
    return path
