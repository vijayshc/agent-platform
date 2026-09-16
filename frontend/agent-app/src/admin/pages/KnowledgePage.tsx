import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  adminDelete,
  AdminLoading,
  AdminError,
  PageHeader,
} from "../adminShared";
import { KnowledgeDocumentsTable, type KnownDoc } from "./KnowledgeDocumentsTable";
import { KnowledgeAccessModal } from "./KnowledgeAccessModal";
import { KnowledgeDeleteModal } from "./KnowledgeDeleteModal";
import { KnowledgeDocumentViewer } from "./KnowledgeDocumentViewer";
import { KnowledgeUploadModal } from "./KnowledgeUploadModal";
import { KnowledgeTextModal } from "./KnowledgeTextModal";
import { INGEST_META_FALLBACK, loadCollections, loadIngestMeta, type IngestMeta, type KnowledgeCollection } from "./knowledgeIngest";

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

interface RolesResp {
  success: boolean;
  roles: string[];
}

interface TagsResp {
  success: boolean;
  tags: string[];
}

interface StatusResp {
  status: string;
  message?: string;
  processed_at?: string;
}

type ModalKind = "upload" | "text" | null;
type ListMode = "checking" | "available" | "unavailable";

/* ------------------------------------------------------------------ *
 * Component
 * ------------------------------------------------------------------ */

