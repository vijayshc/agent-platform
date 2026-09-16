import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, FileArchive, Pencil, Trash2 } from "lucide-react";
import { AdminModal, AdminField, PageHeader, adminPostJson } from "../adminShared";
import { ArtifactExplorer } from "./ArtifactExplorer";
import { ImportZipDialog } from "./ImportZipDialog";
import { SkillAccessSection } from "./SkillAccessSection";
import "./skills.css";
import {
  createSkillPackage,
  deleteSkillPackage,
  exportSkillUrl,
  fetchSkills,
  formatBytes,
  type ImportResult,
  type LegacySkill,
  type SkillPackage,
  type SkillsOverview,
} from "./skillsApi";

type ModalKind = "import" | "new" | "delete" | "legacy" | null;

/**
 * Skill Library.
 *
 * Lists the *live* SKILL.md packages the agent runtime loads through
 * `SkillsProvider` (`/api/v1/studio/resources` -> `list_packages`). The legacy
 * DB-backed library is still shown, read-only, so nothing disappears from the
 * admin surface while the package model becomes the single source of truth.
 */
export function SkillsPage() {
  const [data, setData] = useState<SkillsOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [vectorizing, setVectorizing] = useState(false);

  const [modal, setModal] = useState<ModalKind>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SkillPackage | null>(null);
  const [legacyView, setLegacyView] = useState<LegacySkill | null>(null);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setData(await fetchSkills());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const packages = data?.packages || [];
  const legacy = data?.legacy || [];

  const editingPkg = useMemo(
    () => (editing ? packages.find((item) => item.name === editing) || null : null),
    [packages, editing],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return packages;
    return packages.filter((item) =>
      `${item.name} ${item.description} ${item.path}`.toLowerCase().includes(needle),
    );
  }, [packages, query]);

  const visibleLegacy = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return legacy;
    return legacy.filter((item) => `${item.name} ${item.description} ${item.category}`.toLowerCase().includes(needle));
  }, [legacy, query]);

  const totalFiles = data?.total_files ?? 0;

  async function reprocessVector() {
    setVectorizing(true);
    setStatus(null);
    try {
      const res = await adminPostJson<{ success: boolean; message?: string }>("/api/skills/vectorize", {});
      setStatus(res.success ? res.message || "Vector store reprocessed." : "Reprocessing returned an error.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    } finally {
      setVectorizing(false);
    }
  }

  async function createSkill() {
    const name = newName.trim();
    if (!name) {
      setFormError("Name is required.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      await createSkillPackage(name, newDescription.trim());
      setModal(null);
      setNewName("");
      setNewDescription("");
      await load();
      setEditing(name);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setBusy(true);
    setFormError(null);
    try {
      await deleteSkillPackage(deleteTarget.name);
      setModal(null);
      setDeleteTarget(null);
      setStatus(`Deleted ${deleteTarget.name}`);
      await load();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function handleImported(result: ImportResult) {
    if (result.imported.length) {
      setStatus(`Imported ${result.imported.map((item) => item.name).join(", ")}`);
      void load();
    }
  }

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="Skill Library"
        subtitle="SKILL.md packages the agent runtime loads — SKILL.md plus scripts, references and assets."
        actions={
          <div className="aa-admin-toolbar-inline">
            {status && (
              <span className="aa-muted" style={{ marginRight: 8 }} data-testid="skills-status">
                {status}
              </span>
            )}
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => void reprocessVector()} disabled={vectorizing}>
              {vectorizing ? "Reprocessing…" : "Reprocess Vector Store"}
            </button>
            <button type="button" className="aa-btn" data-testid="open-import" onClick={() => setModal("import")}>
              <FileArchive size={14} strokeWidth={1.8} /> Import ZIP
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              data-testid="open-new-skill"
              onClick={() => {
                setNewName("");
                setNewDescription("");
                setFormError(null);
                setModal("new");
              }}
            >
              New Skill
            </button>
          </div>
        }
      />

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head aa-skill-panel-head">
          <span className="aa-muted">
            {packages.length} package{packages.length === 1 ? "" : "s"} · {totalFiles} artifacts
            {legacy.length ? ` · ${legacy.length} legacy` : ""}
          </span>
          <input
            type="search"
            className="aa-search"
            placeholder="Search skills…"
            value={query}
            aria-label="Search skills"
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {loading ? (
          <div className="aa-admin-state">Loading skills…</div>
        ) : error ? (
          <div className="aa-admin-state aa-admin-error">{error}</div>
        ) : (
          <>
            <div className="aa-skill-cards" data-testid="package-list">
              {visible.map((item) => (
                <article className="aa-skill-card" key={item.name} data-testid={`skill-card-${item.name}`}>
                  <header className="aa-skill-card-head">
                    <div>
                      <h3>{item.name}</h3>
                      <div className="aa-skill-card-path" title={item.path}>
                        {item.path}
                      </div>
                    </div>
                    <span className={`aa-chip${item.seeded ? " seeded" : ""}`}>
                      {item.seeded ? "seeded" : item.writable ? "package" : "read-only"}
                    </span>
                  </header>
                  <p className="aa-skill-card-desc">{item.description || <span className="aa-muted">No description in frontmatter.</span>}</p>
                  <div className="aa-skill-card-stats">
                    <span>{item.file_count} files</span>
                    <span>{item.dir_count} folders</span>
                    <span>{formatBytes(item.size_bytes)}</span>
                    {!item.has_skill_md && <span className="aa-skill-warn">missing SKILL.md</span>}
                  </div>
                  <footer className="aa-skill-card-actions">
                    <button type="button" className="aa-btn aa-btn-primary aa-btn-mini" onClick={() => setEditing(item.name)}>
                      <Pencil size={13} strokeWidth={1.8} /> Edit
                    </button>
                    <a className="aa-btn aa-btn-ghost aa-btn-mini" href={exportSkillUrl(item.name)} download>
                      <Download size={13} strokeWidth={1.8} /> Download
                    </a>
                    {item.seeded ? (
                      <span className="aa-muted aa-skill-seeded-hint" title="Seeded skills ship with the app; edit one to create an editable copy.">
                        built-in
                      </span>
                    ) : item.can_manage ? (
                      <button
                        type="button"
                        className="aa-btn aa-btn-danger aa-btn-mini"
                        onClick={() => {
                          setDeleteTarget(item);
                          setFormError(null);
                          setModal("delete");
                        }}
                      >
                        <Trash2 size={13} strokeWidth={1.8} /> Delete
                      </button>
                    ) : (
                      <span className="aa-muted aa-skill-seeded-hint" title="Only the owner or an administrator can delete this skill.">
                        read-only
                      </span>
                    )}
                  </footer>
                </article>
              ))}
              {!visible.length && (
                <div className="aa-admin-state">
                  {packages.length ? "No skills match that search." : "No skill packages yet — import a ZIP to get started."}
                </div>
              )}
            </div>

            {visibleLegacy.length > 0 && (
              <div className="aa-legacy-block">
                <div className="aa-legacy-head">
                  <h3>Legacy library</h3>
                  <span className="aa-muted">
                    Rows from the original database skill table. Read-only here — they are not loaded by the agent runtime.
                  </span>
                </div>
                <div className="aa-legacy-rows">
                  {visibleLegacy.map((item) => (
                    <div className="aa-legacy-row" key={item.skill_id || item.name}>
                      <div>
                        <strong>{item.name}</strong>
                        <div className="aa-muted aa-skill-card-path">{item.skill_id}</div>
                      </div>
                      <span className="aa-chip">{item.category || "general"}</span>
                      <span className="aa-status-pill">{item.status}</span>
                      <span className="aa-muted aa-legacy-desc">{item.description || "—"}</span>
                      <button
                        type="button"
                        className="aa-btn aa-btn-ghost aa-btn-mini"
                        onClick={() => {
                          setLegacyView(item);
                          setModal("legacy");
                        }}
                      >
                        View
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {editing && (
        <AdminModal
          title={`Edit skill — ${editing}`}
          open
          onClose={() => {
            setEditing(null);
            setStatus(null);
          }}
          wide
          footer={
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={() => {
                setEditing(null);
                setStatus(null);
              }}
            >
              Done
            </button>
          }
        >
          {status && <div className="aa-muted" style={{ marginBottom: 8 }}>{status}</div>}
          {editingPkg?.can_manage && editingPkg.id != null && (
            <SkillAccessSection skillId={editingPkg.id} skillName={editingPkg.name} />
          )}
          <ArtifactExplorer
            skillName={editing}
            canManage={editingPkg?.can_manage ?? false}
            onChanged={() => void load()}
            onStatus={setStatus}
          />
        </AdminModal>
      )}

      <ImportZipDialog
        open={modal === "import"}
        onClose={() => setModal(null)}
        onImported={handleImported}
      />

      <AdminModal
        title="New Skill"
        open={modal === "new"}
        onClose={() => setModal(null)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setModal(null)}>
              Cancel
            </button>
            <button type="button" className="aa-btn aa-btn-primary" onClick={() => void createSkill()} disabled={busy}>
              {busy ? "Creating…" : "Create"}
            </button>
          </>
        }
      >
        {formError && <div className="aa-error">{formError}</div>}
        <AdminField label="Name" hint="Lowercase letters, digits and hyphens. Becomes the package folder and SKILL.md name.">
          <input
            type="text"
            value={newName}
            autoFocus
            placeholder="pdf-forms"
            data-testid="new-skill-name"
            onChange={(event) => setNewName(event.target.value)}
          />
        </AdminField>
        <AdminField label="Description" hint="One line telling the agent when to use this skill.">
          <input
            type="text"
            value={newDescription}
            placeholder="Fill and validate PDF forms."
            onChange={(event) => setNewDescription(event.target.value)}
          />
        </AdminField>
      </AdminModal>

      <AdminModal
        title="Delete Skill"
        open={modal === "delete"}
        onClose={() => setModal(null)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setModal(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              style={{ background: "var(--danger-color)" }}
              onClick={() => void confirmDelete()}
              disabled={busy}
            >
              {busy ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        {formError && <div className="aa-error">{formError}</div>}
        <p style={{ margin: 0 }}>
          Delete <strong>{deleteTarget?.name}</strong> and all {deleteTarget?.file_count ?? 0} of its files from disk? This
          cannot be undone.
        </p>
      </AdminModal>

      <AdminModal
        title={legacyView ? `Legacy skill — ${legacyView.name}` : "Legacy skill"}
        open={modal === "legacy"}
        onClose={() => setModal(null)}
        footer={
          <button type="button" className="aa-btn aa-btn-primary" onClick={() => setModal(null)}>
            Close
          </button>
        }
      >
        {legacyView && (
          <div>
            <p className="aa-muted" style={{ marginTop: 0 }}>
              This row lives in the legacy database skill table. Convert it into a runtime package by creating a skill and
              pasting these instructions into <code>SKILL.md</code>.
            </p>
            <div className="aa-admin-field">
              <div className="aa-admin-field-label">Skill ID</div>
              <div>{legacyView.skill_id}</div>
            </div>
            <div className="aa-admin-field">
              <div className="aa-admin-field-label">Category / version</div>
              <div>
                {legacyView.category || "—"} · {legacyView.version || "—"}
              </div>
            </div>
            <div className="aa-admin-field">
              <div className="aa-admin-field-label">Description</div>
              <div>{legacyView.description || "—"}</div>
            </div>
          </div>
        )}
      </AdminModal>
    </div>
  );
}
