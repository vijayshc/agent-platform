import { useEffect, useId, useMemo, useState } from "react";

/* ------------------------------------------------------------------ *
 * ResourceAccessControl — the one presentational control for granting
 * roles on any tenanted asset.
 *
 * It renders the granted roles as removable chips plus an "Add role"
 * picker, and hands the complete new id list to the caller's `onSave`.
 * The caller owns the network call (usually via `putResourceAccess`), so
 * the same component serves every feature module. Styling comes entirely
 * from the shared theme classes.
 * ------------------------------------------------------------------ */

export interface RoleOption {
  id: number;
  name: string;
}

export interface ResourceAccessControlProps {
  roles: RoleOption[];
  /** Granted role ids. */
  value: number[];
  onSave: (roleIds: number[]) => Promise<void>;
  saving?: boolean;
  error?: string | null;
  label?: string;
  hint?: string;
}

function sameIds(a: number[], b: number[]): boolean {
  if (a.length !== b.length) return false;
  const left = [...a].sort((x, y) => x - y);
  const right = [...b].sort((x, y) => x - y);
  return left.every((id, index) => id === right[index]);
}

export function ResourceAccessControl({
  roles,
  value,
  onSave,
  saving = false,
  error = null,
  label = "Access",
  hint,
}: ResourceAccessControlProps) {
  const selectId = useId();
  const [selected, setSelected] = useState<number[]>(value);
  const [pendingId, setPendingId] = useState("");

  // Re-sync when the parent replaces the value (e.g. after a save response).
  useEffect(() => {
    setSelected((current) => (sameIds(current, value) ? current : [...value]));
  }, [value]);

  const roleById = useMemo(() => new Map(roles.map((role) => [role.id, role])), [roles]);
  const nameOf = (roleId: number) => roleById.get(roleId)?.name ?? `role ${roleId}`;
  const available = useMemo(
    () => roles.filter((role) => !selected.includes(role.id)),
    [roles, selected],
  );

  const commit = (next: number[]) => {
    setSelected(next);
    void onSave(next);
  };

  return (
    <div className="aa-admin-field" data-testid="resource-access-control">
      <span className="aa-admin-field-label">{label}</span>

      {selected.length === 0 ? (
        <p className="aa-access-empty">No roles granted — owner and administrators only.</p>
      ) : (
        <div className="aa-chips" data-testid="resource-access-chips">
          {selected.map((roleId) => (
            <span className="aa-chip aa-chip-role" key={roleId} title={nameOf(roleId)}>
              {nameOf(roleId)}
              <button
                type="button"
                aria-label={`Remove ${nameOf(roleId)}`}
                disabled={saving}
                onClick={() => commit(selected.filter((id) => id !== roleId))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="aa-access-add">
        <select
          id={selectId}
          className="aa-access-select"
          aria-label="Add role"
          value={pendingId}
          disabled={saving || available.length === 0}
          onChange={(event) => setPendingId(event.target.value)}
        >
          <option value="">Add role…</option>
          {available.map((role) => (
            <option key={role.id} value={role.id}>
              {role.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="aa-btn aa-btn-primary"
          disabled={saving || !pendingId}
          onClick={() => {
            const roleId = Number(pendingId);
            if (!Number.isFinite(roleId)) return;
            setPendingId("");
            commit([...selected, roleId]);
          }}
        >
          Add
        </button>
      </div>

      {hint && <span className="aa-admin-field-hint">{hint}</span>}
      {error && (
        <div className="aa-error" role="alert">
          {error}
        </div>
      )}
      {saving && <span className="aa-muted">Saving…</span>}
    </div>
  );
}
