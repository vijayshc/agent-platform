import { useEffect, useState } from "react";
import { AdminModal } from "../adminShared";
import { ResourceAccessControl } from "../../shared/ResourceAccessControl";
import {
  getResourceAccess,
  putResourceAccess,
  type RoleOption,
} from "../../shared/resourceAccess";

/* ------------------------------------------------------------------ *
 * KnowledgeAccessModal — manage the role grants of one knowledge
 * document through the generic /api/v1/access/knowledge_document/<id>
 * endpoint.  `accessId` is the document's stable integer identity (NOT
 * the UUID document id).
 * ------------------------------------------------------------------ */

interface KnowledgeAccessModalProps {
  open: boolean;
  accessId: number | null;
  documentLabel: string;
  onClose: () => void;
  onSaved?: (roleNames: string[]) => void;
}

export function KnowledgeAccessModal({
  open,
  accessId,
  documentLabel,
  onClose,
  onSaved,
}: KnowledgeAccessModalProps) {
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [value, setValue] = useState<number[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || accessId === null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getResourceAccess("knowledge_document", accessId)
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
  }, [open, accessId]);

  const save = async (roleIds: number[]) => {
    if (accessId === null) return;
    setSaving(true);
    setError(null);
    try {
      const res = await putResourceAccess("knowledge_document", accessId, roleIds);
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
      title="Document Access"
      open={open}
      onClose={onClose}
      footer={
        <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
          Close
        </button>
      }
    >
      <p className="aa-muted" style={{ marginTop: 0 }}>
        <strong>{documentLabel}</strong> · access id {accessId ?? "—"}
      </p>
      {error && (
        <div className="aa-error" role="alert">
          {error}
        </div>
      )}
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
          hint="Granted roles can view and use this document; only the owner or an administrator can delete it."
        />
      )}
    </AdminModal>
  );
}
