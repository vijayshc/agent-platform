"""Arize Phoenix & OpenTelemetry integration for LangGraph and Agent Platform.

Uses standard out-of-the-box OpenInference and Arize Phoenix OpenTelemetry
instrumentation for LangGraph and LangChain.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from opentelemetry.sdk.trace import SpanProcessor

logger = logging.getLogger("text2sql.observability")

_tracer_providers: dict[str, Any] = {}
_providers_lock = threading.Lock()

# Flushing the Phoenix batch on every streamed token was a major hot-path cost
# (synchronous, under a global lock, per token). We throttle it so the batch is
# flushed at most this often, trading a tiny amount of trace latency on the
# stream for a large reduction in per-token lock contention.
_FLUSH_INTERVAL_SECONDS = 5.0
_last_flush: dict[str, float] = {}
_flush_guard = threading.Lock()


def _span_metadata(attrs: dict[str, Any]) -> dict[str, Any]:
    """Read the run metadata OpenInference attaches to a span.

    LangChain instrumentation records the graph-config metadata as one
    ``metadata`` attribute holding a JSON object (Phoenix later expands it into
    nested keys when displaying), so a flat lookup is not enough.
    """
    raw = attrs.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class _RunTraceSpanProcessor(SpanProcessor):
    """Persist a run's Phoenix trace/root-span id straight from exported spans.

    Reading the id off LangChain callbacks is not reliable here: LangGraph's own
    instrumentation can suppress the explicit OpenInference tracer's root span, so
    the app's spans are emitted by a tracer this module never sees.  Every
    instrumented span *does* carry the run metadata the host attaches to the graph
    config (``metadata.run_id``/``metadata.session_id``), so observing the span
    stream is the one place guaranteed to see the real trace id.  At most two
    writes per run: the trace id from the first span that ends, the root span id
    when the root span itself ends.
    """

    def __init__(self) -> None:
        self._trace_recorded: set[int] = set()
        self._root_recorded: set[int] = set()
        self._lock = threading.Lock()

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        return None

    def on_end(self, span: Any) -> None:
        attrs = getattr(span, "attributes", None) or {}
        metadata = _span_metadata(attrs)
        raw_run_id = metadata.get("run_id", attrs.get("metadata.run_id"))
        if raw_run_id in (None, ""):
            return
        try:
            run_id = int(raw_run_id)
        except (TypeError, ValueError):
            return
        context = span.get_span_context()
        trace_id = getattr(context, "trace_id", 0) or 0
        if not trace_id:
            return
        is_root = getattr(span, "parent", None) is None
        with self._lock:
            need_trace = run_id not in self._trace_recorded
            need_root = is_root and run_id not in self._root_recorded
            if not need_trace and not need_root:
                return
            if need_trace:
                self._trace_recorded.add(run_id)
            if need_root:
                self._root_recorded.add(run_id)
        session = metadata.get("session_id") or attrs.get("session.id") or attrs.get("metadata.session_id")
        project = metadata.get("agent_name") or attrs.get("metadata.agent_name")
        try:
            from src.agent_platform.execution.run_store import RunStore

            RunStore.set_trace(
                run_id,
                trace_id=format(trace_id, "032x") if need_trace else None,
                root_span_id=format(getattr(context, "span_id", 0) or 0, "016x") if need_root else None,
                session_id=str(session) if session else None,
                project=str(project) if project else None,
            )
        except Exception:
            with self._lock:
                if need_trace:
                    self._trace_recorded.discard(run_id)
                if need_root:
                    self._root_recorded.discard(run_id)
            logger.debug("could not persist Phoenix trace id for run %s", run_id, exc_info=True)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


def get_agent_tracer_provider(project_name: str | None = None) -> Any:
    """Get or create standard Phoenix TracerProvider for a specific project."""
    name = (project_name or "Default").strip() or "Default"
    with _providers_lock:
        if name in _tracer_providers:
            return _tracer_providers[name]

        from src.services.phoenix_service import get_phoenix_url, start_phoenix_server

        start_phoenix_server()
        endpoint = f"{get_phoenix_url()}/v1/traces"

        from phoenix.otel import register

        tp = register(
            endpoint=endpoint,
            project_name=name,
            batch=False,
            set_global_tracer_provider=False,
            verbose=False,
        )
        try:
            # Phoenix's TracerProvider.add_span_processor *replaces and shuts down*
            # the default exporter processor unless told otherwise; passing the
            # flag is what keeps spans flowing to Phoenix while we observe them.
            tp.add_span_processor(_RunTraceSpanProcessor(), replace_default_processor=False)
        except TypeError:
            logger.warning(
                "Phoenix TracerProvider cannot keep its default processor when "
                "another is added; run trace ids will be resolved from Phoenix metadata."
            )
        except Exception as exc:
            logger.warning("Could not attach the run trace-id processor: %s", exc)
        _tracer_providers[name] = tp
        return tp


def setup_observability(project_name: str | None = None):
    """Initialize Arize Phoenix observability server without global auto-capturing."""
    try:
        from src.services.phoenix_service import start_phoenix_server

        start_phoenix_server()
        logger.info("Arize Phoenix server initialized for agent tracing")
    except Exception as e:
        logger.warning("Observability setup failed: %s", e)


def get_agent_tracer_callback(agent_name: str | None = None) -> list[Any]:
    """Return standard OpenInference callback handler for a given agent project."""
    try:
        from openinference.instrumentation.langchain._tracer import OpenInferenceTracer

        tp = get_agent_tracer_provider(agent_name)
        tracer = tp.get_tracer("openinference-langchain")
        return [OpenInferenceTracer(tracer=tracer, separate_trace_from_runtime_context=False)]
    except Exception as exc:
        logger.warning("Could not instantiate OpenInferenceTracer: %s", exc)
        return []


class SpanTracker:
    def __init__(self, run_id: int, agent_name: Optional[str] = None):
        self.run_id = run_id
        self.agent_name = agent_name


def flush_agent_traces(agent_name: Optional[str] = None) -> None:
    """Force flush all buffered spans to Phoenix collector.

    Throttled so it does not run once per streamed token. The first flush for a
    given agent project is immediate; subsequent flushes within
    ``_FLUSH_INTERVAL_SECONDS`` are skipped. A final flush is still guaranteed by
    ``stop_span_capture`` on run completion.
    """
    now = time.monotonic()
    with _flush_guard:
        last = _last_flush.get(agent_name or "", 0.0)
        if now - last < _FLUSH_INTERVAL_SECONDS:
            return
        _last_flush[agent_name or ""] = now
    with _providers_lock:
        if agent_name and agent_name in _tracer_providers:
            providers = [_tracer_providers[agent_name]]
        else:
            providers = list(_tracer_providers.values())
    for tp in providers:
        if tp and hasattr(tp, "force_flush"):
            try:
                tp.force_flush()
            except Exception:
                pass


def start_span_capture(run_id: int, agent_name: Optional[str] = None, conversation_id: Optional[str] = None) -> SpanTracker:
    return SpanTracker(run_id, agent_name=agent_name)


def stop_span_capture(tracker: Optional[SpanTracker]):
    if tracker is not None and tracker.agent_name:
        flush_agent_traces(tracker.agent_name)
