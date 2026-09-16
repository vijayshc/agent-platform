import { useCallback, useEffect, useState } from "react";
import { getResourceAccess, putResourceAccess, type RoleOption } from "../../shared/resourceAccess";
import { ResourceAccessControl } from "../../shared/ResourceAccessControl";
import type { HostedAppSummary } from "./HostedAppEditDialog";

export { EditHostedAppDialog } from "./HostedAppEditDialog";
export type { HostedAppSummary } from "./HostedAppEditDialog";

/**
 * Who may open an application. Grants are stored and served by the generic
 * per-resource access endpoint (``/api/v1/access/hosted_app/<id>``) through the
 * shared ``resourceAccess`` helpers, and edited with the shared
 * ``ResourceAccessControl``, so a hosted app is shared exactly like every other
 * tenanted asset - one way to do this in the product, not two.
 */
export function HostedAppAccessDialog({
  app,
  onClose,
  onSaved,
}: {
  app: HostedAppSummary | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [access, setAccess] = useState<number[]>([]);
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!app?.id) return;
    setError(null);
    try {
      const body = await getResourceAccess("hosted_app", app.id);
      setAccess(body.access.map((entry) => entry.role_id));
      setRoles(body.roles || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [app]);

  useEffect(() => {
    setAccess([]);
    setRoles([]);
    void load();
  }, [load]);

  const save = useCallback(
    async (roleIds: number[]) => {
      if (!app?.id) return;
      setSaving(true);
      setError(null);
      try {
        const body = await putResourceAccess("hosted_app", app.id, roleIds);
        setAccess(body.access.map((entry) => entry.role_id));
        setRoles(body.roles || []);
        onSaved();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [app, onSaved],
  );

  if (!app) return null;

  return (
    <div className="aa-overlay" data-testid="hosted-access-dialog" onClick={onClose}>
      <div className="aa-access-card" onClick={(event) => event.stopPropagation()}>
        <div className="aa-access-head">
          <div>
            <div className="aa-access-title">Access</div>
            <div className="aa-access-sub">{app.name}</div>
          </div>
          <button type="button" className="aa-access-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <p className="aa-muted" style={{ margin: "0 0 10px" }}>
          The owner and administrators can always open this application. Grant a role here to let
          its members open it too.
        </p>

        <ResourceAccessControl
          roles={roles}
          value={access}
          onSave={save}
          saving={saving}
          error={error}
          label="Roles that may open this application"
        />
      </div>
    </div>
  );
}


/**
 * Import an application, then watch the install happen.
 *
 * The upload returns as soon as the files are extracted; the dependency install
 * (pip, then npm, then the build) runs in the background and can take minutes.
 * This dialog stays open and follows it step by step, because "Importing…" with
 * nothing else is indistinguishable from a hang when something goes wrong.
 */
/**
 * Follow one app's install until it finishes.
 *
 * Used both for a fresh upload and for retrying an app whose install did not
 * complete: the steps, the log and the outcome are identical either way.
 */
