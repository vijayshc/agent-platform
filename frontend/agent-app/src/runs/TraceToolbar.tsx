/** Filter / view toolbar above the trace waterfall. */
import type { TraceSpan } from "../types";
import { traceKindCounts, type TraceSortMode } from "./traceUtils";

interface TraceToolbarProps {
  spans: TraceSpan[];
  kind: string;
  onKindChange: (kind: string) => void;
  errorOnly: boolean;
  onErrorOnlyChange: (value: boolean) => void;
  query: string;
  onQueryChange: (value: string) => void;
  sortMode: TraceSortMode;
  onSortModeChange: (mode: TraceSortMode) => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
  matchCount: number;
}

const CANONICAL = ["LLM", "TOOL", "CHAIN", "AGENT"];
const OTHER_ORDER = ["RETRIEVER", "EMBEDDING", "RERANKER", "GUARDRAIL", "EVALUATOR", "UNKNOWN"];

function kindLabel(kind: string): string {
  if (kind === "LLM") return "LLM";
  return kind.charAt(0) + kind.slice(1).toLowerCase();
}

export function TraceToolbar({
  spans,
  kind,
  onKindChange,
  errorOnly,
  onErrorOnlyChange,
  query,
  onQueryChange,
  sortMode,
  onSortModeChange,
  onExpandAll,
  onCollapseAll,
  matchCount,
}: TraceToolbarProps) {
  const counts = traceKindCounts(spans);
  const present = Object.keys(counts);
  const ordered = [
    ...CANONICAL.filter((k) => (counts[k] || 0) > 0),
    ...OTHER_ORDER.filter((k) => (counts[k] || 0) > 0),
    ...present.filter((k) => !CANONICAL.includes(k) && !OTHER_ORDER.includes(k)).sort(),
  ];

  return (
    <div className="aa-tx-toolbar" data-testid="trace-toolbar">
      <div className="aa-tx-kinds">
        <button
          type="button"
          className={`aa-tx-chip${kind === "" ? " active" : ""}`}
          onClick={() => onKindChange("")}
        >
          All <span className="aa-tx-chip-count">{spans.length}</span>
        </button>
        {ordered.map((k) => (
          <button
            key={k}
            type="button"
            className={`aa-tx-chip${kind === k ? " active" : ""}`}
            onClick={() => onKindChange(kind === k ? "" : k)}
            title={`${kindLabel(k)} spans`}
          >
            {kindLabel(k)} <span className="aa-tx-chip-count">{counts[k]}</span>
          </button>
        ))}
      </div>

      <div className="aa-tx-toolbar-spacer" />

      <label className="aa-check" title="Show only spans that failed">
        <input
          type="checkbox"
          checked={errorOnly}
          onChange={(event) => onErrorOnlyChange(event.target.checked)}
        />
        Errors only
      </label>

      <input
        className="aa-search aa-tx-search"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder="Search name, tool, model, node…"
        aria-label="Search spans"
      />

      <select
        className="aa-tx-select"
        value={sortMode}
        onChange={(event) => onSortModeChange(event.target.value as TraceSortMode)}
        aria-label="Sort spans"
        title="Sibling ordering"
      >
        <option value="time">Start time</option>
        <option value="duration">Duration</option>
        <option value="tokens">Tokens</option>
      </select>

      <button type="button" className="aa-btn aa-tx-toolbar-btn" onClick={onExpandAll}>
        Expand all
      </button>
      <button type="button" className="aa-btn aa-tx-toolbar-btn" onClick={onCollapseAll}>
        Collapse all
      </button>

      <span className="aa-muted" style={{ fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
        {matchCount === spans.length
          ? `${spans.length} spans`
          : `${matchCount} of ${spans.length} spans`}
      </span>
    </div>
  );
}
