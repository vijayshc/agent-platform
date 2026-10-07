import { useMemo } from "react";
import { Info } from "lucide-react";
import { aggregateColumn, formatValue } from "./dashboardData";
import type { ProgressSpec, ToolDataPayload } from "./toolDataTypes";

/** One measure against a literal target: value, bar, and percent. */
export function ToolProgress({ data, spec }: { data: ToolDataPayload; spec: ProgressSpec }) {
  const metric = String(spec.metric || "");
  const mode = spec.aggregate || "sum";
  const value = useMemo(() => aggregateColumn(data, metric, mode), [data, metric, mode]);
  const target = Number(spec.target);
  const format = useMemo(
    () => (v: number | null) => formatValue(v, spec.valueFormat || "number", spec.currency),
    [spec.valueFormat, spec.currency],
  );
  const ratio = value != null && Number.isFinite(target) && target > 0 ? value / target : 0;
  const pct = Math.max(0, Math.min(1, ratio));
  const over = ratio > 1;
  const title = spec.title || `${mode} of ${metric} vs target`;

  return (
    <figure className="td-card td-progress" data-layout={spec.layout || "full"} data-testid={`progress-card-${data.call_id}`}>
      <figcaption className="td-card-head">
        <div className="td-card-titles">
          <span className="td-card-title">{title}</span>
          {spec.subtitle ? <span className="td-card-sub">{spec.subtitle}</span> : null}
        </div>
        <div className="td-card-tools">
          {spec.diagnostics?.length ? (
            <span
              className="td-card-note-icon"
              title={spec.diagnostics.join("\n")}
              aria-label={spec.diagnostics.join(" ")}
            >
              <Info size={14} strokeWidth={1.9} />
            </span>
          ) : null}
          {value != null ? (
            <span className={`td-progress-pill${over ? " td-progress-pill-over" : ""}`}>
              {over ? "↑" : ""}
              {Math.round(ratio * 100)}%
            </span>
          ) : null}
        </div>
      </figcaption>
      <div className="td-progress-body">
        <div className="td-progress-numbers">
          <span className="td-progress-value">{format(value)}</span>
          <span className="td-progress-target">of {format(Number.isFinite(target) ? target : null)}</span>
        </div>
        <div
          className="td-progress-track"
          role="progressbar"
          aria-valuenow={value != null ? Math.round(pct * 100) : 0}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={`${title}: ${format(value)} of ${format(Number.isFinite(target) ? target : null)}`}
        >
          <div
            className={`td-progress-fill${over ? " td-progress-fill-over" : ""}`}
            style={{
              width: `${Math.round(pct * 100)}%`,
              ...(spec.color && !over ? { background: spec.color, boxShadow: "none" } : {}),
            }}
          />
          {[25, 50, 75].map((tick) => (
            <span key={tick} className="td-progress-tick" style={{ left: `${tick}%` }} aria-hidden="true" />
          ))}
        </div>
        <div className="td-progress-meta">
          {value != null && Number.isFinite(target) ? (
            over ? (
              <span className="td-progress-note">Above target</span>
            ) : (
              <span>
                {format(target - value)} to go · {format(target)} target
              </span>
            )
          ) : (
            <span>{format(Number.isFinite(target) ? target : null)} target</span>
          )}
        </div>
      </div>
      {data.truncated ? (
        <div className="td-card-foot">
          Showing {data.returned_rows.toLocaleString()} of {data.total_rows.toLocaleString()} rows
          (cache limit).
        </div>
      ) : null}
    </figure>
  );
}
