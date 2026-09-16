from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite.aio import AsyncSqliteStore


def checkpoint_namespace(*, run_id: int | str | None, conversation_id: int | str | None = None) -> str:
    """Build a stable, globally-unique, non-reused checkpoint namespace.

    The LangGraph ``thread_id`` and the on-disk checkpoint directory MUST be
    derived from a UUID (a run/conversation ``public_id``), never from a
    recycled auto-increment integer id.
    """
    from src.agent_platform.conversations.store import ConversationStore
    from src.agent_platform.execution.run_store import RunStore

    if conversation_id not in (None, "", 0, "0"):
        try:
            conv_public = None
            if isinstance(conversation_id, str):
                conv = ConversationStore.get(conversation_id)
                if conv is not None:
                    conv_public = conv.get("public_id")
            else:
                conv = ConversationStore.get(int(conversation_id))
                if conv is not None:
                    conv_public = conv.get("public_id")
            if conv_public:
                return f"conv-{conv_public}"
        except Exception:
            pass

    if run_id not in (None, "", 0, "0"):
        try:
            run = RunStore.get(run_id)
            run_public = run.get("public_id") if run else None
            if run_public:
                return f"run-{run_public}"
        except Exception:
            pass

    if conversation_id not in (None, "", 0, "0"):
        return f"conv-{conversation_id}"
    return f"run-{run_id}"


def sqlite_checkpoint_storage(path: str | Path) -> AsyncSqliteSaver:
    target = Path(path)
    if target.is_dir() or target.suffix == "":
        target.mkdir(parents=True, exist_ok=True)
        db_file = target / "checkpoints.db"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        db_file = target
    return AsyncSqliteSaver.from_conn_string(str(db_file))


def file_checkpoint_storage(path: str | Path, **_kwargs: Any) -> AsyncSqliteSaver:
    return sqlite_checkpoint_storage(path)


def store_db_path() -> Path:
    """Cross-thread LangGraph Store (one SQLite file for all conversations)."""
    from src.agent_platform.paths import uploads_dir

    root = uploads_dir() / "agent-checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root / "store.db"


def sqlite_store_storage(path: str | Path | None = None) -> AsyncSqliteStore:
    """Persistent LangGraph Store for user facts across conversations."""
    db_file = Path(path) if path else store_db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteStore.from_conn_string(str(db_file))


def ensure_store(store: Any) -> BaseStore:
    if store is not None:
        return store
    return InMemoryStore()


def memory_enabled(config: dict[str, Any]) -> bool:
    """Long-term memory is on unless the definition sets disable_memory."""
    if config.get("disable_memory") or config.get("disableMemory"):
        return False
    return True
