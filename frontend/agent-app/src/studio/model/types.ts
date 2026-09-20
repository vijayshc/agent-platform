/** Agent Studio v2 — definition schema and capability-catalog types.
 *
 * Everything the editor renders is described by these types; the concrete
 * capability lists (runtimes, patterns, node kinds, middleware) always arrive
 * from `GET /api/v1/studio/catalog` and are never hardcoded in TS.
 *
 * Two shapes live side by side:
 *  * `NodeData` — camelCase canvas state, one object per node card.
 *  * `AgentSpec` / `GraphSpec` — the snake_case semantic layer saved in
 *    `config` (see docs/agent-studio-v2.md §1). `serialize.ts` is the only
 *    module allowed to translate between them.
 */
import type { Edge, Node, Viewport } from "@xyflow/react";
import type { StudioResources } from "../../types";

/* ------------------------------------------------------------------ catalog */

/** UI schema for one configurable value, rendered generically by Fields.tsx. */
export interface CatalogField {
  name: string;
  label: string;
  type: "text" | "textarea" | "number" | "boolean" | "select" | "tags" | "json" | "keyvalue" | "routes";
  options?: (string | number | boolean)[];
  help?: string;
  default?: unknown;
  group?: string;
}

export interface RuntimeFeatures {
  tools?: boolean;
  skills?: boolean;
  middleware?: boolean;
  structured_output?: boolean;
  subagents?: boolean;
  virtual_filesystem?: boolean;
  memory?: boolean;
  permissions?: boolean;
}

export interface CatalogRuntime {
  id: string;
  label: string;
  builder: string;
  summary: string;
  when?: string;
  icon?: string;
  docs?: string;
  features?: RuntimeFeatures;
}

export interface CatalogPattern {
  id: string;
  label: string;
  template: string;
  builder: string;
  summary: string;
  when?: string;
  icon?: string;
  docs?: string;
  min_agents?: number;
}

export interface CatalogNodeKind {
  id: string;
  label: string;
  summary?: string;
  icon?: string;
  builder: string;
  fields: CatalogField[];
}

export interface CatalogMiddleware {
  id: string;
  label: string;
  /** Fully qualified library class; `builder` mirrors it for provenance badges. */
  class: string;
  builder?: string;
  summary?: string;
  icon?: string;
  docs?: string;
  fields: CatalogField[];
}

/** A blueprint node as shipped by the registry (stable `key`, no ids yet). */
export interface TemplateNode {
  key: string;
  kind: string;
  label: string;
  agent?: Record<string, unknown>;
  routes?: { name: string; description?: string; to?: string }[];
  default?: string;
  strategy?: string;
  over?: string;
  to?: string;
  message?: string;
  ref?: string;
  values?: Record<string, unknown>;
  model?: Record<string, unknown>;
}

export interface CatalogTemplate {
  pattern: string;
  nodes: TemplateNode[];
  edges: { from: string; to: string; when?: string; map?: { over?: string; to?: string } }[];
  entry: string;
}

export interface StudioCatalog {
  schema: number;
  docs: Record<string, string>;
  runtimes: CatalogRuntime[];
  patterns: CatalogPattern[];
  node_kinds: CatalogNodeKind[];
  middleware: CatalogMiddleware[];
  templates: Record<string, CatalogTemplate>;
  resources: StudioResources;
  loadedAt: number;
}

/* --------------------------------------------------------------- canvas data */

/** Per-tool handling of large tabular results (charts / full tables). */
export interface ToolDataSetting {
  /** Send only a sample of the rows to the model; keep the rest aside. */
  sample: boolean;
  /** Rows sent to the model when sampling is on. */
  sampleRows: number;
  /** Rows kept in the intermediate cache; 0 means every row. */
  cacheRows: number;
}

export interface McpBinding {
  serverId: number | null;
  serverName: string;
  tools: string[];
  approval: string[];
  /** Data settings keyed by tool name; absent for tools the author left alone. */
  data?: Record<string, ToolDataSetting>;
}

export interface ResponseFormat {
  strategy: string;
  schema: unknown;
}

export interface DeepSubagent {
  name: string;
  description: string;
  prompt: string;
  tools: string[];
  model: string;
}

export interface DeepPermission {
  operations: string[];
  paths: string[];
  mode: string;
}

export interface DeepAgentSpec {
  subagents: DeepSubagent[];
  memory: string[];
  permissions: DeepPermission[];
  interruptOn: Record<string, boolean>;
}

export interface RouterRoute {
  name: string;
  description: string;
  to: string;
}

