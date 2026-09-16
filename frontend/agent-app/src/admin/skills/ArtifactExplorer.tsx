import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FilePlus, FolderPlus, X } from "lucide-react";
import { CodeEditor } from "../../shared/CodeEditor";
import { AdminLoading } from "../adminShared";
import { ArtifactIcon } from "./ArtifactIcon";
import {
  createFolder,
  deleteArtifact,
  fetchArtifact,
  fetchSkillTree,
  flattenTree,
  formatBytes,
  saveArtifact,
  type ArtifactContent,
  type SkillTree,
} from "./skillsApi";

interface Props {
  skillName: string;
  /** Whether the caller owns the package (or is an admin). Grantees view only. */
  canManage: boolean;
  onChanged: () => void;
  onStatus: (message: string | null) => void;
}

/** Join a folder scope with a user-entered name without doubling separators. */
function joinPath(dir: string, name: string): string {
  const clean = name.replace(/^\/+/, "");
  return dir ? `${dir}/${clean}` : clean;
}

/**
 * Two-pane package editor: the artifact tree on the left, the selected file in
 * a Monaco editor on the right. SKILL.md is just another artifact -- editing
 * the frontmatter *is* editing the skill definition -- so there is a single
 * source of truth and no duplicate description field to drift out of sync.
 */
