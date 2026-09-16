import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  adminPostJson,
  adminDelete,
  AdminLoading,
  AdminError,
  AdminModal,
  AdminStatusPill,
  PageHeader,
} from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";
import { ActionsMenu } from "../../shared/ActionsMenu";
import { LlmConnectionModal } from "./llm/LlmConnectionModal";
import { LlmAccessModal } from "./llm/LlmAccessModal";
import type {
  LlmActionResponse,
  LlmConnection,
  LlmListResponse,
  LlmTestResponse,
} from "./llm/llmConnection";

const BASE = "/admin/config/llm/api";

/** What the Test button reported, plus the endpoint/model the app really called. */
interface TestOutcome {
  ok: boolean;
  message: string;
  target: string;
}

function testedTarget(d: LlmTestResponse): string {
  const parts = [d.endpoint, d.model].filter((v): v is string => Boolean(v));
  return parts.length ? `Tested ${parts.join(" · ")}` : "";
}

export function LlmManagerPage() {
  const [connections, setConnections] = useState<LlmConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Only administrators may change the global default; the server tells us so.
  const [canSetDefault, setCanSetDefault] = useState(false);

  const [modal, setModal] = useState<{ open: boolean; editing: LlmConnection | null }>({
    open: false,
    editing: null,
  });

  const [testTarget, setTestTarget] = useState<LlmConnection | null>(null);
  const [testing, setTesting] = useState(false);
  const [testOutcome, setTestOutcome] = useState<TestOutcome | null>(null);

  const [deleting, setDeleting] = useState<LlmConnection | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const [accessTarget, setAccessTarget] = useState<LlmConnection | null>(null);

  const [defaultBusyId, setDefaultBusyId] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminGet<LlmListResponse>(`${BASE}/list`)
      .then((d) => {
        if (d.status !== "success") {
          throw new Error(d.status === "error" ? "Failed to load connections" : d.status);
        }
        setConnections(d.data || []);
        setCanSetDefault(d.can_set_default === true);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function runTest() {
    if (!testTarget) return;
    setTesting(true);
    setTestOutcome(null);
    try {
      const d = await adminPostJson<LlmTestResponse>(`${BASE}/test`, { id: testTarget.id });
      const target = testedTarget(d);
      if (d.status === "success") {
        setTestOutcome({ ok: true, message: d.reply || d.message || "Connection test succeeded.", target });
      } else {
        setTestOutcome({ ok: false, message: d.message || "Connection test failed.", target });
      }
    } catch (e) {
      setTestOutcome({ ok: false, message: e instanceof Error ? e.message : String(e), target: "" });
    } finally {
      setTesting(false);
    }
  }

  async function setDefault(c: LlmConnection) {
    setDefaultBusyId(c.id);
    try {
      const d = await adminPostJson<LlmActionResponse>(`${BASE}/set-default/${c.id}`, {});
      if (d.status !== "success") throw new Error(d.message || "Failed to set default");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDefaultBusyId(null);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      const d = await adminDelete<LlmActionResponse>(`${BASE}/delete/${deleting.id}`);
      if (d.status !== "success") throw new Error(d.message || "Delete failed");
      setDeleting(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  }

  const connColumns: Column<LlmConnection>[] = [
    {
      key: "name",
      header: "Name",
      width: "180px",
      sortValue: (c) => c.name,
      render: (c) => (
        <div>
          <div style={{ fontWeight: 600 }}>{c.name}</div>
          {c.api_key_masked && <div className="aa-muted">••••••••</div>}
        </div>
      ),
    },
    {
      key: "base_url",
      header: "Base URL",
      width: "220px",
      sortValue: (c) => c.base_url,
      render: (c) => (
        <div title={c.base_url} className="aa-muted" style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {c.base_url}
        </div>
      ),
    },
    {
      key: "model_name",
      header: "Model",
      width: "150px",
      sortValue: (c) => c.model_name,
      render: (c) => <span className="aa-chip">{c.model_name}</span>,
    },
    {
      key: "is_default",
      header: "Default",
      width: "90px",
      sortValue: (c) => (c.is_default ? "default" : "other"),
      render: (c) =>
        c.is_default ? <span className="aa-chip">Default</span> : <span className="aa-muted">—</span>,
    },
    {
      key: "enabled",
      header: "Enabled",
      width: "90px",
      sortValue: (c) => (c.enabled ? "enabled" : "disabled"),
      render: (c) => <AdminStatusPill status={c.enabled ? "enabled" : "disabled"} />,
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      width: "120px",
      render: (c) =>
        // The server tells us whether this caller owns the connection (or is an
        // administrator). Anything else is use-only via a granted role, so no
        // destructive action is offered — and the API enforces the same line.
        c.can_manage !== true ? (
          <span className="aa-muted" title="You have use access through a role">
            Use only
          </span>
        ) : (
          <ActionsMenu
            label="Actions"
            testId={`llm-actions-${c.id}`}
            items={[
              {
                key: "test",
                label: "Test",
                testId: `llm-test-${c.id}`,
                onSelect: () => {
                  setTestTarget(c);
                  setTestOutcome(null);
                },
              },
              {
                key: "default",
                label: "Set Default",
                testId: `llm-set-default-${c.id}`,
                disabled: c.is_default || defaultBusyId === c.id || c.can_set_default !== true,
                title: c.can_set_default !== true
                  ? "Administrator only"
                  : c.is_default
                    ? `${c.name} is already the default`
                    : `Make ${c.name} the default`,
                onSelect: () => void setDefault(c),
              },
              {
                key: "edit",
                label: "Edit",
                testId: `llm-edit-${c.id}`,
                onSelect: () => setModal({ open: true, editing: c }),
              },
              {
                key: "access",
                label: "Access",
                testId: `llm-access-${c.id}`,
                onSelect: () => setAccessTarget(c),
              },
              {
                key: "delete",
                label: "Delete",
                testId: `llm-delete-${c.id}`,
                danger: true,
                onSelect: () => setDeleting(c),
              },
            ]}
          />
        ),
    },
  ];

  if (loading) return <AdminLoading label="Loading LLM connections…" />;

  return (
    <div className="aa-admin-page">
      {error && <AdminError message={error} />}

      <PageHeader
        title="LLM Manager"
        actions={
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={() => setModal({ open: true, editing: null })}
          >
            New Connection
          </button>
        }
      />
      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">{connections.length} total</span>
        </div>
        <AdminDataTable<LlmConnection>
          columns={connColumns}
          rows={connections}
          rowKey={(c) => c.id}
          searchText={(c) => `${c.name} ${c.base_url} ${c.model_name}`}
          searchPlaceholder="Search connections…"
          emptyMessage="No LLM connections found"
        />
      </div>

      {modal.open && (
        <LlmConnectionModal
          key={modal.editing?.id ?? "new"}
          editing={modal.editing}
          canSetDefault={canSetDefault}
          onClose={() => setModal({ open: false, editing: null })}
          onSaved={() => {
            setModal({ open: false, editing: null });
            load();
          }}
        />
      )}

      {accessTarget && (
        <LlmAccessModal
          connection={accessTarget}
          onClose={() => setAccessTarget(null)}
        />
      )}

      {/* Test result modal */}
      <AdminModal
        title={testTarget ? `Test — ${testTarget.name}` : "Test Connection"}
        open={testTarget !== null}
        onClose={() => {
          setTestTarget(null);
          setTestOutcome(null);
        }}
        footer={
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={() => {
              setTestTarget(null);
              setTestOutcome(null);
            }}
          >
            Close
          </button>
        }
      >
        {testOutcome && (
          <div className="aa-llm-test-report" data-testid="llm-test-result">
            <span className={`aa-status-pill ${testOutcome.ok ? "success" : "error"}`}>
              {testOutcome.ok ? "Success" : "Failed"}
            </span>
            <p
              className={testOutcome.ok ? "aa-llm-test-reply" : "aa-error aa-llm-test-reply"}
              data-testid="llm-test-message"
            >
              {testOutcome.message}
            </p>
            {testOutcome.target && (
              <p className="aa-muted aa-llm-test-target" data-testid="llm-test-target">
                {testOutcome.target}
              </p>
            )}
          </div>
        )}
        <div style={{ marginTop: testOutcome ? 12 : 0 }}>
          <button type="button" className="aa-btn aa-btn-primary" onClick={runTest} disabled={testing}>
            {testing ? "Testing…" : testOutcome ? "Run Test Again" : "Run Test"}
          </button>
        </div>
      </AdminModal>

      {/* Delete confirm modal */}
      <AdminModal
        title="Delete Connection"
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setDeleting(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={confirmDelete}
              disabled={deleteBusy}
              style={{ background: "var(--danger-color)" }}
            >
              {deleteBusy ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        <div>
          Are you sure you want to delete <strong>{deleting?.name}</strong>? This action cannot be undone.
        </div>
      </AdminModal>
    </div>
  );
}
