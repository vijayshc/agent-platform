import { useCallback, useEffect, useState } from "react";
import { Download, FileCode2, FileImage, FileSpreadsheet, FileText, FileType2, FolderOpen, RefreshCw, Trash2, X } from "lucide-react";
import { apiGet } from "../api";
import "./workspaceFiles.css";

export interface WorkspaceFile {
  path: string;
  name: string;
  size: number;
  modified_at: number;
  kind: string;
}

interface FilesResponse {
  files: WorkspaceFile[];
  truncated: boolean;
  total: number;
}

export function formatSize(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export function downloadUrl(conversationId: string, path: string): string {
  return `/api/v1/conversations/${encodeURIComponent(conversationId)}/files/download?path=${encodeURIComponent(path)}`;
}

function iconFor(kind: string) {
  switch (kind) {
    case "document":
      return FileType2;
    case "image":
      return FileImage;
    case "data":
      return FileSpreadsheet;
    case "code":
      return FileCode2;
    default:
      return FileText;
  }
}

/**
 * Right-hand panel listing everything the agent has written into this
 * conversation's workspace, with a download per file. Files touched since the
 * last turn are flagged so the user can see what the agent just produced.
 */
export function WorkspaceFiles({
  conversationId,
  open,
  onClose,
  refreshKey,
  since,
  onCount,
  live,
}: {
  conversationId: string | null;
  open: boolean;
  onClose: () => void;
  /** Bump to re-fetch (e.g. when a turn finishes). */
  refreshKey: number;
  /** Epoch seconds; files modified at/after this are marked as new. */
  since: number | null;
  /** Reports how many files exist, for the header badge. */
  onCount?: (count: number) => void;
  /** True while a run is in flight: poll so new artifacts show up promptly. */
  live?: boolean;
}) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!conversationId) {
      setFiles([]);
      onCount?.(0);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<FilesResponse>(`/api/v1/conversations/${encodeURIComponent(conversationId)}/files`);
      setFiles(res.files || []);
      onCount?.((res.files || []).length);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [conversationId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey, open]);

  // While the agent is working, files land as it goes. Polling keeps the list
  // and the badge current without waiting for the stream to close.
  useEffect(() => {
    if (!live || !conversationId) return;
    const timer = setInterval(() => void load(), 8000);
    return () => clearInterval(timer);
  }, [live, conversationId, load]);

  async function remove(file: WorkspaceFile) {
    if (!conversationId) return;
    setError(null);
    try {
      const res = await fetch(
        `/api/v1/conversations/${encodeURIComponent(conversationId)}/files?path=${encodeURIComponent(file.path)}`,
        { method: "DELETE", credentials: "same-origin" },
      );
      if (!res.ok) throw new Error(res.statusText);
      setFiles((prev) => prev.filter((f) => f.path !== file.path));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!open) return null;

  return (
    <aside className="aa-files-panel" data-testid="workspace-files">
      <div className="aa-files-head">
        <span className="aa-files-title">
          <FolderOpen size={15} strokeWidth={1.8} /> Files
        </span>
        <span className="aa-files-head-actions">
          <button type="button" className="aa-icon-btn" title="Refresh" aria-label="Refresh files" onClick={() => void load()}>
            <RefreshCw size={14} strokeWidth={1.8} />
          </button>
          <button type="button" className="aa-icon-btn" title="Close" aria-label="Close files panel" onClick={onClose}>
            <X size={15} strokeWidth={1.8} />
          </button>
        </span>
      </div>

      {error && <div className="aa-error aa-files-error">{error}</div>}

      {loading && files.length === 0 ? (
        <div className="aa-files-empty">Loading…</div>
      ) : files.length === 0 ? (
        <div className="aa-files-empty">
          <p>No files yet.</p>
          <p className="aa-muted">Files this agent creates in its workspace will appear here to download.</p>
        </div>
      ) : (
        <ul className="aa-files-list">
          {files.map((file) => {
            const Icon = iconFor(file.kind);
            const isNew = since != null && file.modified_at >= since;
            return (
              <li className="aa-files-row" key={file.path} data-testid={`workspace-file-${file.path}`}>
                <span className="aa-files-icon">
                  <Icon size={15} strokeWidth={1.8} />
                </span>
                <span className="aa-files-meta">
                  <span className="aa-files-name" title={file.path}>
                    {file.name}
                    {isNew && <span className="aa-files-new">new</span>}
                  </span>
                  <span className="aa-files-sub">
                    {file.path.includes("/") ? `${file.path.split("/").slice(0, -1).join("/")}/ · ` : ""}
                    {formatSize(file.size)} · {new Date(file.modified_at * 1000).toLocaleTimeString()}
                  </span>
                </span>
                <span className="aa-files-actions">
                  <a
                    className="aa-icon-btn"
                    href={downloadUrl(conversationId as string, file.path)}
                    download={file.name}
                    title={`Download ${file.name}`}
                    aria-label={`Download ${file.name}`}
                    data-testid={`download-${file.path}`}
                  >
                    <Download size={14} strokeWidth={1.8} />
                  </a>
                  <button
                    type="button"
                    className="aa-icon-btn danger"
                    title={`Delete ${file.name}`}
                    aria-label={`Delete ${file.name}`}
                    onClick={() => void remove(file)}
                  >
                    <Trash2 size={14} strokeWidth={1.8} />
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
