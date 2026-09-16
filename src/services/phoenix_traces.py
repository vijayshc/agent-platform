"""Read-only Phoenix trace access for the tenant-scoped Observability UI.

The in-memory :class:`SpanSink` buffer is a replay aid for a *live* run; it is
bounded, process-local, and disappears on restart.  Phoenix is the durable system
of record for interactions, so the run detail page reads span trees from Phoenix
instead:

* every run persists its deterministic ``session_id`` and the Phoenix
  ``trace_id``/``root_span_id`` the instrumentation emitted (see
  ``RunStore.set_trace``);
* :func:`fetch_run_trace` loads that trace through Phoenix's own GraphQL API and
  normalizes it for the UI;
* runs recorded before trace capture existed (or whose capture raced) are still
  re-fetchable by looking the run's public id up in Phoenix's span metadata.

Access is only ever by *this run's* trace/session: the caller supplies a run id,
never a trace id, so the tenant gate on the run endpoint is the whole story.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from src.services.phoenix_span_format import normalize_span, summarize
from src.services.phoenix_service import get_phoenix_url

logger = logging.getLogger("text2sql.services.phoenix_traces")

_INTERNAL_AUTH_HEADER = {"X-Internal-Phoenix-Auth": "agent_platform_phoenix_secret_2026"}
_REQUEST_TIMEOUT = 20.0
_PAGE_SIZE = 200
_MAX_SPANS = 5000

#: Raw traces are big (a 500-span trace is ~26k rows of payload); a click on a
#: span right after a list poll reuses this instead of re-fetching.  The list
#: fetch itself always bypasses it (``max_age=0``) so live runs keep advancing.
_CACHE_TTL_DETAIL = 15.0
_CACHE_MAX = 24
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()

_SPAN_NODE = """
  spanId
  parentId
  name
  spanKind
  startTime
  endTime
  latencyMs
  statusCode
  statusMessage
  tokenCountTotal
  tokenCountPrompt
  tokenCountCompletion
  cumulativeTokenCountTotal
  attributes
  events { name message timestamp attributes }
  input { value mimeType }
  output { value mimeType }
"""


class PhoenixUnavailable(RuntimeError):
    """Phoenix could not be reached or answered with an error."""


def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{get_phoenix_url()}/graphql"
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "AgentPlatform", **_INTERNAL_AUTH_HEADER},
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise PhoenixUnavailable(str(exc)) from exc
    errors = body.get("errors")
    if errors:
        raise PhoenixUnavailable(errors[0].get("message") or "Phoenix GraphQL error")
    return body.get("data") or {}


def fetch_raw_trace(trace_id: str) -> dict[str, Any] | None:
    """Fetch one trace (all its spans, paginated) from Phoenix, or ``None``."""
    if not trace_id:
        return None
    trace_fields = """
      traceId
      latencyMs
      numSpans
      errorCount
      spanCountsByKind { spanKind count }
      costSummary { total { cost } }
      session { sessionId }
      rootSpan { spanId name spanKind }
    """
    query = (
        "query($t:String!,$after:String){ getTraceByOtelId(traceId:$t){ "
        + trace_fields
        + " spans(first:%d, after:$after){ pageInfo { hasNextPage endCursor } edges { node { %s } } } } }"
        % (_PAGE_SIZE, _SPAN_NODE)
    )
    nodes: list[dict[str, Any]] = []
    after: str | None = None
    trace: dict[str, Any] | None = None
    while True:
        data = _graphql(query, {"t": trace_id, "after": after})
        trace = data.get("getTraceByOtelId")
        if trace is None:
            return None
        connection = trace.get("spans") or {}
        nodes.extend(edge["node"] for edge in connection.get("edges") or [] if edge.get("node"))
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage") or len(nodes) >= _MAX_SPANS:
            break
        after = page.get("endCursor")
        if not after:
            break
    trace["spanNodes"] = nodes
    return trace


def find_trace_id(run_public_id: str, project: str | None) -> str | None:
    """Find the trace of a run by its public id in Phoenix span metadata."""
    if not run_public_id or not project:
        return None
    # The value is a run public id, but escape it so a malformed row can never
    # break out of the Phoenix filter expression.
    safe_id = str(run_public_id).replace("\\", "\\\\").replace("'", "\\'")
    condition = f"metadata['run_public_id'] == '{safe_id}'"
    query = """
    query($name:String!,$condition:String!){
      getProjectByName(name:$name){
        spans(first:1, filterCondition:$condition){ edges { node { trace { traceId } } } }
      }
    }
    """
    data = _graphql(query, {"name": project, "condition": condition})
    project_node = data.get("getProjectByName")
    if not project_node:
        return None
    edges = ((project_node.get("spans") or {}).get("edges")) or []
    if not edges:
        return None
    trace = edges[0].get("node", {}).get("trace") or {}
    return trace.get("traceId")


def _ordered(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(spans, key=lambda span: (str(span.get("start_time") or ""), str(span.get("id") or "")))


def _unavailable(reason: str, message: str, run: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "message": message,
        "trace_id": run.get("trace_id"),
        "session_id": run.get("session_id"),
        "project": run.get("phoenix_project"),
        "spans": [],
    }


def _load_trace(trace_id: str, max_age: float = 0.0) -> dict[str, Any] | None:
    """Fetch a trace, reusing a very short-lived cache.

    A list fetch is always fresh (``max_age=0``) so a live run's watermark keeps
    moving, but it still *stores* the result so clicking a span right after a
    poll does not re-fetch a large trace.  Detail fetches may reuse it briefly.
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(trace_id)
        if cached and now - cached[0] <= max_age:
            return cached[1]
    trace = fetch_raw_trace(trace_id)
    if trace is not None:
        with _cache_lock:
            _cache[trace_id] = (now, trace)
            if len(_cache) > _CACHE_MAX:
                oldest = sorted(_cache.items(), key=lambda item: item[1][0])[: len(_cache) - _CACHE_MAX]
                for key, _ in oldest:
                    _cache.pop(key, None)
    return trace


