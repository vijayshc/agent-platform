import { useEffect, useState } from "react";
import { AdminField, AdminModal, adminPostJson } from "../adminShared";
import { SearchableSelect } from "../SearchableSelect";
import { CodeEditor } from "../../shared/CodeEditor";
import { KnowledgeRoleGrid } from "./KnowledgeRoleGrid";
import type { KnownDoc } from "./KnowledgeDocumentsTable";
import { ChunkingFields } from "./KnowledgeIngestFields";
import {
  chunkingFieldErrors,
  defaultCollection,
  splitTags,
  type IngestMeta,
  type KnowledgeCollection,
  type TextResponse,
} from "./knowledgeIngest";

/* ------------------------------------------------------------------ *
 * Paste Text modal.  Same chunking knobs as a file upload so ad-hoc
 * notes index with the same strategy the rest of the corpus uses.
 * ------------------------------------------------------------------ */

const CONTENT_TYPES = ["policy", "documentation", "procedure", "report", "article", "guide", "notes", "other"];

interface KnowledgeTextModalProps {
  open: boolean;
  roles: string[];
  meta: IngestMeta;
  collections: KnowledgeCollection[];
  onClose: () => void;
  onAdded: (doc: KnownDoc) => void;
}

export function KnowledgeTextModal({ open, roles, meta, collections, onClose, onAdded }: KnowledgeTextModalProps) {
  const [name, setName] = useState("");
  const [contentType, setContentType] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());
  const [collection, setCollection] = useState("");
  const [method, setMethod] = useState(meta.defaults.chunking_method);
  const [size, setSize] = useState(String(meta.defaults.chunk_size));
  const [overlap, setOverlap] = useState(String(meta.defaults.chunk_overlap));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fieldErrors = chunkingFieldErrors(size, overlap);
  const chunkingInvalid = fieldErrors.size !== null || fieldErrors.overlap !== null;

  useEffect(() => {
    if (!open) return;
    setName("");
    setContentType("");
    setContent("");
    setTags("");
    setSelectedRoles(new Set());
    setCollection(defaultCollection(collections));
    setMethod(meta.defaults.chunking_method);
    setSize(String(meta.defaults.chunk_size));
    setOverlap(String(meta.defaults.chunk_overlap));
    setSaving(false);
    setError(null);
  }, [open, meta, collections]);

  const toggleRole = (role: string) =>
    setSelectedRoles((prev) => {
      const next = new Set(prev);
      if (next.has(role)) next.delete(role);
      else next.add(role);
      return next;
    });

  const submit = async () => {
    if (!name.trim() || !contentType || !content.trim()) {
      setError("Name, content type, and content are required.");
      return;
    }
    if (chunkingInvalid) {
      setError("Fix the chunk size and overlap values before adding content.");
      return;
    }
    if (!collection) {
      setError("Select a collection to index into.");
      return;
    }
    setSaving(true);
    setError(null);
    const tagList = splitTags(tags);
    try {
      const res = await adminPostJson<TextResponse>("/api/knowledge/text", {
        name: name.trim(),
        content_type: contentType,
        content,
        tags: tagList,
        allowed_roles: Array.from(selectedRoles),
        chunking_method: method,
        chunk_size: size,
        chunk_overlap: overlap,
        collection_name: collection,
      });
      if (!res.success) {
        setError(res.error || res.message || "Failed to add content.");
        return;
      }
      onAdded({
        id: res.documentId,
        access_id: res.accessId,
        can_manage: true,
        name: res.name || name.trim(),
        content_type: contentType,
        status: "processing",
        created_at: new Date().toISOString(),
        tags: res.tags ?? tagList,
        allowed_roles: res.allowed_roles ?? Array.from(selectedRoles),
        chunking_method: res.chunking_method,
        chunk_size: res.chunk_size,
        chunk_overlap: res.chunk_overlap,
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
      title="Paste Text"
      open={open}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            disabled={saving || chunkingInvalid || !collection}
            onClick={() => void submit()}
            data-testid="knowledge-text-submit"
          >
            {saving ? "Adding…" : "Add Content"}
          </button>
        </>
      }
    >
      {error && <div className="aa-error" role="alert">{error}</div>}

      <AdminField label="Name *" hint="A short name for this content.">
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Onboarding Policy" autoFocus />
      </AdminField>

      <AdminField label="Content Type *" hint="Required. Used for filtering and display.">
        <select value={contentType} onChange={(e) => setContentType(e.target.value)}>
          <option value="">Select content type</option>
          {CONTENT_TYPES.map((ct) => (
            <option key={ct} value={ct}>{ct}</option>
          ))}
        </select>
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

      <ChunkingFields
        meta={meta}
        method={method}
        size={size}
        overlap={overlap}
        onMethod={setMethod}
        onSize={setSize}
        onOverlap={setOverlap}
      />
      <AdminField label="Tags" hint="Comma-separated (e.g. policy, notes)">
        <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="policy, notes" />
      </AdminField>

      <AdminField label="Allowed Roles" hint="Leave empty for owner and administrators only.">
        <KnowledgeRoleGrid roles={roles} selected={selectedRoles} onToggle={toggleRole} />
      </AdminField>

      <AdminField label="Content Text *">
        <CodeEditor
          value={content}
          onChange={setContent}
          language="markdown"
          filename="knowledge.md"
          label="Knowledge content"
          height={300}
          testId="knowledge-content-editor"
        />
      </AdminField>
    </AdminModal>
  );
}