export function ArtifactExplorer({ skillName, canManage, onChanged, onStatus }: Props) {
  const [tree, setTree] = useState<SkillTree | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [file, setFile] = useState<ArtifactContent | null>(null);
  // Which path the in-memory draft belongs to. Saving is refused unless this
  // matches the current selection, so a Ctrl+S during a file switch cannot
  // write the previous file's text into the newly selected path.
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [newEntry, setNewEntry] = useState<{ kind: "file" | "dir"; value: string } | null>(null);
  const [activeDir, setActiveDir] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  const loadTree = useCallback(
    async (keepSelection = true) => {
      try {
        const data = await fetchSkillTree(skillName);
        setTree(data);
        setError(null);
        setExpanded((prev) => {
          if (prev.size) return prev;
          const roots = new Set(data.artifacts.filter((a) => a.type === "dir").map((a) => a.path.split("/")[0]));
          const withNested = new Set([...roots].filter((r) => data.artifacts.some((a) => a.path.startsWith(`${r}/`) && a.path.split("/").length > 2)));
          return withNested;
        });
        if (!keepSelection || !selected) setSelected("SKILL.md");
        return data;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      }
    },
    [skillName, selected],
  );

  useEffect(() => {
    setSelected("SKILL.md");
    setTree(null);
    setFile(null);
    setDirty(false);
    setNewEntry(null);
    setActiveDir("");
    setConfirming(null);
    setRevision(0);
    void loadTree(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [skillName]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setBusy(true);
    setLoadedPath(null);
    setFile(null);
    setDraft("");
    fetchArtifact(skillName, selected)
      .then((data) => {
        if (cancelled) return;
        setFile(data);
        setDraft(data.content);
        setLoadedPath(data.path);
        setDirty(false);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skillName, selected]);

  const rows = useMemo(() => (tree ? flattenTree(tree.artifacts, expanded) : []), [tree, expanded]);

  /** Clicking a folder toggles it and aims creation at it; clicking a file aims at its parent. */
  function selectRow(path: string, type: "file" | "dir") {
    const willExpand = type === "dir" ? !expanded.has(path) : false;
    if (type === "dir") {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (willExpand) next.add(path);
        else next.delete(path);
        return next;
      });
      setActiveDir(willExpand ? path : "");
      return;
    }
    setSelected(path);
    setActiveDir(path.includes("/") ? path.split("/").slice(0, -1).join("/") : "");
  }

  async function save() {
    if (!canManage || !selected || !file || busy) return;
    if (loadedPath !== selected) {
      setError("Still loading that file — try saving again in a moment.");
      return;
    }
    setBusy(true);
    onStatus(null);
    try {
      const saved = await saveArtifact(skillName, selected, draft);
      setFile(saved);
      setDirty(false);
      setRevision((value) => value + 1);
      onStatus(`Saved ${selected}`);
      await loadTree();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(path: string) {
    if (!canManage) return;
    setConfirming(null);
    setBusy(true);
    try {
      await deleteArtifact(skillName, path);
      onStatus(`Deleted ${path}`);
      if (selected && (selected === path || selected.startsWith(`${path}/`))) setSelected("SKILL.md");
      await loadTree();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function createFile(path: string) {
    if (!canManage) return;
    const exists = (tree?.artifacts || []).some((a) => a.path === path);
    if (exists) {
      setError(`${path} already exists — refusing to overwrite it.`);
      return;
    }
    setBusy(true);
    try {
      await saveArtifact(skillName, path, "");
      setNewEntry(null);
      setSelected(path);
      revealParents(path);
      await loadTree();
      onChanged();
      onStatus(`Created ${path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function createDirectory(path: string) {
    if (!canManage) return;
    setBusy(true);
    try {
      await createFolder(skillName, path);
      setNewEntry(null);
      revealParents(path);
      setExpanded((prev) => new Set(prev).add(path));
      setActiveDir(path);
      await loadTree();
      onChanged();
      onStatus(`Created folder ${path}/`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function revealParents(path: string) {
    const segments = path.split("/").slice(0, -1);
    if (!segments.length) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      segments.forEach((_, index) => next.add(segments.slice(0, index + 1).join("/")));
      return next;
    });
  }

  function submitNew() {
    const value = (newEntry?.value || "").trim();
    if (!value) return;
    const target = joinPath(activeDir, value);
    if (newEntry?.kind === "dir") void createDirectory(target);
    else void createFile(target);
  }

  if (!tree && !error) return <AdminLoading label={`Loading ${skillName}…`} />;

  return (
    <div className="aa-skill-explorer">
      <div className="aa-skill-tree">
        <div className="aa-skill-tree-head">
          <span className="aa-muted">
            {tree ? `${tree.artifacts.filter((a) => a.type === "file").length} files` : "—"}
          </span>
          {canManage && (
            <span className="aa-skill-tree-actions">
              <button
                type="button"
                className="aa-btn aa-btn-ghost aa-btn-mini"
                data-testid="new-file"
                onClick={() => setNewEntry({ kind: "file", value: "" })}
                disabled={busy}
              >
                <FilePlus size={13} strokeWidth={1.8} /> File
              </button>
              <button
                type="button"
                className="aa-btn aa-btn-ghost aa-btn-mini"
                data-testid="new-folder"
                onClick={() => setNewEntry({ kind: "dir", value: "" })}
                disabled={busy}
              >
                <FolderPlus size={13} strokeWidth={1.8} /> Folder
              </button>
            </span>
          )}
        </div>
        {newEntry && (
          <form
            className="aa-skill-newfile"
            onSubmit={(event) => {
              event.preventDefault();
              submitNew();
            }}
          >
            <div className="aa-skill-newfile-scope">
              {newEntry.kind === "dir" ? "New folder in" : "New file in"}{" "}
              <code>{activeDir ? `${activeDir}/` : `${skillName}/`}</code>
              {activeDir && (
                <button type="button" className="aa-skill-scope-reset" onClick={() => setActiveDir("")}>
                  (root)
                </button>
              )}
            </div>
            <div className="aa-skill-newfile-row">
              <input
                autoFocus
                value={newEntry.value}
                placeholder={newEntry.kind === "dir" ? "schemas" : "scripts/new_tool.py"}
                onChange={(event) => setNewEntry({ ...newEntry, value: event.target.value })}
                aria-label={newEntry.kind === "dir" ? "New folder name" : "New file path"}
              />
              <button type="submit" className="aa-btn aa-btn-primary aa-btn-mini" disabled={busy || !newEntry.value.trim()}>
                Create
              </button>
              <button type="button" className="aa-btn aa-btn-ghost aa-btn-mini" onClick={() => setNewEntry(null)}>
                Cancel
              </button>
            </div>
          </form>
        )}
        <div className="aa-skill-tree-list" role="tree">
          {rows.map((row) => {
            const isOpen = expanded.has(row.path);
            const active = selected === row.path;
            const isActiveDir = row.type === "dir" && activeDir === row.path;
            return (
              <div
                key={row.path}
                role="treeitem"
                aria-selected={active}
                aria-expanded={row.type === "dir" ? isOpen : undefined}
                className={`aa-skill-tree-row${active ? " active" : ""}${isActiveDir ? " scope" : ""}`}
                style={{ paddingLeft: 8 + row.depth * 14 }}
                data-testid={`artifact-${row.path}`}
                onClick={() => selectRow(row.path, row.type)}
              >
                {row.type === "dir" && (
                  <span className="aa-skill-caret">
                    {isOpen ? <ChevronDown size={12} strokeWidth={2} /> : <ChevronRight size={12} strokeWidth={2} />}
                  </span>
                )}
                <span className="aa-skill-icon">
                  <ArtifactIcon item={row} expanded={isOpen} />
                </span>
                <span className="aa-skill-name">{row.name}{row.type === "dir" ? "/" : ""}</span>
                {row.type === "file" && <span className="aa-skill-size">{formatBytes(row.size)}</span>}
                {row.type === "dir" && isActiveDir && <span className="aa-skill-scope-chip">target</span>}
                {canManage && !(row.type === "file" && row.is_skill_md) && confirming !== row.path && (
                  <button
                    type="button"
                    className="aa-skill-row-del"
                    title={`Delete ${row.path}${row.type === "dir" ? " and everything inside" : ""}`}
                    aria-label={`Delete ${row.path}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      setConfirming(row.path);
                    }}
                  >
                    <X size={13} strokeWidth={2} />
                  </button>
                )}
                {confirming === row.path && (
                  <span className="aa-skill-row-confirm" onClick={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="aa-btn aa-btn-danger aa-btn-mini"
                      onClick={() => void remove(row.path)}
                      disabled={busy}
                    >
                      Delete
                    </button>
                    <button type="button" className="aa-btn aa-btn-ghost aa-btn-mini" onClick={() => setConfirming(null)}>
                      No
                    </button>
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="aa-skill-editor">
        <div className="aa-skill-editor-head">
          <div className="aa-skill-editor-path">
            <strong>{selected || "Select a file"}</strong>
            {file && <span className="aa-muted"> · {formatBytes(file.size)}{file.binary ? " · binary" : ""}</span>}
          </div>
          <div className="aa-skill-editor-actions">
            {canManage ? (
              <>
                <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setDraft(file?.content ?? "")} disabled={!dirty || busy}>
                  Revert
                </button>
                <button type="button" className="aa-btn aa-btn-primary" onClick={() => void save()} disabled={!file?.editable || busy || !dirty}>
                  {busy ? "Saving…" : "Save"}
                </button>
              </>
            ) : (
              <span className="aa-muted">Read-only — view/use access</span>
            )}
          </div>
        </div>
        {error && <div className="aa-error">{error}</div>}
        {file && !file.editable ? (
          <div className="aa-skill-binary">
            <p>
              <strong>{file.path}</strong> is a binary artifact ({formatBytes(file.size)}). Binary files are imported,
              stored and served with the package, but cannot be edited inline.
            </p>
            <a className="aa-btn" href={`/api/v1/skills/${encodeURIComponent(skillName)}/export`} download>
              Download package ZIP
            </a>
          </div>
        ) : (
          <CodeEditor
            key={`${skillName}:${selected || "none"}`}
            value={draft}
            readOnly={!canManage}
            onChange={(next) => {
              setDraft(next);
              setDirty(true);
            }}
            filename={selected || "SKILL.md"}
            label={`${skillName} · ${selected || "SKILL.md"}`}
            fill
            minHeight={320}
            revision={canManage ? revision : undefined}
            onSave={canManage ? () => void save() : undefined}
            testId="skill-artifact-editor"
          />
        )}
      </div>
    </div>
  );
}
