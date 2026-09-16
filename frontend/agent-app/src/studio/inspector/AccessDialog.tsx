import { useCallback, useEffect, useRef, useState } from "react";
import { ResourceAccessControl } from "../../shared/ResourceAccessControl";
import {
  getResourceAccess,
  putResourceAccess,
  type RoleOption,
} from "../../shared/resourceAccess";
import type { AgentDef } from "../../types";

interface Props {
  agent: AgentDef;
  onClose: () => void;
}

function errorText(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

/**
 * Role-based access for one saved definition.
 *
 * Grants are stored and served by the generic per-resource endpoint
 * (``/api/v1/access/agent/<id>``) through the shared ``resourceAccess``
 * helpers and edited with the shared ``ResourceAccessControl``, so an agent is
 * shared exactly like every other tenanted asset — one access UI in the
 * product. Only the owner or an administrator may open this dialog; the server
 * answers 403 for anyone else and that error is surfaced here.
 */
export function AccessDialog({ agent, onClose }: Props) {
  const [grants, setGrants] = useState<number[]>([]);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await getResourceAccess("agent", agent.id);
      setGrants(body.access.map((entry) => entry.role_id));
      setRoles(body.roles || []);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  }, [agent.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const save = useCallback(
    async (roleIds: number[]) => {
      setSaving(true);
      setError(null);
      try {
        const body = await putResourceAccess("agent", agent.id, roleIds);
        setGrants(body.access.map((entry) => entry.role_id));
        setRoles(body.roles || []);
      } catch (cause) {
        setError(errorText(cause));
      } finally {
        setSaving(false);
      }
    },
    [agent.id],
  );

  return (
    <div
      className="as-overlay"
      data-testid="access-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="as-dialog"
        data-testid="access-card"
        role="dialog"
        aria-modal="true"
        aria-label={`Access for ${agent.name}`}
        ref={dialogRef}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="as-dialog-head">
          <div className="as-dialog-title">Access</div>
          <p className="as-dialog-sub">{agent.name}</p>
          <button
            type="button"
            className="as-btn-icon as-dialog-close"
            aria-label="Close access dialog"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="as-dialog-body">
          <p className="as-help">
            The owner and administrators can always use this agent. Grant a role to let its
            members use and publish it too.
          </p>
          {loading ? (
            <p className="as-empty" role="status">
              Loading access…
            </p>
          ) : (
            <ResourceAccessControl
              roles={roles}
              value={grants}
              onSave={save}
              saving={saving}
              error={error}
              label="Roles that may use this agent"
            />
          )}
        </div>

        <div className="as-dialog-foot as-row">
          <button type="button" className="as-btn" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
