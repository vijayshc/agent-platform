import { useCallback, useEffect, useState } from "react";
import { AdminLoading, AdminModal } from "../../adminShared";
import { ResourceAccessControl } from "../../../shared/ResourceAccessControl";
import {
  getResourceAccess,
  putResourceAccess,
  type RoleOption,
} from "../../../shared/resourceAccess";
import type { LlmConnection } from "./llmConnection";

interface Props {
  connection: LlmConnection;
  onClose: () => void;
}

/**
 * Role grants for one LLM connection, served by the shared generic access API.
 *
 * Only the owner or an administrator can reach this dialog (the page hides the
 * action otherwise, and the API answers 403), so a granted role is always
 * "may use this connection", never "may edit or delete it".
 */
export function LlmAccessModal({ connection, onClose }: Props) {
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [granted, setGranted] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    (data: { roles?: RoleOption[]; access?: { role_id: number }[] }) => {
      setRoles(data.roles || []);
      setGranted((data.access || []).map((entry) => entry.role_id));
    },
    [],
  );

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getResourceAccess("llm_connection", connection.id)
      .then((data) => apply(data))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [apply, connection.id]);

  useEffect(() => {
    load();
  }, [load]);

  async function save(roleIds: number[]) {
    setSaving(true);
    setError(null);
    try {
      apply(await putResourceAccess("llm_connection", connection.id, roleIds));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // The control updates optimistically; re-read so it shows what really saved.
      setGranted((current) => [...current]);
      load();
    } finally {
      setSaving(false);
    }
  }

  return (
    <AdminModal
      title={`Access — ${connection.name}`}
      open
      onClose={onClose}
      footer={
        <button type="button" className="aa-btn aa-btn-primary" onClick={onClose}>
          Close
        </button>
      }
    >
      {loading ? (
        <AdminLoading label="Loading access…" />
      ) : (
        <ResourceAccessControl
          roles={roles}
          value={granted}
          onSave={save}
          saving={saving}
          error={error}
          label="Roles that may use this connection"
          hint="Granted roles can pick this connection as a model. Only you and administrators can edit, test, or delete it."
        />
      )}
    </AdminModal>
  );
}
