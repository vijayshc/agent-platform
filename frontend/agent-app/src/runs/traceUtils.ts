/** Trace-explorer helpers for Phoenix spans.
 *
 * Split out of runUtils.ts (which keeps the event/replay helpers) so both
 * modules stay well under the 600-line ceiling.  Pure presentation helpers:
 * tree building/filtering, time geometry, kind mapping and formatting.
 */
import type { TraceSpan, TraceSummary } from "../types";
import { coerceJson, parseTs, pretty } from "./runUtils";

export type TraceSortMode = "time" | "duration" | "tokens";

export interface TraceSpanNode {
  id: string;
  span: TraceSpan;
  depth: number;
  children: TraceSpanNode[];
  hasChildren: boolean;
}

/** Kinds we render a dedicated badge for, in the order chips are shown. */
export const TRACE_KIND_ORDER = [
  "AGENT",
  "CHAIN",
  "LLM",
  "TOOL",
  "RETRIEVER",
  "EMBEDDING",
  "RERANKER",
  "GUARDRAIL",
  "EVALUATOR",
];

export function traceKindSlug(kind?: string | null): string {
  return (kind || "unknown").toLowerCase();
}

export function traceKindGlyph(kind?: string | null): string {
  switch ((kind || "").toUpperCase()) {
    case "AGENT":
      return "A";
    case "CHAIN":
      return "C";
    case "LLM":
      return "L";
    case "TOOL":
      return "T";
    case "RETRIEVER":
      return "R";
    case "EMBEDDING":
      return "E";
    case "RERANKER":
      return "K";
    case "GUARDRAIL":
      return "G";
    case "EVALUATOR":
      return "V";
    default:
      return "•";
  }
}

export function traceSpanDuration(span: TraceSpan): number {
  const direct = Number(span.duration_ms);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const start = parseTs(span.start_time);
  const end = parseTs(span.end_time);
  if (start != null && end != null) return Math.max(0, end - start);
  return 0;
}

export function traceSpanIsError(span: TraceSpan): boolean {
  return Boolean(span.error) || (span.status || "").toUpperCase() === "ERROR";
}

/** Human label for a span row: the tool it invoked, else its own name. */
export function traceSpanLabel(span: TraceSpan): string {
  const name = (span.name || "").trim();
  if (span.tool_name && span.tool_name.trim() && span.tool_name.trim() !== name) {
    return span.tool_name.trim();
  }
  return name || span.kind || "span";
}

/** Secondary descriptor for a waterfall row: the model for LLM spans, the
 *  graph node for everything else.  The agent name is deliberately omitted --
 *  it is identical on every row (and shown in the header), so repeating it just
 *  steals width from the span name. */
export function traceSpanMeta(span: TraceSpan): string {
  const model = (span.model || "").trim();
  const node = (span.node || "").trim();
  if (model) return model;
  if (node && node.toLowerCase() !== model.toLowerCase()) return node;
  return "";
}

export function traceKindCounts(spans: TraceSpan[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const span of spans) {
    const kind = (span.kind || "UNKNOWN").toUpperCase();
    counts[kind] = (counts[kind] || 0) + 1;
  }
  return counts;
}

function traceStartValue(span: TraceSpan): number {
  return parseTs(span.start_time) ?? 0;
}

function compareSpanSiblings(mode: TraceSortMode) {
  return (a: TraceSpan, b: TraceSpan): number => {
    if (mode === "duration") {
      const diff = traceSpanDuration(b) - traceSpanDuration(a);
      if (diff) return diff;
    } else if (mode === "tokens") {
      const diff = Number(b.total_tokens || 0) - Number(a.total_tokens || 0);
      if (diff) return diff;
    }
    const ta = traceStartValue(a);
    const tb = traceStartValue(b);
    if (ta !== tb) return ta - tb;
    return traceSpanDuration(b) - traceSpanDuration(a);
  };
}

/** Build the span forest. Orphans and cycle members are promoted to roots so
 *  no span the server returned is silently dropped from the waterfall. */
