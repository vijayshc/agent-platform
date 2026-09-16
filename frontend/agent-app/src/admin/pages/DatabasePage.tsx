import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  adminGet,
  adminPostJson,
  AdminLoading,
  AdminError,
  PageHeader,
} from "../adminShared";
import { CodeEditor } from "../../shared/CodeEditor";

/* ------------------------------------------------------------------ *
 * Shared constants
 * ------------------------------------------------------------------ */

const MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

/* ------------------------------------------------------------------ *
 * Types (mirrors the /admin/database API contracts)
 * ------------------------------------------------------------------ */

interface SchemaColumn {
  name: string;
  type: string;
  nullable: boolean;
}

interface SchemaTable {
  name: string;
  columns: SchemaColumn[];
  primary_keys: string[];
  foreign_keys: Array<{
    column?: string;
    constrained_columns?: string[];
    references_table?: string;
    referred_table?: string;
    references_column?: string;
    referred_columns?: string[];
  }>;
}

interface SchemaView {
  name: string;
  definition?: string;
  columns?: SchemaColumn[];
}

interface SchemaResponse {
  tables: SchemaTable[];
  views: SchemaView[];
}

interface ExecuteResponse {
  success: boolean;
  isSelect?: boolean;
  columns?: string[];
  data?: Array<Record<string, unknown>>;
  rowCount?: number;
  message?: string;
  error?: string;
}

/* ------------------------------------------------------------------ *
 * Cell formatting helper
 * ------------------------------------------------------------------ */

