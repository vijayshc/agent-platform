"""The namespace a cached tool result is stored under."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.agent_platform.runtime.tool_data.archive import ARCHIVE_DIRNAME


@dataclass(frozen=True)
class ToolDataScope:
    """The namespace a cached result is stored under."""

    key: str
    root: Path | None = None

    @property
    def persistent(self) -> bool:
        return self.root is not None


def scope_from_namespace(namespace: str, checkpoint_dir: str | Path) -> ToolDataScope:
    """The scope of an already-resolved checkpoint namespace."""
    return ToolDataScope(key=namespace, root=Path(checkpoint_dir) / ARCHIVE_DIRNAME)


def conversation_scope(public_id: str) -> ToolDataScope:
    """The scope of a conversation identified by its public id."""
    from src.agent_platform.paths import checkpoint_dir_path

    namespace = f"conv-{public_id}"
    return scope_from_namespace(namespace, checkpoint_dir_path(namespace))


def scope_for(
    run_id: int | str | None,
    conversation_id: int | str | None = None,
) -> ToolDataScope:
    """The scope for a run, derived exactly like its checkpoint namespace."""
    from src.agent_platform.paths import checkpoint_dir_for
    from src.agent_platform.runtime.checkpointing import checkpoint_namespace

    if conversation_id in (None, "", 0, "0") and run_id in (None, "", 0, "0"):
        return ToolDataScope(key="anonymous")
    namespace = checkpoint_namespace(run_id=run_id, conversation_id=conversation_id)
    return scope_from_namespace(namespace, checkpoint_dir_for(namespace))


__all__ = [
    "ToolDataScope",
    "scope_from_namespace",
    "conversation_scope",
    "scope_for",
]
