import { useId, useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { chartAxisProps, chartGridProps, useChartPalette, type ChartPalette } from "../admin/chartTheme";
import { ChartTooltip } from "../shared/ChartTooltip";
import { buildChartModel, niceDomain, plottedValues, valueFormatter, type ChartModel } from "./chartData";
import type { ChartSpec, ToolDataPayload } from "./toolDataTypes";

const MAX_TICK_LABEL = 16;
const MAX_VALUE_LABELS = 15;

function truncate(value: unknown): string {
  const text = String(value ?? "");
  return text.length > MAX_TICK_LABEL ? `${text.slice(0, MAX_TICK_LABEL - 1)}…` : text;
}

/** A plotted value. The tool declared these columns numeric, so a non-number is
 *  a gap, not something to coerce. */
function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function ChartLegend({ items }: { items: Array<{ label: string; color: string }> }) {
  if (!items.length) return null;
  return (
    <ul className="td-legend" aria-label="Chart legend">
      {items.map((item) => (
        <li key={item.label} className="td-legend-item" title={item.label}>
          <span className="td-legend-dot" style={{ background: item.color }} />
          <span className="td-legend-label">{item.label}</span>
        </li>
      ))}
    </ul>
  );
}

function axisCommon(p: ChartPalette, vertical = false) {
  const base = chartAxisProps(p);
  return {
    ...base,
    ...(vertical ? {} : { minTickGap: 16 }),
  };
}

function PieView({
  model,
  p,
  height,
  format,
  colors,
  showLegend,
}: {
  model: ChartModel;
  p: ChartPalette;
  height: number;
  format: (value: number) => string;
  colors: string[];
  showLegend: boolean;
}) {
  const valueKey = model.valueKeys[0];
  const slices = model.data
    .map((row) => ({ name: String(row[model.xKey]), value: Number(row[valueKey]) || 0 }))
    .filter((slice) => slice.value !== 0);
  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const donut = String(model.type) === "donut";
  return (
    <div className={`td-pie-wrap${showLegend ? "" : " td-pie-solo"}`}>
      <div className="td-pie-chart" style={{ height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              dataKey="value"
              nameKey="name"
              innerRadius={donut ? "60%" : 0}
              outerRadius="90%"
              paddingAngle={slices.length > 1 ? (donut ? 2 : 1) : 0}
              // One slice is the whole circle: an outline would draw its two
              // radii as a stray line across the chart.
              stroke={slices.length > 1 ? p.card : "none"}
              strokeWidth={slices.length > 1 ? 2 : 0}
              isAnimationActive={slices.length <= 60}
            >
              {slices.map((slice, index) => (
                <Cell key={slice.name} fill={colors[index % colors.length]} />
              ))}
            </Pie>
            <Tooltip content={<ChartTooltip format={format} />} />
          </PieChart>
        </ResponsiveContainer>
        {donut ? (
          <div className="td-pie-total">
            <div className="td-pie-total-value">{format(total)}</div>
            <div className="td-pie-total-label">total</div>
          </div>
        ) : null}
      </div>
      {showLegend ? (
        <ul className="td-legend td-legend-pie" aria-label="Chart legend">
          {slices.slice(0, 12).map((slice, index) => (
            <li key={slice.name} className="td-legend-item" title={slice.name}>
              <span className="td-legend-dot" style={{ background: colors[index % colors.length] }} />
              <span className="td-legend-label">{slice.name}</span>
              <span className="td-legend-value">{format(slice.value)}</span>
              <span className="td-legend-pct">
                {total > 0 ? `${Math.round((slice.value / total) * 100)}%` : "0%"}
              </span>
            </li>
          ))}
          {slices.length > 12 ? <li className="td-legend-more">+{slices.length - 12} more</li> : null}
        </ul>
      ) : null}
    </div>
  );
}

function ScatterView({
  model,
  p,
  height,
  format,
  showGrid,
  showLegend,
}: {
  model: ChartModel;
  p: ChartPalette;
  height: number;
  format: (value: number) => string;
  showGrid: boolean;
  showLegend: boolean;
}) {
  const pointsFor = (key: string) =>
    model.data
      .map((row) => ({ x: asNumber(row[model.xKey]), y: asNumber(row[key]) }))
      .filter((point): point is { x: number; y: number } => point.x != null && point.y != null);
  return (
    <>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 4 }}>
          {showGrid ? <CartesianGrid {...chartGridProps(p)} /> : null}
          <XAxis
            type="number"
            dataKey="x"
            name={model.xLabel}
            {...chartAxisProps(p)}
            allowDecimals={!model.integerValues}
            tickFormatter={(v) => format(Number(v))}
          />
          <YAxis
            type="number"
            dataKey="y"
            {...chartAxisProps(p)}
            tickFormatter={(v) => format(Number(v))}
            width={56}
          />
          <Tooltip content={<ChartTooltip format={format} />} cursor={{ strokeDasharray: "3 3" }} />
          {model.valueKeys.map((key, index) => (
            <Scatter
              key={key}
              name={key}
              data={pointsFor(key)}
              fill={model.series[index % model.series.length]?.color}
              fillOpacity={0.8}
              isAnimationActive={pointsFor(key).length <= 400}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
      {showLegend ? (
        <ChartLegend items={model.series.map((s) => ({ label: s.label, color: s.color }))} />
      ) : null}
    </>
  );
}

function CartesianView({
  model,
  p,
  height,
  format,
  colors,
  uid,
  showGrid,
}: {
  model: ChartModel;
  p: ChartPalette;
  height: number;
  format: (value: number) => string;
  colors: string[];
  uid: string;
  showGrid: boolean;
}) {
  const grid = chartGridProps(p);
  const horizontal = model.type === "hbar";
  const isBar = model.type === "bar" || horizontal;
  const isArea = model.type === "area";
  const singleSeries = model.series.length === 1;
  const perCategory = model.colorBy === "category" && singleSeries;
  const showValues = isBar && singleSeries && !model.stacked && model.data.length <= MAX_VALUE_LABELS;
  const [valueMin, valueMax] = niceDomain(plottedValues(model));
  const gradientId = (suffix: string) => `tdg-${uid}-${suffix.replace(/[^a-zA-Z0-9]/g, "")}`;
  // Many or long category labels: rotate them so every bar keeps its label.
  const longestLabel = model.data.reduce(
    (longest, row) => Math.max(longest, String(row[model.xKey] ?? "").length),
    0,
  );
  const rotateTicks = !horizontal && (model.data.length > 6 || longestLabel > 10);
  const xAxis = {
    dataKey: horizontal ? undefined : model.xKey,
    type: horizontal ? ("number" as const) : ("category" as const),
    ...axisCommon(p, false),
    ...(horizontal
      ? {
          tickFormatter: (v: number) => format(Number(v)),
          allowDecimals: !model.integerValues,
          domain: [valueMin, valueMax] as [number, number],
        }
      : {
          tickFormatter: truncate,
          interval: rotateTicks ? (0 as const) : ("preserveStartEnd" as const),
          ...(rotateTicks ? { angle: -32, textAnchor: "end" as const, height: 64, tickMargin: 8 } : {}),
        }),
  };
  const yAxis = {
    type: horizontal ? ("category" as const) : ("number" as const),
    dataKey: horizontal ? model.xKey : undefined,
    ...axisCommon(p, true),
    ...(horizontal
      ? { width: 140, tickFormatter: truncate, tick: { fill: p.text, fontSize: 11 } }
      : {
          width: 62,
          tickFormatter: (v: number) => format(Number(v)),
          allowDecimals: !model.integerValues,
          domain: [valueMin, valueMax] as [number, number],
        }),
  };
  const tooltip = (
    <Tooltip content={<ChartTooltip format={format} />} cursor={isBar ? { fill: p.grid, opacity: 0.3 } : { stroke: p.grid }} />
  );
  const stackId = model.stacked ? "stack" : undefined;
  // A single-series chart is better named by its axis label ("Price ($)") than
  // by the raw column key ("price") in the tooltip.
  const seriesName = (label: string) => (singleSeries && model.yLabel ? model.yLabel : label);

  const marks = model.series.map((series, seriesIndex) =>
    isBar ? (
      <Bar
        key={series.key}
        dataKey={series.key}
        name={seriesName(series.label)}
        fill={perCategory ? colors[seriesIndex % colors.length] : `url(#${gradientId(series.key)})`}
        stackId={stackId}
        radius={horizontal ? [0, 6, 6, 0] : [6, 6, 0, 0]}
        maxBarSize={horizontal ? 24 : 54}
        isAnimationActive={model.data.length <= 200}
      >
        {perCategory
          ? model.data.map((row, index) => (
              <Cell key={`${series.key}-${index}`} fill={colors[index % colors.length]} />
            ))
          : null}
        {showValues ? (
          <LabelList
            dataKey={series.key}
            position={horizontal ? "right" : "top"}
            className="td-value-label"
            formatter={(value: unknown) => format(Number(value))}
          />
        ) : null}
      </Bar>
    ) : isArea ? (
      <Area
        key={series.key}
        type={model.smooth ? "monotone" : "linear"}
        dataKey={series.key}
        name={seriesName(series.label)}
        stroke={series.color}
        strokeWidth={2.25}
        fill={`url(#${gradientId(series.key)})`}
        fillOpacity={model.stacked ? 0.75 : 1}
        stackId={stackId}
        dot={model.data.length <= 40 ? { r: 2 } : false}
        activeDot={{ r: 4 }}
        isAnimationActive={model.data.length <= 200}
      />
    ) : (
      <Line
        key={series.key}
        type={model.smooth ? "monotone" : "linear"}
        dataKey={series.key}
        name={seriesName(series.label)}
        stroke={series.color}
        strokeWidth={2.5}
        dot={model.data.length <= 40 ? { r: 2.5 } : false}
        activeDot={{ r: 4.5 }}
        isAnimationActive={model.data.length <= 200}
      />
    ),
  );

  // Dispatch, not selection: `model.type` is one of bar/hbar/area/line by the
  // time a cartesian chart is drawn, and buildChartModel rejects anything else.
  const Chart = isBar ? BarChart : isArea ? AreaChart : LineChart;
  return (
    <div className={`td-cartesian${model.yLabel ? " has-ylabel" : ""}`}>
      {model.yLabel ? <span className="td-axis-y">{model.yLabel}</span> : null}
      <ResponsiveContainer width="100%" height={height}>
        <Chart
          data={model.data}
          layout={horizontal ? "vertical" : "horizontal"}
          margin={{ top: showValues && !horizontal ? 20 : 8, right: 16, bottom: 4, left: 4 }}
        >
          <defs>
            {model.series.map((series) => (
              <linearGradient key={series.key} id={gradientId(series.key)} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={series.color} stopOpacity={0.95} />
                <stop offset="100%" stopColor={series.color} stopOpacity={0.55} />
              </linearGradient>
            ))}
          </defs>
          {showGrid ? <CartesianGrid {...grid} vertical={horizontal} horizontal={!horizontal} /> : null}
          <XAxis {...xAxis} />
          <YAxis {...yAxis} />
          {tooltip}
          {marks}
        </Chart>
      </ResponsiveContainer>
      {model.xLabel ? <div className="td-axis-x">{model.xLabel}</div> : null}
    </div>
  );
}

export function ToolChart({ data, spec }: { data: ToolDataPayload; spec: ChartSpec }) {
  const p = useChartPalette();
  const model = useMemo(() => buildChartModel(data, spec, p.series), [data, spec, p.series]);
  const format = useMemo(() => valueFormatter(spec), [spec]);
  const height = Math.min(720, Math.max(180, Number(spec.height) || 300));
  const colors = spec.colors && spec.colors.length ? spec.colors : p.series;
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const title = spec.title || `${model.type} · ${data.tool_name}`;
  const isPie = model.type === "pie" || model.type === "donut";
  // Every field the renderer needs was resolved and written by the server; there
  // is nothing to default here.
  const showLegend = Boolean(spec.showLegend);
  const showGrid = spec.showGrid !== false;
  const diagnostics = spec.diagnostics ?? [];
  const meta = isPie
    ? `${data.total_rows.toLocaleString()} row${data.total_rows === 1 ? "" : "s"} · ${model.data.length} group${
        model.data.length === 1 ? "" : "s"
      }`
    : `${data.total_rows.toLocaleString()} row${data.total_rows === 1 ? "" : "s"}${
        data.truncated ? ` · showing ${data.returned_rows.toLocaleString()}` : ""
      }`;

  return (
    <figure className="td-card" data-testid={`chart-card-${data.call_id}`}>
      <figcaption className="td-card-head">
        <div className="td-card-titles">
          <span className="td-card-title">{title}</span>
          {spec.subtitle ? <span className="td-card-sub">{spec.subtitle}</span> : null}
        </div>
        <span className="td-card-meta" title="Rows behind this chart">
          {meta}
        </span>
      </figcaption>
      {model.empty ? (
        <div className="td-empty">{model.empty}</div>
      ) : (
        <div
          className="td-chart"
          role="img"
          aria-label={`${model.type} chart of ${model.valueKeys.join(", ")} by ${model.xKey}, ${data.returned_rows} rows`}
        >
          {isPie ? (
            <PieView
              model={model}
              p={p}
              height={height}
              format={format}
              colors={colors}
              showLegend={showLegend}
            />
          ) : model.type === "scatter" ? (
            <ScatterView
              model={model}
              p={p}
              height={height}
              format={format}
              showGrid={showGrid}
              showLegend={showLegend}
            />
          ) : (
            <>
              <CartesianView
                model={model}
                p={p}
                height={height}
                format={format}
                colors={colors}
                uid={uid}
                showGrid={showGrid}
              />
              {showLegend ? (
                <ChartLegend items={model.series.map((s) => ({ label: s.label, color: s.color }))} />
              ) : null}
            </>
          )}
        </div>
      )}
      {data.truncated ? (
        <div className="td-card-foot">
          Showing {data.returned_rows.toLocaleString()} of {data.total_rows.toLocaleString()} rows (cache limit).
        </div>
      ) : null}
      {diagnostics.length ? (
        <ul className="td-card-notes" aria-label="Chart notes">
          {diagnostics.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </figure>
  );
}
