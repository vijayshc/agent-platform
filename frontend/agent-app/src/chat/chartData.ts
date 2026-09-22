/** Turn a typed cached table + a validated spec into chart-ready data.
 *
 * The server has already checked that every column the spec names exists and
 * has a type that fits its role, and has filled in every default. This module
 * therefore does exactly one thing: group and aggregate the declared values for
 * the plot. It never guesses a column, a type, a chart type or an aggregation —
 * a chart that shows something other than what was asked for is worse than a
 * chart that says it cannot be drawn.
 */
import type {
  ChartSpec,
  ChartType,
  ColumnType,
  ToolDataColumn,
  ToolDataPayload,
  ToolDataValue,
} from "./toolDataTypes";

export interface ChartSeriesDef {
  key: string;
  label: string;
  color: string;
}

export interface ChartModel {
  data: Array<Record<string, ToolDataValue>>;
  xKey: string;
  xType: ColumnType;
  valueKeys: string[];
  series: ChartSeriesDef[];
  type: ChartType;
  stacked: boolean;
  smooth: boolean;
  colorBy: "category" | "series" | "single";
  xLabel: string;
  yLabel: string;
  /** Every plotted value is a whole number, so the axis must not show fractions. */
  integerValues: boolean;
  /** Human-readable reason the chart cannot be drawn, if any. */
  empty: string | null;
}

