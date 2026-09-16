import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPostJson } from "../api";
import type { AgentDef } from "../types";
import { AccessDialog } from "./inspector/AccessDialog";
import { ApiIntegrationDialog } from "./inspector/ApiIntegrationDialog";
import { DataTable } from "../shared/DataTable";
import { ActionsMenu } from "../shared/ActionsMenu";
import { buildAgentFile, buildExportBundle, downloadJson, parseImportFile } from "./agentTransfer";
import { blueprintLabel } from "./model/catalog";

/** Runtimes, both the v2 id and the pre-v2 spelling. */
const RUNTIME_LABELS: Record<string, string> = {
  agent: "Single Agent",
  deep_agent: "Deep Agent",
  harness: "Deep Agent",
};

function orchestrationLabel(agent: AgentDef): { label: string; kind: string } {
  const config = (agent.config || {}) as Record<string, unknown>;
  if (agent.kind === "workflow") {
    const pattern = String(agent.pattern || config.pattern || "graph");
    const template = String(config.template ?? agent.template ?? "");
    // A graph flow is named by the blueprint it was built from; supervisor and
    // swarm keep their own pattern name.
    return { label: blueprintLabel(pattern, template) || "Workflow", kind: "workflow" };
  }
  const runtime = String(config.runtime ?? "agent");
  return { label: RUNTIME_LABELS[runtime] || "Single Agent", kind: "agent" };
}

