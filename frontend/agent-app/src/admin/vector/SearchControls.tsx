/** Query input, search mode switch and result count. */

import { Search, X } from "lucide-react";

import { LIMIT_OPTIONS, MODE_INFO, type SearchMode } from "./vectorTypes";

const MODES: SearchMode[] = ["semantic", "keyword", "hybrid"];

interface Props {
  query: string;
  onQueryChange: (value: string) => void;
  onSearch: () => void;
  searching: boolean;
  canSearch: boolean;
  mode: SearchMode;
  onModeChange: (mode: SearchMode) => void;
  limit: number;
  onLimitChange: (limit: number) => void;
  placeholder: string;
  dirty: boolean;
  filterApplied: boolean;
}

export function SearchControls({
  query,
  onQueryChange,
  onSearch,
  searching,
  canSearch,
  mode,
  onModeChange,
  limit,
  onLimitChange,
  placeholder,
  dirty,
  filterApplied,
}: Props) {
  return (
    <div className="vb-search-card">
      <div className="vb-search-row">
        <div className="vb-search-field">
          <Search size={16} className="vb-search-icon" />
          <input
            className="vb-search-input"
            value={query}
            placeholder={placeholder}
            aria-label="Search text"
            data-testid="vector-search-input"
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSearch();
              if (event.key === "Escape") onQueryChange("");
            }}
          />
          {query && (
            <button type="button" className="vb-clear" aria-label="Clear search" onClick={() => onQueryChange("")}>
              <X size={15} />
            </button>
          )}
        </div>
        <button
          type="button"
          className="aa-btn aa-btn-primary vb-search-btn"
          onClick={onSearch}
          disabled={searching || !canSearch}
          data-testid="vector-search-submit"
        >
          {searching ? "Searching…" : filterApplied && !query.trim() ? "Show records" : "Search"}
        </button>
      </div>

      <div className="vb-controls">
        <div className="vb-modes" role="group" aria-label="Search mode">
          {MODES.map((item) => (
            <button
              key={item}
              type="button"
              className={`vb-mode${mode === item ? " active" : ""}`}
              title={MODE_INFO[item].hint}
              aria-pressed={mode === item}
              data-testid={`vector-mode-${item}`}
              onClick={() => onModeChange(item)}
            >
              {MODE_INFO[item].label}
            </button>
          ))}
        </div>

        <label className="vb-limit">
          <span>Results</span>
          <select value={limit} onChange={(event) => onLimitChange(Number(event.target.value))} aria-label="Result count">
            {LIMIT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>

        <span className="vb-mode-hint">
          {query.trim() ? MODE_INFO[mode].hint : "Filter only: the records matching the filter are listed."}
        </span>
      </div>

      {dirty && !searching && <div className="vb-dirty-hint">Press Enter to run the search</div>}
    </div>
  );
}
