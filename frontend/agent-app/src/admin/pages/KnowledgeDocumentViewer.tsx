import { useEffect, useState } from "react";
import { AdminModal, adminGet } from "../adminShared";
import { MarkdownRenderer } from "../../chat/MarkdownRenderer";
import { describeChunking, type KnownDoc } from "./KnowledgeDocumentsTable";

/* ------------------------------------------------------------------ *
 * Read-only viewer for one knowledge document: the preserved text
 * (rendered for prose, verbatim for tabular) plus the indexing
 * properties captured at upload time.  Replaces the old raw-JSON
 * "Markdown" link that navigated away from the page.
 * ------------------------------------------------------------------ */

interface DocDetails {
  original_filename?: string;
  content_type?: string;
  status?: string;
  chunking_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  data_columns?: string[];
  metadata_columns?: string[];
  chunk_count?: number;
  tags?: string[];
  created_at?: string;
  processed_at?: string;
  collection_name?: string;
}

interface KnowledgeDocumentViewerProps {
  doc: KnownDoc | null;
  onClose: () => void;
}

function formatDate(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export function KnowledgeDocumentViewer({ doc, onClose }: KnowledgeDocumentViewerProps) {
  const [content, setContent] = useState("");
  const [details, setDetails] = useState<DocDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setContent("");
    setDetails(null);
    Promise.all([
      adminGet<{ success: boolean; markdown_content?: string }>(`/api/knowledge/view/markdown/${doc.id}`),
      adminGet<{ success: boolean; document?: DocDetails }>(`/api/knowledge/info/${doc.id}`),
    ])
      .then(([markdown, info]) => {
        if (cancelled) return;
        setContent(markdown.markdown_content || "");
        setDetails(info.document || null);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [doc]);

  const dataColumns = details?.data_columns ?? [];
  const metadataColumns = details?.metadata_columns ?? [];
  const isTabular = dataColumns.length > 0;
  const chunking = doc ? describeChunking(doc).label : "—";

  return (
    <AdminModal
      title={doc ? `View: ${doc.name}` : "View document"}
      open={doc !== null}
      onClose={onClose}
      wide
      footer={
        <>
          {doc && (
            <a className="aa-btn aa-btn-ghost" href={`/api/knowledge/view/original/${doc.id}`}>Download original</a>
          )}
          <button type="button" className="aa-btn aa-btn-primary" onClick={onClose}>Close</button>
        </>
      }
    >
      {error && <div className="aa-error" role="alert">{error}</div>}
      {loading && <div className="aa-muted">Loading document…</div>}

      {!loading && details && (
        <div className="aa-doc-viewer-props">
          <div><span className="aa-muted">Status</span><strong>{details.status || "—"}</strong></div>
          <div><span className="aa-muted">Chunks</span><strong>{details.chunk_count ?? "—"}</strong></div>
          <div><span className="aa-muted">Collection</span><strong>{details.collection_name || doc?.collection_name || "—"}</strong></div>
          <div><span className="aa-muted">Indexing</span><strong>{chunking}</strong></div>
          <div><span className="aa-muted">Added</span><strong>{formatDate(details.created_at)}</strong></div>
        </div>
      )}

      {!loading && isTabular && (
        <div className="aa-doc-viewer-cols">
          <div>
            <span className="aa-muted">Embedded columns</span>
            <div className="aa-chips">
              {dataColumns.map((c) => <span className="aa-chip" key={c}>{c}</span>)}
            </div>
          </div>
          <div>
            <span className="aa-muted">Metadata columns (not embedded)</span>
            <div className="aa-chips">
              {metadataColumns.length
                ? metadataColumns.map((c) => <span className="aa-chip" key={c}>{c}</span>)
                : <span className="aa-muted">—</span>}
            </div>
          </div>
        </div>
      )}

      {!loading && content && (
        isTabular ? (
          <pre className="aa-doc-viewer-raw">{content}</pre>
        ) : (
          <div className="aa-doc-viewer-markdown">
            <MarkdownRenderer content={content} />
          </div>
        )
      )}

      {!loading && !error && !content && (
        <div className="aa-muted">No preserved text is available for this document yet.</div>
      )}
    </AdminModal>
  );
}
