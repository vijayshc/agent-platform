import { useId, useMemo } from "react";
import {
  Activity,  ArrowDownRight,
  ArrowUpRight,
  Award,
  BarChart3,
  Briefcase,
  Calendar,
  CircleCheck,
  Clock,
  DollarSign,
  Globe,
  Hash,
  Info,
  LineChart as LineChartIcon,
  Package,
  Percent,
  PiggyBank,
  ShoppingCart,
  Star,
  Target,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  Truck,
  User,
  Users,
  Wallet,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  YAxis,
} from "recharts";
import { useChartPalette } from "../admin/chartTheme";
import { ChartTooltip } from "../shared/ChartTooltip";
import { aggregateColumn, formatValue, sparkPoints } from "./dashboardData";
import type { CardSpec, ToolDataPayload } from "./toolDataTypes";

/** Kebab-case icon names (server allowlist) mapped to Lucide components.
 *  An unlisted name resolves to nothing — the card simply shows no icon. */
const ICONS: Record<string, LucideIcon> = {
  "trending-up": TrendingUp,
  "trending-down": TrendingDown,
  wallet: Wallet,
  "shopping-cart": ShoppingCart,
  users: Users,
  user: User,
  package: Package,
  truck: Truck,
  "piggy-bank": PiggyBank,
  target: Target,
  award: Award,
  star: Star,
  activity: Activity,
  "bar-chart-3": BarChart3,
  "line-chart": LineChartIcon,
  percent: Percent,
  hash: Hash,
  calendar: Calendar,
  clock: Clock,
  globe: Globe,
  briefcase: Briefcase,
  zap: Zap,
  "arrow-up-right": ArrowUpRight,
  "arrow-down-right": ArrowDownRight,
  "circle-check": CircleCheck,
  "triangle-alert": TriangleAlert,
  "dollar-sign": DollarSign,
};