function formatDate(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusLabel(agent: AgentDef): string {
  return agent.published ? "Published" : "Draft";
}

export function AgentListPage() {
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [manageAgent, setManageAgent] = useState<AgentDef | null>(null);
  const [integrationAgent, setIntegrationAgent] = useState<AgentDef | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [sortCol, setSortCol] = useState<"agent" | "orchestration" | "status" | "created" | "modified">("agent");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const handleSort = useCallback((col: "agent" | "orchestration" | "status" | "created" | "modified") => {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  }, [sortCol]);

  const sortClass = useCallback((col: "agent" | "orchestration" | "status" | "created" | "modified") => {
    if (sortCol !== col) return "sorting";
    return sortDir === "asc" ? "sorting sorting_asc" : "sorting sorting_desc";
  }, [sortCol, sortDir]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<{ agents: AgentDef[] }>("/api/v1/agents?include_drafts=1");
      setAgents(res.agents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function togglePublish(agent: AgentDef) {
    setRefreshing(true);
    setError(null);
    try {
      await apiPostJson<AgentDef>(`/api/v1/agents/${agent.slug}/publish`, {
        published: !agent.published,
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRefreshing(false);
    }
  }

  function exportAgent(agent: AgentDef) {
    downloadJson(`${agent.slug || "agent"}.json`, buildAgentFile(agent));
  }

  function exportAll() {
    if (!agents.length) return;
    const stamp = new Date().toISOString().slice(0, 10);
    downloadJson(`agents-export-${stamp}.json`, buildExportBundle(agents));
  }

  async function importAgents(file: File) {
    setImporting(true);
    setError(null);
    setNotice(null);
    try {
      const items = parseImportFile(await file.text());
      let imported = 0;
      const failures: string[] = [];
      for (const item of items) {
        try {
          await apiPostJson<AgentDef>("/api/v1/agents", {
            name: item.name,
            slug: item.slug,
            kind: item.kind,
            config: item.config,
            published: false,
          });
          imported += 1;
        } catch (e) {
          failures.push(`${item.name}: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      await load();
      if (failures.length) {
        setError(`Imported ${imported} of ${items.length}. Failed — ${failures.join("; ")}`);
      } else {
        setNotice(`Imported ${imported} agent${imported === 1 ? "" : "s"}.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setImporting(false);
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) =>
      `${a.name} ${a.slug} ${a.description} ${a.pattern || ""} ${orchestrationLabel(a).label}`
        .toLowerCase()
        .includes(q),
    );
  }, [agents, query]);

  const sorted = useMemo(() => {
    const list = [...filtered];
    list.sort((a, b) => {
      let vA: string | number = "";
      let vB: string | number = "";
      switch (sortCol) {
        case "agent":
          vA = (a.name || a.slug || "").toLowerCase();
          vB = (b.name || b.slug || "").toLowerCase();
          break;
        case "orchestration":
          vA = orchestrationLabel(a).label.toLowerCase();
          vB = orchestrationLabel(b).label.toLowerCase();
          break;
        case "status":
          vA = a.published ? 1 : 0;
          vB = b.published ? 1 : 0;
          break;
        case "created":
          vA = a.created_at ? new Date(a.created_at.replace(" ", "T")).getTime() : 0;
          vB = b.created_at ? new Date(b.created_at.replace(" ", "T")).getTime() : 0;
          break;
        case "modified":
          vA = a.updated_at ? new Date(a.updated_at.replace(" ", "T")).getTime() : 0;
          vB = b.updated_at ? new Date(b.updated_at.replace(" ", "T")).getTime() : 0;
          break;
      }
      if (vA < vB) return sortDir === "asc" ? -1 : 1;
      if (vA > vB) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return list;
  }, [filtered, sortCol, sortDir]);

  useEffect(() => {
    setPage(1);
  }, [query, agents]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const paged = sorted.slice((safePage - 1) * pageSize, safePage * pageSize);

  function onAccessChanged() {
    void load();
  }

  return (
    <div className="aa-root aa-list-root" data-testid="agent-list">
      <div className="aa-list-main">
        <header className="aa-header aa-list-header">
          <div className="aa-list-header-actions">
            <button
              type="button"
              className="aa-btn aa-btn-ghost"
              disabled={importing}
              onClick={() => fileRef.current?.click()}
              data-testid="agent-import"
            >
              {importing ? "Importing…" : "Import"}
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-ghost"
              disabled={agents.length === 0}
              onClick={exportAll}
              data-testid="agent-export"
            >
              Export
            </button>
            <a className="aa-btn aa-btn-primary" href="/agent-studio/editor" data-testid="create-agent">
              + Create Agent
            </a>
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void importAgents(f);
              e.target.value = "";
            }}
          />
        </header>

        <div className="aa-dt-toolbar">
          <div className="aa-dt-length">
            <span>Show</span>
            <select
              value={pageSize}
              onChange={(e) => {
                setPageSize(Number(e.target.value));
                setPage(1);
              }}
              data-testid="agent-page-size"
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
            <span>entries</span>
          </div>
          <div className="aa-dt-filter">
            <span>Search:</span>
            <input
              className="aa-dt-search"
              placeholder="Search agents…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="agent-list-search"
            />
          </div>
          {error && <span className="aa-error">{error}</span>}
          {!error && notice && (
            <span className="aa-notice" data-testid="agent-transfer-notice">
              {notice}
            </span>
          )}
        </div>

        <DataTable
          className="aa-table aa-dt chat-datatable-table"
          wrapperClassName="aa-list-table-wrap"
          testId="agent-table"
          tableInit="false"
        >
          <thead>
            <tr>
              <th className={sortClass("agent")} onClick={() => handleSort("agent")}>Agent</th>
              <th className={sortClass("orchestration")} onClick={() => handleSort("orchestration")}>Orchestration</th>
              <th className={sortClass("status")} onClick={() => handleSort("status")}>Status</th>
              <th className={sortClass("created")} onClick={() => handleSort("created")}>Created</th>
              <th className={sortClass("modified")} onClick={() => handleSort("modified")}>Modified</th>
              <th>Access</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="aa-table-empty">
                  Loading agents…
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={7} className="aa-table-empty">
                  {query ? "No agents match your search." : "No agents yet. Create your first agent."}
                </td>
              </tr>
            ) : (
              paged.map((agent) => {
                const orch = orchestrationLabel(agent);
                const access = agent.access || [];
                return (
                  <tr key={agent.id} data-slug={agent.slug} data-testid="agent-row">
                    <td className="aa-table-main">
                      <a className="aa-table-name" href={`/agent-studio/editor?slug=${encodeURIComponent(agent.slug)}`}>
                        {agent.name}
                      </a>
                      <div className="aa-table-sub">{agent.description || agent.slug}</div>
                    </td>
                    <td>
                      <span className={`aa-orch aa-orch-${orch.kind}`}>{orch.label}</span>
                    </td>
                    <td>
                      <span className={`aa-status-pill${agent.published ? " success" : ""}`}>
                        {statusLabel(agent)}
                      </span>
                      {agent.version != null && <span className="aa-table-version">v{agent.version}</span>}
                    </td>
                    <td className="aa-table-date">{formatDate(agent.created_at)}</td>
                    <td className="aa-table-date">
                      <div>{formatDate(agent.updated_at)}</div>
                      {agent.updated_by_name && <div className="aa-table-by">{agent.updated_by_name}</div>}
                    </td>
                    <td>
                      <div className="aa-access-chips">
                        {access.length === 0 ? (
                          <span className="aa-table-muted">Owner only</span>
                        ) : (
                          <div className="aa-chips">
                            {access.slice(0, 3).map((entry) => (
                              <span
                                className="aa-chip aa-chip-role"
                                key={entry.role_id}
                                title={entry.role_name}
                                data-testid="access-chip"
                              >
                                {entry.role_name}
                              </span>
                            ))}
                            {access.length > 3 && <span className="aa-chip">+{access.length - 3}</span>}
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="aa-table-actions">
                      <ActionsMenu
                        label="Actions"
                        testId="agent-actions"
                        items={[
                          {
                            key: "edit",
                            label: "Edit",
                            testId: "edit-agent",
                            onSelect: () => {
                              window.location.href = `/agent-studio/editor?slug=${encodeURIComponent(agent.slug)}`;
                            },
                          },
                          {
                            key: "api",
                            label: "API URL",
                            testId: "agent-api-url",
                            disabled: !agent.published,
                            title: agent.published
                              ? `Get the API URL for ${agent.name}`
                              : "Publish the agent to get its API URL",
                            onSelect: () => setIntegrationAgent(agent),
                          },
                          {
                            key: "publish",
                            label: agent.published ? "Unpublish" : "Publish",
                            testId: "toggle-publish",
                            disabled: refreshing,
                            onSelect: () => void togglePublish(agent),
                          },
                          {
                            key: "export",
                            label: "Export",
                            testId: "agent-export-row",
                            onSelect: () => exportAgent(agent),
                          },
                          // Only the owner or an administrator may change
                          // grants; the server 403s anyone else, so a granted
                          // role never sees a management affordance it cannot use.
                          ...(agent.can_manage
                            ? [
                                {
                                  key: "access",
                                  label: "Manage access",
                                  testId: "access-manage",
                                  onSelect: () => setManageAgent(agent),
                                },
                              ]
                            : []),
                        ]}
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </DataTable>

        {filtered.length > 0 && (
          <div className="aa-pagination aa-list-pagination" data-testid="agent-pagination">
            <span className="aa-page-count">
              Showing {(safePage - 1) * pageSize + 1}–{Math.min(safePage * pageSize, filtered.length)} of{" "}
              {filtered.length}
            </span>
            <button
              type="button"
              className="aa-btn aa-page-btn"
              data-testid="agent-prev"
              disabled={safePage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ‹ Prev
            </button>
            <span className="aa-page-info">
              Page {safePage} of {totalPages}
            </span>
            <button
              type="button"
              className="aa-btn aa-page-btn"
              data-testid="agent-next"
              disabled={safePage >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next ›
            </button>
          </div>
        )}
      </div>

      {manageAgent ? (
        <AccessDialog
          agent={manageAgent}
          onClose={() => {
            setManageAgent(null);
            onAccessChanged();
          }}
        />
      ) : null}

      {integrationAgent ? (
        <ApiIntegrationDialog
          agent={integrationAgent}
          onClose={() => setIntegrationAgent(null)}
        />
      ) : null}
    </div>
  );
}
