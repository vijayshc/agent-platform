import { useEffect, useMemo, useState } from "react";
import { AdminField, AdminModal, adminPostForm } from "../adminShared";
import { SearchableSelect } from "../SearchableSelect";
import { KnowledgeRoleGrid } from "./KnowledgeRoleGrid";
import type { KnownDoc } from "./KnowledgeDocumentsTable";
import { ChunkingFields, ColumnPreview, ColumnRolePicker } from "./KnowledgeIngestFields";
import {
  chunkingFieldErrors,
  columnsWithRole,
  defaultCollection,
  defaultColumnRoles,
  extensionOf,
  inspectFile,
  isTabularFile,
  splitTags,
  type ColumnRole,
  type IngestMeta,
  type InspectResult,
  type KnowledgeCollection,
  type UploadResponse,
} from "./knowledgeIngest";

/* ------------------------------------------------------------------ *
 * Upload File modal.  Prose files get chunking knobs; CSV/Excel files
 * are inspected first so each row can be indexed with an explicit
 * data/metadata/skip role per column.
 * ------------------------------------------------------------------ */

interface KnowledgeUploadModalProps {
  open: boolean;
  roles: string[];
  meta: IngestMeta;
  collections: KnowledgeCollection[];
  onClose: () => void;
  onUploaded: (doc: KnownDoc) => void;
}

