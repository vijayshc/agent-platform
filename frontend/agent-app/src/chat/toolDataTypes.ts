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

export type ChartType =
  | "line"
  | "area"
  | "bar"
  | "hbar"
  | "pie"
  | "donut"
  | "scatter"
  /** The protocol's shorthand for a stacked bar/area; the renderer maps it to
   *  its base type with `stacked: true`. */
  | "stackedBar"
  | "stackedArea";

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
  /** `half`/`third`/`quarter` share a row with neighbours; default full width. */
  layout: BlockLayout;
  valueFormat: "number" | "compact" | "percent" | "currency";
  currency?: string;
  /** y measures drawn against a right-hand axis with its own scale, for two
   *  measures on incomparable scales (order counts vs basket dollars). A
   *  strict non-empty subset of `y`; bar/line/area only, never stacked. */
  rightAxis?: string[];
  /** Axis label for the right-hand axis. */
  rightYLabel?: string;
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
  layout?: BlockLayout;
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

/** Narrow-block layout: full width, or shared rows of 2/3/4. */
export type BlockLayout = "full" | "half" | "third" | "quarter";

/** A KPI card spec as the server validated it. The value is computed from the
 *  cached rows — the model never sends a literal number. */
export interface CardSpec {
  metric: string;
  aggregate: Aggregate;
  title?: string;
  subtitle?: string;
  hint?: string;
  layout: BlockLayout;
  valueFormat: "number" | "compact" | "percent" | "currency";
  currency?: string;
  deltaMetric?: string;
  deltaAggregate?: Aggregate;
  /** Kebab-case icon name from the server's allowlist (e.g. "trending-up"). */
  icon?: string;
  /** Optional hex accent chosen by the model for the rail/sparkline. */
  color?: string;
  spark?: { x: string; type?: "line" | "area" | "bar" };
  diagnostics?: string[];
  error?: string;
}

/** A leaderboard spec: top-N labels by one aggregated measure. */
export interface ListSpec {
  label: string;
  value: string;
  aggregate: Aggregate;
  limit?: number;
  title?: string;
  subtitle?: string;
  layout: BlockLayout;
  valueFormat: "number" | "compact" | "percent" | "currency";
  currency?: string;
  color?: string;
  /** How the ranked rows are drawn: proportional bars, plain rows, or share. */
  variant?: "bars" | "plain" | "share";
  showShare?: boolean;
  diagnostics?: string[];
  error?: string;
}

/** Progress of one measure toward a literal target. */
export interface ProgressSpec {
  metric: string;
  aggregate: Aggregate;
  target: number;
  title?: string;
  subtitle?: string;
  layout: BlockLayout;
  valueFormat: "number" | "compact" | "percent" | "currency";
  currency?: string;
  color?: string;
  diagnostics?: string[];
  error?: string;
}

/** A free-text dashboard box. Carries no data reference. */
export interface NoteSpec {
  style?: "title" | "insight" | "info" | "warning" | "success";
  title?: string;
  body?: string;
  layout?: BlockLayout;
  error?: string;
}

export interface RichCardSegment {
  kind: "card";
  callId: string;
  spec: CardSpec;
}

export interface RichListSegment {
  kind: "list";
  callId: string;
  spec: ListSpec;
}

export interface RichProgressSegment {
  kind: "progress";
  callId: string;
  spec: ProgressSpec;
}

export interface RichNoteSegment {
  kind: "note";
  callId: string;
  spec: NoteSpec;
}

export type RichSegment =
  | RichMarkdownSegment
  | RichChartSegment
  | RichTableSegment
  | RichCardSegment
  | RichListSegment
  | RichProgressSegment
  | RichNoteSegment;
export type RichBlock =
  | RichChartSegment
  | RichTableSegment
  | RichCardSegment
  | RichListSegment
  | RichProgressSegment
  | RichNoteSegment;

export function blockLayout(block: RichBlock): BlockLayout {
  const layout = (block.spec as { layout?: string }).layout;
  return layout === "half" || layout === "third" || layout === "quarter" ? layout : "full";
}