function formatCell(value: unknown): ReactNode {
  if (value === undefined) return null;
  if (value === null) return <span className="aa-muted">NULL</span>;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/* ------------------------------------------------------------------ *
 * Component
 * ------------------------------------------------------------------ */

export function DatabasePage() {
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const [sql, setSql] = useState("");
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<ExecuteResponse | null>(null);
  const [resultError, setResultError] = useState<string | null>(null);

  const loadSchema = useCallback(() => {
    setLoading(true);
    setError(null);
    adminGet<SchemaResponse>("/admin/database/schema")
      .then((data) => {
        setSchema({
          tables: data.tables || [],
          views: data.views || [],
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadSchema();
  }, [loadSchema]);

  const toggleExpanded = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const insertSelect = (name: string) => {
    setSql(`SELECT * FROM ${name};`);
  };

  const execute = useCallback(async () => {
    const query = sql.trim();
    if (!query || executing) return;
    setExecuting(true);
    setResult(null);
    setResultError(null);
    try {
      const res = await adminPostJson<ExecuteResponse>("/admin/database/execute", {
        sql: query,
      });
      if (!res.success) {
        setResultError(res.error || "Query failed.");
      } else {
        setResult(res);
      }
    } catch (e) {
      setResultError(e instanceof Error ? e.message : String(e));
    } finally {
      setExecuting(false);
    }
  }, [sql, executing]);

  const clearEditor = () => {
    setSql("");
    setResult(null);
    setResultError(null);
  };

  /* -------------------- Search filter -------------------- */
  const filteredTables = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return schema?.tables ?? [];
    return (schema?.tables ?? []).filter((t) =>
      t.name.toLowerCase().includes(q),
    );
  }, [schema, search]);

  const filteredViews = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return schema?.views ?? [];
    return (schema?.views ?? []).filter((v) =>
      v.name.toLowerCase().includes(q),
    );
  }, [schema, search]);

  const tableCount = schema?.tables.length ?? 0;
  const viewCount = schema?.views.length ?? 0;

  const shownTables = filteredTables.length;
  const shownViews = filteredViews.length;

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="Database Query Editor"
        actions={
          <button type="button" className="aa-btn aa-btn-ghost" onClick={loadSchema}>
            Refresh Schema
          </button>
        }
      />

      {/* ============================ Two-panel layout ============================ */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(280px, 340px) minmax(0, 1fr)",
          gap: 16,
          alignItems: "start",
        }}
      >
        {/* -------- Left: Schema Browser -------- */}
        <div className="aa-admin-panel" style={{ minHeight: 0 }}>
          <div className="aa-admin-panel-head">
            <h2>Schema Browser</h2>
          </div>
          <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              type="text"
              className="aa-search"
              placeholder="Search tables & views…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />

            {/* Tables */}
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
              <span className="aa-admin-field-label" style={{ margin: 0 }}>
                Tables
              </span>
              <span className="aa-muted" style={{ fontSize: 11 }}>
                {shownTables}/{tableCount}
              </span>
            </div>

            {shownTables === 0 ? (
              <div className="aa-muted" style={{ fontSize: 13, padding: "4px 0" }}>
                No tables found
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column" }}>
                {filteredTables.map((table) => {
                  const key = `t:${table.name}`;
                  const isOpen = expanded.has(key);
                  return (
                    <div key={table.name}>
                      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <button
                          type="button"
                          className="aa-btn aa-btn-ghost"
                          aria-label={isOpen ? "Collapse" : "Expand"}
                          onClick={() => toggleExpanded(key)}
                          style={{ padding: "2px 4px", fontSize: 12, lineHeight: 1 }}
                        >
                          {isOpen ? "▾" : "▸"}
                        </button>
                        <button
                          type="button"
                          style={{
                            textAlign: "left",
                            flex: 1,
                            background: "none",
                            border: 0,
                            cursor: "pointer",
                            fontSize: 14,
                            fontWeight: 600,
                            color: "var(--text-primary)",
                            padding: 0,
                            fontFamily: "inherit",
                          }}
                          onClick={() => insertSelect(table.name)}
                          title="Insert SELECT * INTO editor"
                        >
                          {table.name}
                        </button>
                      </div>

                      {isOpen && (
                        <div
                          style={{
                            marginLeft: 20,
                            padding: "6px 10px",
                            borderLeft: "1px solid var(--border-color)",
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                          }}
                        >
                          {table.columns.length === 0 ? (
                            <span className="aa-muted" style={{ fontSize: 12 }}>
                              No columns
                            </span>
                          ) : (
                            table.columns.map((col) => {
                              const isPk = table.primary_keys.includes(col.name);
                              return (
                                <div
                                  key={col.name}
                                  style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}
                                >
                                  <span style={{ fontFamily: MONO_FONT }}>{col.name}</span>
                                  <span className="aa-chip" style={{ fontSize: 11 }}>
                                    {col.type}
                                  </span>
                                  {isPk && (
                                    <span className="aa-chip" style={{ fontSize: 10 }}>
                                      PK
                                    </span>
                                  )}
                                  {col.nullable && (
                                    <span className="aa-muted" style={{ fontSize: 10.5 }}>
                                      nullable
                                    </span>
                                  )}
                                </div>
                              );
                            })
                          )}

                          {table.foreign_keys.length > 0 && (
                            <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 2 }}>
                              {table.foreign_keys.map((fk, i) => {
                                const local =
                                  fk.column ??
                                  fk.constrained_columns?.join(", ") ??
                                  "";
                                const refTable = fk.references_table ?? fk.referred_table ?? "";
                                const refColumn =
                                  fk.references_column ??
                                  fk.referred_columns?.join(", ") ??
                                  "";
                                return (
                                  <span key={i} className="aa-muted" style={{ fontSize: 11 }}>
                                    FK {local} → {refTable}
                                    {refColumn ? `.${refColumn}` : ""}
                                  </span>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Views */}
            {viewCount > 0 && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                  <span className="aa-admin-field-label" style={{ margin: 0 }}>
                    Views
                  </span>
                  <span className="aa-muted" style={{ fontSize: 11 }}>
                    {shownViews}/{viewCount}
                  </span>
                </div>

                {shownViews === 0 ? (
                  <div className="aa-muted" style={{ fontSize: 13, padding: "4px 0" }}>
                    No views found
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {filteredViews.map((view) => {
                      const key = `v:${view.name}`;
                      const isOpen = expanded.has(key);
                      return (
                        <div key={view.name}>
                          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                            <button
                              type="button"
                              className="aa-btn aa-btn-ghost"
                              aria-label={isOpen ? "Collapse" : "Expand"}
                              onClick={() => toggleExpanded(key)}
                              style={{ padding: "2px 4px", fontSize: 12, lineHeight: 1 }}
                            >
                              {isOpen ? "▾" : "▸"}
                            </button>
                            <button
                              type="button"
                              style={{
                                textAlign: "left",
                                flex: 1,
                                background: "none",
                                border: 0,
                                cursor: "pointer",
                                fontSize: 14,
                                fontWeight: 600,
                                color: "var(--text-primary)",
                                padding: 0,
                                fontFamily: "inherit",
                              }}
                              onClick={() => insertSelect(view.name)}
                              title="Insert SELECT * INTO editor"
                            >
                              {view.name}
                            </button>
                          </div>

                          {isOpen && (
                            <div
                              style={{
                                marginLeft: 20,
                                padding: "6px 10px",
                                borderLeft: "1px solid var(--border-color)",
                                display: "flex",
                                flexDirection: "column",
                                gap: 4,
                              }}
                            >
                              {view.definition && (
                                <span
                                  className="aa-muted"
                                  style={{ fontSize: 11, fontFamily: MONO_FONT }}
                                >
                                  {view.definition}
                                </span>
                              )}
                              {(view.columns ?? []).map((col) => (
                                <div
                                  key={col.name}
                                  style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}
                                >
                                  <span style={{ fontFamily: MONO_FONT }}>{col.name}</span>
                                  <span className="aa-chip" style={{ fontSize: 11 }}>
                                    {col.type}
                                  </span>
                                  {col.nullable && (
                                    <span className="aa-muted" style={{ fontSize: 10.5 }}>
                                      nullable
                                    </span>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {/* -------- Right: SQL Editor + Results -------- */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
          <div className="aa-admin-panel">
            <div className="aa-admin-panel-head">
              <h2>SQL Editor</h2>
            </div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              <div className="aa-admin-field">
                <span className="aa-admin-field-label">SQL</span>
                <CodeEditor
                  value={sql}
                  onChange={setSql}
                  language="sql"
                  filename="query.sql"
                  label="SQL query"
                  height={200}
                  minHeight={140}
                  onSubmit={() => void execute()}
                  testId="sql-editor"
                />
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="aa-btn aa-btn-primary"
                  onClick={() => void execute()}
                  disabled={executing || !sql.trim()}
                >
                  {executing ? "Executing…" : "Execute"}
                </button>
                <button
                  type="button"
                  className="aa-btn aa-btn-ghost"
                  onClick={clearEditor}
                  disabled={executing}
                >
                  Clear
                </button>
                <span className="aa-muted" style={{ alignSelf: "center", fontSize: 11.5 }}>
                  Ctrl+Enter to run
                </span>
              </div>
            </div>
          </div>

          {/* Results */}
          {resultError && (
            <div className="aa-error" style={{ fontSize: 13 }}>
              {resultError}
            </div>
          )}

          {result && result.isSelect && (
            <div className="aa-admin-panel">
              <div className="aa-admin-panel-head">
                <h2>Results</h2>
                <span className="aa-muted" style={{ fontSize: 12 }}>
                  {result.rowCount ?? result.data?.length ?? 0} row
                  {(result.rowCount ?? 0) === 1 ? "" : "s"}
                </span>
              </div>
              <div style={{ overflowX: "auto", maxHeight: "60vh", overflowY: "auto" }}>
                <table className="aa-table no-datatable">
                  <thead>
                    <tr>
                      {(result.columns ?? []).map((col) => (
                        <th key={col}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(result.data ?? []).length === 0 ? (
                      <tr>
                        <td colSpan={(result.columns ?? []).length || 1} className="aa-table-empty">
                          No rows returned
                        </td>
                      </tr>
                    ) : (
                      (result.data ?? []).map((row, i) => (
                        <tr key={i}>
                          {(result.columns ?? []).map((col) => (
                            <td key={col}>{formatCell(row[col])}</td>
                          ))}
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result && !result.isSelect && (
            <div className="aa-admin-panel">
              <div className="aa-admin-panel-head">
                <h2>Result</h2>
              </div>
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 6 }}>
                <span className="aa-muted" style={{ fontSize: 13 }}>
                  {result.message || "Query executed successfully."}
                </span>
                <span style={{ fontSize: 13 }}>
                  <strong>{result.rowCount ?? 0}</strong> row
                  {(result.rowCount ?? 0) === 1 ? "" : "s"} affected
                </span>
              </div>
            </div>
          )}

          {!result && !resultError && (
            <div className="aa-muted" style={{ fontSize: 13, textAlign: "center", padding: 8 }}>
              Run a query above to see results.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
