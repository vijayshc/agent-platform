import { useCallback, useEffect, useRef, useState } from "react";
import { Folder, File, Upload, UploadCloud } from "lucide-react";
import { AdminDataTable, type Column } from "../AdminDataTable";
import {
  adminGet,
  adminPutJson,
  adminPostForm,
  AdminLoading,
  AdminError,
  AdminModal,
  PageHeader,
} from "../adminShared";
import { CodeEditor } from "../../shared/CodeEditor";
import { ActionsMenu } from "../../shared/ActionsMenu";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

interface Breadcrumb {
  label: string;
  path: string;
}

interface FileItem {
  name: string;
  isDirectory: boolean;
  size: number | null;
  modified: string;
  relativePath: string;
  uploadedBy?: number;
  uploadedByName?: string;
  updatedAt?: string;
}

interface FileListing {
  path: string;
  breadcrumbs: Breadcrumb[];
  items: FileItem[];
}

interface ContentResponse {
  path: string;
  content: string;
}

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

function encodePath(path: string): string {
  return encodeURIComponent(path);
}

function downloadFileUrl(path: string): string {
  return `/admin/file-browser/api/download-file?path=${encodePath(path)}`;
}

function downloadFolderUrl(path: string): string {
  return `/admin/file-browser/api/download-folder?path=${encodePath(path)}`;
}

/** Same-origin download triggered from a menu item (mirrors an <a download> click). */
function triggerDownload(url: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function getCsrfToken(): string | null {
  const el = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
  return el ? el.getAttribute("content") : null;
}

function deleteWithBody(path: string): Promise<{ success: boolean }> {
  return fetch("/admin/file-browser/api/item", {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() || "" },
    body: JSON.stringify({ path }),
  }).then(async (res) => {
    if (!res.ok) {
      let message = res.statusText;
      try {
        const body = await res.json();
        message = body.error || body.message || message;
      } catch {
        // ignore parse errors
      }
      throw new Error(message);
    }
    return res.json() as Promise<{ success: boolean }>;
  });
}

