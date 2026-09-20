/** Turn a cached markdown table + a model-authored spec into chart-ready data.
 *
 * The model names columns in prose; the cache holds raw strings. Everything
 * here is about being forgiving: match columns case-insensitively, coerce
 * "1,234", "12%", "(50)" and "$5" to numbers, aggregate duplicate categories,
 * and fall back to sensible defaults when the spec is vague.
 */
import type { ChartSpec, ChartType, ToolDataPayload } from "./toolDataTypes";

export interface ChartSeriesDef {
  key: string;
  label: string;
  color: string;
}

export interface ChartModel {
  data: Array<Record<string, string | number>>;
  xKey: string;
  valueKeys: string[];
  series: ChartSeriesDef[];
  type: ChartType;
  stacked: boolean;
  smooth: boolean;
  colorBy: "category" | "series" | "single";
  xLabel: string;
  yLabel: string;
  /** Human-readable reason the chart cannot be drawn, if any. */
  empty: string | null;
}

const DATE_RE = /^\d{4}[-/]\d{1,2}([-/]\d{1,2})?([T ]\d{1,2}:\d{2})?/;

function normalizeName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

export function resolveColumn(columns: string[], name: unknown): string | undefined {
  if (typeof name !== "string" || !name.trim()) return undefined;
  const wanted = name.trim();
  const exact = columns.find((c) => c === wanted);
  if (exact) return exact;
  const lower = columns.find((c) => c.toLowerCase() === wanted.toLowerCase());
  if (lower) return lower;
  const norm = normalizeName(wanted);
  return columns.find((c) => normalizeName(c) === norm);
}

export function toNumber(raw: unknown): number | null {
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (raw == null) return null;
  let text = String(raw).trim();
  if (!text) return null;
  let negative = false;
  if (/^\(.*\)$/.test(text)) {
    negative = true;
    text = text.slice(1, -1);
  }
  text = text.replace(/[$€£¥,\s]/g, "").replace(/%$/, "");
  if (!text) return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  return negative ? -value : value;
}

/** Round ``max`` up to a "nice" number a reader can tick evenly.
 *
 * A count axis whose values are all ``1`` must not become ``0…4``: that is what
 * a fixed tick count plus whole-number ticks produces, and it leaves every bar
 * squat against the baseline. Choosing the ceiling from the data keeps the plot
 * filled while still landing on 0/2/4/8-style ticks. */
function niceCeil(max: number, tickCount = 5): number {
  if (!Number.isFinite(max) || max <= 0) return 1;
  const rough = max / tickCount;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10) * magnitude;
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
      for (const key of model.valueKeys) total += toNumber(row[key]) ?? 0;
      values.push(total);
    } else {
      for (const key of model.valueKeys) {
        const value = toNumber(row[key]);
        if (value != null) values.push(value);
      }
    }
  }
  return values;
}

const IDENTIFIER_TOKEN =
  /(^|[^a-z0-9])(id|uuid|guid|key|code|zip|postal|phone|ssn|isbn|ref|reference|year|yr|quarter|qtr|month|mon|week|wk|day)($|[^a-z0-9])/i;
const IDENTIFIER_SUFFIX = /(?:Id|ID|Uuid|UUID|Guid|GUID|Key|Code|Ref|Year|Month|Week|Day)$/;
const IDENTIFIER_EXACT = /^(id|uuid|guid|zip|zipcode|postalcode|phone|ssn|isbn)$/i;

/** A number that is not a measure: an identifier (`order_id`, `zipcode`) or a
 *  calendar part (`year`, `month`). Charting one by default would plot a key or
 *  a date component as if it were a quantity; an explicit `y` still plots it. */
export function looksLikeIdentifier(name: string): boolean {
  const trimmed = name.trim();
  if (!trimmed) return true;
  return (
    IDENTIFIER_TOKEN.test(trimmed) ||
    IDENTIFIER_SUFFIX.test(trimmed) ||
    IDENTIFIER_EXACT.test(trimmed.replace(/[^a-z0-9]/gi, ""))
  );
}

function numericColumns(columns: string[], rows: string[][]): string[] {
  return columns.filter((column, index) => {
    if (looksLikeIdentifier(column)) return false;
    let seen = 0;
    let numeric = 0;
    for (const row of rows) {
      const cell = row[index];
      if (cell == null || !String(cell).trim()) continue;
      seen += 1;
      if (toNumber(cell) !== null) numeric += 1;
    }
    return seen > 0 && numeric / seen >= 0.6;
  });
}

