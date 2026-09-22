/** Wire + render types for tool-result data (charts and full tables). */

/** The declared type of a result column, decided by the tool that produced it. */
export type ColumnType =
  | "string"
  | "integer"
  | "number"
  /** An exact number carried as a string, so it is never rounded. */
  | "decimal"
  | "boolean"
  | "date"
  | "datetime"
  | "time"
  /** A column whose cells are not all one type; each cell keeps its own scalar. */
  | "mixed"
  | "unknown";

export interface ToolDataColumn {
  name: string;
  type: ColumnType;
}

/** A cell as it arrives: a native JSON scalar, never a stringified value. */
export type ToolDataValue = string | number | boolean | null;

/** One cached tool result, as the server sends it with the turn's final event. */
export interface ToolDataPayload {
  /** The short reference the model used (`D1`); the client keys on this. */
  call_id: string;
  /** The short reference, when present. */
  ref?: string;
  /** The provider's real tool call id (traceability only). */
  source_call_id?: string;
  tool_name: string;
  columns: ToolDataColumn[];
  rows: ToolDataValue[][];
  total_rows: number;
  returned_rows: number;
  truncated: boolean;
}

export type ChartType = "line" | "area" | "bar" | "hbar" | "pie" | "donut" | "scatter";

export type Aggregate = "sum" | "avg" | "count" | "min" | "max" | "none";

/**
 * A chart spec as the server validated and normalised it. Every field the
 * renderer depends on is explicit — the client never fills in a missing column,
 * chart type or aggregation, because that is how a chart ends up showing
 * something nobody asked for.
 */
export interface ChartSpec {
  type: ChartType;
  /** Category / time column. */
  x: string;
  /** One or more numeric columns. */
  y: string[];
  /** Column whose values split the rows into series. */
  series?: string;
  aggregate: Aggregate;
  sort: "asc" | "desc" | "x" | "none";
  limit?: number;
  title?: string;
  subtitle?: string;
  xLabel?: string;
  yLabel?: string;
  /** `half` pairs with the next half chart; default full width. */
  layout: "full" | "half";
  valueFormat: "number" | "compact" | "percent" | "currency";
  currency?: string;
  colorBy: "category" | "series" | "single";
  height: number;
  colors?: string[];
  smooth: boolean;
  showLegend: boolean;
  showGrid: boolean;
  stacked: boolean;
  /** Notes the server attached: how rows were grouped, whether data was clipped. */
  diagnostics?: string[];
  /** Set when the spec could not be drawn; the card shows this instead. */
  error?: string;
}

/** The optional ```table fence spec. */
export interface TableSpec {
  title?: string;
  pageLength?: number;
  layout?: "full" | "half";
  /** Set when the spec could not be rendered; the card shows this instead. */
  error?: string;
}

export interface RichMarkdownSegment {
  kind: "markdown";
  text: string;
}

export interface RichChartSegment {
  kind: "chart";
  callId: string;
  spec: ChartSpec;
}

export interface RichTableSegment {
  kind: "table";
  callId: string;
  spec: TableSpec;
}

export type RichSegment = RichMarkdownSegment | RichChartSegment | RichTableSegment;
export type RichBlock = RichChartSegment | RichTableSegment;

export function blockLayout(block: RichBlock): "full" | "half" {
  return block.spec.layout === "half" ? "half" : "full";
}
