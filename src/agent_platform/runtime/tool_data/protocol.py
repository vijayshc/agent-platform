"""Resolve the chart/table placeholders in an assistant reply.

The model's answer refers to cached tool data with ``#CHART_<ref>`` or
``#TABLE_<ref>``. Only the referenced results are packaged for the client, so an
unused cached result never travels to the browser.

A reply may reference data cached in an *earlier turn* of the same conversation
("plot both charts side by side"), which is why the lookup goes through the
conversation scope rather than the current run.

A reference written inside an ordinary code fence is an example, not a
placeholder: the client renders such a fence literally, so the extractor ignores
it here too.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from src.agent_platform.runtime.tool_data.scope import ToolDataScope
from src.agent_platform.runtime.tool_data.store import TOOL_DATA_STORE, ToolDataStore

#: ``#CHART_<ref>`` / ``#TABLE_<ref>``. Refs are short (`D1`) but the pattern
#: also accepts a provider call id, so the body is one-or-more id characters.
REFERENCE_RE = re.compile(r"#(?:CHART|TABLE)_([A-Za-z0-9][A-Za-z0-9_-]*)")

_FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
#: Fence languages whose bodies *are* placeholders.
_DATA_FENCES = frozenset({"chart", "table"})


def _visible_text(text: str) -> str:
    """``text`` with the bodies of non-chart/table code fences removed.

    Mirrors the client parser: a ``#TABLE_D1`` written inside a ```python
    example is prose, so it must not pull that table into the payload.
    """
    lines = text.splitlines()
    kept: list[str] = []
    index = 0
    while index < len(lines):
        opener = _FENCE_OPEN.match(lines[index])
        if opener is None:
            kept.append(lines[index])
            index += 1
            continue
        marker = opener.group(1)[0]
        length = len(opener.group(1))
        info = (opener.group(2) or "").strip().lower()
        language = re.split(r"[\s{]", info, maxsplit=1)[0]
        end = index + 1
        closed = False
        while end < len(lines):
            closer = _FENCE_CLOSE.match(lines[end])
            if closer and closer.group(1)[0] == marker and len(closer.group(1)) >= length:
                closed = True
                break
            end += 1
        last = end if closed else len(lines)
        if language in _DATA_FENCES:
            kept.extend(lines[index:last])
        index = last + 1 if closed else len(lines)
    return "\n".join(kept)


def referenced_call_ids(text: str | None) -> list[str]:
    """The call ids referenced by ``text``, in first-seen order.

    A reference that only appears inside an ordinary code fence is ignored.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for call_id in REFERENCE_RE.findall(_visible_text(text or "")):
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
    """Render payloads for every cached id the reply references.

    An id that matches nothing stays unresolved and reaches the client as a
    "no longer available" card. Substituting another cached table here would
    draw a chart of data the model never asked for.
    """
    return resolve_references(referenced_call_ids(text), scope, store)


def descriptors_from_payloads(payloads: Iterable[dict[str, Any]]) -> list[dict]:
    """The persisted form of a turn's tool data: everything but the rows.

    The rows live in the conversation's tool-data archive. Storing them again in
    the message would duplicate every cached result in the database.
    """
    return [{key: value for key, value in payload.items() if key != "rows"} for payload in payloads]


def payloads_from_descriptors(
    descriptors: Iterable[dict[str, Any]],
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
