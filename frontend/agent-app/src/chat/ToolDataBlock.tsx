import { AlertTriangle } from "lucide-react";
import { useToolData, useToolDataPending } from "./toolDataContext";
import { ToolChart } from "./ToolChart";
import { ToolDataTable } from "./ToolDataTable";
import type { RichBlock } from "./toolDataTypes";

/** One chart/table placeholder resolved against the turn's cached tool data. */
export function ToolDataBlock({ block }: { block: RichBlock }) {
  const data = useToolData(block.callId);
  const pending = useToolDataPending();

  if (!data) {
    // While the turn streams, the full table has not reached the client yet —
    // showing "no longer available" here would be a false error.
    if (pending) {
      return (
        <div className="td-card td-card-skeleton" data-testid={`${block.kind}-skeleton`} aria-busy="true">
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
      <figure className="td-card td-card-missing" data-testid={`missing-card-${block.callId}`}>
        <div className="td-empty">
          <AlertTriangle size={14} />
          <span>This {block.kind} refers to tool data that is no longer available in this session.</span>
        </div>
      </figure>
    );
  }
  return block.kind === "table" ? (
    <ToolDataTable data={data} spec={block.spec} />
  ) : (
    <ToolChart data={data} spec={block.spec} />
  );
}