/** One KPI number computed from the cached rows. */
export function ToolCard({ data, spec }: { data: ToolDataPayload; spec: CardSpec }) {
  const p = useChartPalette();
  const metric = String(spec.metric || "");
  const mode = spec.aggregate || "sum";
  const value = useMemo(() => aggregateColumn(data, metric, mode), [data, metric, mode]);
  const delta = useMemo(
    () =>
      spec.deltaMetric
        ? aggregateColumn(data, spec.deltaMetric, spec.deltaAggregate || "sum")
        : null,
    [data, spec.deltaMetric, spec.deltaAggregate],
  );
  const format = useMemo(
    () => (v: number | null) => formatValue(v, spec.valueFormat || "number", spec.currency),
    [spec.valueFormat, spec.currency],
  );
  const points = useMemo(
    () =>
      spec.spark?.x
        ? sparkPoints(data, spec.spark.x, metric, mode).map((pt) => ({ x: pt.x, v: pt.value ?? 0 }))
        : [],
    [data, spec.spark, metric, mode],
  );
  const title = spec.title || `${mode} of ${metric}`;
  const sparkType = spec.spark?.type || "bar";
  // The model may pick the accent; otherwise a stable per-card palette color so
  // a KPI row reads as a designed set rather than monochrome.
  const accent = useMemo(() => {
    if (spec.color) return spec.color;
    let hash = 0;
    for (let i = 0; i < title.length; i += 1) hash = (hash * 31 + title.charCodeAt(i)) >>> 0;
    return p.series[hash % p.series.length];
  }, [p.series, title, spec.color]);
  const mutedBar = p.muted;
  const Icon = spec.icon ? ICONS[spec.icon] : undefined;
  const sparkId = useId().replace(/[^a-zA-Z0-9]/g, "");
  // A tight domain for line/area sparks: values that differ by a few percent
  // would otherwise draw as a flat line pinned to the top of a zero-based axis.
  const sparkDomain: [number, number] | null = useMemo(() => {
    if (!points.length || (sparkType !== "line" && sparkType !== "area")) return null;
    let min = Infinity;
    let max = -Infinity;
    for (const pt of points) {
      if (pt.v < min) min = pt.v;
      if (pt.v > max) max = pt.v;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (max === min) return [min - Math.abs(min) * 0.05 - 1, max + Math.abs(max) * 0.05 + 1];
    const pad = (max - min) * 0.25;
    return [min - pad, max + pad];
  }, [points, sparkType]);
  const sparkTip = (
    <Tooltip
      content={<ChartTooltip format={(v: number) => format(v)} />}
      cursor={sparkType === "bar" ? { fill: p.grid, opacity: 0.3 } : { stroke: p.grid }}
    />
  );

  return (
    <figure
      className="td-card td-kpi"
      data-layout={spec.layout || "full"}
      data-testid={`card-card-${data.call_id}`}
    >
      <span className="td-kpi-rail" aria-hidden="true" style={{ background: accent }} />
      {Icon ? (
        <span className="td-kpi-chip" aria-hidden="true" style={{ color: accent }}>
          <Icon size={18} strokeWidth={2.1} />
        </span>
      ) : null}
      <div className="td-kpi-body">
        <div className="td-kpi-caption-row">
          <span className="td-kpi-caption">{title}</span>
          {spec.diagnostics?.length ? (
            <span
              className="td-card-note-icon"
              title={spec.diagnostics.join("\n")}
              aria-label={spec.diagnostics.join(" ")}
            >
              <Info size={13} strokeWidth={1.9} />
            </span>
          ) : null}
        </div>
        <div className="td-kpi-top">
          <span
            className="td-kpi-value"
            data-testid={`card-value-${data.call_id}`}
            title={format(value)}
          >
            {format(value)}
          </span>
          {delta !== null && spec.deltaMetric ? (
            <span className="td-kpi-delta-pill">
              {format(delta)}
              <span className="td-kpi-delta-sep">·</span>
              <span className="td-kpi-delta-label">{spec.deltaMetric}</span>
            </span>
          ) : null}
        </div>
        {spec.subtitle || spec.hint ? (
          <div className="td-kpi-hint">{spec.subtitle || spec.hint}</div>
        ) : null}
        {points.length > 1 ? (
          <div className="td-kpi-spark">
            <ResponsiveContainer width="100%" height={48}>
              {sparkType === "line" ? (
                <LineChart data={points} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
                  {sparkDomain ? <YAxis hide domain={sparkDomain} /> : null}
                  <Line
                    type="monotone"
                    dataKey="v"
                    name={metric}
                    stroke={accent}
                    strokeWidth={2.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                  {sparkTip}
                </LineChart>
              ) : sparkType === "area" ? (
                <AreaChart data={points} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
                  <defs>
                    <linearGradient id={`tdkpi-${sparkId}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={accent} stopOpacity={0.5} />
                      <stop offset="100%" stopColor={accent} stopOpacity={0.03} />
                    </linearGradient>
                  </defs>
                  {sparkDomain ? <YAxis hide domain={sparkDomain} /> : null}
                  <Area
                    type="monotone"
                    dataKey="v"
                    name={metric}
                    stroke={accent}
                    strokeWidth={2.5}
                    fill={`url(#tdkpi-${sparkId})`}
                    dot={false}
                    isAnimationActive={false}
                  />
                  {sparkTip}
                </AreaChart>
              ) : (
                <BarChart data={points} margin={{ top: 6, right: 4, bottom: 0, left: 4 }}>
                  <Bar dataKey="v" name={metric} radius={[3, 3, 3, 3]} maxBarSize={12} isAnimationActive={false}>
                    {points.map((_, index) => (
                      <Cell
                        key={index}
                        fill={index === points.length - 1 ? accent : mutedBar}
                        fillOpacity={index === points.length - 1 ? 1 : 0.45}
                      />
                    ))}
                  </Bar>
                  {sparkTip}
                </BarChart>
              )}
            </ResponsiveContainer>
          </div>
        ) : null}
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
