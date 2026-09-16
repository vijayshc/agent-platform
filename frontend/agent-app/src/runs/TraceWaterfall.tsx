/** Time-proportional, hierarchical span waterfall for one Phoenix trace. */
import { useEffect, useMemo, useRef } from "react";

import type { TraceSpan } from "../types";
import { formatDur, parseTs } from "./runUtils";
import {
  flattenTraceTree,
  formatTokens,
  traceBarGeometry,
  traceKindGlyph,
  traceKindSlug,
  traceSpanDuration,
  traceSpanIsError,
  traceSpanLabel,
  traceSpanMeta,
  type TraceBounds,
  type TraceSpanNode,
} from "./traceUtils";
import "./traceWaterfall.css";

interface TraceWaterfallProps {
  nodes: TraceSpanNode[];
  bounds: TraceBounds;
  selectedId: string | null;
  onSelect: (spanId: string) => void;
  collapsed: Set<string>;
  onToggle: (spanId: string) => void;
  /** The run is still producing spans. */
  live: boolean;
  /** Unfiltered span count, used to tell "nothing yet" from "nothing matches". */
  totalSpans: number;
}

const TICK_COUNT = 5;

function axisLabel(ms: number): string {
  if (ms <= 0) return "0";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  return `${seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)}s`;
}

function offsetLabel(span: TraceSpan, origin: number): string {
  const start = parseTs(span.start_time);
  if (start == null) return "";
  const delta = Math.max(0, start - origin);
  if (delta < 1000) return `+${Math.round(delta)}ms`;
  return `+${(delta / 1000).toFixed(delta >= 10000 ? 1 : 2)}s`;
}

export function TraceWaterfall({
  nodes,
  bounds,
  selectedId,
  onSelect,
  collapsed,
  onToggle,
  live,
  totalSpans,
}: TraceWaterfallProps) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const flat = useMemo(() => flattenTraceTree(nodes, collapsed), [nodes, collapsed]);

  useEffect(() => {
    if (!selectedId) return;
    const container = bodyRef.current;
    if (!container) return;
    const row = container.querySelector<HTMLElement>(`[data-span-id="${selectedId}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [selectedId, flat.length]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    if (!flat.length) return;
    event.preventDefault();
    const current = flat.findIndex((node) => node.id === selectedId);
    const next =
      event.key === "ArrowDown"
        ? Math.min(current + 1, flat.length - 1)
        : Math.max(current - 1, 0);
    const target = flat[current < 0 ? 0 : next];
    if (target) onSelect(target.id);
  };

  const ticks = Array.from({ length: TICK_COUNT }, (_, index) => ({
    pct: (index / (TICK_COUNT - 1)) * 100,
    label: axisLabel((bounds.total * index) / (TICK_COUNT - 1)),
  }));

  return (
    <div className="aa-tx-tree" data-testid="trace-waterfall">
      <div className="aa-tx-axis">
        <div className="aa-tx-axis-scale">
          <div className="aa-tx-axis-rule" />
          {ticks.map((tick) => (
            <span key={tick.pct} className="aa-tx-axis-tick" style={{ left: `${tick.pct}%` }}>
              {tick.label}
            </span>
          ))}
        </div>
      </div>

      <div
        ref={bodyRef}
        className="aa-tx-tree-body"
        role="tree"
        aria-label="Trace spans"
        tabIndex={0}
        onKeyDown={handleKeyDown}
      >
        {flat.map((node) => {
          const span = node.span;
          const slug = traceKindSlug(span.kind);
          const kind = (span.kind || "UNKNOWN").toUpperCase();
          const error = traceSpanIsError(span);
          const selected = node.id === selectedId;
          const duration = traceSpanDuration(span);
          const bar = traceBarGeometry(span, bounds);
          const label = traceSpanLabel(span);
          const meta = traceSpanMeta(span);
          const tokens = Number(span.total_tokens || 0) || (Number(span.prompt_tokens || 0) + Number(span.completion_tokens || 0));
          const offset = offsetLabel(span, bounds.start);
          const barTitle = [label, offset, formatDur(duration) || "0ms"].filter(Boolean).join(" · ");

          return (
            <div
              key={node.id}
              role="treeitem"
              aria-selected={selected}
              aria-level={node.depth + 1}
              tabIndex={-1}
              data-span-id={node.id}
              data-kind={kind}
              data-error={error}
              className={`aa-span aa-tx-row${selected ? " active" : ""}`}
              title={
                node.hasChildren
                  ? "Click to inspect · double-click to expand/collapse this subtree"
                  : "Click to inspect"
              }
              onClick={() => onSelect(node.id)}
              onDoubleClick={(event) => {
                if (!node.hasChildren) return;
                event.preventDefault();
                event.stopPropagation();
                onToggle(node.id);
              }}
            >
              <div className="aa-span-top">
                <div className="aa-span-title" style={{ paddingLeft: `${node.depth * 18}px` }}>
                  {node.depth > 0 ? (
                    <span
                      className="aa-tx-indent"
                      aria-hidden="true"
                      style={{ width: `${node.depth * 18}px`, marginLeft: `${node.depth * -18}px` }}
                    />
                  ) : null}
                  {node.hasChildren ? (
                    <button
                      type="button"
                      className="aa-tx-caret"
                      title={collapsed.has(node.id) ? "Expand subtree" : "Collapse subtree"}
                      aria-label={collapsed.has(node.id) ? "Expand subtree" : "Collapse subtree"}
                      onClick={(event) => {
                        event.stopPropagation();
                        onToggle(node.id);
                      }}
                    >
                      {collapsed.has(node.id) ? "▸" : "▾"}
                    </button>
                  ) : (
                    <span className="aa-tx-caret-spacer" />
                  )}
                  <span className={`aa-tx-badge k-${slug}`} aria-hidden="true">
                    {traceKindGlyph(span.kind)}
                  </span>
                  <span className="aa-span-kind">{kind}</span>
                  <span className="aa-span-label" title={label}>
                    {label}
                  </span>
                  {meta ? (
                    <span className="aa-tx-row-meta" title={meta}>
                      {meta}
                    </span>
                  ) : null}
                </div>
                <div className="aa-span-end">
                  {offset ? (
                    <span className="aa-tx-offset" title={`Starts ${offset} into the trace`}>
                      {offset}
                    </span>
                  ) : null}
                  {tokens > 0 ? (
                    <span
                      className="aa-tx-tok"
                      title={`${span.prompt_tokens ?? 0} prompt / ${span.completion_tokens ?? 0} completion tokens`}
                    >
                      {formatTokens(tokens)}
                    </span>
                  ) : null}
                  <span className="aa-span-dur">{formatDur(duration) || (duration === 0 ? "0ms" : "")}</span>
                </div>
              </div>
              <div className="aa-bar-track" title={barTitle}>
                <div
                  className={`aa-bar k-${slug}`}
                  style={{ left: `${bar.left}%`, width: `${bar.width}%` }}
                />
              </div>
            </div>
          );
        })}

        {flat.length === 0 ? (
          <div className="aa-muted aa-tx-tree-empty" data-testid="waterfall-empty">
            {totalSpans === 0
              ? live
                ? "Waiting for the first spans to reach Phoenix…"
                : "This trace has no spans."
              : "No spans match the current filters."}
          </div>
        ) : null}
      </div>
    </div>
  );
}
