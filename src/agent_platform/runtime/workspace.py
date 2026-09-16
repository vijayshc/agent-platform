"""Sandbox directory helpers. Isolation is cwd + HITL, not a new Python."""

from __future__ import annotations

import json

import shutil
from pathlib import Path
from typing import Any

from src.agent_platform.paths import SAMPLE_SERVICE_DIR, workspace_dir_for


def ensure_workspace(run_or_conversation_id: str) -> Path:
    return workspace_dir_for(run_or_conversation_id)


# Fixtures an agent may explicitly ask for via ``config.workspace.seed``. These
# are demo/test assets (a deliberately buggy service used by the Developer demo
# agent and the eval suite) -- they must never be seeded into every run.
WORKSPACE_SEEDS: dict[str, Path] = {
    "sample-service": SAMPLE_SERVICE_DIR,
}


def seed_workspace(workspace: str | Path, seed: str | None) -> Path | None:
    """Copy an allow-listed fixture into the workspace, if one was requested.

    Returns the destination, or ``None`` when the definition did not opt in.
    """
    key = (seed or "").strip()
    if not key:
        return None
    src = WORKSPACE_SEEDS.get(key)
    if src is None or not src.is_dir():
        return None
    dest = Path(workspace) / key
    if not dest.exists():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    ensure_workspace_baseline(workspace)
    return dest


def workspace_seed_for(config: dict[str, Any] | None) -> str | None:
    """The seed requested by a definition, if any."""
    workspace_cfg = (config or {}).get("workspace")
    if isinstance(workspace_cfg, dict):
        return str(workspace_cfg.get("seed") or "") or None
    return None


def preload_sample_service(workspace: str | Path) -> Path:
    """Explicitly seed the demo service (eval suite and demo agents only)."""
    return seed_workspace(workspace, "sample-service") or Path(workspace) / "sample-service"


# Files the workspace is seeded with (the sample service scaffold) are not agent
# output. A baseline manifest records what existed before the agent ran, so the
# conversation "Files" panel can show only what the agent actually produced.
BASELINE_NAME = ".agent-workspace-baseline.json"


# Dependency/build output, never an agent deliverable.
IGNORED_WORKSPACE_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", ".pytest_cache",
    ".mypy_cache", ".ipynb_checkpoints", ".cache", "dist", "build",
}


def _workspace_inventory(root: Path) -> dict[str, dict[str, float]]:
    inventory: dict[str, dict[str, float]] = {}
    if not root.is_dir():
        return inventory
    for path in root.rglob("*"):
        if not path.is_file() or path.name == BASELINE_NAME:
            continue
        if any(part in IGNORED_WORKSPACE_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        inventory[path.relative_to(root).as_posix()] = {"size": stat.st_size, "mtime": stat.st_mtime}
    return inventory


def ensure_workspace_baseline(workspace: str | Path) -> Path:
    """Record the pre-existing workspace contents once, before the agent writes."""
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    target = root / BASELINE_NAME
    if not target.is_file():
        target.write_text(json.dumps(_workspace_inventory(root)), encoding="utf-8")
    return target


# Directories the workspace is seeded with. Used only when no usable baseline
# exists (a workspace created before this bookkeeping), so the seeded scaffold is
# still never reported as agent output.
SEEDED_PREFIXES = ("sample-service/",)


def produced_files(workspace: str | Path) -> list[str]:
    """Relative paths of files the agent created or modified.

    Read-only: the baseline is written when the workspace is initialised (see
    :func:`ensure_workspace_baseline`), never from here.
    """
    root = Path(workspace)
    baseline: dict[str, dict[str, float]] = {}
    target = root / BASELINE_NAME
    if target.is_file():
        try:
            parsed = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(parsed, dict) and parsed:
                baseline = parsed
        except (OSError, ValueError):
            baseline = {}
    produced: list[str] = []
    for rel, meta in _workspace_inventory(root).items():
        before = baseline.get(rel)
        if before is not None:
            if before.get("size") != meta["size"] or before.get("mtime") != meta["mtime"]:
                produced.append(rel)
            continue
        if not baseline and rel.startswith(SEEDED_PREFIXES):
            continue
        produced.append(rel)
    return produced