function formatSize(size: number | null): string {
  if (size === null || size === undefined) return "—";
  if (size === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
  const value = size / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/* ------------------------------------------------------------------ *
 * Component
 * ------------------------------------------------------------------ */

export function FileBrowserPage() {
  const [path, setPath] = useState("");
  const [listing, setListing] = useState<FileListing>({
    path: "",
    breadcrumbs: [],
    items: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const [editing, setEditing] = useState<FileItem | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState<FileItem | null>(null);

  const load = useCallback(
    (targetPath: string) => {
      setLoading(true);
      setError(null);
      adminGet<FileListing>(`/admin/file-browser/api/list?path=${encodePath(targetPath)}`)
        .then((d) => {
          setListing(d);
          setPath(d.path);
        })
        .catch((e) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setLoading(false));
    },
    []
  );

  useEffect(() => {
    load("");
  }, [load]);

  function navigate(targetPath: string) {
    if (targetPath === path) return;
    load(targetPath);
  }

  async function onUploadFile(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("path", path);
    setBusy("upload-file");
    try {
      await adminPostForm("/admin/file-browser/api/upload-file", fd);
      load(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function onUploadFolder(files: FileList) {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;
    const fd = new FormData();
    fd.append("path", path);
    fileArray.forEach((f: File) => {
      fd.append("files", f);
      fd.append("relative_paths", f.webkitRelativePath || f.name);
    });
    setBusy("upload-folder");
    try {
      await adminPostForm("/admin/file-browser/api/upload-folder", fd);
      load(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function openEdit(item: FileItem) {
    setEditing(item);
    setEditContent("");
    setEditError(null);
    setEditLoading(true);
    adminGet<ContentResponse>(`/admin/file-browser/api/file-content?path=${encodePath(item.relativePath)}`)
      .then((d) => setEditContent(d.content))
      .catch((e) => setEditError(e instanceof Error ? e.message : String(e)))
      .finally(() => setEditLoading(false));
  }

  async function saveEdit() {
    if (!editing) return;
    setBusy("save-edit");
    setEditError(null);
    try {
      await adminPutJson("/admin/file-browser/api/file-content", {
        path: editing.relativePath,
        content: editContent,
      });
      setEditing(null);
      load(path);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setBusy("delete");
    try {
      await deleteWithBody(deleting.relativePath);
      setDeleting(null);
      load(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const downloadUrlFor = (item: FileItem) =>
    item.isDirectory ? downloadFolderUrl(item.relativePath) : downloadFileUrl(item.relativePath);

  if (loading) return <AdminLoading label="Loading file browser…" />;
  if (error) return <AdminError message={error} />;

  const fileColumns: Column<FileItem>[] = [
    {
      key: "name",
      header: "Name",
      sortValue: (f) => f.name.toLowerCase(),
      render: (f) => (
        <>
          <div className="aa-table-name" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {f.isDirectory ? (
              <Folder size={16} style={{ flexShrink: 0, color: "var(--info-color)" }} />
            ) : (
              <File size={16} style={{ flexShrink: 0, color: "var(--text-muted)" }} />
            )}
            <span style={{ fontWeight: 600 }}>{f.name}</span>
          </div>
          {f.uploadedByName && (
            <div className="aa-table-sub">
              {f.isDirectory ? "Folder" : "Uploaded by"} {f.uploadedByName}
            </div>
          )}
        </>
      ),
    },
    {
      key: "size",
      header: "Size",
      width: "120px",
      sortValue: (f) => (f.size ?? -1),
      render: (f) => <span className="aa-table-date">{formatSize(f.size)}</span>,
    },
    {
      key: "modified",
      header: "Modified",
      width: "200px",
      sortValue: (f) => new Date(f.modified).getTime(),
      render: (f) => <span className="aa-table-date">{formatDate(f.modified)}</span>,
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      width: "130px",
      render: (f) => (
        <span onClick={(e) => e.stopPropagation()}>
          <ActionsMenu
            label="Actions"
            testId={`file-actions-${f.relativePath}`}
            items={[
              f.isDirectory
                ? {
                    key: "open",
                    label: "Open",
                    testId: `file-open-${f.relativePath}`,
                    onSelect: () => navigate(f.relativePath),
                  }
                : {
                    key: "edit",
                    label: "Edit",
                    testId: `file-edit-${f.relativePath}`,
                    onSelect: () => openEdit(f),
                  },
              {
                key: "download",
                label: "Download",
                testId: `file-download-${f.relativePath}`,
                onSelect: () => triggerDownload(downloadUrlFor(f)),
              },
              {
                key: "delete",
                label: "Delete",
                danger: true,
                testId: `file-delete-${f.relativePath}`,
                onSelect: () => setDeleting(f),
              },
            ]}
          />
        </span>
      ),
    },
  ];

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="File Browser"
        actions={
          <>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              disabled={busy !== null}
              onClick={() => fileInputRef.current?.click()}
            >
              <Upload size={14} style={{ marginRight: 6, verticalAlign: "middle" }} />
              Upload File
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              disabled={busy !== null}
              onClick={() => {
                const el = folderInputRef.current;
                if (el) {
                  el.setAttribute("webkitdirectory", "");
                  el.click();
                }
              }}
            >
              <UploadCloud size={14} style={{ marginRight: 6, verticalAlign: "middle" }} />
              Upload Folder
            </button>
          </>
        }
      />

      <input
        ref={fileInputRef}
        type="file"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onUploadFile(f);
          e.target.value = "";
        }}
      />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        style={{ display: "none" }}
        onChange={(e) => {
          if (e.target.files) onUploadFolder(e.target.files);
          e.target.value = "";
        }}
      />

      {/* Breadcrumbs */}
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {listing.breadcrumbs.map((crumb, i) => (
          <span key={crumb.path} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {i > 0 && <span className="aa-muted">/</span>}
            <button
              type="button"
              className="aa-btn aa-btn-ghost"
              style={{ padding: "4px 10px" }}
              onClick={() => navigate(crumb.path)}
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </div>

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <h2>Contents</h2>
          <span className="aa-muted">
            {listing.items.length} item{listing.items.length === 1 ? "" : "s"}
          </span>
        </div>
        <AdminDataTable<FileItem>
          columns={fileColumns}
          rows={listing.items}
          rowKey={(f) => f.relativePath}
          searchText={(f) => f.name}
          searchPlaceholder="Filter files…"
          emptyMessage="This folder is empty"
          onRowClick={(f) => {
            if (f.isDirectory) navigate(f.relativePath);
          }}
        />
      </div>

      {/* Edit modal */}
      <AdminModal
        title={editing ? `Edit — ${editing.name}` : "Edit File"}
        open={editing !== null}
        onClose={() => setEditing(null)}
        wide
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={saveEdit}
              disabled={busy !== null || editLoading || !editing}
            >
              {busy === "save-edit" ? "Saving…" : "Save"}
            </button>
          </>
        }
      >
        {editLoading ? (
          <AdminLoading label="Loading file content…" />
        ) : editError ? (
          <AdminError message={editError} />
        ) : (
          <CodeEditor
            key={editing?.relativePath || "file"}
            value={editContent}
            onChange={setEditContent}
            filename={editing?.name}
            label={editing ? `Edit ${editing.name}` : "Edit file"}
            height={420}
            onSave={saveEdit}
            testId="file-content-editor"
          />
        )}
      </AdminModal>

      {/* Delete confirm modal */}
      <AdminModal
        title="Delete Item"
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setDeleting(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="aa-btn aa-btn-primary"
              onClick={confirmDelete}
              disabled={busy !== null}
              style={{ background: "var(--danger-color)" }}
            >
              {busy === "delete" ? "Deleting…" : "Delete"}
            </button>
          </>
        }
      >
        <div>
          Are you sure you want to delete <strong>{deleting?.name}</strong>? This action cannot be
          undone.
        </div>
      </AdminModal>
    </div>
  );
}
