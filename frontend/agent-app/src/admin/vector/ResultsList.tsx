/** Result list with its loading, empty and error states. */

import { AlertCircle, SearchX } from "lucide-react";

import { formatCount } from "./resultFormat";
import { ResultCard } from "./ResultCard";
import type { SearchHit, SearchStats } from "./vectorTypes";

interface Props {
  hits: SearchHit[] | null;
  stats: SearchStats | null;
  query: string;
  searching: boolean;
  error: string | null;
  onRetry: () => void;
  hasFilter: boolean;
  onClearFilter: () => void;
}

function Summary({ stats }: { stats: SearchStats }) {
  if (stats.mode === "filter") {
    return (
      <div className="vb-summary">
        {`${formatCount(stats.returned)} ${stats.returned === 1 ? "record" : "records"} matching the filter · ${stats.took_ms} ms`}
      </div>
    );
  }
  const parts = [
    `${stats.returned} ${stats.returned === 1 ? "result" : "results"}`,
    stats.mode === "semantic" ? null : `${stats.keyword_hits} keyword`,
    stats.mode === "keyword" ? null : `${stats.semantic_hits} semantic`,
    stats.scanned !== null ? `scanned ${formatCount(stats.scanned)} of ${formatCount(stats.total ?? 0)}` : null,
    `${stats.took_ms} ms`,
  ].filter(Boolean);
  return <div className="vb-summary">{parts.join(" · ")}</div>;
}

export function ResultsList({
  hits,
  stats,
  query,
  searching,
  error,
  onRetry,
  hasFilter,
  onClearFilter,
}: Props) {
  if (searching && (!hits || hits.length === 0)) {
    return (
      <div className="vb-skeleton-list" data-testid="vector-results-loading">
        {[0, 1, 2].map((row) => (
          <div className="vb-skeleton" key={row}>
            <span className="vb-skeleton-line short" />
            <span className="vb-skeleton-line" />
            <span className="vb-skeleton-line medium" />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="vb-notice error" data-testid="vector-results-error">
        <AlertCircle size={16} />
        <div>
          <strong>Search failed</strong>
          <div className="vb-notice-detail">{error}</div>
        </div>
        <button type="button" className="aa-btn aa-btn-ghost" onClick={onRetry}>
          Retry
        </button>
      </div>
    );
  }

  if (hits === null) return null;

  if (hits.length === 0) {
    return (
      <div className="vb-notice" data-testid="vector-results-empty">
        <SearchX size={16} />
        <div>
          <strong>{query ? `No matches for “${query}”` : "No records match this filter"}</strong>
          <div className="vb-notice-detail">
            {hasFilter && query
              ? "Try removing the filter, or search in Hybrid mode."
              : hasFilter
                ? "Check the field names and values in the where clause."
                : "Try Hybrid mode, fewer words, or a different phrasing."}
          </div>
        </div>
        {hasFilter && (
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClearFilter}>
            Clear filter
          </button>
        )}
      </div>
    );
  }

  const maxBm25 = hits.reduce((max, hit) => Math.max(max, hit.bm25 ?? 0), 0);

  return (
    <div className="vb-results" data-testid="vector-results">
      {stats && <Summary stats={stats} />}
      {stats?.truncated && (
        <div className="vb-truncated">
          {stats.mode === "filter"
            ? `Showing the first ${formatCount(stats.returned)} matching records — raise Results to see more.`
            : `Keyword ranking scanned the first ${formatCount(stats.scanned ?? 0)} of ${formatCount(stats.total ?? 0)} vectors.`}
        </div>
      )}
      {hits.map((hit, index) => (
        <ResultCard key={String(hit.id)} hit={hit} rank={index + 1} maxBm25={maxBm25} />
      ))}
    </div>
  );
}