/** A declared numeric value. A string is never coerced: the tool said what it is. */
function asNumber(value: ToolDataValue | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Round ``max`` up to a "nice" number a reader can tick evenly. */
function niceCeil(max: number, tickCount = 5): number {
  if (!Number.isFinite(max) || max <= 0) return 1;
  const rough = max / tickCount;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step =
    (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10) *
    magnitude;
  return Math.ceil(max / step) * step;
}

/** The numeric-axis domain that fits ``values``, with zero as the baseline. */
export function niceDomain(values: number[]): [number, number] {
  let max = 0;
  let min = 0;
  for (const value of values) {
    if (!Number.isFinite(value)) continue;
    if (value > max) max = value;
    if (value < min) min = value;
  }
  const top = niceCeil(max);
  return [min < 0 ? -niceCeil(-min) : 0, top];
}

/** Every value a chart plots. A stacked chart plots the row total, not the
 *  individual segments, so its axis has to be sized against the sums. */
export function plottedValues(model: ChartModel): number[] {
  const values: number[] = [];
  for (const row of model.data) {
    if (model.stacked) {
      let total = 0;
      for (const key of model.valueKeys) total += asNumber(row[key]) ?? 0;
      values.push(total);
    } else {
      for (const key of model.valueKeys) {
        const value = asNumber(row[key]);
        if (value != null) values.push(value);
      }
    }
  }
  return values;
}

function aggregateValues(values: number[], mode: ChartSpec["aggregate"]): number | null {
  // A group with no value is a gap, not a zero: plotting 0 would state a fact the
  // data does not contain.
  if (!values.length) return null;
  switch (mode) {
    case "avg":
      return values.reduce((a, b) => a + b, 0) / values.length;
    case "count":
      return values.length;
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    case "none":
      return values[0];
    default:
      return values.reduce((a, b) => a + b, 0);
  }
}

/** Order two x values by the type the tool declared for the column. */
function compareX(a: ToolDataValue | undefined, b: ToolDataValue | undefined, type: ColumnType): number {
  if (type === "date" || type === "datetime") {
    const left = Date.parse(String(a ?? ""));
    const right = Date.parse(String(b ?? ""));
    if (!Number.isNaN(left) && !Number.isNaN(right)) return left - right;
  }
  if (type === "integer" || type === "number" || type === "decimal") {
    const left = asNumber(a);
    const right = asNumber(b);
    if (left != null && right != null) return left - right;
  }
  // ISO times and labels sort lexicographically.
  return String(a ?? "").localeCompare(String(b ?? ""));
}

/** The chart types the renderer knows how to draw. The server validates the
 *  spec against the same set, so anything else is a contract violation and is
 *  reported rather than drawn as some other chart. */
const KNOWN_TYPES: ChartType[] = ["line", "area", "bar", "hbar", "pie", "donut", "scatter"];
/** Aggregations the server can write. ``none`` is only ever written for a
 *  scatter, where no grouping happens. */
const KNOWN_AGGREGATES: ChartSpec["aggregate"][] = ["sum", "avg", "count", "min", "max", "none"];

/** A decimal cell for plotting: parsed to a float, because a plot is inherently
 *  approximate. The table keeps the exact string. */
function decimalToNumber(value: ToolDataValue): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function buildChartModel(
  data: ToolDataPayload,
  spec: ChartSpec,
  palette: string[],
): ChartModel {
  const columns = data.columns ?? [];
  const byName = new Map(columns.map((column) => [column.name, column]));
  const base: ChartModel = {
    data: [],
    xKey: spec.x,
    xType: byName.get(spec.x)?.type ?? "unknown",
    valueKeys: [],
    series: [],
    type: spec.type,
    stacked: Boolean(spec.stacked),
    smooth: spec.smooth !== false,
    colorBy: spec.colorBy,
    xLabel: spec.xLabel || spec.x,
    yLabel: spec.yLabel || (spec.y.length === 1 ? spec.y[0] : ""),
    integerValues: false,
    empty: null,
  };
  // A spec the server would not have produced is a contract violation. Drawing
  // the nearest chart instead would be exactly the silent substitution the
  // validation exists to prevent.
  if (!KNOWN_TYPES.includes(spec.type)) {
    return { ...base, empty: `Unsupported chart type “${spec.type}”.` };
  }
  if (!KNOWN_AGGREGATES.includes(spec.aggregate)) {
    return { ...base, empty: `Unsupported aggregation “${spec.aggregate}”.` };
  }
  if (spec.type !== "scatter" && spec.aggregate === "none") {
    return { ...base, empty: "This chart asks for no aggregation on grouped data." };
  }
  if (spec.type === "scatter" && spec.series) {
    return { ...base, empty: "A scatter chart plots one point per row and cannot use a series." };
  }
  if (!columns.length || !data.rows.length) {
    return { ...base, empty: "This result has no rows to chart." };
  }

  const xColumn = byName.get(spec.x);
  if (!xColumn) {
    return { ...base, empty: `The x column “${spec.x}” is not in this result.` };
  }
  const valueColumns: ToolDataColumn[] = [];
  for (const name of spec.y) {
    const column = byName.get(name);
    if (!column) {
      return { ...base, empty: `The y column “${name}” is not in this result.` };
    }
    valueColumns.push(column);
  }
  const seriesColumn = spec.series ? byName.get(spec.series) : undefined;
  if (spec.series && !seriesColumn) {
    return { ...base, empty: `The series column “${spec.series}” is not in this result.` };
  }

  const valueKeys = valueColumns.map((column) => column.name);
  const records: Array<Record<string, ToolDataValue>> = data.rows.map((row) => {
    const record: Record<string, ToolDataValue> = {};
    columns.forEach((column, index) => {
      const cell = row[index] ?? null;
      // A decimal travels as an exact string; a plot needs a number.
      record[column.name] =
        column.type === "decimal" ? decimalToNumber(cell) : cell;
    });
    return record;
  });

  let chartData: Array<Record<string, ToolDataValue>>;
  let keys = valueKeys;

  if (seriesColumn) {
    // Pivot: one property per distinct value of the series column. With more
    // than one measure, each (series value × measure) pair is its own series, so
    // no measure is silently dropped.
    const seriesValues: string[] = [];
    const labelFor = (seriesValue: string, yKey: string) =>
      valueKeys.length > 1 ? `${seriesValue} · ${yKey}` : seriesValue;
    const byX = new Map<
      string,
      { row: Record<string, ToolDataValue>; values: Map<string, number[]> }
    >();
    for (const record of records) {
      const seriesValue = String(record[seriesColumn.name] ?? "").trim() || "(blank)";
      const rawX = record[xColumn.name] ?? null;
      // Keyed by type as well as text, matching the server's category count: a
      // mixed column holding 1 and "1" is two categories, not one.
      const x = `${typeof rawX}:${String(rawX ?? "")}`;
      const entry = byX.get(x) ?? { row: { [xColumn.name]: rawX }, values: new Map() };
      for (const yKey of valueKeys) {
        const label = labelFor(seriesValue, yKey);
        if (!seriesValues.includes(label)) seriesValues.push(label);
        const value = asNumber(record[yKey]);
        if (value == null) continue;
        const bucket = entry.values.get(label) ?? [];
        bucket.push(value);
        entry.values.set(label, bucket);
      }
      byX.set(x, entry);
    }
    chartData = [...byX.values()].map(({ row, values }) => {
      // Only series that actually have a value at this x are written: a missing
      // combination stays absent (a gap) instead of being invented as a zero.
      for (const [label, bucket] of values) row[label] = aggregateValues(bucket, spec.aggregate);
      return row;
    });
    keys = seriesValues;
  } else if (spec.type === "scatter") {
    // A scatter is one point per row: combining rows that share an x would erase
    // exactly the spread the chart exists to show.
    chartData = records.map((record) => {
      const out: Record<string, ToolDataValue> = { [xColumn.name]: record[xColumn.name] };
      for (const key of valueKeys) {
        const value = asNumber(record[key]);
        if (value != null) out[key] = value;
      }
      return out;
    });
    const hasPoint = chartData.some(
      (row) => asNumber(row[xColumn.name]) != null && valueKeys.some((key) => asNumber(row[key]) != null),
    );
    if (!hasPoint) {
      return {
        ...base,
        valueKeys,
        empty: `A scatter chart needs a numeric x and y; “${xColumn.name}” vs ${valueKeys.join(", ")} has no numeric points.`,
      };
    }
  } else {
    // The map is keyed by the x value's text so equal categories group, but the
    // row keeps the value as it arrived: a numeric or decimal x must stay a
    // number, or `sort: "x"` would order it as text ("100" before "9").
    const byX = new Map<string, { x: ToolDataValue; values: Record<string, number[]> }>();
    for (const record of records) {
      const rawX = record[xColumn.name] ?? null;
      // Type-keyed for the same reason as the series branch above.
      const x = `${typeof rawX}:${String(rawX ?? "")}`;
      const entry = byX.get(x) ?? { x: rawX, values: {} };
      for (const key of valueKeys) {
        const value = asNumber(record[key]);
        if (value == null) continue;
        (entry.values[key] = entry.values[key] ?? []).push(value);
      }
      byX.set(x, entry);
    }
    chartData = [...byX.values()].map((entry) => {
      const out: Record<string, ToolDataValue> = { [xColumn.name]: entry.x };
      for (const key of valueKeys) out[key] = aggregateValues(entry.values[key] ?? [], spec.aggregate);
      return out;
    });
  }

  const primary = keys[0];
  const byValue =
    (direction: 1 | -1) =>
    (a: Record<string, ToolDataValue>, b: Record<string, ToolDataValue>) =>
      direction * ((asNumber(a[primary]) ?? 0) - (asNumber(b[primary]) ?? 0));
  const limit = spec.limit ?? 0;
  if (limit > 0 && chartData.length > limit) {
    // `limit` selects the top N by value; the requested display order is then
    // applied to that selection instead of being silently discarded.
    chartData.sort(byValue(-1));
    chartData = chartData.slice(0, limit);
  }
  if (spec.sort === "asc") chartData.sort(byValue(1));
  else if (spec.sort === "desc") chartData.sort(byValue(-1));
  else if (spec.sort === "x") {
    chartData.sort((a, b) => compareX(a[xColumn.name], b[xColumn.name], xColumn.type));
  }

  if (
    (spec.type === "pie" || spec.type === "donut") &&
    !chartData.some((row) => (asNumber(row[primary]) ?? 0) !== 0)
  ) {
    return { ...base, valueKeys: keys, empty: "This result has no non-zero values to chart." };
  }

  const colors = spec.colors && spec.colors.length ? spec.colors : palette;
  const series: ChartSeriesDef[] = keys.map((key, index) => ({
    key,
    label: key,
    color: colors[index % colors.length],
  }));

  return {
    data: chartData,
    xKey: xColumn.name,
    xType: xColumn.type,
    valueKeys: keys,
    series,
    type: spec.type,
    stacked: Boolean(spec.stacked),
    smooth: spec.smooth !== false,
    colorBy: spec.colorBy,
    xLabel: spec.xLabel || xColumn.name,
    yLabel: spec.yLabel || (keys.length === 1 ? keys[0] : ""),
    integerValues: valueColumns.every((column) => column.type === "integer"),
    empty: null,
  };
}

/** A value formatter driven by the spec's `valueFormat`/`currency`. */
export function valueFormatter(spec: ChartSpec): (value: number) => string {
  const format = String(spec.valueFormat ?? "number").toLowerCase();
  if (format === "compact") {
    // Whole units only: axis ticks read "7K"/"20K" rather than "6.5K"/"19.5K".
    return (value) =>
      new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 0 }).format(value);
  }
  if (format === "percent") {
    return (value) => `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}%`;
  }
  if (format === "currency") {
    const currency = String(spec.currency || "USD").toUpperCase();
    return (value) =>
      new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
      }).format(value);
  }
  return (value) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
}
