/** Wire + render types for tool-result data (charts and full tables). */

/** One cached tool result, as the server sends it with the turn's final event. */
export interface ToolDataPayload {
  /** The short reference the model used (`D1`); the client keys on this. */
  call_id: string;
  /** The short reference, when present. */
  ref?: string;
  /** The provider's real tool call id (traceability only). */
  source_call_id?: string;
  tool_name: string;
  columns: string[];
  rows: string[][];
  total_rows: number;
  returned_rows: number;
  truncated: boolean;
}

export type ChartType =
  | "line"
  | "area"
  | "bar"
  | "hbar"
  | "stackedBar"
  | "stackedArea"
  | "pie"
  | "donut"
  | "scatter";

/** The chart description the model writes inside a ```chart fence. */
export interface ChartSpec {
  type?: ChartType | string;
  /** Category / time column. */
  x?: string;
  /** One numeric column, or several for a multi-series chart. */
  y?: string | string[];
  /** Column whose values split the rows into series. */
  series?: string;
  aggregate?: "sum" | "avg" | "count" | "min" | "max";
  sort?: "asc" | "desc" | "x" | "none";
  limit?: number;
  title?: string;
  subtitle?: string;
  xLabel?: string;
  yLabel?: string;
  /** `half` pairs with the next half chart; default full width. */
  layout?: "full" | "half";
  valueFormat?: "number" | "compact" | "percent" | "currency";
  currency?: string;
  /** Colour strategy: one per bar/slice, one per series, or a single colour. */
  colorBy?: "category" | "series" | "single";
  height?: number;
  colors?: string[];
  smooth?: boolean;
  showLegend?: boolean;
  showGrid?: boolean;
  stacked?: boolean;
}

/** The optional ```table fence spec. */
export interface TableSpec {
  title?: string;
  pageLength?: number;
  layout?: "full" | "half";
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
