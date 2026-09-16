import { useEffect, useState } from "react";
import { AdminError, AdminLoading, AdminModal, adminGet, adminPutJson } from "../adminShared";

export interface ModuleDefinition {
  key: string;
  label: string;
  description?: string;
  href?: string;
  icon?: string;
  /** Administrator-only capability: enforced server-side, never grantable. */
  admin_only?: boolean;
}

type ModuleAccess = "read" | "write";

interface RolePermissionsResponse {
  module_levels?: Record<string, ModuleAccess>;
  modules?: string[];
}

/* ------------------------------------------------------------------ *
 * Role permissions are module grants with a READ / WRITE level.
 *
 * The module catalog comes from the server, so a new module added to
 * src/auth/modules.py automatically appears here.
 * ------------------------------------------------------------------ */

export function RolePermissionsModal({
  open,
  roleId,
  roleName,
  isAdmin,
  onClose,
}: {
  open: boolean;
  roleId: number | null;
  roleName: string;
  isAdmin: boolean;
  onClose: () => void;
}) {
  const [catalog, setCatalog] = useState<ModuleDefinition[]>([]);
  const [levels, setLevels] = useState<Record<string, ModuleAccess>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || roleId === null) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setCatalog([]);
    setLevels({});

    Promise.all([
      adminGet<{ modules: ModuleDefinition[] }>("/admin/api/modules"),
      adminGet<RolePermissionsResponse>(`/admin/api/roles/${roleId}/permissions`),
    ])
      .then(([catalogData, roleData]) => {
        if (cancelled) return;
        const modules = catalogData.modules || [];
        setCatalog(modules);
        // Admin-only modules are enforced server-side and can never be granted,
        // so drop any stale grant from the editable/ persisted state.
        const assignable = new Set(
          modules.filter((module) => !module.admin_only).map((module) => module.key),
        );
        const rawLevels: Record<string, ModuleAccess> = roleData.module_levels
          ? roleData.module_levels
          : Object.fromEntries(
              (roleData.modules || []).map((key) => [key, "write" as ModuleAccess]),
            );
        const editableLevels: Record<string, ModuleAccess> = {};
        for (const [key, level] of Object.entries(rawLevels)) {
          if (assignable.has(key) && (level === "read" || level === "write")) {
            editableLevels[key] = level;
          }
        }
        setLevels(editableLevels);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, roleId]);

  const assignableCatalog = catalog.filter((module) => !module.admin_only);
  const selectedCount = Object.keys(levels).length;
  const writeCount = Object.values(levels).filter((level) => level === "write").length;

  const setLevel = (key: string, value: string) => {
    if (isAdmin) return;
    const module = catalog.find((entry) => entry.key === key);
    if (module?.admin_only) return;
    setLevels((prev) => {
      const next = { ...prev };
      if (value === "read" || value === "write") next[key] = value;
      else delete next[key];
      return next;
    });
  };

  const save = async () => {
    if (roleId === null || isAdmin) return;
    setSaving(true);
    setError(null);
    try {
      await adminPutJson(`/admin/api/roles/${roleId}/permissions`, {
        modules: Object.entries(levels).map(([key, access]) => ({ key, access })),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminModal
      title={`Manage Permissions — ${roleName || "Role"}`}
      open={open}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            disabled={saving || loading || isAdmin}
            onClick={save}
          >
            {saving ? "Saving…" : "Save Permissions"}
          </button>
        </>
      }
    >
      {loading && <AdminLoading label="Loading modules…" />}
      {error && <div className="aa-error">{error}</div>}

      {!loading && !error && (
        <>
          <p className="aa-muted" style={{ marginTop: 0 }}>
            {isAdmin
              ? "The admin role always has full WRITE access to every module. Module grants cannot be reduced."
              : "Grant READ or WRITE access per module. Users always retain access to the main agent chat."}
          </p>

          <div className="aa-module-grid" data-testid="role-module-grid">
            {catalog.map((module) => {
              const locked = Boolean(module.admin_only) && !isAdmin;
              if (locked) {
                return (
                  <label
                    key={module.key}
                    className="aa-module-option disabled"
                    data-testid={`role-module-${module.key}`}
                    data-admin-only="true"
                  >
                    <span className="aa-module-option-copy">
                      <span className="aa-module-option-label">{module.label}</span>
                      {module.description && (
                        <span className="aa-module-option-description">{module.description}</span>
                      )}
                    </span>
                    <span className="aa-muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                      Administrator only
                    </span>
                  </label>
                );
              }
              const level = isAdmin ? "write" : levels[module.key] || "";
              return (
                <label
                  key={module.key}
                  className={`aa-module-option${level ? " active" : ""}${isAdmin ? " disabled" : ""}`}
                  data-testid={`role-module-${module.key}`}
                >
                  <span className="aa-module-option-copy">
                    <span className="aa-module-option-label">{module.label}</span>
                    {module.description && (
                      <span className="aa-module-option-description">{module.description}</span>
                    )}
                  </span>
                  <select
                    className="aa-module-level-select"
                    aria-label={`${module.label} access level`}
                    value={level}
                    disabled={isAdmin}
                    onChange={(e) => setLevel(module.key, e.target.value)}
                  >
                    <option value="">No access</option>
                    <option value="read">Read</option>
                    <option value="write">Write</option>
                  </select>
                </label>
              );
            })}
          </div>

          <p className="aa-muted" style={{ marginBottom: 0 }}>
            {isAdmin
              ? `All ${catalog.length} modules enabled at WRITE level.`
              : `${selectedCount} of ${assignableCatalog.length} modules selected (${writeCount} write, ${selectedCount - writeCount} read).`}
          </p>
        </>
      )}
    </AdminModal>
  );
}
