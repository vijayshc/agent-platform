"""Long-term memory tools using LangGraph InjectedStore / InjectedState."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import InjectedState, InjectedStore
from langgraph.store.base import BaseStore


def _namespace(user_id: Any) -> tuple[str, ...]:
    return ("users", str(user_id or "anon"), "memories")


def _emit(message: str) -> None:
    get_stream_writer()({"type": "progress", "message": message})


async def _aput(store: BaseStore, ns: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
    await store.aput(ns, key, value)


async def _asearch(store: BaseStore, ns: tuple[str, ...], query: str | None) -> list[Any]:
    kwargs: dict[str, Any] = {"limit": 20}
    if query:
        kwargs["query"] = query
    return list(await store.asearch(ns, **kwargs))


@tool
async def remember_fact(
    fact: str,
    store: Annotated[Any, InjectedStore()],
    user_id: Annotated[int | None, InjectedState("user_id")] = None,
) -> str:
    """Save a durable fact about the user so it is available in later conversations."""
    text = (fact or "").strip()
    if not text:
        return "No fact provided."
    _emit("Saving memory")
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    await _aput(store, _namespace(user_id), key, {"fact": text})
    return f"Remembered: {text}"


@tool
async def recall_facts(
    query: str,
    store: Annotated[Any, InjectedStore()],
    user_id: Annotated[int | None, InjectedState("user_id")] = None,
) -> str:
    """Recall previously saved facts. Pass a search phrase, or 'all' for every fact."""
    _emit("Searching memories")
    q = (query or "").strip()
    search = None if q.lower() in {"", "all", "*"} else q
    items = await _asearch(store, _namespace(user_id), search)
    facts: list[str] = []
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("fact"):
            facts.append(str(value["fact"]))
        elif value:
            facts.append(str(value))
    if not facts:
        return "No saved memories."
    return "Saved memories:\n" + "\n".join(f"- {f}" for f in facts)


def memory_tools() -> list[Any]:
    return [remember_fact, recall_facts]
