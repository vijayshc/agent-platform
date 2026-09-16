import type { HeaderRow } from "./llmConnection";

interface Props {
  rows: HeaderRow[];
  onChange: (rows: HeaderRow[]) => void;
}

/**
 * Free-form HTTP header editor: any number of name/value pairs, added and
 * removed by the operator. Values already stored are returned masked by the API
 * and are restored server-side when saved unchanged.
 */
export function HttpHeadersEditor({ rows, onChange }: Props) {
  function update(index: number, patch: Partial<HeaderRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function remove(index: number) {
    onChange(rows.filter((_, i) => i !== index));
  }

  function add() {
    onChange([...rows, { name: "", value: "" }]);
  }

  return (
    <div className="aa-header-rows" data-testid="llm-http-headers">
      {rows.length === 0 && <div className="aa-muted">No custom headers. Requests use the provider defaults.</div>}
      {rows.map((row, index) => (
        <div className="aa-header-row" key={index}>
          <input
            type="text"
            id={`llm-header-name-${index}`}
            name={`llm-header-name-${index}`}
            value={row.name}
            placeholder="Header name (e.g. X-Tenant)"
            aria-label={`Header ${index + 1} name`}
            onChange={(e) => update(index, { name: e.target.value })}
          />
          <input
            type="text"
            id={`llm-header-value-${index}`}
            name={`llm-header-value-${index}`}
            value={row.value}
            placeholder="Value"
            aria-label={`Header ${index + 1} value`}
            onChange={(e) => update(index, { value: e.target.value })}
          />
          <button
            type="button"
            className="aa-btn aa-btn-ghost aa-btn-icon"
            aria-label={`Remove header ${index + 1}`}
            title="Remove header"
            onClick={() => remove(index)}
          >
            ×
          </button>
        </div>
      ))}
      <div>
        <button type="button" className="aa-btn aa-btn-ghost aa-btn-xs" onClick={add}>
          + Add header
        </button>
      </div>
    </div>
  );
}
