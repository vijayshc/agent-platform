import { useEffect, useState } from "react";
import { AdminModal } from "../adminShared";
import { ResourceAccessControl } from "../../shared/ResourceAccessControl";
import {
  getResourceAccess,
  putResourceAccess,
  type RoleOption,
} from "../../shared/resourceAccess";
import type { CollectionInfo } from "./vectorTypes";

/* ------------------------------------------------------------------ *
 * Manage the role grants of one vector collection through the shared
 * /api/v1/access/vector_collection/<access_id> endpoint.  This is the
 * same control every other tenanted asset uses; a collection follows
 * the same "grant a role, that role can use it" rule.
 * ------------------------------------------------------------------ */

interface CollectionAccessModalProps {
  collection: CollectionInfo | null;
  onClose: () => void;
  onSaved?: (roleNames: string[]) => void;
}

export function CollectionAccessModal({ collection, onClose, onSaved }: CollectionAccessModalProps) {
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [value, setValue] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accessId = collection?.access_id ?? null;

  useEffect(() => {
    if (!collection || accessId === null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getResourceAccess("vector_collection", accessId)
      .then((res) => {
        if (cancelled) return;
        setRoles(res.roles);
        setValue(res.access.map((entry) => entry.role_id));
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [collection, accessId]);

  const save = async (roleIds: number[]) => {
    if (accessId === null) return;
    setSaving(true);
    setError(null);
    try {
      const res = await putResourceAccess("vector_collection", accessId, roleIds);
      setRoles(res.roles);
      setValue(res.access.map((entry) => entry.role_id));
      onSaved?.(res.access.map((entry) => entry.role_name));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminModal
      title="Collection Access"
      open={collection !== null}
      onClose={onClose}
      footer={
        <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>Close</button>
      }
    >
      <p className="aa-muted" style={{ marginTop: 0 }}>
        <strong>{collection?.name}</strong> · {collection?.count ?? 0} vectors
      </p>
      {error && <div className="aa-error" role="alert">{error}</div>}
      {loading ? (
        <span className="aa-muted">Loading roles…</span>
      ) : (
        <ResourceAccessControl
          roles={roles}
          value={value}
          onSave={save}
          saving={saving}
          error={error}
          label="Granted roles"
          hint="Only granted roles (and administrators) can select this collection when uploading knowledge."
        />
      )}
    </AdminModal>
  );
}