def _resolve_trace(
    run: dict[str, Any], candidates: list[str], max_age: float = 0.0
) -> tuple[dict[str, Any] | None, str | None, str | None, str | None]:
    """Resolve this run's trace: stored id first, then a Phoenix metadata lookup.

    Returns ``(trace, trace_id, project, resolved_from)``; the trace is ``None``
    when neither route found one.
    """
    run_public_id = str(run.get("public_id") or run.get("id") or "")
    stored_trace = run.get("trace_id")
    if stored_trace:
        trace = _load_trace(str(stored_trace), max_age)
        if trace is not None:
            return trace, str(stored_trace), run.get("phoenix_project"), "stored"
    for candidate in candidates:
        found = find_trace_id(run_public_id, candidate)
        if not found:
            continue
        trace = _load_trace(found, max_age)
        if trace is not None:
            return trace, found, candidate, "phoenix_lookup"
    return None, None, None, None


def _candidates(run: dict[str, Any], project_candidates: list[str] | None) -> list[str]:
    return [name for name in ([run.get("phoenix_project")] + list(project_candidates or [])) if name]


def fetch_run_trace(
    run: dict[str, Any],
    project_candidates: list[str] | None = None,
    *,
    include_details: bool = False,
) -> dict[str, Any]:
    """Resolve and load the Phoenix trace for one run row.

    Resolution order:
      1. the ``trace_id`` persisted when the run's root span opened;
      2. a Phoenix lookup by ``run_public_id`` inside the run's agent project
         (covers runs recorded before trace capture, and capture races).

    Spans are returned as light rows unless ``include_details`` is set; payloads
    are fetched per span by :func:`fetch_run_span`.  Never raises for expected
    conditions: the UI gets ``available: false`` with a reason so it can render an
    honest empty state instead of an error page.
    """
    run_public_id = str(run.get("public_id") or run.get("id") or "")
    candidates = _candidates(run, project_candidates)
    stored_trace = run.get("trace_id")
    try:
        trace, trace_id, project, resolved_from = _resolve_trace(run, candidates)
    except PhoenixUnavailable as exc:
        logger.warning("Phoenix unavailable while resolving trace for run %s: %s", run_public_id, exc)
        return _unavailable("phoenix_unavailable", "Phoenix is not reachable right now.", run)

    if trace is None:
        if not stored_trace and not candidates:
            return _unavailable("no_trace", "This run has no Phoenix trace recorded.", run)
        return _unavailable(
            "trace_not_found",
            "No Phoenix trace was found for this run yet.",
            run,
        )

    spans = _ordered([normalize_span(node, detail=include_details) for node in trace.get("spanNodes") or []])
    stored = {
        "session_id": run.get("session_id"),
        "root_span_id": run.get("root_span_id"),
    }
    return {
        "available": True,
        "reason": None,
        "message": None,
        "trace_id": trace_id,
        "session_id": (trace.get("session") or {}).get("sessionId") or run.get("session_id"),
        "project": project or run.get("phoenix_project"),
        "resolved_from": resolved_from,
        "summary": summarize(spans, trace, stored),
        "spans": spans,
    }


def fetch_run_span(
    run: dict[str, Any],
    span_id: str,
    project_candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Load one span of a run's trace *with its payloads*.

    The waterfall only needs light rows, so payloads are fetched per selected
    span: a trace with hundreds of spans would otherwise serialize to tens of
    megabytes on every poll.  Tenancy is unchanged -- the caller still passes a
    run id, and the span id is only ever looked up inside that run's own trace.
    """
    candidates = _candidates(run, project_candidates)
    try:
        trace, trace_id, project, _ = _resolve_trace(run, candidates, _CACHE_TTL_DETAIL)
    except PhoenixUnavailable as exc:
        logger.warning("Phoenix unavailable while loading span %s: %s", span_id, exc)
        return {"available": False, "reason": "phoenix_unavailable", "message": "Phoenix is not reachable right now.", "span": None}
    if trace is None:
        return {"available": False, "reason": "trace_not_found", "message": "No Phoenix trace was found for this run yet.", "span": None}
    node = next(
        (n for n in trace.get("spanNodes") or [] if str(n.get("spanId")) == str(span_id)),
        None,
    )
    if node is None:
        return {"available": False, "reason": "span_not_found", "message": "That span is not in this run's trace.", "span": None}
    return {
        "available": True,
        "reason": None,
        "message": None,
        "trace_id": trace_id,
        "project": project,
        "span": normalize_span(node, detail=True),
    }
