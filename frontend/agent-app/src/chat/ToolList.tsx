import { useMemo } from "react";
import { Info } from "lucide-react";
import { formatValue, topRanked } from "./dashboardData";
import type { ListSpec, ToolDataPayload } from "./toolDataTypes";

/** A dense top-N ranking. The model chooses the presentation:
 *  `bars` (proportional bars), `plain` (ranked rows), or `share` (with %). */
export function ToolList({ data, spec }: { data: ToolDataPayload; spec: ListSpec }) {
  const limit = Math.max(2, Math.min(20, Math.floor(Number(spec.limit) || 8)));
  const rows = useMemo(
    () => topRanked(data, String(spec.label || ""), String(spec.value || ""), spec.aggregate || "sum", limit),
    [data, spec.label, spec.value, spec.aggregate, limit],
  );
  const format = useMemo(
    () => (v: number | null) => formatValue(v, spec.valueFormat || "number", spec.currency),
    [spec.valueFormat, spec.currency],
  );
  const max = rows.reduce((m, r) => Math.max(m, r.value ?? 0), 0);
  const total = rows.reduce((sum, r) => sum + Math.max(0, r.value ?? 0), 0);
  const variant = spec.variant || "bars";
  const showShare = spec.showShare ?? variant === "share";
  const title = spec.title || `Top ${limit} by ${spec.value}`;

  return (
    <figure className="td-card td-list" data-layout={spec.layout || "full"} data-testid={`list-card-${data.call_id}`}>
      <figcaption className="td-card-head">
        <div className="td-card-titles">
          <span className="td-card-title">{title}</span>
          {spec.subtitle ? <span className="td-card-sub">{spec.subtitle}</span> : null}
        </div>
        {spec.diagnostics?.length ? (
          <span
            className="td-card-note-icon"
            title={spec.diagnostics.join("\n")}
            aria-label={spec.diagnostics.join(" ")}
          >
            <Info size={14} strokeWidth={1.9} />
          </span>
        ) : null}
      </figcaption>
      {rows.length === 0 ? (
        <div className="td-empty">This result has no rows to rank.</div>
      ) : (
        <ol className={`td-rank td-rank-${variant}`}>
          {rows.map((row, index) => {
            const share = total > 0 && row.value != null ? row.value / total : null;
            return (
              <li key={`${row.label}-${index}`} className="td-rank-row">
                <span className={`td-rank-pos${index < 3 ? ` td-rank-top${index + 1}` : ""}`}>
                  {index + 1}
                </span>
                <span className="td-rank-main">
                  <span className="td-rank-top">
                    <span className="td-rank-label" title={row.label}>
                      {row.label}
                    </span>
                    <span className="td-rank-value">
                      {format(row.value)}
                      {showShare && share != null ? (
                        <span className="td-rank-share">{Math.round(share * 100)}%</span>
                      ) : null}
                    </span>
                  </span>
                  {variant === "plain" ? null : (
                    <span
                      className="td-rank-track"
                      role="img"
                      aria-label={`${row.label}: ${format(row.value)}`}
                    >
                      <span
                        className="td-rank-fill"
                        style={{
                          width: `${max > 0 && row.value != null ? Math.max(4, (row.value / max) * 100) : 0}%`,
                          ...(spec.color
                            ? {
                                background: `linear-gradient(90deg, color-mix(in srgb, ${spec.color} 72%, transparent), ${spec.color})`,
                              }
                            : {}),
                        }}
                      />
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      )}
      {data.truncated ? (
        <div className="td-card-foot">
          Showing {data.returned_rows.toLocaleString()} of {data.total_rows.toLocaleString()} rows
          (cache limit).
        </div>
      ) : null}
    </figure>
  );
}
