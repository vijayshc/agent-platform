import { memo } from "react";
import { useChartPalette, compact } from "../admin/chartTheme";

interface TooltipEntry {
  name?: string | number;
  value?: number | string;
  color?: string;
  payload?: Record<string, unknown>;
  dataKey?: string | number;
}

/**
 * The shared chart tooltip used by the admin dashboard and by chat charts, so
 * both surfaces read as one product. Reads the live theme palette, so it
 * follows dark/light switches without a re-mount.
 */
export const ChartTooltip = memo(function ChartTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: string | number;
  format?: (value: number) => string;
}) {
  const p = useChartPalette();
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="aa-chart-tip" style={{ background: p.tooltipBg, border: `1px solid ${p.tooltipBorder}` }}>
      {label != null && label !== "" ? <div className="aa-chart-tip-date">{String(label)}</div> : null}
      {payload.map((entry, index) => {
        const value = typeof entry.value === "number" ? entry.value : Number(entry.value ?? 0);
        const swatch =
          entry.color || (entry.payload?.fill as string | undefined) || p.series[index % p.series.length];
        return (
          <div key={`${entry.name ?? index}-${index}`} className="aa-chart-tip-row">
            <span style={{ width: 8, height: 8, borderRadius: 2, background: swatch, flex: "0 0 auto" }} />
            <span className="aa-chart-tip-name">{String(entry.name ?? entry.dataKey ?? "")}</span>
            <span className="aa-chart-tip-val">
              {format && Number.isFinite(value) ? format(value) : compact(Number.isFinite(value) ? value : 0)}
            </span>
          </div>
        );
      })}
    </div>
  );
});
