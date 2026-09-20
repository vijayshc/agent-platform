"""What a cached tool result belongs to.

References are handed to the model as short names (``D1``) that behave like
variables, so they need a lifetime. That lifetime is **the conversation**: a
user who asks for a chart and then, in a later turn, asks to see the same result
side by side must get the same rows back, not a "no longer available" card.

Everything in a conversation shares one namespace, which is why the reference
counter and the archive live in the conversation's checkpoint directory. A run
without a conversation (an evaluation, a one-shot invocation) falls back to its
own run namespace, so the semantics are unchanged for callers that never had a
conversation to begin with.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.agent_platform.runtime.tool_data.archive import ARCHIVE_DIRNAME


@dataclass(frozen=True)
class ToolDataScope:
    """The namespace a cached result is stored under.

    ``key`` identifies the namespace in the in-process cache; ``root`` is where
    its Parquet archive lives, or ``None`` when the caller has no durable home
    (an anonymous compile) and the data is memory-only.
    """

    key: str
    root: Path | None = None

    @property
    def persistent(self) -> bool:
        return self.root is not None


def scope_from_namespace(namespace: str, checkpoint_dir: str | Path) -> ToolDataScope:
    """The scope of an already-resolved checkpoint namespace."""
    return ToolDataScope(key=namespace, root=Path(checkpoint_dir) / ARCHIVE_DIRNAME)


def conversation_scope(public_id: str) -> ToolDataScope:
    """The scope of a conversation identified by its public id.

    Used by the delete path, so it resolves the directory without creating it.
    """
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
