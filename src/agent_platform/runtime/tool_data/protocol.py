"""Resolving ``#CHART_D1`` / ``#TABLE_D1`` references against the tool-data cache."""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.agent_platform.runtime.tool_data.scope import ToolDataScope
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolDataStore

REFERENCE_RE = re.compile(r"#(?:CHART|TABLE|CARD|LIST|PROGRESS)_([A-Za-z0-9][A-Za-z0-9_-]*)")


def referenced_call_ids(text: str | None) -> list[str]:
    """The call ids referenced by ``text``, in first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for call_id in REFERENCE_RE.findall(text or ""):
        if call_id not in seen:
            seen.add(call_id)
            ordered.append(call_id)
    return ordered


def resolve_references(
    refs: Iterable[str],
    scope: ToolDataScope | None,
    store: ToolDataStore | None = None,
) -> list[dict]:
    """Full payloads for ``refs``, de-duplicated and in first-seen order."""
    cache = store or TOOL_DATA_STORE
    payloads: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        data = cache.resolve(scope, str(ref or ""))
        if data is None or data.key in seen:
            continue
        seen.add(data.key)
        payloads.append(data.to_payload())
    return payloads


def resolve_tool_data(
    text: str | None,
    scope: ToolDataScope | None,
    store: ToolDataStore | None = None,
) -> list[dict]:
    """Render payloads for every cached id the reply references."""
    return resolve_references(referenced_call_ids(text), scope, store)


def descriptors_from_payloads(payloads: Iterable[dict[str, Any]]) -> list[dict]:
    """The persisted form of a turn's tool data: everything but the rows."""
    return [{key: value for key, value in payload.items() if key != "rows"} for payload in payloads]


def payloads_from_descriptors(
    descriptors: Iterable[dict],
    scope: ToolDataScope | None,
    store: ToolDataStore | None = None,
) -> list[dict]:
    """Re-resolve persisted descriptors against the archive for a read."""
    refs: list[str] = []
    for descriptor in descriptors or []:
        if not isinstance(descriptor, dict):
            continue
        ref = descriptor.get("call_id") or descriptor.get("ref")
        if ref:
            refs.append(str(ref))
    return resolve_references(refs, scope, store)


__all__ = [
    "REFERENCE_RE",
    "referenced_call_ids",
    "resolve_references",
    "resolve_tool_data",
    "descriptors_from_payloads",
    "payloads_from_descriptors",
]
