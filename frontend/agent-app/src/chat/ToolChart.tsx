import { useId, useMemo, useRef, useState } from "react";
import { Download, Info, Loader2 } from "lucide-react";
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
import { buildChartModel, niceDomain, plottedValues, plottedValuesFor, axisFormatter, valueFormatter, type ChartModel } from "./chartData";
import { downloadChartPng } from "./chartExport";
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
  axisFormat,
  showGrid,
  showLegend,
}: {
  model: ChartModel;
  p: ChartPalette;
  height: number;
  format: (value: number) => string;
  axisFormat: (value: number) => string;
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
            tickFormatter={(v) => axisFormat(Number(v))}
          />
          <YAxis
            type="number"
            dataKey="y"
            {...chartAxisProps(p)}
            tickFormatter={(v) => axisFormat(Number(v))}
            width={52}
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
  axisFormat,
  colors,
  uid,
  showGrid,
}: {
  model: ChartModel;
  p: ChartPalette;
  height: number;
  format: (value: number) => string;
  axisFormat: (value: number) => string;
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
  // A dual-axis chart sizes each side against its own measures: order counts
  // on the left and basket dollars on the right each get a readable scale
  // instead of sharing one domain that flattens the smaller to zero.
  const dual = !horizontal && !model.stacked && model.rightKeys.length > 0;
  const leftKeys = dual ? model.valueKeys.filter((key) => !model.rightKeys.includes(key)) : model.valueKeys;
  const [valueMin, valueMax] = niceDomain(dual ? plottedValuesFor(model, leftKeys) : plottedValues(model));
  const [rightMin, rightMax] = dual ? niceDomain(plottedValuesFor(model, model.rightKeys)) : [0, 1];
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
          tickFormatter: (v: number) => axisFormat(Number(v)),
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
    yAxisId: "left" as const,
    type: horizontal ? ("category" as const) : ("number" as const),
    dataKey: horizontal ? model.xKey : undefined,
    ...axisCommon(p, true),
    ...(horizontal
      ? { width: 140, tickFormatter: truncate, tick: { fill: p.text, fontSize: 12, fontWeight: 500 } }
      : {
          width: 52,
          tickFormatter: (v: number) => axisFormat(Number(v)),
          allowDecimals: dual ? !model.leftIntegerValues : !model.integerValues,
          domain: [valueMin, valueMax] as [number, number],
        }),
  };
  // The right axis shares the spec's single valueFormat; only its scale and
  // integer ticks come from its own measures' declared column types.
  const rightAxis = dual
    ? {
        yAxisId: "right" as const,
        type: "number" as const,
        orientation: "right" as const,
        ...axisCommon(p, true),
        width: 52,
        tickFormatter: (v: number) => axisFormat(Number(v)),
        allowDecimals: !model.rightIntegerValues,
        domain: [rightMin, rightMax] as [number, number],
      }
    : null;
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
        yAxisId={model.rightKeys.includes(series.key) ? "right" : "left"}
        fill={perCategory ? colors[seriesIndex % colors.length] : `url(#${gradientId(series.key)})`}
        stackId={stackId}
        radius={horizontal ? [0, 7, 7, 0] : [7, 7, 0, 0]}
        maxBarSize={horizontal ? 24 : 48}
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
        yAxisId={model.rightKeys.includes(series.key) ? "right" : "left"}
        stroke={series.color}
        strokeWidth={3}
        strokeLinecap="round"
        strokeLinejoin="round"
        fill={`url(#${gradientId(series.key)}-wash)`}
        fillOpacity={model.stacked ? 0.6 : 1}
        stackId={stackId}
        dot={
          model.data.length <= 40
            ? { r: 3, strokeWidth: 2, stroke: p.card, fill: series.color }
            : false
        }
        activeDot={{ r: 5, strokeWidth: 2, stroke: p.card, fill: series.color }}
        isAnimationActive={model.data.length <= 200}
      />
    ) : (
      <Line
        key={series.key}
        type={model.smooth ? "monotone" : "linear"}
        dataKey={series.key}
        name={seriesName(series.label)}
        yAxisId={model.rightKeys.includes(series.key) ? "right" : "left"}
        stroke={series.color}
        strokeWidth={3}
        strokeLinecap="round"
        strokeLinejoin="round"
        dot={
          model.data.length <= 40
            ? { r: 3, strokeWidth: 2, stroke: p.card, fill: series.color }
            : false
        }
        activeDot={{ r: 5, strokeWidth: 2, stroke: p.card, fill: series.color }}
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
      {dual && model.rightYLabel ? (
        <span className="td-axis-y td-axis-y-right">{model.rightYLabel}</span>
      ) : null}
      <ResponsiveContainer width="100%" height={height}>
        <Chart
          data={model.data}
          layout={horizontal ? "vertical" : "horizontal"}
          margin={{ top: showValues && !horizontal ? 20 : 8, right: dual ? 60 : 28, bottom: 4, left: 4 }}
        >
          <defs>
            {model.series.map((series) => (
              <linearGradient key={series.key} id={gradientId(series.key)} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={series.color} stopOpacity={0.95} />
                <stop offset="100%" stopColor={series.color} stopOpacity={0.55} />
              </linearGradient>
            ))}
            {model.series.map((series) => (
              <linearGradient
                key={`wash-${series.key}`}
                id={`${gradientId(series.key)}-wash`}
                x1="0"
                y1="0"
                x2="0"
                y2="1"
              >
                <stop offset="0%" stopColor={series.color} stopOpacity={0.28} />
                <stop offset="100%" stopColor={series.color} stopOpacity={0.03} />
              </linearGradient>
            ))}
          </defs>
          {showGrid ? <CartesianGrid {...grid} vertical={horizontal} horizontal={!horizontal} /> : null}
          <XAxis {...xAxis} />
          <YAxis {...yAxis} />
          {rightAxis ? <YAxis {...rightAxis} /> : null}
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
  const axisFormat = useMemo(() => axisFormatter(spec), [spec]);
  const height = Math.min(720, Math.max(180, Number(spec.height) || 300));
  const colors = spec.colors && spec.colors.length ? spec.colors : p.series;
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const title = spec.title || `${model.type} · ${data.tool_name}`;
  const isPie = model.type === "pie" || model.type === "donut";
  const cardRef = useRef<HTMLElement>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  // The model resolved `showLegend`/`colorBy` (with the protocol's defaults), so
  // the card reads them from the model rather than the raw spec.
  const showLegend = model.showLegend;
  const showGrid = spec.showGrid !== false;
  const diagnostics = model.diagnostics;

  // The card is captured as rendered, so the download carries the same title,
  // subtitle, axis labels and legend the reader sees on screen.
  async function handleDownload() {
    const node = cardRef.current;
    if (!node || exporting) return;
    setExporting(true);
    setExportError(null);
    try {
      await downloadChartPng(node, title);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "The chart could not be downloaded.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <figure
      className="td-card td-card-chart"
      ref={cardRef}
      data-layout={spec.layout || "full"}
      data-testid={`chart-card-${data.call_id}`}
    >
      <div className="td-flat-head">
        <div className="td-card-titles">
          <span className="td-flat-title">{title}</span>
          {spec.subtitle ? <span className="td-card-sub">{spec.subtitle}</span> : null}
        </div>
        {model.empty ? null : (
          <div className="td-card-tools">
            {diagnostics.length ? (
              <span
                className="td-card-note-icon"
                title={diagnostics.join("\n")}
                aria-label={diagnostics.join(" ")}
              >
                <Info size={14} strokeWidth={1.9} />
              </span>
            ) : null}
            <button
              type="button"
              className="td-card-download"
              data-export-skip="true"
              data-testid={`chart-download-${data.call_id}`}
              title="Download chart as PNG"
              aria-label="Download chart as PNG"
              disabled={exporting}
              onClick={handleDownload}
            >
              {exporting ? (
                <Loader2 size={14} strokeWidth={2} className="td-spin" />
              ) : (
                <Download size={14} strokeWidth={1.9} />
              )}
            </button>
          </div>
        )}
      </div>
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
              axisFormat={axisFormat}
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
                axisFormat={axisFormat}
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
      {exportError ? (
        <div className="td-card-foot td-export-error" role="alert">
          {exportError}
        </div>
      ) : null}
    </figure>
  );
}
