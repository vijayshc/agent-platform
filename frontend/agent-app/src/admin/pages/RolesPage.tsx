import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  adminPostJson,
  adminPutJson,
  adminDelete,
  AdminLoading,
  AdminError,
  AdminModal,
  AdminField,
  PageHeader,
} from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";
import { RolePermissionsModal } from "./RolePermissionsModal";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

interface Role {
  id: number;
  name: string;
  description?: string;
  user_count: number;
}

type ModalKind = "create" | "edit" | "permissions" | "delete" | null;

/* ------------------------------------------------------------------ *
 * Component
 * ------------------------------------------------------------------ */

export function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [modal, setModal] = useState<ModalKind>(null);
  const [activeRole, setActiveRole] = useState<Role | null>(null);

  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadRoles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await adminGet<{ roles: Role[] }>("/admin/api/roles");
      setRoles(data.roles || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  /* ---------------- Create / Edit ---------------- */

  const openCreate = () => {
    setActiveRole(null);
    setFormName("");
    setFormDesc("");
    setFormError(null);
    setModal("create");
  };

  const openEdit = (role: Role) => {
    setActiveRole(role);
    setFormName(role.name);
    setFormDesc(role.description || "");
    setFormError(null);
    setModal("edit");
  };

  const closeForm = () => {
    setFormError(null);
    setSaving(false);
    setModal(null);
    setActiveRole(null);
  };

  const submitForm = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    const name = formName.trim();
    if (!name) {
      setFormError("Role name is required.");
      return;
    }
    setSaving(true);
    try {
      const payload = { name, description: formDesc.trim() };
      if (modal === "create") {
        await adminPostJson("/admin/api/roles", payload);
      } else if (activeRole) {
        await adminPutJson(`/admin/api/roles/${activeRole.id}`, payload);
      }
      await loadRoles();
      closeForm();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  /* ---------------- Permissions ---------------- */

  const openPermissions = (role: Role) => {
    setActiveRole(role);
    setModal("permissions");
  };

  const closePermissions = () => {
    setModal(null);
    setActiveRole(null);
  };

  /* ---------------- Delete ---------------- */

  const openDelete = (role: Role) => {
    setActiveRole(role);
    setDeleteError(null);
    setModal("delete");
  };

  const closeDelete = () => {
    setDeleting(false);
    setDeleteError(null);
    setModal(null);
    setActiveRole(null);
  };

  const confirmDelete = async () => {
    if (!activeRole) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await adminDelete(`/admin/api/roles/${activeRole.id}`);
      await loadRoles();
      closeDelete();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  /* ---------------- Render ---------------- */

  if (loading) return <AdminLoading label="Loading roles…" />;
  if (error && roles.length === 0) return <AdminError message={error} />;

  const roleColumns: Column<Role>[] = [
    {
      key: "name",
      header: "Name",
      sortValue: (r) => (r.name || "").toLowerCase(),
      render: (r) => <strong>{r.name}</strong>,
    },
    {
      key: "description",
      header: "Description",
      render: (r) => r.description || <span className="aa-muted">—</span>,
      sortValue: (r) => (r.description || "").toLowerCase(),
    },
    {
      key: "user_count",
      header: "Users",
      sortValue: (r) => r.user_count,
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      render: (r) => (
        <span className="aa-admin-dt-actions">
          <button type="button" className="aa-btn" onClick={() => openEdit(r)}>
            Edit
          </button>
          <button type="button" className="aa-btn" onClick={() => openPermissions(r)}>
            Permissions
          </button>
          <button type="button" className="aa-btn aa-btn-danger" disabled={r.name === "admin"} onClick={() => openDelete(r)}>
            Delete
          </button>
        </span>
      ),
    },
  ];

  return (
    <div className="aa-admin-page">
      {error && (
        <div className="aa-error">
          {error}
          <button type="button" className="aa-btn aa-btn-ghost" onClick={loadRoles} style={{ marginLeft: 8 }}>
            Retry
          </button>
        </div>
      )}

      <PageHeader
        title="Roles & Permissions"
        subtitle="Assign module access to roles. Users can always use the main agent chat."
        actions={
          <button type="button" className="aa-btn aa-btn-primary" onClick={openCreate}>
            New Role
          </button>
        }
      />

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">{roles.length} role{roles.length === 1 ? "" : "s"}</span>
        </div>

        <AdminDataTable<Role>
          rows={roles}
          rowKey={(r) => r.id}
          searchText={(r) => `${r.name} ${r.description || ""}`}
          searchPlaceholder="Search roles…"
          emptyMessage="No roles found"
          columns={roleColumns}
        />
      </div>

      <AdminModal
        title={modal === "create" ? "New Role" : "Edit Role"}
        open={modal === "create" || modal === "edit"}
        onClose={closeForm}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={closeForm}>
              Cancel
            </button>
            <button type="button" className="aa-btn aa-btn-primary" disabled={saving} onClick={submitForm}>
              {saving ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        <form onSubmit={submitForm}>
          {formError && <div className="aa-error">{formError}</div>}
          <AdminField label="Name" hint="A short, unique identifier for this role.">
            <input
              type="text"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
              placeholder="e.g. analyst"
              autoFocus
              disabled={activeRole?.name === "admin"}
            />
          </AdminField>
          <AdminField label="Description" hint="Optional. What is this role used for?">
            <textarea
              value={formDesc}
              onChange={(e) => setFormDesc(e.target.value)}
              placeholder="Describe the responsibilities of this role…"
            />
          </AdminField>
        </form>
      </AdminModal>

      <RolePermissionsModal
        open={modal === "permissions"}
        roleId={activeRole?.id ?? null}
        roleName={activeRole?.name ?? ""}
        isAdmin={activeRole?.name === "admin"}
        onClose={closePermissions}
      />

      <AdminModal
        title="Delete Role"
        open={modal === "delete"}
        onClose={closeDelete}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={closeDelete}>
              Cancel
            </button>
            <button type="button" className="aa-btn aa-btn-primary" disabled={deleting} onClick={confirmDelete}>
              {deleting ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        {deleteError && <div className="aa-error">{deleteError}</div>}
        <p style={{ margin: 0 }}>
          Are you sure you want to delete the role <strong>{activeRole?.name}</strong>? This action cannot be undone.
        </p>
      </AdminModal>
    </div>
  );
}