export function KnowledgePage() {
  const [roles, setRoles] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [ingest, setIngest] = useState<IngestMeta>(INGEST_META_FALLBACK);
  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [metaLoading, setMetaLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Documents we know about (server list if available, otherwise locally tracked).
  const [docs, setDocs] = useState<KnownDoc[]>([]);
  const [listMode, setListMode] = useState<ListMode>("checking");

  // Modal state
  const [modal, setModal] = useState<ModalKind>(null);

  // Delete target
  const [deleteTarget, setDeleteTarget] = useState<KnownDoc | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Per-document role-grant management (generic access API)
  const [accessTarget, setAccessTarget] = useState<KnownDoc | null>(null);

  // Per-document read-only content view (preserved text + indexing properties)
  const [viewTarget, setViewTarget] = useState<KnownDoc | null>(null);

  /* ---------------- Meta (tags / roles / ingest options) ---------------- */

  const loadMeta = useCallback(async () => {
    setMetaLoading(true);
    setError(null);
    try {
      const [rolesRes, tagsRes, ingestRes, collectionsRes] = await Promise.all([
        adminGet<RolesResp>("/api/knowledge/roles"),
        adminGet<TagsResp>("/api/knowledge/tags"),
        // Fall back to the built-in method list so upload stays usable.
        loadIngestMeta().catch(() => INGEST_META_FALLBACK),
        loadCollections().catch(() => [] as KnowledgeCollection[]),
      ]);
      setRoles(rolesRes.roles || []);
      setTags(tagsRes.tags || []);
      setIngest(ingestRes);
      setCollections(collectionsRes);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMetaLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  /* ---------------- Discover a list endpoint (optional) ---------------- */

  const loadDocuments = useCallback(async () => {
    try {
      const data = await adminGet<{ success: boolean; documents?: Record<string, unknown>[] }>(
        "/api/knowledge/documents",
      );
      if (data.success && Array.isArray(data.documents)) {
        setDocs(
          data.documents.map((d) => ({
            id: String(d.id ?? ""),
            access_id: typeof d.access_id === "number" ? d.access_id : undefined,
            owner_id:
              d.owner_id === null || typeof d.owner_id === "number"
                ? (d.owner_id as number | null)
                : undefined,
            can_manage: d.can_manage === true,
            name: String(d.name ?? d.original_filename ?? d.filename ?? `#${d.id}`),
            content_type: String(d.content_type ?? "file"),
            status: String(d.status ?? "unknown"),
            created_at: typeof d.created_at === "string" ? d.created_at : undefined,
            chunk_count: typeof d.chunk_count === "number" ? d.chunk_count : undefined,
            tags: Array.isArray(d.tags) ? (d.tags as string[]) : [],
            allowed_roles: Array.isArray(d.allowed_roles) ? (d.allowed_roles as string[]) : [],
            chunking_method: typeof d.chunking_method === "string" ? d.chunking_method : undefined,
            chunk_size: typeof d.chunk_size === "number" ? d.chunk_size : undefined,
            metadata_columns: Array.isArray(d.metadata_columns) ? (d.metadata_columns as string[]) : [],
            data_columns: Array.isArray(d.data_columns) ? (d.data_columns as string[]) : [],
            collection_name: typeof d.collection_name === "string" ? d.collection_name : undefined,
          })),
        );
        setListMode("available");
      } else {
        setListMode("unavailable");
      }
    } catch {
      setListMode("unavailable");
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  /* ---------------- Poll processing status of "processing" docs ---------------- */

  useEffect(() => {
    const processingIds = docs.filter((d) => d.status === "processing").map((d) => d.id);
    if (processingIds.length === 0) return;

    const timer = setInterval(() => {
      Promise.all(
        processingIds.map((id) =>
          adminGet<StatusResp>(`/api/knowledge/status/${id}`)
            .then((s) => ({ id, s }))
            .catch(() => ({ id, s: null })),
        ),
      ).then((results) => {
        const finished = results.filter(
          (r) => r.s && (r.s.status === "completed" || r.s.status === "error"),
        );
        setDocs((prev) => {
          let changed = false;
          const next = prev.map((d) => {
            const r = results.find((x) => x.id === d.id);
            if (r && r.s && r.s.status && r.s.status !== d.status) {
              changed = true;
              return { ...d, status: r.s.status };
            }
            return d;
          });
          return changed ? next : prev;
        });
        // The status endpoint does not carry chunk counts, so re-read the
        // server list once a document settles; otherwise the "(N chunks)"
        // column stays empty until the user reloads the page.
        if (finished.length > 0) void loadDocuments();
      });
    }, 4000);

    return () => clearInterval(timer);
  }, [docs, loadDocuments]);

  /* ---------------- Shared helpers ---------------- */

  const closeModal = () => setModal(null);

  const addDocument = (doc: KnownDoc) => setDocs((prev) => [doc, ...prev]);

  /* ---------------- Delete ---------------- */

  const openDelete = (doc: KnownDoc) => {
    setDeleteTarget(doc);
    setDeleteError(null);
  };

  const closeDelete = () => {
    setDeleting(false);
    setDeleteError(null);
    setDeleteTarget(null);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await adminDelete<{ success: boolean; message?: string }>(
        `/api/knowledge/delete/${deleteTarget.id}`,
      );
      if (!res.success) {
        setDeleteError(res.message || "Failed to delete document.");
        return;
      }
      setDocs((prev) => prev.filter((d) => d.id !== deleteTarget.id));
      closeDelete();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  };

  /* ---------------- Render ---------------- */

  if (metaLoading) return <AdminLoading label="Loading knowledge base…" />;
  if (error) return <AdminError message={error} />;

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="Knowledge Management"
        actions={
          <>
            <button type="button" className="aa-btn aa-btn-primary" onClick={() => setModal("upload")}>Upload File</button>
            <button type="button" className="aa-btn" onClick={() => setModal("text")}>Paste Text</button>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={loadMeta}>Refresh</button>
          </>
        }
      />

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <h2>Knowledge Documents</h2>
          <span className="aa-muted">{docs.length} document{docs.length === 1 ? "" : "s"}</span>
        </div>

        {listMode === "unavailable" && (
          <div className="aa-error" style={{ marginBottom: 12 }}>
            The document list endpoint is not available. Documents you upload or paste in this session are shown
            below.
          </div>
        )}

        <KnowledgeDocumentsTable docs={docs} onView={setViewTarget} onAccess={setAccessTarget} onDelete={openDelete} />
      </div>

      <KnowledgeUploadModal
        open={modal === "upload"}
        roles={roles}
        meta={ingest}
        collections={collections}
        onClose={closeModal}
        onUploaded={addDocument}
      />

      <KnowledgeTextModal
        open={modal === "text"}
        roles={roles}
        meta={ingest}
        collections={collections}
        onClose={closeModal}
        onAdded={addDocument}
      />

      {/* Read-only content + indexing-properties viewer */}
      <KnowledgeDocumentViewer doc={viewTarget} onClose={() => setViewTarget(null)} />

      {/* Delete confirm modal */}
      <KnowledgeDeleteModal
        name={deleteTarget ? deleteTarget.name : null}
        deleting={deleting}
        error={deleteError}
        onClose={closeDelete}
        onConfirm={confirmDelete}
      />

      {/* Per-document role grants (generic access API) */}
      <KnowledgeAccessModal
        open={accessTarget !== null}
        accessId={accessTarget?.access_id ?? null}
        documentLabel={accessTarget?.name ?? ""}
        onClose={() => setAccessTarget(null)}
        onSaved={(roleNames) => {
          const id = accessTarget?.id;
          if (id) setDocs((prev) => prev.map((d) => (d.id === id ? { ...d, allowed_roles: roleNames } : d)));
        }}
      />
    </div>
  );
}
