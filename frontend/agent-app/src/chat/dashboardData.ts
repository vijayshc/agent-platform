/** Shared aggregation for dashboard blocks (KPI card, leaderboard, progress).
 *
 * The server has already checked that every column the spec names exists and
 * has a type that fits its role. This module only groups the declared values —
 * it never guesses a column or an aggregation.
 */
import type { Aggregate, ToolDataPayload, ToolDataValue } from "./toolDataTypes";

/** A declared numeric value. Strings are parsed only for `decimal` columns,
 *  which travel as exact strings; anything else non-numeric is a gap. */
export function cellNumber(value: ToolDataValue, type?: string): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && type === "decimal") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function aggregateNumbers(values: number[], mode: Aggregate): number | null {
  const finite = values.filter((v) => Number.isFinite(v));
  if (mode === "count") return finite.length;
  if (!finite.length) return null;
  switch (mode) {
    case "avg":
      return finite.reduce((a, b) => a + b, 0) / finite.length;
    case "min":
      return Math.min(...finite);
    case "max":
      return Math.max(...finite);
    default:
      return finite.reduce((a, b) => a + b, 0);
  }
}

/** Aggregate one column over all rows (KPI card / progress value). */
export function aggregateColumn(
  data: ToolDataPayload,
  column: string,
  mode: Aggregate,
): number | null {
  const index = data.columns.findIndex((c) => c.name === column);
  if (index < 0) return null;
  const type = data.columns[index]?.type;
  if (mode === "count") {
    let count = 0;
    for (const row of data.rows) if (row[index] !== null && row[index] !== undefined) count += 1;
    return count;
  }
  const values: number[] = [];
  for (const row of data.rows) {
    const v = cellNumber(row[index], type);
    if (v != null) values.push(v);
  }
  return aggregateNumbers(values, mode);
}

export interface RankedRow {
  label: string;
  value: number | null;
}

/** Top-N labels by one aggregated measure (leaderboard). */
export function topRanked(
  data: ToolDataPayload,
  label: string,
  value: string,
  mode: Aggregate,
  limit: number,
): RankedRow[] {
  const labelIndex = data.columns.findIndex((c) => c.name === label);
  const valueIndex = data.columns.findIndex((c) => c.name === value);
  if (labelIndex < 0 || valueIndex < 0) return [];
  const valueType = data.columns[valueIndex]?.type;
  const groups = new Map<string, number[]>();
  for (const row of data.rows) {
    const raw = row[labelIndex];
    const key = raw === null || raw === undefined || String(raw).trim() === "" ? "(blank)" : String(raw);
    const bucket = groups.get(key) ?? [];
    if (mode === "count") {
      if (row[valueIndex] !== null && row[valueIndex] !== undefined) bucket.push(1);
    } else {
      const v = cellNumber(row[valueIndex], valueType);
      if (v != null) bucket.push(v);
    }
    groups.set(key, bucket);
  }
  const rows: RankedRow[] = [...groups.entries()].map(([labelText, bucket]) => ({
    label: labelText,
    value: mode === "count" ? bucket.length : aggregateNumbers(bucket, mode),
  }));
  rows.sort((a, b) => (b.value ?? Number.NEGATIVE_INFINITY) - (a.value ?? Number.NEGATIVE_INFINITY));
  return rows.slice(0, Math.max(1, limit));
}

/** Group one measure by an x column for a sparkline. */
export function sparkPoints(
  data: ToolDataPayload,
  x: string,
  metric: string,
  mode: Aggregate,
): Array<{ x: string; value: number | null }> {
  const xIndex = data.columns.findIndex((c) => c.name === x);
  const mIndex = data.columns.findIndex((c) => c.name === metric);
  if (xIndex < 0 || mIndex < 0) return [];
  const mType = data.columns[mIndex]?.type;
  const groups = new Map<string, number[]>();
  const order: string[] = [];
  for (const row of data.rows) {
    const raw = row[xIndex];
    const key = raw === null || raw === undefined ? "(blank)" : String(raw);
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    const v = cellNumber(row[mIndex], mType);
    if (v != null) groups.get(key)!.push(v);
  }
  // ISO dates sort chronologically; other labels keep first-seen order capped.
  const isoDate = /^\d{4}-\d{2}-\d{2}/;
  const keys = order.length && order.every((k) => isoDate.test(k)) ? [...order].sort() : order;
  return keys.slice(0, 60).map((k) => ({ x: k, value: aggregateNumbers(groups.get(k) ?? [], mode) }));
}

export function formatValue(
  value: number | null,
  format: string,
  currency?: string,
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const kind = String(format ?? "number").toLowerCase();
  if (kind === "compact") {
    return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
  }
  if (kind === "percent") {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}%`;
  }
  if (kind === "currency") {
    const code = String(currency || "USD").toUpperCase();
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: code,
      currencyDisplay: "narrowSymbol",
      maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
    }).format(value);
  }
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}
