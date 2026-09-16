/** Embedded Arize Phoenix view (administrator-only).
 *
 * The raw Phoenix proxy is admin-only because Phoenix's own project store cannot
 * be tenant-scoped per request; everyone else uses the runs list, which the
 * server scopes to the agents they may access.  This component is the original
 * embedded view, unchanged, and is only mounted when the proxy is reachable.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { apiGet } from "../api";
import { syncPhoenixThemeFromApp } from "./phoenixTheme";

interface PhoenixStatus {
  url: string;
  healthy: boolean;
  project: string;
}

interface PhoenixProject {
  id: string;
  name: string;
  traceCount: number;
}

export function PhoenixTraces() {
  const [status, setStatus] = useState<PhoenixStatus | null>(null);
  const [projects, setProjects] = useState<PhoenixProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>("all");
  const [activeTab, setActiveTab] = useState<"sessions" | "traces">("sessions");
  const [loading, setLoading] = useState(true);
  const [iframeKey, setIframeKey] = useState(0);

  const fetchStatusAndProjects = useCallback(async () => {
    try {
      const [statusData, projData] = await Promise.all([
        apiGet<PhoenixStatus>("/api/v1/phoenix/status"),
        apiGet<{ projects: PhoenixProject[] }>("/api/v1/phoenix/projects"),
      ]);
      setStatus(statusData);
      setProjects(projData.projects || []);
    } catch {
      setStatus({
        url: "/api/v1/phoenix/proxy",
        healthy: false,
        project: "default",
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatusAndProjects();
    const timer = window.setInterval(fetchStatusAndProjects, 8000);
    return () => window.clearInterval(timer);
  }, [fetchStatusAndProjects]);

  // Phoenix defaults to dark and stores its own key. Map the host app theme
  // (light / lightColored → light, dark → dark) before the iframe boots, and
  // remount the iframe when the operator switches themes.
  useLayoutEffect(() => {
    syncPhoenixThemeFromApp();
  }, []);

  useEffect(() => {
    const onThemeChanged = () => {
      syncPhoenixThemeFromApp();
      setIframeKey((k) => k + 1);
    };
    document.addEventListener("themeChanged", onThemeChanged);
    return () => document.removeEventListener("themeChanged", onThemeChanged);
  }, []);

  // Default is "All Agent Projects" (selectedProjectId === "all"). We do NOT
  // auto-select a single project on load: aggregating every agent's traces in
  // one view is the expected enterprise observability default, and the operator
  // can drill into a specific project from the dropdown.

  const handleRefresh = () => {
    fetchStatusAndProjects();
    setIframeKey((k) => k + 1);
  };

  const phoenixBaseUrl = status?.url || "/api/v1/phoenix/proxy";

  const activeIframeUrl = useMemo(() => {
    if (selectedProjectId === "all" || !selectedProjectId) {
      return `${phoenixBaseUrl}/projects`;
    }
    if (activeTab === "sessions") {
      return `${phoenixBaseUrl}/projects/${selectedProjectId}/sessions`;
    }
    return `${phoenixBaseUrl}/projects/${selectedProjectId}`;
  }, [phoenixBaseUrl, selectedProjectId, activeTab]);

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%", height: "100%", overflow: "hidden", background: "var(--bg)" }}>
      {/* Header bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 18px",
          background: "var(--card-bg)",
          borderBottom: "1px solid var(--border-color)",
          flexShrink: 0,
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <span style={{ fontWeight: 700, fontSize: "14px", color: "var(--text-primary)" }}>
            Arize Phoenix Observability
          </span>
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "5px",
              padding: "2px 8px",
              borderRadius: "999px",
              fontSize: "11px",
              fontWeight: 600,
              background: status?.healthy ? "rgba(16, 185, 129, 0.15)" : "rgba(239, 68, 68, 0.15)",
              color: status?.healthy ? "#10b981" : "#ef4444",
              border: `1px solid ${status?.healthy ? "rgba(16, 185, 129, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
            }}
          >
            <span
              style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                background: status?.healthy ? "#10b981" : "#ef4444",
              }}
            />
            {status?.healthy ? "Live Traces Active" : "Connecting..."}
          </span>
        </div>

        {/* Project Selector & Actions */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <label htmlFor="agent-project-select" style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-muted)" }}>
              Agent Project:
            </label>
            <select
              id="agent-project-select"
              value={selectedProjectId}
              onChange={(e) => setSelectedProjectId(e.target.value)}
              className="aa-select"
              style={{
                padding: "5px 10px",
                fontSize: "12px",
                fontWeight: 500,
                background: "var(--hover-bg)",
                color: "var(--text-primary)",
                border: "1px solid var(--border-color)",
                borderRadius: "6px",
                cursor: "pointer",
              }}
            >
              <option value="all">📂 All Agent Projects ({projects.reduce((acc, p) => acc + (p.traceCount || 0), 0)} traces)</option>
              {projects
                .filter((p) => p.name !== "default")
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    🤖 {p.name} ({p.traceCount || 0} traces)
                  </option>
                ))}
            </select>
          </div>

          {selectedProjectId !== "all" && (
            <div
              style={{
                display: "inline-flex",
                background: "var(--hover-bg)",
                padding: "2px",
                borderRadius: "6px",
                border: "1px solid var(--border-color)",
                gap: "2px",
              }}
            >
              <button
                type="button"
                onClick={() => setActiveTab("sessions")}
                className="aa-btn"
                style={{
                  padding: "4px 10px",
                  fontSize: "12px",
                  fontWeight: activeTab === "sessions" ? 600 : 400,
                  background: activeTab === "sessions" ? "var(--card-bg)" : "transparent",
                  color: activeTab === "sessions" ? "var(--text-primary)" : "var(--text-muted)",
                  boxShadow: activeTab === "sessions" ? "0 1px 2px rgba(0,0,0,0.1)" : "none",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                💬 Sessions
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("traces")}
                className="aa-btn"
                style={{
                  padding: "4px 10px",
                  fontSize: "12px",
                  fontWeight: activeTab === "traces" ? 600 : 400,
                  background: activeTab === "traces" ? "var(--card-bg)" : "transparent",
                  color: activeTab === "traces" ? "var(--text-primary)" : "var(--text-muted)",
                  boxShadow: activeTab === "traces" ? "0 1px 2px rgba(0,0,0,0.1)" : "none",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer",
                }}
              >
                ⚡ Traces
              </button>
            </div>
          )}

          <button
            type="button"
            onClick={handleRefresh}
            className="aa-btn"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "5px 12px",
              fontSize: "12px",
              fontWeight: 500,
              background: "var(--hover-bg)",
              border: "1px solid var(--border-color)",
              borderRadius: "6px",
              cursor: "pointer",
              color: "var(--text-primary)",
            }}
          >
            Refresh
          </button>
          <a
            href={activeIframeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="aa-btn aa-btn-primary"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "5px 12px",
              fontSize: "12px",
              fontWeight: 600,
              background: "#3b82f6",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              textDecoration: "none",
              cursor: "pointer",
            }}
          >
            <span>↗</span> Open in New Tab
          </a>
        </div>
      </div>

      {/* Embedded Phoenix View */}
      <div style={{ flex: 1, position: "relative", width: "100%", height: "100%", minHeight: 0 }}>
        {loading && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              background: "var(--bg)",
              color: "var(--text-muted)",
              fontSize: "13px",
              zIndex: 1,
            }}
          >
            Loading Arize Phoenix AI Observability...
          </div>
        )}
        <iframe
          key={`${iframeKey}-${activeIframeUrl}`}
          src={activeIframeUrl}
          title="Arize Phoenix AI Observability"
          style={{
            width: "100%",
            height: "100%",
            border: "none",
            display: "block",
          }}
          allow="clipboard-read; clipboard-write"
        />
      </div>
    </div>
  );
}
