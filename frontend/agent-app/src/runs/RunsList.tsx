/** Tenant-scoped runs list with the filters a developer actually triages with.
 *
 * Rows come exclusively from ``GET /api/v1/runs``: the server already returns
 * only the runs the caller may see (owned runs, plus runs of agent definitions
 * granted to the caller's roles), so the client never widens visibility.  The
 * filters below are client-side refinements of that already-scoped set.
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { AdminDataTable, type Column } from "../admin/AdminDataTable";
import type { RunRow } from "../types";
import { fetchRuns } from "./runsApi";
import { formatWhen, latency, latencyExact, statusLabel, userLabel } from "./runUtils";
import "./runsList.css";

interface RunsListProps {
  onSelect: (runId: string) => void;
}

interface ListState {
  loading: boolean;
  runs: RunRow[];
  total: number;
  error: string | null;
  forbidden: boolean;
}

/** One fetch, paged client-side: a single page-size control, not two. */
const LOAD_LIMIT = 200;

const STATUS_COLORS: Record<string, string> = {
  running: "var(--info-color)",
  pending: "var(--warning-color)",
  awaiting_approval: "var(--warning-color)",
  error: "var(--danger-color)",
  cancelled: "var(--text-muted)",
  success: "var(--success-color)",
  done: "var(--success-color)",
  completed: "var(--success-color)",
};

function hasError(run: RunRow): boolean {
  return (run.status || "").toLowerCase() === "error" || Boolean((run.error || "").trim());
}

function errorSnippet(run: RunRow): string {
  const text = (run.error || "").replace(/\s+/g, " ").trim();
  if (text) return text;
  return (run.status || "").toLowerCase() === "error" ? "Run failed" : "";
}

const SELECT_STYLE: React.CSSProperties = {
  padding: "5px 26px 5px 10px",
  fontSize: "12px",
  background: "var(--input-bg)",
  color: "var(--text-primary)",
  border: "1px solid var(--input-border)",
  borderRadius: "6px",
};

