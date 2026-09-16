import { useCallback, useEffect, useState, type FormEvent, type MouseEvent } from "react";
import {
  adminGet,
  adminPostJson,
  adminPutJson,
  adminDelete,
  AdminLoading,
  AdminError,
  AdminModal,
  AdminField,
  AdminTags,
  AdminStatusPill,
  PageHeader,
} from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

interface Role {
  id: number;
  name: string;
  description?: string;
}

interface User {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
  roles: Role[];
}

interface FormState {
  username: string;
  email: string;
  password: string;
  roles: number[];
}

const emptyForm: FormState = { username: "", email: "", password: "", roles: [] };

export function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      adminGet<{ users: User[] }>("/admin/api/users"),
      adminGet<{ roles: Role[] }>("/admin/api/roles"),
    ])
      .then(([u, r]) => {
        setUsers(u.users || []);
        setRoles(r.roles || []);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // ---------- Modal: open / close ----------
  const openCreate = () => {
    setEditing(null);
    setForm(emptyForm);
    setFormError(null);
    setModalOpen(true);
  };

  const openEdit = (user: User) => {
    setEditing(user);
    setForm({
      username: user.username,
      email: user.email,
      password: "",
      roles: user.roles.map((r) => r.id),
    });
    setFormError(null);
    setModalOpen(true);
  };

  const closeModal = () => {
    if (saving) return;
    setModalOpen(false);
    setFormError(null);
  };

  // ---------- Role checkbox toggling ----------
  const toggleRole = (id: number) => {
    setForm((prev) => ({
      ...prev,
      roles: prev.roles.includes(id)
        ? prev.roles.filter((rid) => rid !== id)
        : [...prev.roles, id],
    }));
  };

  // ---------- Create / Edit submit ----------
  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
    if (saving) return;

    if (!form.username.trim() || !form.email.trim()) {
      setFormError("Username and email are required.");
      return;
    }
    if (!editing && form.password.length < 6) {
      setFormError("Password must be at least 6 characters long.");
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const body: Record<string, unknown> = {
        username: form.username.trim(),
        email: form.email.trim(),
        roles: form.roles,
      };

      if (editing) {
        if (form.password) body.password = form.password;
        await adminPutJson<{ success: boolean; message?: string }>(
          `/admin/api/users/${editing.id}`,
          body,
        );
        setMessage(`User "${form.username.trim()}" updated.`);
      } else {
        body.password = form.password;
        await adminPostJson<{ success: boolean; message?: string }>("/admin/api/users", body);
        setMessage(`User "${form.username.trim()}" created.`);
      }

      setModalOpen(false);
      load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // ---------- Delete ----------
  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      await adminDelete<{ success: boolean }>(`/admin/api/users/${deleteTarget.id}`);
      setMessage(`User "${deleteTarget.username}" deleted.`);
      setDeleteTarget(null);
      load();
    } catch (err) {
      setMessage(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const stopClick = (e: MouseEvent) => e.stopPropagation();

  if (loading) return <AdminLoading />;
  if (error) return <AdminError message={error} />;

  const userColumns: Column<User>[] = [
    {
      key: "username",
      header: "Username",
      sortValue: (u) => (u.username || "").toLowerCase(),
    },
    {
      key: "email",
      header: "Email",
      render: (u) => u.email || "—",
      sortValue: (u) => (u.email || "").toLowerCase(),
    },
    {
      key: "roles",
      header: "Roles",
      render: (u) => <AdminTags items={u.roles.map((r) => r.name)} />,
      sortValue: (u) => u.roles.map((r) => r.name).join(",").toLowerCase(),
    },
    {
      key: "status",
      header: "Status",
      render: (u) => <AdminStatusPill status={u.is_active ? "active" : "inactive"} />,
      sortValue: (u) => (u.is_active ? "active" : "inactive"),
    },
    {
      key: "created_at",
      header: "Created",
      className: "aa-table-date",
      render: (u) => (u.created_at ? new Date(u.created_at).toLocaleString() : "—"),
      sortValue: (u) => (u.created_at ? new Date(u.created_at).getTime() : 0),
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      render: (u) => (
        <span className="aa-admin-dt-actions">
          <button type="button" className="aa-btn" onClick={() => openEdit(u)}>
            Edit
          </button>
          <button type="button" className="aa-btn aa-btn-danger" onClick={() => setDeleteTarget(u)}>
            Delete
          </button>
        </span>
      ),
    },
  ];

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="Users"
        actions={
          <>
            {message && (
              <span className="aa-muted" style={{ fontSize: 13 }}>
                {message}
              </span>
            )}
            <button type="button" className="aa-btn aa-btn-primary" onClick={openCreate}>
              Create User
            </button>
          </>
        }
      />

      {/* ============================ Table ============================ */}
      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">
            {users.length} user{users.length === 1 ? "" : "s"}
          </span>
        </div>
        <AdminDataTable<User>
          rows={users}
          rowKey={(u) => u.id}
          searchText={(u) => `${u.username} ${u.email} ${u.roles.map((r) => r.name).join(" ")}`}
          searchPlaceholder="Search users…"
          emptyMessage="No users found"
          columns={userColumns}
        />
      </div>

      {/* ============================ Create / Edit modal ============================ */}
      <AdminModal
        title={editing ? `Edit User: ${editing.username}` : "Create User"}
        open={modalOpen}
        onClose={closeModal}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={closeModal} disabled={saving}>
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={() => void handleSubmit()}
              disabled={saving}
            >
              {saving ? "Saving…" : editing ? "Save Changes" : "Create User"}
            </button>
          </>
        }
      >
        <form onSubmit={(e) => void handleSubmit(e)} noValidate>
          <div className="aa-admin-grid2">
            <AdminField label="Username">
              <input
                type="text"
                value={form.username}
                onChange={(e) => setForm((p) => ({ ...p, username: e.target.value }))}
                autoFocus
              />
            </AdminField>
            <AdminField label="Email">
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm((p) => ({ ...p, email: e.target.value }))}
              />
            </AdminField>
          </div>

          <AdminField
            label="Password"
            hint={editing ? "Leave blank to keep the current password." : "At least 6 characters."}
          >
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm((p) => ({ ...p, password: e.target.value }))}
              autoComplete="new-password"
            />
          </AdminField>

          {roles.length > 0 && (
            <div className="aa-admin-field">
              <span className="aa-admin-field-label">Roles</span>
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "6px 14px",
                  padding: "8px 10px",
                  border: "1px solid var(--input-border)",
                  borderRadius: 10,
                  background: "var(--input-bg)",
                }}
              >
                {roles.map((r) => {
                  const checked = form.roles.includes(r.id);
                  return (
                    <label
                      key={r.id}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        fontSize: 13,
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleRole(r.id)}
                        onClick={stopClick}
                      />
                      <span>{r.name}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {formError && (
            <div className="aa-error" style={{ fontSize: 13 }}>
              {formError}
            </div>
          )}
        </form>
      </AdminModal>

      {/* ============================ Delete confirm modal ============================ */}
      <AdminModal
        title="Delete User"
        open={deleteTarget !== null}
        onClose={() => !deleting && setDeleteTarget(null)}
        footer={
          <>
            <button
              type="button"
              className="aa-btn aa-btn-ghost"
              onClick={() => setDeleteTarget(null)}
              disabled={deleting}
            >
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={() => void handleDelete()}
              disabled={deleting}
            >
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        <p style={{ margin: 0, fontSize: 14 }}>
          Are you sure you want to delete{" "}
          <strong>{deleteTarget?.username}</strong>? This action cannot be undone.
        </p>
      </AdminModal>
    </div>
  );
}
