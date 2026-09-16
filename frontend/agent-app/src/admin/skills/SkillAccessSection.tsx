import { useCallback, useEffect, useState } from "react";
import { ResourceAccessControl, type RoleOption } from "../../shared/ResourceAccessControl";
import { getResourceAccess, putResourceAccess } from "../../shared/resourceAccess";

/**
 * Role grants for one skill package.
 *
 * Uses the generic per-resource access API, so the same control serves every
 * tenanted asset. Only the owner or an administrator can read/manage a
 * package's grants; anyone else gets a 403 and the section stays hidden.
 */
export function SkillAccessSection({ skillId, skillName }: { skillId: number; skillName: string }) {
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [value, setValue] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setForbidden(false);
    getResourceAccess("skill_package", skillId)
      .then((body) => {
        if (cancelled) return;
        setRoles(body.roles || []);
        setValue((body.access || []).map((entry) => entry.role_id));
      })
      .catch(() => {
        if (!cancelled) setForbidden(true);
      });
    return () => {
      cancelled = true;
    };
  }, [skillId]);

  const onSave = useCallback(
    async (roleIds: number[]) => {
      setSaving(true);
      setError(null);
      try {
        const body = await putResourceAccess("skill_package", skillId, roleIds);
        setRoles(body.roles || []);
        setValue((body.access || []).map((entry) => entry.role_id));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSaving(false);
      }
    },
    [skillId],
  );

  if (forbidden) return null;

  return (
    <ResourceAccessControl
      roles={roles}
      value={value}
      onSave={onSave}
      saving={saving}
      error={error}
      label={`Shared with roles — ${skillName}`}
      hint="Roles granted here may view and use this skill. Only you and administrators can edit or delete it."
    />
  );
}