export function buildTraceTree(spans: TraceSpan[], sortMode: TraceSortMode = "time"): TraceSpanNode[] {
  const byId = new Map<string, TraceSpan>();
  for (const span of spans) {
    if (span.id) byId.set(span.id, span);
  }

  const childrenOf = new Map<string, TraceSpan[]>();
  const roots: TraceSpan[] = [];
  for (const span of spans) {
    const parentId = span.parent_id || "";
    if (parentId && parentId !== span.id && byId.has(parentId)) {
      const list = childrenOf.get(parentId) || [];
      list.push(span);
      childrenOf.set(parentId, list);
    } else {
      roots.push(span);
    }
  }

  const cmp = compareSpanSiblings(sortMode);
  roots.sort(cmp);
  for (const list of childrenOf.values()) list.sort(cmp);

  const seen = new Set<string>();
  const create = (span: TraceSpan, depth: number): TraceSpanNode => {
    seen.add(span.id);
    const children = (childrenOf.get(span.id) || [])
      .filter((child) => !seen.has(child.id))
      .map((child) => create(child, depth + 1));
    return { id: span.id, span, depth, children, hasChildren: children.length > 0 };
  };

  const nodes = roots.map((root) => create(root, 0));
  for (const span of spans) {
    if (!seen.has(span.id)) nodes.push(create(span, 0));
  }
  return nodes;
}

export function flattenTraceTree(
  nodes: TraceSpanNode[],
  collapsed: Set<string> = new Set()
): TraceSpanNode[] {
  const flat: TraceSpanNode[] = [];
  const walk = (node: TraceSpanNode) => {
    flat.push(node);
    if (!collapsed.has(node.id)) {
      for (const child of node.children) walk(child);
    }
  };
  for (const node of nodes) walk(node);
  return flat;
}

/** Prune to matching spans while keeping every ancestor that leads to a match,
 *  so filtered results still read as a tree with its context intact. */
export function filterTraceTree(
  nodes: TraceSpanNode[],
  keep: (span: TraceSpan) => boolean
): TraceSpanNode[] {
  const out: TraceSpanNode[] = [];
  for (const node of nodes) {
    const children = filterTraceTree(node.children, keep);
    if (keep(node.span) || children.length) {
      out.push({ ...node, children, hasChildren: children.length > 0 });
    }
  }
  return out;
}

export function allTraceParentIds(nodes: TraceSpanNode[]): Set<string> {
  const ids = new Set<string>();
  const walk = (node: TraceSpanNode) => {
    if (node.hasChildren) ids.add(node.id);
    node.children.forEach(walk);
  };
  nodes.forEach(walk);
  return ids;
}

export interface TraceBounds {
  start: number;
  end: number;
  total: number;
}

/** Absolute window of the trace, widened by the summary when it knows better. */
export function traceBounds(spans: TraceSpan[], summary?: TraceSummary | null): TraceBounds {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const span of spans) {
    const spanStart = parseTs(span.start_time);
    const spanEnd = parseTs(span.end_time);
    if (spanStart != null) {
      start = Math.min(start, spanStart);
      end = Math.max(end, spanStart + traceSpanDuration(span));
    }
    if (spanEnd != null) end = Math.max(end, spanEnd);
  }
  const summaryStart = parseTs(summary?.start_time);
  const summaryEnd = parseTs(summary?.end_time);
  if (summaryStart != null) start = Math.min(start, summaryStart);
  if (summaryEnd != null) end = Math.max(end, summaryEnd);
  if (!Number.isFinite(start)) start = 0;
  if (!Number.isFinite(end) || end < start) end = start;
  return { start, end, total: Math.max(1, end - start) };
}

/** A span's [left, width] as percentages of the trace window. */
export function traceBarGeometry(
  span: TraceSpan,
  bounds: TraceBounds
): { left: number; width: number } {
  const spanStart = parseTs(span.start_time) ?? bounds.start;
  const left = ((spanStart - bounds.start) / bounds.total) * 100;
  const width = (traceSpanDuration(span) / bounds.total) * 100;
  return {
    left: Math.min(100, Math.max(0, left)),
    width: Math.min(100, Math.max(0.35, width)),
  };
}

export function formatTokens(value?: number | null): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
}

export function formatCost(value?: number | null): string {
  if (value == null) return "";
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return `$${n.toFixed(4)}`;
}

/** Clipboard write with a legacy fallback (non-secure embeds lack the API). */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the textarea shim */
  }
  try {
    const el = document.createElement("textarea");
    el.value = text;
    el.setAttribute("readonly", "");
    el.style.position = "fixed";
    el.style.opacity = "0";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(el);
    return ok;
  } catch {
    return false;
  }
}

/** Pull a pretty string out of a span IO payload. */
export function ioValueText(value?: string | null): string {
  const raw = (value || "").trim();
  if (!raw) return "";
  const coerced = coerceJson(raw);
  if (typeof coerced === "string") return coerced;
  try {
    return JSON.stringify(coerced, null, 2);
  } catch {
    return raw;
  }
}

