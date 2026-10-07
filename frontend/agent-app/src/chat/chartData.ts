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
  showLegend: boolean;
  xLabel: string;
  yLabel: string;
  /** Every plotted value is a whole number, so the axis must not show fractions. */
  integerValues: boolean;
  /** Series keys drawn against the right-hand axis (empty on a single axis). */
  rightKeys: string[];
  rightYLabel: string;
  /** Per-axis whole-number flags, from the declared column types of the
   *  measures on each side — never inferred from value patterns. */
  leftIntegerValues: boolean;
  rightIntegerValues: boolean;
  /** Notes about how the rows were grouped, shown under the chart. */
  diagnostics: string[];
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
  return plottedValuesFor(model, model.valueKeys);
}

/** The plotted values of one side of a dual-axis chart, for its own domain. */
export function plottedValuesFor(model: ChartModel, keys: string[]): number[] {
  const pick = new Set(keys);
  const values: number[] = [];
  for (const row of model.data) {
    if (model.stacked) {
      let total = 0;
      for (const key of model.valueKeys) {
        if (!pick.has(key)) continue;
        total += asNumber(row[key]) ?? 0;
      }
      values.push(total);
    } else {
      for (const key of model.valueKeys) {
        if (!pick.has(key)) continue;
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
const KNOWN_TYPES: ChartType[] = [
  "line",
  "area",
  "bar",
  "hbar",
  "pie",
  "donut",
  "scatter",
  "stackedBar",
  "stackedArea",
];
/** The protocol's stacked shorthand maps to its base type plus `stacked`. */
const STACKED_ALIASES: Partial<Record<ChartType, ChartType>> = {
  stackedBar: "bar",
  stackedArea: "area",
};
/** Aggregations the server can write. ``none`` is only ever written for a
 *  scatter, where no grouping happens. */
const KNOWN_AGGREGATES: ChartSpec["aggregate"][] = ["sum", "avg", "count", "min", "max", "none"];
/** Types where one measure colors each bar/slice rather than the series. */
const CATEGORY_COLORED = new Set<ChartType>(["bar", "hbar", "pie", "donut"]);
const PIE_TYPES = new Set<ChartType>(["pie", "donut"]);

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
  // The model may name a single measure as a bare string; the protocol accepts
  // both, so normalise it here rather than iterating its characters.
  const yKeys = Array.isArray(spec.y) ? spec.y : typeof spec.y === "string" ? [spec.y] : [];
  // The server used to write these defaults into the spec. The client owns them
  // now, so a spec the model left implicit still renders as the protocol says.
  const type: ChartType = STACKED_ALIASES[spec.type] ?? spec.type;
  const stacked = Boolean(spec.stacked) || type !== spec.type;
  const colorBy =
    spec.colorBy ?? (yKeys.length === 1 && CATEGORY_COLORED.has(type) ? "category" : "series");
  const showLegend =
    spec.showLegend ?? (PIE_TYPES.has(type) || yKeys.length > 1 || Boolean(spec.series));
  const base: ChartModel = {
    data: [],
    xKey: spec.x,
    xType: byName.get(spec.x)?.type ?? "unknown",
    valueKeys: [],
    series: [],
    type,
    stacked,
    smooth: spec.smooth !== false,
    colorBy,
    showLegend,
    xLabel: spec.xLabel || spec.x,
    yLabel: spec.yLabel || (yKeys.length === 1 ? yKeys[0] : ""),
    integerValues: false,
    rightKeys: [],
    rightYLabel: "",
    leftIntegerValues: false,
    rightIntegerValues: false,
    diagnostics: [],
    empty: null,
  };
  // A spec the server would not have produced is a contract violation. Drawing
  // the nearest chart instead would be exactly the silent substitution the
  // validation exists to prevent.
  if (!KNOWN_TYPES.includes(spec.type)) {
    return { ...base, empty: `Unsupported chart type “${spec.type}”.` };
  }
  // A scatter plots one point per row and never aggregates, so its aggregation
  // is `none` by definition — a spec that omits it is still a scatter.
  const aggregate = type === "scatter" ? "none" : spec.aggregate;
  if (!KNOWN_AGGREGATES.includes(aggregate)) {
    return { ...base, empty: `Unsupported aggregation “${aggregate}”.` };
  }
  if (type !== "scatter" && aggregate === "none") {
    return { ...base, empty: "This chart asks for no aggregation on grouped data." };
  }
  if (type === "scatter" && spec.series) {
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
  for (const name of yKeys) {
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
  // A second axis is an explicit, server-validated subset of y on a
  // bar/line/area chart. Anything else arrives without one.
  const rightSet = new Set(
    (Array.isArray(spec.rightAxis) ? spec.rightAxis : []).filter((name) =>
      valueKeys.includes(name),
    ),
  );
  const dual =
    (type === "bar" || type === "line" || type === "area") &&
    !stacked &&
    rightSet.size > 0 &&
    rightSet.size < valueKeys.length;
  // Series keys on the right axis. The pivot branch below adds its
  // `series · measure` labels; plain measures match by column name.
  const rightLabels = new Set<string>(
    [...rightSet].filter((name) => valueKeys.includes(name)),
  );
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
        if (dual && rightSet.has(yKey)) rightLabels.add(label);
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
      for (const [label, bucket] of values) row[label] = aggregateValues(bucket, aggregate);
      return row;
    });
    keys = seriesValues;
  } else if (type === "scatter") {
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
      for (const key of valueKeys) out[key] = aggregateValues(entry.values[key] ?? [], aggregate);
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
    (type === "pie" || type === "donut") &&
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
  const rightKeys = dual ? keys.filter((key) => rightLabels.has(key)) : [];
  // Integer ticks come from the declared column types on each side — pivot
  // labels are looked up by their measure column, not the label text.
  const leftCols = dual ? valueColumns.filter((column) => !rightSet.has(column.name)) : valueColumns;
  const leftIntegerValues = leftCols.every((column) => column.type === "integer");
  const rightIntegerValues =
    dual &&
    valueColumns
      .filter((column) => rightSet.has(column.name))
      .every((column) => column.type === "integer");

  // The server used to disclose grouping in the spec's diagnostics. Restore it
  // here so the card still says when rows were combined.
  const diagnostics: string[] = [];
  if (type !== "scatter" && data.rows.length) {
    const cellKey = (row: ToolDataValue[], at: number) => `${typeof row[at]}:${String(row[at] ?? "")}`;
    const xIndex = columns.findIndex((column) => column.name === xColumn.name);
    const seriesIndex = seriesColumn
      ? columns.findIndex((column) => column.name === seriesColumn.name)
      : -1;
    const groups = new Set<string>();
    for (const row of data.rows) {
      groups.add(seriesIndex >= 0 ? `${cellKey(row, xIndex)}|${cellKey(row, seriesIndex)}` : cellKey(row, xIndex));
    }
    if (data.rows.length > groups.size) {
      diagnostics.push(
        seriesIndex >= 0
          ? `${data.rows.length} rows grouped into ${groups.size} x/series points using ${aggregate}`
          : `${data.rows.length} rows grouped into ${groups.size} categories using ${aggregate}`,
      );
    }
  }

  return {
    data: chartData,
    xKey: xColumn.name,
    xType: xColumn.type,
    valueKeys: keys,
    series,
    type,
    stacked,
    smooth: spec.smooth !== false,
    colorBy,
    showLegend,
    xLabel: spec.xLabel || xColumn.name,
    yLabel: spec.yLabel || (keys.length === 1 ? keys[0] : ""),
    integerValues: valueColumns.every((column) => column.type === "integer"),
    rightKeys,
    rightYLabel: dual ? spec.rightYLabel || "" : "",
    leftIntegerValues,
    rightIntegerValues,
    diagnostics,
    empty: null,
  };
}

/** Compact axis ticks: full precision belongs in tooltips, not on tick labels.
 *  Large values shorten (`$50K`), small ones keep their exact reading. */
export function axisFormatter(spec: ChartSpec): (value: number) => string {
  const full = valueFormatter(spec);
  const format = String(spec.valueFormat ?? "number").toLowerCase();
  if (format === "percent") return full;
  const compact = (value: number): string => {
    const abs = Math.abs(value);
    const short =
      abs >= 1_000_000_000
        ? `${(value / 1_000_000_000).toFixed(abs >= 10_000_000_000 ? 0 : 1)}B`
        : abs >= 1_000_000
          ? `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`
          : abs >= 1_000
            ? `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`
            : new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
    if (format === "currency") {
      const code = String(spec.currency || "USD").toUpperCase();
      const symbol =
        (() => {
          try {
            const parts = new Intl.NumberFormat(undefined, {
              style: "currency",
              currency: code,
              currencyDisplay: "narrowSymbol",
            }).formatToParts(0);
            return parts.find((part) => part.type === "currency")?.value ?? code;
          } catch {
            return code;
          }
        })();
      return `${symbol}${short}`;
    }
    return short;
  };
  return (value) => (Math.abs(value) >= 1000 ? compact(value) : full(value));
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
        currencyDisplay: "narrowSymbol",
        maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
      }).format(value);
  }
  return (value) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
}
