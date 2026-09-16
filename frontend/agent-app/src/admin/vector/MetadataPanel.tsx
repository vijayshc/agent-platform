/**
 * Metadata panel: the fields available in the collection on the left, and the
 * native ChromaDB `where` clause applied to searches on the right.
 */

import { Check, Eraser } from "lucide-react";

import type { FieldInfo } from "./vectorTypes";

interface Props {
  fields: FieldInfo[];
  fieldsSampled: number;
  fieldsLoading: boolean;
  fieldsError: string | null;
  filterText: string;
  onFilterTextChange: (value: string) => void;
  onApply: () => void;
  onClear: () => void;
  appliedFilterText: string;
  error: string | null;
}

export function MetadataPanel({
  fields,
  fieldsSampled,
  fieldsLoading,
  fieldsError,
  filterText,
  onFilterTextChange,
  onApply,
  onClear,
  appliedFilterText,
  error,
}: Props) {
  return (
    <div className="vb-meta-panel" data-testid="vector-metadata-panel">
      <section className="vb-meta-col vb-meta-fields">
        <h4 className="vb-panel-title">Filter columns</h4>
        {fieldsLoading && <div className="vb-muted">Reading metadata…</div>}
        {!fieldsLoading && fieldsError && <div className="vb-filter-error">{fieldsError}</div>}
        {!fieldsLoading && !fieldsError && fields.length === 0 && (
          <div className="vb-muted">This collection has no metadata fields.</div>
        )}
        {!fieldsLoading && fields.length > 0 && (
          <>
            <ul className="vb-field-list">
              {fields.map((field) => (
                <li key={field.name} title={field.values.length > 0 ? `${field.name}: ${field.values.join(", ")}` : field.name}>
                  <span className="vb-field-name">{field.name}</span>
                  <span className="vb-field-example">
                    {field.values.length > 0 ? field.values.join(", ") : "no sample values"}
                  </span>
                </li>
              ))}
            </ul>
            {fieldsSampled > 0 && (
              <div className="vb-field-note">Discovered from {fieldsSampled} sampled records.</div>
            )}
          </>
        )}
      </section>

      <section className="vb-meta-col vb-meta-filter">
        <h4 className="vb-panel-title">ChromaDB where filter</h4>
        <textarea
          className="vb-where-input"
          value={filterText}
          spellCheck={false}
          placeholder={'{"table": "products"}'}
          aria-label="ChromaDB where filter"
          data-testid="vector-where-input"
          onChange={(event) => onFilterTextChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              onApply();
            }
          }}
        />
        <div className="vb-filter-actions">
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={onApply}
            data-testid="vector-where-apply"
          >
            <Check size={14} />
            Apply filter
          </button>
          {(appliedFilterText || filterText) && (
            <button type="button" className="aa-btn aa-btn-ghost" onClick={onClear} data-testid="vector-where-clear">
              <Eraser size={14} />
              Clear
            </button>
          )}
          <span className={`vb-filter-state${appliedFilterText ? " active" : ""}`}>
            {appliedFilterText ? "Filter applied" : "No filter applied"}
          </span>
        </div>
        {error && <div className="vb-filter-error">{error}</div>}
        <p className="vb-filter-hint">
          ChromaDB filter operators: <code>{'"field": "value"'}</code>, <code>$eq</code> <code>$ne</code>{" "}
          <code>$gt</code> <code>$gte</code> <code>$lt</code> <code>$lte</code> <code>$in</code>{" "}
          <code>$nin</code>, combined with <code>$and</code> / <code>$or</code>. With a filter and no search text,
          the records matching the filter are listed.
        </p>
      </section>
    </div>
  );
}
