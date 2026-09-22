import { AdminStatusPill, AdminTags } from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";
import { ActionsMenu, type ActionItem } from "../../shared/ActionsMenu";

/** A known document tracked in the UI. Backend `list_documents` uses `filename`; local adds use the display name. */
export interface KnownDoc {
  id: string;
  /** Stable integer identity used by the generic access API/UI. */
  access_id?: number;
  /** Uploader id; `null` means an owner-less legacy (public) document. */
  owner_id?: number | null;
  /** Server-computed: owner or administrator. A role grant is view/use only. */
  can_manage?: boolean;
  name: string;
  content_type: string;
  status: string;
  created_at?: string;
  chunk_count?: number;
  tags: string[];
  allowed_roles: string[];
  /** Ingestion properties captured at upload time. */
  chunking_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  /** For CSV/Excel: columns stored as metadata (not embedded). */
  metadata_columns?: string[];
  /** For CSV/Excel: columns embedded as searchable text. */
  data_columns?: string[];
  /** Vector collection the document was indexed into. */
  collection_name?: string;
}

const CHUNK_METHOD_LABELS: Record<string, string> = {
  recursive: "Recursive",
  paragraph: "Paragraph",
  sentence: "Sentence",
  markdown: "Markdown",
  fixed: "Fixed",
};

/** One-line description of how the document was indexed, with a full tooltip. */
export function describeChunking(doc: KnownDoc): { label: string; title: string } {
  const data = doc.data_columns ?? [];
  const meta = doc.metadata_columns ?? [];
  const collection = doc.collection_name ? `\nCollection: ${doc.collection_name}` : "";
  if (data.length > 0) {
    return {
      label: `Row-based · ${data.length} embedded`,
      title: `Each row is one chunk.\nEmbedded: ${data.join(", ")}${meta.length ? `\nMetadata (not embedded): ${meta.join(", ")}` : ""}${collection}`,
    };
  }
  if (doc.chunking_method) {
    const label = CHUNK_METHOD_LABELS[doc.chunking_method] ?? doc.chunking_method;
    const size = typeof doc.chunk_size === "number" ? ` · ${doc.chunk_size}` : "";
    return { label: `${label}${size}`, title: `Chunking method: ${label}${size ? `\nChunk size: ${doc.chunk_size}` : ""}${collection}` };
  }
  return { label: "—", title: `No chunking details recorded.${collection}` };
}

function formatDate(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

/**
 * The knowledge document list. Every row exposes a single "Actions" dropdown
 * (the same control Agent Studio and LLM Manager use). Its items follow the
 * caller's rights: anyone who can see a document may view it, while
 * Access/Delete are offered only when the server says `can_manage`.
 *
 * The indexing properties (chunking method/size, row-based column mapping) are
 * shown as a second line inside the Document cell rather than as their own
 * column, keeping the table from overflowing.
 */
export function KnowledgeDocumentsTable({
  docs,
  onView,
  onAccess,
  onDelete,
}: {
  docs: KnownDoc[];
  onView: (doc: KnownDoc) => void;
  onAccess: (doc: KnownDoc) => void;
  onDelete: (doc: KnownDoc) => void;
}) {
  const columns: Column<KnownDoc>[] = [
    { key: "name", header: "Document", className: "aa-doc-col", sortValue: (d) => d.name, render: (d) => {
      const { label, title } = describeChunking(d);
      return (
        <span className="aa-doc-cell">
          <span className="aa-doc-name">
            <span style={{ fontWeight: 600 }}>{d.name}</span>
            {typeof d.chunk_count === "number" && (
              <span className="aa-muted aa-doc-chunk-count" style={{ marginLeft: 6 }}>({d.chunk_count} chunks)</span>
            )}
          </span>
          <span className="aa-muted aa-doc-chunking" title={title}>{label}</span>
        </span>
      );
    } },
    { key: "content_type", header: "Type", width: "70px", render: (d) => d.content_type || <span className="aa-muted">—</span>, sortValue: (d) => d.content_type },
    { key: "status", header: "Status", width: "120px", render: (d) => <AdminStatusPill status={d.status} />, sortValue: (d) => d.status },
    { key: "tags", header: "Tags", width: "150px", render: (d) => <AdminTags items={d.tags} />, sortValue: (d) => d.tags.join(",") },
    { key: "created_at", header: "Added", width: "170px", className: "aa-table-date", render: (d) => (d.created_at ? formatDate(d.created_at) : <span className="aa-muted">—</span>), sortValue: (d) => (d.created_at ? new Date(d.created_at).getTime() : 0) },
    { key: "actions", header: "Actions", className: "aa-table-actions", width: "120px", render: (d) => {
      // Viewing/using the document is available to everyone who can see it;
      // Access/Delete are owner-or-admin only (`can_manage`).
      const items: ActionItem[] = [
        {
          key: "preview",
          label: "View content",
          testId: `knowledge-preview-${d.id}`,
          onSelect: () => onView(d),
        },
        {
          key: "original",
          label: "Download original",
          testId: `knowledge-original-${d.id}`,
          href: `/api/knowledge/view/original/${d.id}`,
        },
      ];
      if (d.can_manage === true) {
        items.push(
          {
            key: "access",
            label: "Access",
            testId: `knowledge-access-${d.id}`,
            disabled: typeof d.access_id !== "number",
            title:
              typeof d.access_id === "number"
                ? `Manage who can access ${d.name}`
                : "Access controls are not available for this document",
            onSelect: () => onAccess(d),
          },
          {
            key: "delete",
            label: "Delete",
            testId: `knowledge-delete-${d.id}`,
            danger: true,
            onSelect: () => onDelete(d),
          },
        );
      }
      return <ActionsMenu label="Actions" testId={`knowledge-actions-${d.id}`} items={items} />;
    } },
  ];

  return (
    <AdminDataTable<KnownDoc>
      rows={docs}
      rowKey={(d) => d.id}
      columns={columns}
      tableClassName="aa-knowledge-table"
      searchText={(d) => `${d.name} ${d.content_type} ${d.tags.join(" ")} ${describeChunking(d).label}`}
      searchPlaceholder="Search documents…"
      emptyMessage="No documents yet. Upload a file or paste text to get started."
    />
  );
}
