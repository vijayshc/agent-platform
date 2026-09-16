/** Observability page: tenant-scoped agent runs, plus Phoenix for admins.
 *
 * The in-app runs list/detail is the tenant-scoped observability surface: it
 * renders only what ``/api/v1/runs`` returns, so a user sees their own runs and
 * the runs of agents granted to their roles.  The raw Phoenix embed remains
 * available only when its admin-only proxy answers, because Phoenix cannot be
 * tenant-scoped per request.
 */
import { useEffect, useMemo, useState } from "react";

import { PhoenixTraces } from "./PhoenixTraces";
import { RunDetail } from "./RunDetail";
import { RunsList } from "./RunsList";
import { runParam } from "./runUtils";
import { phoenixProxyAvailable } from "./runsApi";

type View = "runs" | "phoenix";

function initialView(): View {
  const query = new URLSearchParams(window.location.search);
  return query.get("view") === "phoenix" ? "phoenix" : "runs";
}

function syncUrl(runId: string | null, view: View) {
  try {
    const url = new URL(window.location.href);
    if (runId) url.searchParams.set("run", runId);
    else url.searchParams.delete("run");
    if (view === "phoenix") url.searchParams.set("view", "phoenix");
    else url.searchParams.delete("view");
    window.history.replaceState({}, "", `${url.pathname}${url.search}`);
  } catch {
    /* history may be unavailable in an embed */
  }
}

export function RunsPage() {
  const [selectedRun, setSelectedRun] = useState<string | null>(() => runParam());
  const [view, setView] = useState<View>(() => initialView());
  const [phoenixAvailable, setPhoenixAvailable] = useState(false);

  useEffect(() => {
    let active = true;
    phoenixProxyAvailable().then((ok) => {
      if (active) setPhoenixAvailable(ok);
    });
    return () => {
      active = false;
    };
  }, []);

  const activeView = useMemo<View>(
    () => (view === "phoenix" && phoenixAvailable ? "phoenix" : "runs"),
    [view, phoenixAvailable],
  );

  const openRun = (runId: string) => {
    setSelectedRun(runId);
    syncUrl(runId, activeView);
  };

  const backToList = () => {
    setSelectedRun(null);
    syncUrl(null, activeView);
  };

  const switchView = (next: View) => {
    setView(next);
    syncUrl(selectedRun, next);
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    padding: "5px 12px",
    fontSize: "12px",
    fontWeight: active ? 600 : 500,
    color: active ? "var(--text-primary)" : "var(--text-muted)",
    background: active ? "var(--card-bg)" : "transparent",
    border: "1px solid var(--border-color)",
    borderRadius: "6px",
    cursor: "pointer",
  });

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        minHeight: 0,
        overflow: "hidden",
        background: "var(--light-bg)",
      }}
    >
      {phoenixAvailable && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "8px 18px",
            background: "var(--card-bg)",
            borderBottom: "1px solid var(--border-color)",
          }}
        >
          <button type="button" className="aa-btn" style={tabStyle(activeView === "runs")} onClick={() => switchView("runs")}>
            Agent Runs
          </button>
          <button type="button" className="aa-btn" style={tabStyle(activeView === "phoenix")} onClick={() => switchView("phoenix")}>
            Phoenix Traces
          </button>
        </div>
      )}

      {activeView === "phoenix" ? (
        <PhoenixTraces />
      ) : selectedRun ? (
        <RunDetail runId={selectedRun} onBack={backToList} />
      ) : (
        <RunsList onSelect={openRun} />
      )}
    </div>
  );
}