export function RunsList({ onSelect }: RunsListProps) {
  const [state, setState] = useState<ListState>({ loading: true, runs: [], total: 0, error: null, forbidden: false });
  const [statusFilter, setStatusFilter] = useState("");
  const [agentFilter, setAgentFilter] = useState("");
  const [errorOnly, setErrorOnly] = useState(false);

  const load = useCallback((signal?: AbortSignal) => {
    setState((prev) => ({ ...prev, loading: true }));
    fetchRuns(LOAD_LIMIT, signal)
      .then((res) =>
        setState({
          loading: false,
          runs: res.data?.runs || [],
          total: res.data?.total ?? res.data?.runs?.length ?? 0,
          error: res.error,
          forbidden: res.status === 403,
        }),
      )
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setState({ loading: false, runs: [], total: 0, error: err instanceof Error ? err.message : "request failed", forbidden: false });
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const statuses = useMemo(
    () => Array.from(new Set(state.runs.map((run) => (run.status || "").toLowerCase()).filter(Boolean))).sort(),
    [state.runs],
  );
  const agents = useMemo(
    () => Array.from(new Set(state.runs.map((run) => run.agent_slug || "").filter(Boolean))).sort(),
    [state.runs],
  );
  const errorCount = useMemo(() => state.runs.filter(hasError).length, [state.runs]);

  const rows = useMemo(
    () =>
      state.runs.filter((run) => {
        if (statusFilter && (run.status || "").toLowerCase() !== statusFilter) return false;
        if (agentFilter && (run.agent_slug || "") !== agentFilter) return false;
        if (errorOnly && !hasError(run)) return false;
        return true;
      }),
    [state.runs, statusFilter, agentFilter, errorOnly],
  );

  const columns: Column<RunRow>[] = [
    {
      key: "agent",
      header: "Agent",
      width: "170px",
      sortValue: (row) => row.agent_slug || "",
      render: (row) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", maxWidth: "100%" }}>
          <span
            title={row.agent_slug || ""}
            style={{ fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {row.agent_slug || "—"}
          </span>
          {row.trace_id ? (
            <span
              className="aa-run-trace-dot"
              title="A Phoenix trace is available — open the run to inspect it"
              aria-label="Phoenix trace available"
            />
          ) : null}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      width: "118px",
      sortValue: (row) => row.status || "",
      render: (row) => {
        const key = (row.status || "").toLowerCase();
        const color = STATUS_COLORS[key] || "var(--text-secondary)";
        return (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "5px",
              padding: "2px 8px",
              borderRadius: "999px",
              fontSize: "11px",
              fontWeight: 600,
              color,
              background: "var(--hover-bg)",
              border: "1px solid var(--border-color)",
            }}
          >
            <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: color }} />
            {statusLabel(row.status)}
          </span>
        );
      },
    },
    {
      key: "user",
      header: "User",
      width: "92px",
      sortValue: (row) => userLabel(row),
      render: (row) => <span style={{ color: "var(--text-secondary)" }}>{userLabel(row)}</span>,
    },
    {
      key: "task",
      header: "Task",
      className: "aa-col-task",
      render: (row) => (
        <div style={{ minWidth: 0 }}>
          <div
            title={row.task || ""}
            style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--text-secondary)" }}
          >
            {row.task || "—"}
          </div>
          {hasError(row) && errorSnippet(row) ? (
            <div
              title={row.error || ""}
              style={{
                marginTop: "2px",
                fontSize: "11px",
                color: "var(--danger-color)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {errorSnippet(row)}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      key: "when",
      header: "Started",
      width: "126px",
      sortValue: (row) => row.started_at || "",
      render: (row) => <span style={{ color: "var(--text-muted)" }}>{formatWhen(row.started_at)}</span>,
    },
    {
      key: "duration",
      header: "Duration",
      width: "96px",
      className: "aa-col-num",
      sortValue: (row) => row.duration_ms ?? row.started_at ?? "",
      render: (row) => (
        <span title={latencyExact(row)} style={{ color: "var(--text-muted)", fontVariantNumeric: "tabular-nums" }}>
          {latency(row)}
        </span>
      ),
    },
  ];

  const filtered = statusFilter || agentFilter || errorOnly;
  const subtitle = state.forbidden
    ? "You do not have run visibility."
    : [
        `Showing ${rows.length} of ${state.runs.length} loaded`,
        state.total > state.runs.length ? `${state.total} total` : null,
        state.runs.length >= LOAD_LIMIT ? `most recent ${LOAD_LIMIT}` : null,
        errorCount ? `${errorCount} with errors` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <div style={{ flex: 1, overflow: "auto", padding: "16px 18px" }}>
      <div className="aa-runs-head">
        <div>
          <div style={{ fontSize: "15px", fontWeight: 700, color: "var(--text-primary)" }}>Agent Runs</div>
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>{subtitle}</div>
        </div>
        <div className="aa-runs-controls">
          <label className="aa-filter-field">
            <span className="aa-filter-label">Status</span>
            <select
              id="runs-status-filter"
              name="runs-status-filter"
              className="aa-select"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
              style={SELECT_STYLE}
            >
              <option value="">All statuses</option>
              {statuses.map((status) => (
                <option key={status} value={status}>
                  {statusLabel(status)}
                </option>
              ))}
            </select>
          </label>
          <label className="aa-filter-field">
            <span className="aa-filter-label">Agent</span>
            <select
              id="runs-agent-filter"
              name="runs-agent-filter"
              className="aa-select"
              value={agentFilter}
              onChange={(event) => setAgentFilter(event.target.value)}
              style={SELECT_STYLE}
            >
              <option value="">All agents</option>
              {agents.map((agent) => (
                <option key={agent} value={agent}>
                  {agent}
                </option>
              ))}
            </select>
          </label>
          <label className="aa-check" style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
            <input
              id="runs-errors-only"
              name="runs-errors-only"
              type="checkbox"
              checked={errorOnly}
              onChange={(event) => setErrorOnly(event.target.checked)}
            />
            Errors only
          </label>
          <button type="button" className="aa-btn" onClick={() => load()} style={{ padding: "5px 12px" }}>
            Refresh
          </button>
        </div>
      </div>

      {state.forbidden ? (
        <div className="aa-runs-notice">
          You do not have access to the observability surface. Ask an administrator to grant your
          role the observability module.
        </div>
      ) : state.error ? (
        <div className="aa-runs-notice aa-runs-notice-error">Could not load runs: {state.error}</div>
      ) : (
        <AdminDataTable
          columns={columns}
          rows={rows}
          rowKey={(row) => row.public_id || String(row.id)}
          tableClassName="aa-runs-table"
          rowClassName={(row) => (hasError(row) ? "aa-run-row-error" : undefined)}
          searchText={(row) =>
            [row.agent_slug, row.status, row.task, row.username, row.final_reply, row.trace_id].filter(Boolean).join(" ")
          }
          searchPlaceholder="Search runs, tasks, status…"
          onRowClick={(row) => onSelect(row.public_id || String(row.id))}
          emptyMessage={state.loading ? "Loading runs…" : filtered ? "No runs match these filters." : "No runs visible to you yet."}
          defaultPageSize={25}
          compact
        />
      )}
    </div>
  );
}
