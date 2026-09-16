/** One run's trace explorer: waterfall + span inspector, read live from Phoenix.
 *
 * Every field comes from ``GET /api/v1/runs/<id>`` (run metadata) and
 * ``GET /api/v1/runs/<id>/trace`` (the run's spans, resolved server-side to
 * *this run's* Phoenix trace).  Deep-linking to a run the caller cannot see
 * (403) or that no longer exists (404) renders a clean "not available" state,
 * and an unreachable / not-yet-ingested Phoenix renders an honest empty state --
 * never a crash or a blank page.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { RunTrace, TraceSpan } from "../types";
import { CopyChip } from "./traceBits";
import { RunSummaryCards } from "./RunSummaryCards";
import { SpanDetailPane } from "./SpanDetailPane";
import { TraceToolbar } from "./TraceToolbar";
import { TraceWaterfall } from "./TraceWaterfall";
import "./traceExplorer.css";
import { formatEventWhen, latency, statusLabel, userLabel } from "./runUtils";
import {
  buildTraceTree,
  filterTraceTree,
  flattenTraceTree,
  traceBounds,
  traceSpanIsError,
  type TraceSortMode,
} from "./traceUtils";
import {
  fetchRun,
  fetchRunSpanDetail,
  fetchRunTrace,
  phoenixProxyAvailable,
  type RunDetail as RunDetailData,
} from "./runsApi";

interface RunDetailProps {
  runId: string;
  onBack: () => void;
}

const LIVE_STATUSES = ["running", "pending", "awaiting_approval", "cancelling"];
const POLL_MS = 4000;

function Unavailable({ status, message, onBack }: { status: number; message: string; onBack: () => void }) {
  const notFound = status === 403 || status === 404;
  return (
    <div className="aa-tx-empty" data-testid="run-unavailable">
      <div className="aa-tx-empty-card">
        <div className="aa-tx-empty-icon">{notFound ? "×" : "!"}</div>
        <div className="aa-tx-empty-title">{notFound ? "Run not available" : "Could not load run"}</div>
        <div className="aa-tx-empty-copy">
          {notFound
            ? "This run does not exist, or you do not have access to the agent that produced it."
            : message}
        </div>
        <div className="aa-tx-empty-actions">
          <button type="button" className="aa-btn" onClick={onBack}>
            ← Back to runs
          </button>
        </div>
      </div>
    </div>
  );
}

function TraceEmpty({
  trace,
  run,
  onRefresh,
}: {
  trace: RunTrace;
  run: RunDetailData;
  onRefresh: () => void;
}) {
  const reason = trace.reason || "trace_not_found";
  const copy: Record<string, { icon: string; title: string; body: string }> = {
    phoenix_unavailable: {
      icon: "!",
      title: "Phoenix is not reachable",
      body: "The trace store could not be queried right now. The run itself is safe — retry once Phoenix is back.",
    },
    no_trace: {
      icon: "i",
      title: "No trace recorded for this run",
      body: "This run finished before trace capture was enabled, so there are no Phoenix spans to show.",
    },
    trace_not_found: {
      icon: "i",
      title: "Trace not in Phoenix yet",
      body: "The run finished but its spans have not reached Phoenix yet. Refresh in a moment.",
    },
  };
  const view = copy[reason] || copy.trace_not_found;
  const reply = (run.final_reply || "").trim();
  return (
    <div className="aa-tx-empty-stack" data-testid="trace-unavailable">
      <div className="aa-tx-empty-card">
        <div className="aa-tx-empty-icon">{view.icon}</div>
        <div className="aa-tx-empty-title">{view.title}</div>
        <div className="aa-tx-empty-copy">{trace.message || view.body}</div>
        <div className="aa-tx-empty-actions">
          {trace.trace_id ? <CopyChip label="trace" value={trace.trace_id} /> : null}
          <button type="button" className="aa-btn" onClick={onRefresh}>
            Refresh
          </button>
        </div>
      </div>
      {/* The trace is missing, but the run's own record is still the developer's
          best signal about what the agent did. */}
      {run.task || reply || run.error ? (
        <div className="aa-tx-empty-card aa-tx-local-run">
          <div className="aa-tx-empty-title" style={{ textAlign: "left" }}>
            Run record
          </div>
          {run.task ? (
            <div className="aa-span-field">
              <div className="aa-span-field-label">Task</div>
              <div className="aa-turn-body">{run.task}</div>
            </div>
          ) : null}
          {run.error ? (
            <div className="aa-span-field">
              <div className="aa-span-field-label">Error</div>
              <div className="aa-tx-error-body">{run.error}</div>
            </div>
          ) : null}
          {reply ? (
            <div className="aa-span-field">
              <div className="aa-span-field-label">Final reply</div>
              <div className="aa-turn-body">{reply}</div>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="aa-tx-skeleton" data-testid="run-detail-loading">
      <div className="aa-tx-skeleton-head" />
      <div className="aa-tx-skeleton-cards">
        {Array.from({ length: 6 }, (_, index) => (
          <div className="aa-tx-skeleton-card" key={index} />
        ))}
      </div>
      <div className="aa-tx-skeleton-body" />
    </div>
  );
}

function TraceWaiting() {
  return (
    <div className="aa-tx-empty" data-testid="trace-waiting">
      <div className="aa-tx-empty-card">
        <div className="aa-tx-empty-icon">◌</div>
        <div className="aa-tx-empty-title">Waiting for the first spans</div>
        <div className="aa-tx-empty-copy">
          This run is still executing. Spans appear here as soon as they reach Phoenix — this view
          refreshes automatically.
        </div>
      </div>
    </div>
  );
}

export function RunDetail({ runId, onBack }: RunDetailProps) {
  const [run, setRun] = useState<RunDetailData | null>(null);
  const [runStatus, setRunStatus] = useState(0);
  const [runError, setRunError] = useState<string | null>(null);
  const [runLoading, setRunLoading] = useState(true);
  const [trace, setTrace] = useState<RunTrace | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, TraceSpan>>({});
  const [detailLoading, setDetailLoading] = useState(false);
  const detailsRef = useRef<Record<string, TraceSpan>>({});
  const [reloadTick, setReloadTick] = useState(0);
  const [phoenixOk, setPhoenixOk] = useState(false);

  const [kind, setKind] = useState("");
  const [errorOnly, setErrorOnly] = useState(false);
  const [query, setQuery] = useState("");
  const [sortMode, setSortMode] = useState<TraceSortMode>("time");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [taskExpanded, setTaskExpanded] = useState(false);

  const refresh = useCallback(() => setReloadTick((tick) => tick + 1), []);

  useEffect(() => {
    phoenixProxyAvailable().then(setPhoenixOk).catch(() => setPhoenixOk(false));
  }, []);

  // Reset per-run view state when the deep link changes.
  useEffect(() => {
    setSelectedId(null);
    setCollapsed(new Set());
    setKind("");
    setErrorOnly(false);
    setQuery("");
    setTaskExpanded(false);
    setRun(null);
    setTrace(null);
    setDetails({});
    detailsRef.current = {};
    setRunLoading(true);
  }, [runId]);

  useEffect(() => {
    const controller = new AbortController();
    setRunError(null);
    fetchRun(runId, controller.signal)
      .then((result) => {
        setRun(result.data);
        setRunStatus(result.status);
        setRunError(result.error);
        setRunLoading(false);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setRunError(error instanceof Error ? error.message : "request failed");
        setRunLoading(false);
      });
    return () => controller.abort();
  }, [runId, reloadTick]);

  useEffect(() => {
    const controller = new AbortController();
    setTraceError(null);
    fetchRunTrace(runId, controller.signal)
      .then((result) => {
        if (result.status === 403 || result.status === 404) return;
        setTrace(result.data);
        setTraceError(result.error);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setTraceError(error instanceof Error ? error.message : "request failed");
      });
    return () => controller.abort();
  }, [runId, reloadTick]);

  const live = LIVE_STATUSES.includes(run?.status || "");

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, refresh]);

  const spans = useMemo(() => trace?.spans || [], [trace]);
  const tree = useMemo(() => buildTraceTree(spans, sortMode), [spans, sortMode]);
  const bounds = useMemo(() => traceBounds(spans, trace?.summary), [spans, trace]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!kind && !errorOnly && !needle) return tree;
    return filterTraceTree(tree, (span) => {
      if (kind && (span.kind || "UNKNOWN").toUpperCase() !== kind) return false;
      if (errorOnly && !traceSpanIsError(span)) return false;
      if (needle) {
        const haystack = [span.name, span.tool_name, span.model, span.node, span.agent_name, span.kind]
          .filter(Boolean)
          .join(" ")
          .toLowerCase();
        if (!haystack.includes(needle)) return false;
      }
      return true;
    });
  }, [tree, kind, errorOnly, query]);

  const matchCount = useMemo(() => flattenTraceTree(filtered, new Set()).length, [filtered]);

  // Default the inspector to a useful span: the first failure, else the root.
  useEffect(() => {
    if (!spans.length) return;
    if (selectedId && spans.some((span) => span.id === selectedId)) return;
    const failure = spans.find((span) => traceSpanIsError(span));
    setSelectedId((failure || spans[0]).id);
  }, [spans, selectedId]);

  // Waterfall rows are light (no payloads); the selected span's payloads are
  // fetched on demand and cached.  Live runs refresh the selection each poll.
  useEffect(() => {
    if (!selectedId || !trace?.available) return;
    if (detailsRef.current[selectedId] && !live) return;
    const controller = new AbortController();
    setDetailLoading(true);
    fetchRunSpanDetail(runId, selectedId, controller.signal)
      .then((result) => {
        if (result.data?.available && result.data.span) {
          const detail = result.data.span;
          detailsRef.current = { ...detailsRef.current, [selectedId]: detail };
          setDetails((previous) => ({ ...previous, [selectedId]: detail }));
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
      })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [selectedId, runId, trace?.available, live, reloadTick]);

  const summarySpan = useMemo(
    () => spans.find((span) => span.id === selectedId) || null,
    [spans, selectedId],
  );
  const selectedSpan = useMemo(
    () => (selectedId ? details[selectedId] || summarySpan : null),
    [details, selectedId, summarySpan],
  );

  const toggleCollapse = useCallback((spanId: string) => {
    setCollapsed((previous) => {
      const next = new Set(previous);
      if (next.has(spanId)) next.delete(spanId);
      else next.add(spanId);
      return next;
    });
  }, []);

  const collapseAll = useCallback(() => {
    const parents = new Set<string>();
    const walk = (nodes: typeof tree) => {
      for (const node of nodes) {
        if (node.hasChildren) parents.add(node.id);
        walk(node.children);
      }
    };
    walk(tree);
    setCollapsed(parents);
  }, [tree]);

  if (runLoading) {
    return (
      <div className="aa-tx-page">
        <LoadingSkeleton />
      </div>
    );
  }

  if (!run) {
    return (
      <div className="aa-tx-page">
        <Unavailable status={runStatus} message={runError || "Run unavailable"} onBack={onBack} />
      </div>
    );
  }

  const traceId = trace?.trace_id || run.trace_id || null;
  const sessionId = trace?.session_id || run.session_id || null;
  const project = trace?.project || run.phoenix_project || null;
  const phoenixHref =
    phoenixOk && traceId && project
      ? `/api/v1/phoenix/proxy/projects/${encodeURIComponent(project)}/traces/${encodeURIComponent(traceId)}`
      : null;
  const taskIsLong = (run.task || "").length > 180;

  return (
    <div className="aa-tx-page" data-testid="run-detail">
      <div className="aa-tx-headbar">
        <button type="button" className="aa-btn aa-tx-back" onClick={onBack}>
          ← Back to runs
        </button>
        <div className="aa-tx-headmain">
          <div className="aa-tx-title-row">
            <span className="aa-tx-agent">{run.agent_slug || "agent"}</span>
            <span className={`aa-status-pill ${run.status || ""}`}>{statusLabel(run.status)}</span>
            {live ? (
              <span className="aa-tx-live">
                <span className="aa-tx-live-dot" />
                Live
              </span>
            ) : null}
            {latency(run) ? (
              <span className="aa-chip-meta" title="Wall-clock time from run start to finish">
                run {latency(run)}
              </span>
            ) : null}
            {run.started_at ? <span className="aa-chip-meta">{formatEventWhen(run.started_at)}</span> : null}
            <span className="aa-chip-meta">{userLabel(run)}</span>
          </div>
          {run.task ? (
            <>
              <div className={`aa-tx-task${taskExpanded ? " expanded" : ""}`}>{run.task}</div>
              {taskIsLong ? (
                <button type="button" className="aa-tx-task-toggle" onClick={() => setTaskExpanded((v) => !v)}>
                  {taskExpanded ? "Show less" : "Show full task"}
                </button>
              ) : null}
            </>
          ) : null}
          {run.error ? <div className="aa-tx-error-line">{run.error}</div> : null}
          <div className="aa-tx-ids">
            {traceId ? <CopyChip label="trace" value={traceId} /> : null}
            {sessionId ? <CopyChip label="session" value={sessionId} /> : null}
            {run.root_span_id ? <CopyChip label="root span" value={String(run.root_span_id)} /> : null}
          </div>
        </div>
        <div className="aa-tx-headactions">
          {phoenixHref ? (
            <a className="aa-btn" href={phoenixHref} target="_blank" rel="noreferrer" title="Open this trace in the Phoenix UI">
              Open in Phoenix ↗
            </a>
          ) : null}
          <button type="button" className="aa-btn" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>

      {trace?.available && trace.summary ? <RunSummaryCards trace={trace} run={run} /> : null}

      {!trace ? (
        traceError ? (
          <Unavailable status={0} message={traceError} onBack={onBack} />
        ) : (
          <LoadingSkeleton />
        )
      ) : !trace.available ? (
        live ? (
          <TraceWaiting />
        ) : (
          <TraceEmpty trace={trace} run={run} onRefresh={refresh} />
        )
      ) : (
        <>
          <TraceToolbar
            spans={spans}
            kind={kind}
            onKindChange={setKind}
            errorOnly={errorOnly}
            onErrorOnlyChange={setErrorOnly}
            query={query}
            onQueryChange={setQuery}
            sortMode={sortMode}
            onSortModeChange={setSortMode}
            onExpandAll={() => setCollapsed(new Set())}
            onCollapseAll={collapseAll}
            matchCount={matchCount}
          />
          {filtered.length === 0 ? (
            /* Filters emptied the view: show the empty state instead of a stale
               inspector and a dangling time axis. */
            <div className="aa-tx-empty" data-testid="trace-no-match">
              <div className="aa-tx-empty-card">
                <div className="aa-tx-empty-icon">∅</div>
                <div className="aa-tx-empty-title">No spans match the current filters</div>
                <div className="aa-tx-empty-copy">
                  {spans.length} span{spans.length === 1 ? "" : "s"} in this trace; 0 match the active
                  filters.
                </div>
                <div className="aa-tx-empty-actions">
                  <button
                    type="button"
                    className="aa-btn"
                    onClick={() => {
                      setKind("");
                      setErrorOnly(false);
                      setQuery("");
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="aa-run-split aa-tx-split">
              <TraceWaterfall
                nodes={filtered}
                bounds={bounds}
                selectedId={selectedId}
                onSelect={setSelectedId}
                collapsed={collapsed}
                onToggle={toggleCollapse}
                live={live}
                totalSpans={spans.length}
              />
              <SpanDetailPane
                span={selectedSpan}
                bounds={bounds}
                loading={detailLoading && Boolean(selectedId) && !details[selectedId as string]}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
