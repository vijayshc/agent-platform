import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  adminPostJson,
  adminPutJson,
  adminDelete,
  AdminLoading,
  AdminError,
  AdminEmpty,
  AdminModal,
  AdminField,
  PageHeader,
} from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";
import { CodeEditor } from "../../shared/CodeEditor";
import { ActionsMenu } from "../../shared/ActionsMenu";
import {
  ResourceAccessControl,
  type RoleOption,
} from "../../shared/ResourceAccessControl";
import { getResourceAccess, putResourceAccess } from "../../shared/resourceAccess";

interface McpServer {
  id: number;
  name: string;
  description?: string;
  server_type: string;
  config?: Record<string, unknown>;
  /** Tool names discovered by connecting to the server (empty when it is down). */
  tools?: string[];
  tools_error?: string | null;
  /** Owning user id (null for grandfathered legacy rows). */
  created_by?: number | null;
  /** True only for the owner or an administrator: edit/delete/grant. */
  can_manage?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

interface EmptyForm {
  name: string;
  description: string;
  server_type: string;
  configText: string;
}

interface ToolItem {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
}

/** Normalize the raw MCP tools payload (OpenAI-style {"function": {…}} objects
 *  or plain name strings) into a displayable shape. */
function normalizeTools(raw: unknown[]): ToolItem[] {
  return (raw || [])
    .map((t) => {
      if (typeof t === "string") return { name: t };
      if (t && typeof t === "object") {
        const obj = t as Record<string, unknown>;
        const fn =
          obj.function && typeof obj.function === "object"
            ? (obj.function as Record<string, unknown>)
            : null;
        if (fn) {
          return {
            name: String(fn.name ?? ""),
            description: typeof fn.description === "string" ? fn.description : undefined,
            parameters:
              fn.parameters && typeof fn.parameters === "object"
                ? (fn.parameters as Record<string, unknown>)
                : undefined,
          };
        }
        return {
          name: String(obj.name ?? ""),
          description: typeof obj.description === "string" ? obj.description : undefined,
          parameters:
            obj.parameters && typeof obj.parameters === "object"
              ? (obj.parameters as Record<string, unknown>)
              : undefined,
        };
      }
      return { name: String(t) };
    })
    .filter((t) => t.name);
}

/** Summarize a JSON Schema parameters object into a short argument preview. */
function summarizeArgs(parameters?: Record<string, unknown>): string | null {
  if (!parameters || typeof parameters !== "object") return null;
  const props = (parameters as { properties?: Record<string, unknown> }).properties;
  if (!props || typeof props !== "object") return null;
  const keys = Object.keys(props);
  if (!keys.length) return null;
  const required = new Set(
    Array.isArray((parameters as { required?: unknown[] }).required)
      ? ((parameters as { required: string[] }).required as string[])
      : [],
  );
  return keys.map((k) => `${k}${required.has(k) ? "*" : ""}`).join(", ");
}

const EMPTY_FORM: EmptyForm = {
  name: "",
  description: "",
  server_type: "stdio",
  configText: "{}",
};

export function McpServersPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<McpServer | null>(null);
  const [form, setForm] = useState<EmptyForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [toolsServer, setToolsServer] = useState<McpServer | null>(null);
  const [tools, setTools] = useState<ToolItem[]>([]);
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsError, setToolsError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<McpServer | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const [accessServer, setAccessServer] = useState<McpServer | null>(null);
  const [accessRoles, setAccessRoles] = useState<RoleOption[]>([]);
  const [accessValue, setAccessValue] = useState<number[]>([]);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessSaving, setAccessSaving] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminGet<{ success: boolean; servers: McpServer[] }>("/api/admin/mcp-servers")
      .then((d) => setServers(d.servers || []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openAdd() {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setModalOpen(true);
  }

  function openEdit(s: McpServer) {
    setEditing(s);
    setForm({
      name: s.name || "",
      description: s.description || "",
      server_type: s.server_type || "stdio",
      configText: JSON.stringify(s.config ?? {}, null, 2),
    });
    setFormError(null);
    setModalOpen(true);
  }

  async function save() {
    const name = form.name.trim();
    if (!name) {
      setFormError("Name is required.");
      return;
    }
    let config: Record<string, unknown>;
    try {
      config = form.configText.trim() ? JSON.parse(form.configText) : {};
      if (config === null || typeof config !== "object" || Array.isArray(config)) {
        setFormError("Config must be a JSON object.");
        return;
      }
    } catch {
      setFormError("Config must be valid JSON.");
      return;
    }

    const payload = {
      name,
      description: form.description.trim(),
      server_type: form.server_type,
      config,
    };

    setSaving(true);
    setFormError(null);
    try {
      if (editing) {
        await adminPutJson(`/api/admin/mcp-servers/${editing.id}`, payload);
      } else {
        await adminPostJson("/api/admin/mcp-servers", payload);
      }
      setModalOpen(false);
      load();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function openTools(s: McpServer) {
    setToolsServer(s);
    setTools([]);
    setToolsError(null);
    setToolsLoading(true);
    try {
      const d = await adminGet<{ success: boolean; tools: unknown[]; error?: string }>(
        `/api/admin/mcp-servers/${s.id}/tools`
      );
      if (d.success === false) {
        setToolsError(d.error || "Server did not return any tools");
      } else {
        setTools(normalizeTools(d.tools || []));
      }
    } catch (e) {
      setToolsError(e instanceof Error ? e.message : String(e));
    } finally {
      setToolsLoading(false);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await adminDelete(`/api/admin/mcp-servers/${deleting.id}`);
      setDeleting(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleteBusy(false);
    }
  }

  async function openAccess(s: McpServer) {
    setAccessServer(s);
    setAccessRoles([]);
    setAccessValue([]);
    setAccessError(null);
    setAccessLoading(true);
    try {
      const d = await getResourceAccess("mcp_server", s.id);
      setAccessRoles(d.roles || []);
      setAccessValue((d.access || []).map((entry) => entry.role_id));
    } catch (e) {
      setAccessError(e instanceof Error ? e.message : String(e));
    } finally {
      setAccessLoading(false);
    }
  }

  async function saveAccess(roleIds: number[]) {
    if (!accessServer) return;
    setAccessSaving(true);
    setAccessError(null);
    try {
      const d = await putResourceAccess("mcp_server", accessServer.id, roleIds);
      setAccessRoles(d.roles || []);
      setAccessValue((d.access || []).map((entry) => entry.role_id));
    } catch (e) {
      setAccessError(e instanceof Error ? e.message : String(e));
    } finally {
      setAccessSaving(false);
    }
  }

  if (loading) return <AdminLoading label="Loading MCP servers…" />;
  if (error) return <AdminError message={error} />;

  const serverColumns: Column<McpServer>[] = [
    {
      key: "name",
      header: "Name",
      sortValue: (s) => (s.name || "").toLowerCase(),
      render: (s) => (
        <>
          <div style={{ fontWeight: 600 }}>{s.name}</div>
          {s.description && <div className="aa-table-sub">{s.description}</div>}
        </>
      ),
    },
    {
      key: "server_type",
      header: "Server Type",
      render: (s) => <span className="aa-chip">{s.server_type}</span>,
      sortValue: (s) => s.server_type,
    },
    {
      key: "tools",
      header: "Tools",
      sortValue: (s) => (s.tools || []).length,
      render: (s) =>
        s.tools_error ? (
          <span className="aa-badge aa-badge-sm aa-badge-amber" title={s.tools_error}>
            unreachable
          </span>
        ) : (s.tools || []).length ? (
          <span className="aa-badge aa-badge-sm aa-badge-green">
            {(s.tools || []).length} tools
          </span>
        ) : (
          <span className="aa-badge aa-badge-sm aa-badge-neutral">no tools</span>
        ),
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      render: (s) => (
        <ActionsMenu
          label="Actions"
          testId={`mcp-actions-${s.id}`}
          items={[
            {
              key: "tools",
              label: "Test tools",
              testId: `mcp-test-tools-${s.id}`,
              onSelect: () => openTools(s),
            },
            // A role grant confers *use* (test tools); only the owner or an
            // administrator may grant roles, edit, or delete.
            ...(s.can_manage
              ? [
                  {
                    key: "access",
                    label: "Manage access",
                    testId: `mcp-access-${s.id}`,
                    onSelect: () => openAccess(s),
                  },
                  {
                    key: "edit",
                    label: "Edit",
                    testId: `mcp-edit-${s.id}`,
                    onSelect: () => openEdit(s),
                  },
                  {
                    key: "delete",
                    label: "Delete",
                    testId: `mcp-delete-${s.id}`,
                    danger: true,
                    onSelect: () => setDeleting(s),
                  },
                ]
              : []),
          ]}
        />
      ),
    },
  ];

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="MCP Servers"
        actions={
          <button type="button" className="aa-btn aa-btn-primary" onClick={openAdd}>
            Add MCP Server
          </button>
        }
      />
      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">
            {servers.length} MCP Server{servers.length === 1 ? "" : "s"}
          </span>
        </div>
        <AdminDataTable<McpServer>
          rows={servers}
          rowKey={(s) => s.id}
          searchText={(s) =>
            `${s.name} ${s.description || ""} ${s.server_type} ${(s.tools || []).join(" ")}`
          }
          searchPlaceholder="Search servers…"
          emptyMessage="No MCP servers found"
          columns={serverColumns}
        />
      </div>

      {/* Add / Edit modal */}
      <AdminModal
        title={editing ? `Edit ${editing.name}` : "Add MCP Server"}
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setModalOpen(false)}>
              Cancel
            </button>
            <button type="button" className="aa-btn aa-btn-primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        <AdminField label="Name">
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="My MCP Server"
          />
        </AdminField>
        <AdminField label="Description">
          <input
            type="text"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="Optional description"
          />
        </AdminField>
        <AdminField label="Server Type">
          <select
            value={form.server_type}
            onChange={(e) => setForm({ ...form, server_type: e.target.value })}
          >
            <option value="stdio">stdio</option>
            <option value="http">http</option>
          </select>
        </AdminField>
        <AdminField
          label="Config (JSON)"
          hint="Config object. Masked secret values are preserved on edit."
        >
          <CodeEditor
            value={form.configText}
            onChange={(next) => setForm({ ...form, configText: next })}
            language="json"
            filename="mcp-config.json"
            label="MCP server config"
            height={220}
            testId="mcp-config-editor"
          />
        </AdminField>
        {formError && <div className="aa-error">{formError}</div>}
      </AdminModal>

      {/* Tools modal */}
      <AdminModal
        title={toolsServer ? `Tools — ${toolsServer.name}` : "Tools"}
        open={toolsServer !== null}
        onClose={() => setToolsServer(null)}
        wide
        footer={
          <button type="button" className="aa-btn aa-btn-primary" onClick={() => setToolsServer(null)}>
            Close
          </button>
        }
      >
        {toolsLoading ? (
          <AdminLoading label="Loading tools…" />
        ) : toolsError ? (
          <AdminError message={toolsError} />
        ) : tools.length === 0 ? (
          <AdminEmpty message="No tools returned" />
        ) : (
          <div className="aa-admin-tools-list">
            {tools.map((t) => {
              const args = summarizeArgs(t.parameters);
              return (
                <div className="aa-admin-tool" key={t.name}>
                  <div className="aa-admin-tool-name">{t.name}</div>
                  {t.description && <div className="aa-admin-tool-desc">{t.description}</div>}
                  {args && <div className="aa-admin-tool-args">Arguments: {args}</div>}
                </div>
              );
            })}
          </div>
        )}
      </AdminModal>

      {/* Delete confirm modal */}
      <AdminModal
        title="Delete MCP Server"
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
          Are you sure you want to delete{" "}
          <strong>{deleting?.name}</strong>? This action cannot be undone.
        </div>
      </AdminModal>

      {/* Access (role grants) modal — owner / administrator only */}
      <AdminModal
        title={accessServer ? `Access — ${accessServer.name}` : "Access"}
        open={accessServer !== null}
        onClose={() => setAccessServer(null)}
        footer={
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={() => setAccessServer(null)}
          >
            Close
          </button>
        }
      >
        {accessLoading ? (
          <AdminLoading label="Loading access…" />
        ) : accessRoles.length === 0 && accessError ? (
          <AdminError message={accessError} />
        ) : (
          <ResourceAccessControl
            roles={accessRoles}
            value={accessValue}
            onSave={saveAccess}
            saving={accessSaving}
            error={accessError}
            label="Roles with access"
            hint="Members of these roles can use this server (connect and list tools). Only you and administrators can edit or delete it."
          />
        )}
      </AdminModal>
    </div>
  );
}
