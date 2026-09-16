/* Role checkbox grid shared by the knowledge upload + paste-text forms. */

interface KnowledgeRoleGridProps {
  roles: string[];
  selected: Set<string>;
  onToggle: (role: string) => void;
}

export function KnowledgeRoleGrid({ roles, selected, onToggle }: KnowledgeRoleGridProps) {
  if (roles.length === 0) {
    return <span className="aa-muted">No roles available.</span>;
  }
  return (
    <div className="aa-admin-grid2">
      {roles.map((role) => (
        <label key={role} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <input type="checkbox" checked={selected.has(role)} onChange={() => onToggle(role)} />
          <span>{role}</span>
        </label>
      ))}
    </div>
  );
}
