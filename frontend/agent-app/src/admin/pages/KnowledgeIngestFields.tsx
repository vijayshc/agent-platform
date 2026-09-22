import { Check } from "lucide-react";
import { AdminField } from "../adminShared";
import { chunkingFieldErrors, type ColumnRole, type IngestMeta } from "./knowledgeIngest";

/* ------------------------------------------------------------------ *
 * Reusable upload-property controls: chunking knobs and the
 * data/metadata/skip mapping for CSV/Excel columns.
 * ------------------------------------------------------------------ */

interface ChunkingFieldsProps {
  meta: IngestMeta;
  method: string;
  size: string;
  overlap: string;
  onMethod: (value: string) => void;
  onSize: (value: string) => void;
  onOverlap: (value: string) => void;
}

export function ChunkingFields({
  meta,
  method,
  size,
  overlap,
  onMethod,
  onSize,
  onOverlap,
}: ChunkingFieldsProps) {
  const selected = meta.methods.find((m) => m.id === method);
  const errors = chunkingFieldErrors(size, overlap);
  return (
    <>
      <AdminField label="Chunking Method" hint={selected?.description}>
        <select value={method} onChange={(e) => onMethod(e.target.value)} data-testid="knowledge-chunk-method">
          {meta.methods.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      </AdminField>
      <div className="aa-ingest-grid">
        <AdminField
          label="Chunk Size (characters)"
          hint={errors.size ? undefined : "100–8000. Larger keeps more context; smaller is more precise."}
        >
          <input
            type="number"
            min={100}
            max={8000}
            step={50}
            value={size}
            aria-invalid={errors.size !== null}
            onChange={(e) => onSize(e.target.value)}
            data-testid="knowledge-chunk-size"
          />
          {errors.size && <span className="aa-field-error" role="alert">{errors.size}</span>}
        </AdminField>
        <AdminField
          label="Chunk Overlap"
          hint={errors.overlap ? undefined : "Characters shared between neighbouring chunks."}
        >
          <input
            type="number"
            min={0}
            max={2000}
            step={10}
            value={overlap}
            aria-invalid={errors.overlap !== null}
            onChange={(e) => onOverlap(e.target.value)}
            data-testid="knowledge-chunk-overlap"
          />
          {errors.overlap && <span className="aa-field-error" role="alert">{errors.overlap}</span>}
        </AdminField>
      </div>
    </>
  );
}

const ROLE_OPTIONS: { id: ColumnRole; label: string; hint: string }[] = [
  { id: "data", label: "Embed", hint: "Included in the embedded text and searchable" },
  { id: "metadata", label: "Metadata", hint: "Preserved and shown with results, but not embedded" },
  { id: "skip", label: "Skip", hint: "Excluded from this upload entirely" },
];

interface ColumnRolePickerProps {
  columns: string[];
  roles: Record<string, ColumnRole>;
  onSet: (column: string, role: ColumnRole) => void;
  onSetAll: (role: ColumnRole) => void;
}

export function ColumnRolePicker({ columns, roles, onSet, onSetAll }: ColumnRolePickerProps) {
  const counts = { data: 0, metadata: 0, skip: 0 } as Record<ColumnRole, number>;
  columns.forEach((c) => { counts[roles[c] ?? "skip"] += 1; });

  return (
    <div className="aa-colmap">
      <div className="aa-colmap-head">
        <span className="aa-colmap-summary">
          <strong>{counts.data}</strong> embedded · <strong>{counts.metadata}</strong> metadata ·{" "}
          <strong>{counts.skip}</strong> skipped
        </span>
        <span className="aa-colmap-quick">
          <button type="button" className="aa-btn aa-btn-ghost" onClick={() => onSetAll("data")}>All embedded</button>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={() => onSetAll("skip")}>Clear</button>
        </span>
      </div>
      <div className="aa-colmap-list" role="group" aria-label="Column roles">
        {columns.map((column) => {
          const role = roles[column] ?? "skip";
          return (
            <div className="aa-colmap-row" key={column}>
              <span className="aa-colmap-name" title={column}>{column}</span>
              <span className="aa-colmap-roles">
                {ROLE_OPTIONS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    title={option.hint}
                    aria-pressed={role === option.id}
                    className={`aa-colmap-btn ${option.id}${role === option.id ? " active" : ""}`}
                    onClick={() => onSet(column, option.id)}
                    data-testid={`knowledge-col-${column}-${option.id}`}
                  >
                    {role === option.id && <Check size={11} />}
                    {option.label}
                  </button>
                ))}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface ColumnPreviewProps {
  rows: Record<string, string>[];
  columns: string[];
  roles: Record<string, ColumnRole>;
}

export function ColumnPreview({ rows, columns, roles }: ColumnPreviewProps) {
  if (!rows.length) return null;
  return (
    <div className="aa-colmap-preview-wrap">
      <table className="aa-colmap-preview">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={roles[column] === "data" ? "is-data" : roles[column] === "metadata" ? "is-meta" : ""}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} title={row[column]}>{row[column] || <span className="aa-muted">—</span>}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
