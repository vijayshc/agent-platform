import { AlertTriangle } from "lucide-react";
import { useToolData, useToolDataPending } from "./toolDataContext";
import { ToolCard } from "./ToolCard";
import { ToolChart } from "./ToolChart";
import { ToolDataTable } from "./ToolDataTable";
import { ToolList } from "./ToolList";
import { ToolNote } from "./ToolNote";
import { ToolProgress } from "./ToolProgress";
import type { RichBlock } from "./toolDataTypes";
import "./toolData.css";

/** One chart/table/dashboard placeholder resolved against the turn's cached tool data. */
export function ToolDataBlock({ block }: { block: RichBlock }) {
  const data = useToolData(block.kind === "note" ? "__note__" : block.callId);
  const pending = useToolDataPending();

  // A note carries its own text: it never touches the tool-data cache.
  if (block.kind === "note") {
    if (block.spec.error) {
      return (
        <figure className="td-card td-card-error" data-testid="note-error">
          <div className="td-empty">
            <AlertTriangle size={14} />
            <span>{block.spec.error}</span>
          </div>
        </figure>
      );
    }
    return <ToolNote spec={block.spec} />;
  }

  if (!data) {
    // While the turn streams, the full table has not reached the client yet —
    // showing "no longer available" here would be a false error.
    const layout = (block.spec as { layout?: string }).layout || "full";
    if (pending) {
      return (
        <div className="td-card td-card-skeleton" data-layout={layout} data-testid={`${block.kind}-skeleton`} aria-busy="true">
          <div className="td-card-head">
            <span className="td-card-title">Preparing {block.kind}…</span>
          </div>
          <div className="td-skeleton-body" aria-hidden="true">
            <span className="td-skeleton-bar" />
            <span className="td-skeleton-bar td-skeleton-short" />
            <span className="td-skeleton-bar" />
          </div>
        </div>
      );
    }
    return (
      <figure className="td-card td-card-missing" data-layout={layout} data-testid={`missing-card-${block.callId}`}>
        <div className="td-empty">
          <AlertTriangle size={14} />
          <span>This {block.kind} refers to tool data that is no longer available in this session.</span>
        </div>
      </figure>
    );
  }

  // The server refuses to draw a spec it cannot verify. Every block kind carries
  // the reason the same way, and it is always shown rather than a substitute.
  if (block.spec.error) {
    return (
      <figure
        className="td-card td-card-error"
        data-layout={(block.spec as { layout?: string }).layout || "full"}
        data-testid={`${block.kind}-error-${block.callId}`}
      >
        <figcaption className="td-card-head">
          <div className="td-card-titles">
            <span className="td-card-title">
              {block.kind === "chart"
                ? "Chart not drawn"
                : block.kind === "table"
                  ? "Table not drawn"
                  : block.kind === "card"
                    ? "Card not drawn"
                    : block.kind === "list"
                      ? "List not drawn"
                      : "Progress not drawn"}
            </span>
            <span className="td-card-sub">{data.tool_name}</span>
          </div>
        </figcaption>
        <div className="td-empty">
          <AlertTriangle size={14} />
          <span>{block.spec.error}</span>
        </div>
      </figure>
    );
  }

  if (block.kind === "table") return <ToolDataTable data={data} spec={block.spec} />;
  if (block.kind === "card") return <ToolCard data={data} spec={block.spec} />;
  if (block.kind === "list") return <ToolList data={data} spec={block.spec} />;
  if (block.kind === "progress") return <ToolProgress data={data} spec={block.spec} />;
  return <ToolChart data={data} spec={block.spec} />;
}
