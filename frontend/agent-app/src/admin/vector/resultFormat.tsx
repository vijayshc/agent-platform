/** Highlighting and formatting helpers for search results. */

import type { ReactNode } from "react";

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Wrap every matched query term in the text with <mark>. */
export function highlight(text: string, terms: string[]): ReactNode {
  const clean = [...new Set((terms || []).filter((term) => term.length > 1))];
  if (!text) return <span className="vb-muted">(no text stored)</span>;
  if (clean.length === 0) return text;

  // String.split with a capture group returns the matched terms as their own
  // entries, so membership in the term set identifies what to highlight.
  const lowered = new Set(clean.map((term) => term.toLowerCase()));
  const pattern = new RegExp(`(${clean.map(escapeRegExp).join("|")})`, "gi");
  return text
    .split(pattern)
    .map((part, index) =>
      lowered.has(part.toLowerCase()) ? (
        <mark key={`${index}-${part}`} className="vb-mark">
          {part}
        </mark>
      ) : (
        <span key={`${index}-${part}`}>{part}</span>
      ),
    );
}

export function formatValue(value: unknown, max = 120): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "—";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}