function isTemporal(values: string[]): boolean {
  const sample = values.filter((v) => String(v).trim()).slice(0, 30);
  if (!sample.length) return false;
  const hits = sample.filter((v) => DATE_RE.test(String(v).trim())).length;
  return hits / sample.length >= 0.7;
}

function asStringList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v));
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

function aggregateValues(values: number[], mode: string | undefined): number {
  if (!values.length) return 0;
  switch (mode) {
    case "avg":
      return values.reduce((a, b) => a + b, 0) / values.length;
    case "count":
      return values.length;
    case "min":
      return Math.min(...values);
    case "max":
      return Math.max(...values);
    default:
      return values.reduce((a, b) => a + b, 0);
  }
}

function normalizeType(raw: string | undefined, model: { series: ChartSeriesDef[]; xValues: string[] }): ChartType {
  const value = String(raw ?? "").toLowerCase();
  if (value === "stackedbar") return "stackedBar";
  if (value === "stackedarea") return "stackedArea";
  if (["line", "area", "bar", "hbar", "pie", "donut", "scatter"].includes(value)) {
    return value as ChartType;
  }
  if (model.series.length > 1) return "line";
  return isTemporal(model.xValues) ? "line" : "bar";
}

export function buildChartModel(data: ToolDataPayload, spec: ChartSpec, palette: string[]): ChartModel {
  const base: ChartModel = {
    data: [],
    xKey: "",
    valueKeys: [],
    series: [],
    type: "bar",
    stacked: Boolean(spec.stacked),
    smooth: spec.smooth !== false,
    colorBy: "category",
    xLabel: "",
    yLabel: "",
    empty: null,
  };
  const columns = data.columns ?? [];
  const rows = data.rows ?? [];
  if (!columns.length || !rows.length) {
    return { ...base, empty: "This result has no rows to chart." };
  }

  const numeric = numericColumns(columns, rows);
  const requestedX = typeof spec.x === "string" && spec.x.trim() ? spec.x.trim() : "";
  const resolvedX = resolveColumn(columns, requestedX);
  if (requestedX && !resolvedX) {
    // A named column that does not exist is a spec error, not a licence to
    // chart a different one: falling back would draw the wrong axis.
    return {
      ...base,
      empty: `The x column “${requestedX}” is not in this result (${columns.join(", ")}).`,
    };
  }
  const xKey = resolvedX || columns.find((c) => !numeric.includes(c)) || columns[0];
  const xIndex = columns.indexOf(xKey);
  const seriesKey = resolveColumn(columns, spec.series) || null;

  const requestedY = asStringList(spec.y);
  let valueKeys = requestedY
    .map((name) => resolveColumn(columns, name))
    .filter((name): name is string => Boolean(name) && name !== xKey && name !== seriesKey);
  if (requestedY.length && !valueKeys.length) {
    return {
      ...base,
      xKey,
      empty: `None of the requested y columns (${requestedY.join(", ")}) can be plotted from this result.`,
    };
  }
  if (!valueKeys.length) {
    valueKeys = numeric.filter((c) => c !== xKey && c !== seriesKey);
  }
  if (!valueKeys.length) {
    return { ...base, xKey, empty: "No numeric column was found to plot." };
  }

  const records: Array<Record<string, string>> = rows.map((row) => {
    const record: Record<string, string> = {};
    columns.forEach((column, index) => {
      record[column] = row[index] ?? "";
    });
    record[xKey] = String(row[xIndex] ?? "");
    return record;
  });

  const xValues = records.map((record) => record[xKey]);
  const scatter = String(spec.type ?? "").toLowerCase() === "scatter";
  let chartData: Array<Record<string, string | number>>;
  let keys = valueKeys;

  if (seriesKey) {
    // Pivot: one property per distinct value of the series column. When more
    // than one measure is asked for, each (series value × measure) pair becomes
    // its own series, so no measure is silently dropped.
    const seriesValues: string[] = [];
    const labelFor = (seriesValue: string, yKey: string) =>
      valueKeys.length > 1 ? `${seriesValue} · ${yKey}` : seriesValue;
    const byX = new Map<
      string,
      { row: Record<string, string | number>; values: Map<string, number[]> }
    >();
    for (const record of records) {
      const seriesValue = String(record[seriesKey] ?? "").trim() || "(blank)";
      const x = record[xKey];
      const entry = byX.get(x) ?? { row: { [xKey]: x }, values: new Map<string, number[]>() };
      for (const yKey of valueKeys) {
        const label = labelFor(seriesValue, yKey);
        if (!seriesValues.includes(label)) seriesValues.push(label);
        const value = toNumber(record[yKey]);
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
      for (const [label, bucket] of values) {
        row[label] = aggregateValues(bucket, spec.aggregate);
      }
      return row;
    });
    keys = seriesValues;
  } else if (scatter) {
    // A scatter is one point per row: combining rows that share an x would
    // erase exactly the spread the chart exists to show. A point needs both a
    // numeric x and a numeric y, so a missing cell is omitted rather than
    // plotted as zero.
    chartData = records.map((record) => {
      const out: Record<string, string | number> = { [xKey]: record[xKey] };
      for (const key of valueKeys) {
        const value = toNumber(record[key]);
        if (value != null) out[key] = value;
      }
      return out;
    });
    const hasPoint = chartData.some(
      (row) => toNumber(row[xKey]) != null && valueKeys.some((key) => toNumber(row[key]) != null),
    );
    if (!hasPoint) {
      return {
        ...base,
        xKey,
        valueKeys,
        empty: `A scatter chart needs a numeric x and y; “${xKey}” vs ${valueKeys.join(", ")} has no numeric points.`,
      };
    }
  } else {
    const byX = new Map<string, { x: string; values: Record<string, number[]> }>();
    for (const record of records) {
      const x = record[xKey];
      const entry = byX.get(x) ?? { x, values: {} };
      for (const key of valueKeys) {
        const value = toNumber(record[key]);
        if (value == null) continue;
        (entry.values[key] = entry.values[key] ?? []).push(value);
      }
      byX.set(x, entry);
    }
    chartData = [...byX.values()].map((entry) => {
      const out: Record<string, string | number> = { [xKey]: entry.x };
      for (const key of valueKeys) {
        out[key] = aggregateValues(entry.values[key] ?? [], spec.aggregate);
      }
      return out;
    });
  }

  const type = normalizeType(spec.type, {
    series: keys.map((key) => ({ key, label: key, color: "" })),
    xValues,
  });
  const stacked = Boolean(spec.stacked) || type === "stackedBar" || type === "stackedArea";
  const finalType: ChartType = type === "stackedBar" ? "bar" : type === "stackedArea" ? "area" : type;
  if (finalType === "pie" || finalType === "donut") {
    // A pie is one measure split across the x categories; several measures or a
    // series column has no single honest rendering.
    if (requestedY.length > 1 || seriesKey) {
      return {
        ...base,
        xKey,
        valueKeys: keys,
        empty: "A pie chart needs a single measure and no series column.",
      };
    }
    if (keys.length > 1) keys = keys.slice(0, 1);
  }

  const order = String(spec.sort ?? "").toLowerCase();
  const primary = keys[0];
  const byValue =
    (dir: 1 | -1) =>
    (a: Record<string, string | number>, b: Record<string, string | number>) =>
      dir * ((Number(a[primary]) || 0) - (Number(b[primary]) || 0));
  const limit = Math.floor(Number(spec.limit) || 0);
  if (limit > 0 && chartData.length > limit) {
    // `limit` selects the top N by value; the requested display order is then
    // applied to that selection instead of being silently discarded.
    chartData.sort(byValue(-1));
    chartData = chartData.slice(0, limit);
  }
  if (order === "asc") chartData.sort(byValue(1));
  else if (order === "desc") chartData.sort(byValue(-1));
  else if (order === "x") chartData.sort((a, b) => String(a[xKey]).localeCompare(String(b[xKey])));

  if (
    (finalType === "pie" || finalType === "donut") &&
    !chartData.some((row) => (Number(row[primary]) || 0) !== 0)
  ) {
    return { ...base, xKey, valueKeys: keys, empty: "This result has no non-zero values to chart." };
  }

  const colors = spec.colors && spec.colors.length ? spec.colors : palette;
  const series: ChartSeriesDef[] = keys.map((key, index) => ({
    key,
    label: key,
    color: colors[index % colors.length],
  }));
  const requestedColorBy = String(spec.colorBy ?? "").toLowerCase();
  const colorBy: ChartModel["colorBy"] =
    requestedColorBy === "category" || requestedColorBy === "series" || requestedColorBy === "single"
      ? requestedColorBy
      : keys.length === 1 && ["bar", "hbar", "pie", "donut"].includes(finalType)
        ? "category"
        : "series";

  return {
    data: chartData,
    xKey,
    valueKeys: keys,
    series,
    type: finalType,
    stacked,
    smooth: spec.smooth !== false,
    colorBy,
    xLabel: spec.xLabel || xKey,
    yLabel: spec.yLabel || (keys.length === 1 ? keys[0] : ""),
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
