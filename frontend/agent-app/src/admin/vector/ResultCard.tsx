/** One search hit: score, matched text and metadata. */

import { useState } from "react";
import { ChevronDown, Copy } from "lucide-react";

import { formatValue, highlight } from "./resultFormat";
import { MATCH_LABEL, type SearchHit } from "./vectorTypes";

const VISIBLE_METADATA = 4;

interface Props {
  hit: SearchHit;
  rank: number;
  maxBm25: number;
}

export function ResultCard({ hit, rank, maxBm25 }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const similarity = typeof hit.similarity === "number" ? hit.similarity : null;
  const bm25Ratio = typeof hit.bm25 === "number" && maxBm25 > 0 ? hit.bm25 / maxBm25 : null;
  const fill = similarity !== null ? similarity : bm25Ratio;

  const metadata = Object.entries(hit.metadata || {});
  const shown = expanded ? metadata : metadata.slice(0, VISIBLE_METADATA);
  const hidden = metadata.length - shown.length;

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(String(hit.id));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <article className={`vb-hit${expanded ? " expanded" : ""}`} data-testid="vector-result">
      <div className="vb-hit-head">
        <span className="vb-rank">{rank}</span>
        <span className={`vb-badge ${hit.match}`}>{MATCH_LABEL[hit.match]}</span>

        <span className="vb-score">
          {similarity !== null && (
            <span className="vb-score-value" title="Vector similarity reported by the database">
              {(similarity * 100).toFixed(0)}% match
            </span>
          )}
          {typeof hit.bm25 === "number" && (
            <span className="vb-score-kw" title="BM25 keyword score">
              bm25 {hit.bm25.toFixed(2)}
            </span>
          )}
          {fill !== null && (
            <span className="vb-bar" aria-hidden="true">
              <span className={`vb-bar-fill ${hit.match}`} style={{ width: `${Math.max(6, Math.min(100, fill * 100))}%` }} />
            </span>
          )}
        </span>

        <button type="button" className="vb-icon-btn" onClick={copyId} title="Copy document id">
          <Copy size={13} />
          {copied ? "Copied" : String(hit.id).slice(0, 8)}
        </button>
        <button
          type="button"
          className="vb-icon-btn"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          title={expanded ? "Collapse" : "Expand"}
        >
          <ChevronDown size={14} className={expanded ? "rotated" : ""} />
        </button>
      </div>

      <p className="vb-hit-text">
        {highlight(expanded ? hit.text : hit.text.replace(/\s+/g, " ").trim(), hit.matched_terms)}
      </p>

      {metadata.length > 0 && (
        <div className="vb-meta">
          {shown.map(([key, value]) => (
            <span className="vb-meta-chip" key={key} title={`${key}: ${formatValue(value, 400)}`}>
              <span className="vb-meta-key">{key}</span>
              <span className="vb-meta-value">{formatValue(value, expanded ? 400 : 40)}</span>
            </span>
          ))}
          {!expanded && hidden > 0 && <span className="vb-meta-more">+{hidden} more</span>}
        </div>
      )}
    </article>
  );
}