export function KnowledgeUploadModal({ open, roles, meta, collections, onClose, onUploaded }: KnowledgeUploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [tags, setTags] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());
  const [collection, setCollection] = useState("");
  const [method, setMethod] = useState(meta.defaults.chunking_method);
  const [size, setSize] = useState(String(meta.defaults.chunk_size));
  const [overlap, setOverlap] = useState(String(meta.defaults.chunk_overlap));
  const [inspect, setInspect] = useState<InspectResult | null>(null);
  const [columnRoles, setColumnRoles] = useState<Record<string, ColumnRole>>({});
  const [inspecting, setInspecting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Every open starts from a clean slate so a previous file never leaks in.
  useEffect(() => {
    if (!open) return;
    setFile(null);
    setTags("");
    setSelectedRoles(new Set());
    setCollection(defaultCollection(collections));
    setMethod(meta.defaults.chunking_method);
    setSize(String(meta.defaults.chunk_size));
    setOverlap(String(meta.defaults.chunk_overlap));
    setInspect(null);
    setColumnRoles({});
    setInspecting(false);
    setSaving(false);
    setError(null);
  }, [open, meta, collections]);

  const tabular = useMemo(() => (file ? isTabularFile(meta, file.name) : false), [file, meta]);
  const dataColumns = columnsWithRole(columnRoles, "data");
  const metadataColumns = columnsWithRole(columnRoles, "metadata");
  // For tabular uploads the chunking knobs are not used (one row = one chunk).
  const fieldErrors = tabular ? { size: null, overlap: null } : chunkingFieldErrors(size, overlap);
  const chunkingInvalid = !tabular && (fieldErrors.size !== null || fieldErrors.overlap !== null);

  const pickFile = async (next: File | null) => {
    setFile(next);
    setInspect(null);
    setColumnRoles({});
    setError(null);
    if (!next || !isTabularFile(meta, next.name)) return;

    setInspecting(true);
    try {
      const res = await inspectFile(next);
      if (!res.success) {
        setError(res.error || "Could not read this file's columns.");
        return;
      }
      if (res.kind === "tabular" && res.columns) {
        setInspect(res);
        setColumnRoles(defaultColumnRoles(res.columns));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInspecting(false);
    }
  };

  const setRole = (column: string, role: ColumnRole) =>
    setColumnRoles((prev) => ({ ...prev, [column]: role }));

  const setAllRoles = (role: ColumnRole) =>
    setColumnRoles((prev) => Object.fromEntries(Object.keys(prev).map((c) => [c, role])));

  const toggleRole = (role: string) =>
    setSelectedRoles((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });

  const submit = async () => {
    if (!file) {
      setError("Select a file to upload.");
      return;
    }
    if (!collection) {
      setError("Select a collection to index into.");
      return;
    }
    if (tabular && inspect?.success && dataColumns.length === 0) {
      setError("Choose at least one column to embed, or move all columns to Metadata/Skip.");
      return;
    }
    if (chunkingInvalid) {
      setError("Fix the chunk size and overlap values before uploading.");
      return;
    }
    setSaving(true);
    setError(null);

    const form = new FormData();
    form.append("document", file);
    form.append("tags", tags);
    form.append("collection_name", collection);
    selectedRoles.forEach((role) => form.append("allowed_roles", role));
    form.append("chunking_method", method);
    form.append("chunk_size", size);
    form.append("chunk_overlap", overlap);
    if (tabular && inspect?.success) {
      form.append("data_columns", JSON.stringify(dataColumns));
      form.append("metadata_columns", JSON.stringify(metadataColumns));
    }

    try {
      const res = await adminPostForm<UploadResponse>("/api/knowledge/upload", form);
      if (!res.success) {
        setError(res.error || res.message || "Upload failed.");
        return;
      }
      onUploaded({
        id: res.documentId,
        access_id: res.accessId,
        can_manage: true,
        name: res.originalFilename || file.name,
        content_type: extensionOf(file.name) || "file",
        status: "processing",
        created_at: new Date().toISOString(),
        tags: res.tags ?? splitTags(tags),
        allowed_roles: res.allowed_roles ?? Array.from(selectedRoles),
        chunking_method: res.chunking_method,
        chunk_size: res.chunk_size,
        chunk_overlap: res.chunk_overlap,
        metadata_columns: res.metadata_columns,
        data_columns: res.data_columns,
        collection_name: res.collection_name ?? collection,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AdminModal
      title="Upload File"
      open={open}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            disabled={saving || inspecting || !file || !collection || (tabular && !inspect?.success) || chunkingInvalid}
            onClick={() => void submit()}
            data-testid="knowledge-upload-submit"
          >
            {saving ? "Uploading…" : "Upload & Index"}
          </button>
        </>
      }
    >
      {error && <div className="aa-error" role="alert">{error}</div>}

      <AdminField
        label="File *"
        hint={tabular ? "CSV/Excel files are indexed one row per record." : "Documents are converted to text and chunked."}
      >
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            void pickFile(e.dataTransfer.files?.[0] ?? null);
          }}
          className="aa-dropzone"
        >
          {/* Visually hidden so the native control's filename never duplicates
              the label below; a <label> still opens the picker. */}
          <input
            id="knowledge-upload-file-input"
            className="aa-dropzone-input"
            type="file"
            onChange={(e) => void pickFile(e.target.files?.[0] ?? null)}
            data-testid="knowledge-upload-file"
          />
          {file ? (
            <span className="aa-dropzone-file">
              <span>
                <strong>{file.name}</strong>
                <span className="aa-muted" style={{ marginLeft: 8 }}>{(file.size / 1024).toFixed(1)} KB</span>
              </span>
              <label htmlFor="knowledge-upload-file-input" className="aa-btn aa-btn-ghost aa-dropzone-pick">Replace</label>
            </span>
          ) : (
            <>
              <span className="aa-muted">Drag &amp; drop a file here, or</span>
              <label htmlFor="knowledge-upload-file-input" className="aa-btn aa-btn-ghost aa-dropzone-pick">Choose file</label>
            </>
          )}
        </div>
      </AdminField>

      <AdminField
        label="Collection *"
        hint="Where the vectors are stored. Collections are created and granted on the Vector DB page."
      >
        <SearchableSelect
          options={collections.map((c) => ({
            value: c.name,
            label: c.name,
            hint: c.roles && c.roles.length > 0 ? `roles: ${c.roles.join(", ")}` : undefined,
          }))}
          value={collection}
          onChange={setCollection}
          placeholder={collections.length ? "Select a collection" : "No collections available"}
          searchPlaceholder="Search collections…"
          disabled={collections.length === 0}
          testId="knowledge-collection-select"
        />
      </AdminField>
      {collections.length === 0 && (
        <div className="aa-error" role="alert">
          You are not authorized for any collection. Ask an administrator to grant your role access on the Vector DB page.
        </div>
      )}

      {tabular ? (
        inspecting ? (
          <div className="aa-muted">Reading columns…</div>
        ) : inspect?.success && inspect.columns ? (
          <>
            <AdminField
              label="Column Mapping"
              hint={`${inspect.row_count ?? 0} rows · each row becomes one chunk. “Embed” columns are searchable; “Metadata” is kept but not embedded.`}
            >
              <ColumnRolePicker
                columns={inspect.columns}
                roles={columnRoles}
                onSet={setRole}
                onSetAll={setAllRoles}
              />
            </AdminField>
            <AdminField label="Preview" hint="First rows of the file.">
              <ColumnPreview rows={inspect.sample_rows ?? []} columns={inspect.columns} roles={columnRoles} />
            </AdminField>
          </>
        ) : (
          <div className="aa-muted">Choose a valid CSV/Excel file to map its columns.</div>
        )
      ) : (
        <ChunkingFields
          meta={meta}
          method={method}
          size={size}
          overlap={overlap}
          onMethod={setMethod}
          onSize={setSize}
          onOverlap={setOverlap}
        />
      )}

      <AdminField label="Tags" hint="Comma-separated (e.g. policy, manual, report)">
        <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="policy, manual, report" />
      </AdminField>

      <AdminField label="Allowed Roles" hint="Leave empty for owner and administrators only.">
        <KnowledgeRoleGrid roles={roles} selected={selectedRoles} onToggle={toggleRole} />
      </AdminField>
    </AdminModal>
  );
}