/** One canvas node card. `paletteType` is a catalog id (runtime/pattern/kind). */
export interface NodeData extends Record<string, unknown> {
  paletteType: string;
  label: string;
  name?: string;
  instructions?: string;
  description?: string;
  runtime?: string;
  modelClient?: string;
  modelName?: string;
  temperature?: number;
  maxTokens?: number;
  maxContextWindowTokens?: number;
  mcpBindings?: McpBinding[];
  skillIds?: string[];
  functionTools?: string[];
  middleware?: Record<string, Record<string, unknown>>;
  responseFormat?: ResponseFormat | null;
  deepAgent?: DeepAgentSpec;
  /* building blocks */
  routes?: RouterRoute[];
  defaultRoute?: string;
  /** Router loop bound (`max_visits`): after N visits the first route is taken. */
  maxVisits?: number;
  /** Blueprint keys this canvas does not model, carried through verbatim so a
   *  gallery-created flow never loses an authored setting. */
  extra?: Record<string, unknown>;
  toolName?: string;
  strategy?: string;
  over?: string;
  to?: string;
  message?: string;
  ref?: string;
  values?: Record<string, unknown>;
  /* patterns */
  entry?: boolean;
  managerName?: string;
  managerInstructions?: string;
  outputMode?: string;
  parallelToolCalls?: boolean;
  startAgent?: string;
}

export type StudioNode = Node<NodeData>;
export type StudioEdge = Edge;

export interface GraphMeta {
  name: string;
  slug: string;
  description: string;
  /** Blueprint provenance for graph flows: custom | sequential | … */
  template: string;
  modelClient: string;
  modelName: string;
}

/* ---------------------------------------------------- semantic (snake_case) */

export interface AgentSpec {
  id?: string;
  ref?: string;
  name?: string;
  instructions?: string;
  description?: string;
  runtime?: string;
  model?: { client?: string; name?: string | null };
  default_options?: { temperature?: number; max_tokens?: number };
  maxContextWindowTokens?: number;
  middleware?: Record<string, Record<string, unknown>>;
  response_format?: ResponseFormat | null;
  deep_agent?: {
    skills?: string[];
    memory?: string[];
    permissions?: DeepPermission[];
    subagents?: DeepSubagent[];
    interrupt_on?: Record<string, boolean>;
  };
  mcp_bindings?: {
    server_id?: number;
    server?: string;
    tools?: string[];
    approval?: string[];
    tool_data?: Record<string, { sample?: boolean; sample_rows?: number; cache_rows?: number }>;
  }[];
  maf_skill_ids?: string[];
  function_tools?: string[];
}

/** `config.graph` — the declarative graph spec (docs/agent-studio-v2.md §1). */
export interface GraphSpec {
  entry: string;
  nodes: Record<string, unknown>[];
  edges: { from: string; to: string; when?: string; map?: { over: string; to: string } }[];
}

export interface DefinitionConfig extends Record<string, unknown> {
  schema: number;
  kind: string;
  description?: string;
  model?: { client?: string; name?: string | null };
  studio?: {
    nodes: { id: string; type?: string; position: { x: number; y: number }; data: NodeData }[];
    edges: { id: string; source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }[];
    viewport?: Viewport;
  };
}

export interface DefinitionBody {
  name: string;
  slug: string;
  kind: "agent" | "workflow";
  config: DefinitionConfig;
  autosave?: boolean;
}

export interface ValidateIssue {
  code: string;
  message: string;
}

export interface ValidateReport {
  ok: boolean;
  errors: ValidateIssue[];
  warnings: ValidateIssue[];
  compile?: { ok?: boolean; error?: string; [key: string]: unknown };
}

export interface PlanAgent {
  name?: string;
  builder?: string;
  runtime?: string;
  docs?: string;
  tools?: number;
  middleware?: string[];
  structured_output?: string;
  subagents?: string[];
  skills?: string[];
  memory?: string[];
  permissions?: number;
}

export interface PlanNode {
  id?: string;
  kind?: string;
  label?: string;
  builder?: string;
  agent?: PlanAgent;
  error?: string;
}

export interface CompilePlan {
  ok?: boolean;
  kind: "agent" | "workflow";
  pattern?: string;
  template?: string;
  error?: string;
  plan: {
    name?: string;
    builder?: string;
    docs?: string;
    runtime?: string;
    tools?: number;
    middleware?: string[];
    structured_output?: string;
    subagents?: string[];
    skills?: string[];
    memory?: string[];
    permissions?: number;
    participants?: PlanAgent[];
    manager?: PlanAgent;
    start_agent?: string;
    handoffs?: { from: string; to: string }[];
    nodes?: PlanNode[];
    entry?: string;
  };
}

/* -------------------------------------------------------------------- helpers */

export function emptyDeepAgent(): DeepAgentSpec {
  return { subagents: [], memory: [], permissions: [], interruptOn: {} };
}

/** True for the agent runtimes (`agent`, `deep_agent`) — ids come from the catalog. */
export const AGENT_PALETTE_TYPES = ["agent", "deep_agent"];

export function isAgentType(paletteType: string): boolean {
  return AGENT_PALETTE_TYPES.includes(paletteType);
}

export function isPatternType(paletteType: string): boolean {
  return paletteType === "supervisor" || paletteType === "swarm" || paletteType === "graph";
}
