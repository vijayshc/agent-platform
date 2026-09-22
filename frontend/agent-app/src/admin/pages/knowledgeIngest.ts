import { adminGet, adminPostForm } from "../adminShared";

/* ------------------------------------------------------------------ *
 * Upload-property model shared by the Knowledge upload/text modals.
 * Mirrors GET /api/knowledge/ingest-options and /api/knowledge/inspect.
 * ------------------------------------------------------------------ */

export interface ChunkingMethodInfo {
  id: string;
  label: string;
  description: string;
}

export interface IngestMeta {
  methods: ChunkingMethodInfo[];
  defaults: { chunking_method: string; chunk_size: number; chunk_overlap: number };
  tabular_types: string[];
}

/** Used only if the options endpoint is unreachable, so uploads stay usable. */
export const INGEST_META_FALLBACK: IngestMeta = {
  methods: [
    { id: "recursive", label: "Recursive (recommended)", description: "Splits on paragraph, line, sentence and word boundaries." },
    { id: "paragraph", label: "Paragraph", description: "Keeps paragraphs intact." },
    { id: "sentence", label: "Sentence", description: "Groups whole sentences." },
    { id: "markdown", label: "Markdown headings", description: "Splits at headings, repeating them in each part." },
    { id: "fixed", label: "Fixed size", description: "Plain fixed-width windows." },
  ],
  defaults: { chunking_method: "recursive", chunk_size: 1000, chunk_overlap: 200 },
  tabular_types: ["csv", "tsv", "xlsx", "xls"],
};

export interface InspectResult {
  success: boolean;
  kind: "tabular" | "document";
  content_type?: string;
  columns?: string[];
  sample_rows?: Record<string, string>[];
  row_count?: number;
  error?: string;
}

export type ColumnRole = "data" | "metadata" | "skip";

export interface UploadResponse {
  success: boolean;
  message?: string;
  error?: string;
  documentId: string;
  accessId?: number;
  originalFilename?: string;
  tags?: string[];
  allowed_roles?: string[];
  chunking_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  metadata_columns?: string[];
  data_columns?: string[];
  collection_name?: string;
}

export interface TextResponse {
  success: boolean;
  message?: string;
  error?: string;
  documentId: string;
  accessId?: number;
  name?: string;
  tags?: string[];
  allowed_roles?: string[];
  chunking_method?: string;
  chunk_size?: number;
  chunk_overlap?: number;
  collection_name?: string;
}

/** A vector collection the caller may index into. */
export interface KnowledgeCollection {
  name: string;
  access_id?: number;
  roles?: string[];
}

export function splitTags(value: string): string[] {
  return value.split(",").map((t) => t.trim()).filter(Boolean);
}

export function extensionOf(filename: string): string {
  const parts = filename.split(".");
  return parts.length > 1 ? (parts.pop() as string).toLowerCase() : "";
}

export function isTabularFile(meta: IngestMeta, filename: string): boolean {
  return meta.tabular_types.includes(extensionOf(filename));
}

export function defaultColumnRoles(columns: string[]): Record<string, ColumnRole> {
  return Object.fromEntries(columns.map((c) => [c, "data" as ColumnRole]));
}

export function columnsWithRole(
  roles: Record<string, ColumnRole>,
  role: ColumnRole,
): string[] {
  return Object.entries(roles).filter(([, r]) => r === role).map(([c]) => c);
}

export interface ChunkingFieldErrors {
  size: string | null;
  overlap: string | null;
}

/** Client-side mirror of the backend bounds, so errors sit next to the field. */
export function chunkingFieldErrors(size: string, overlap: string): ChunkingFieldErrors {
  const sizeNum = Number(size);
  const sizeError =
    size.trim() === "" || !Number.isInteger(sizeNum) || sizeNum < 100 || sizeNum > 8000
      ? "Use a whole number between 100 and 8000."
      : null;
  const overlapNum = Number(overlap);
  const overlapError =
    overlap.trim() === "" || !Number.isInteger(overlapNum) || overlapNum < 0 || overlapNum > 2000
      ? "Use a whole number between 0 and 2000."
      : null;
  return { size: sizeError, overlap: overlapError };
}

/** Load the method list + defaults the backend will actually accept. */
export async function loadIngestMeta(): Promise<IngestMeta> {
  const res = await adminGet<{ success: boolean } & IngestMeta>("/api/knowledge/ingest-options");
  return { methods: res.methods, defaults: res.defaults, tabular_types: res.tabular_types };
}

/** Parse a CSV/Excel file's columns and preview without indexing it. */
export async function inspectFile(file: File): Promise<InspectResult> {
  const form = new FormData();
  form.append("document", file);
  return adminPostForm<InspectResult>("/api/knowledge/inspect", form);
}

/** Collections the signed-in user may index into (role-grant filtered). */
export async function loadCollections(): Promise<KnowledgeCollection[]> {
  const res = await adminGet<{ success: boolean; collections?: KnowledgeCollection[] }>(
    "/api/knowledge/collections",
  );
  return res.collections || [];
}

/** Preselect the shared knowledge collection when available, else the first. */
export function defaultCollection(collections: KnowledgeCollection[]): string {
  if (!collections.length) return "";
  const shared = collections.find((c) => c.name === "knowledge_chunks");
  return (shared ?? collections[0]).name;
}
