/** Canvas ⇄ definition config. The only module that knows the mapping.
 *
 * `config.studio` is the visual layer (xyflow nodes/edges); `participants`,
 * `manager`, `graph` and the agent-level keys are the semantic layer that the
 * compiler reads. Every save regenerates the semantic layer from the canvas, so
 * the two can never drift (docs/agent-studio-v2.md §0.5).
 */
import type { Viewport } from "@xyflow/react";
import {
  emptyDeepAgent,
  isAgentType,
  isPatternType,
  type AgentSpec,
  type DeepPermission,
  type DeepSubagent,
  type DefinitionConfig,
  type GraphMeta,
  type GraphSpec,
  type McpBinding,
  type NodeData,
  type RouterRoute,
  type StudioEdge,
  type StudioNode,
} from "./types";

export interface SerializedGraph {
  kind: "agent" | "workflow";
  config: DefinitionConfig;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function uniq(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function byPosition(a: StudioNode, b: StudioNode): number {
  return a.position.y - b.position.y || a.position.x - b.position.x;
}

export function runtimeOf(data: NodeData): string {
  if (data.runtime) return data.runtime === "harness" ? "deep_agent" : data.runtime;
  return data.paletteType === "deep_agent" ? "deep_agent" : "agent";
}

/* ------------------------------------------------------------ agent spec out */

function compiledMcpBindings(data: NodeData): AgentSpec["mcp_bindings"] {
  const rows: NonNullable<AgentSpec["mcp_bindings"]> = [];
  for (const binding of data.mcpBindings ?? []) {
    const tools = binding.tools ?? [];
    if (binding.serverId == null && !binding.serverName && !tools.length) continue;
    rows.push({
      ...(binding.serverId != null ? { server_id: binding.serverId } : {}),
      ...(binding.serverName ? { server: binding.serverName } : {}),
      tools,
      approval: binding.approval ?? [],
    });
  }
  return rows.length ? rows : undefined;
}

function compiledDeepAgent(data: NodeData): AgentSpec["deep_agent"] | undefined {
  if (runtimeOf(data) !== "deep_agent") return undefined;
  const spec = data.deepAgent ?? emptyDeepAgent();
  const subagents = (spec.subagents ?? []).filter((s) => s.name.trim());
  const memories = (spec.memory ?? []).filter(Boolean);
  const permissions = (spec.permissions ?? []).filter((p) => p.operations.length || p.paths.length);
  const interrupt = Object.fromEntries(Object.entries(spec.interruptOn ?? {}).filter(([, on]) => on));
  if (!subagents.length && !memories.length && !permissions.length && !Object.keys(interrupt).length) {
    return undefined;
  }
  return {
    ...(subagents.length ? { subagents } : {}),
    ...(memories.length ? { memory: memories } : {}),
    ...(permissions.length ? { permissions } : {}),
    ...(Object.keys(interrupt).length ? { interrupt_on: interrupt } : {}),
  };
}

/** Canvas node data → the agent spec a participant/graph node saves. */
export function agentSpecFromData(data: NodeData): AgentSpec {
  const runtime = runtimeOf(data);
  const spec: AgentSpec = {
    name: data.name || data.label || "Agent",
    instructions: data.instructions || "",
    description: data.description || "",
    runtime,
  };
  if (data.maxContextWindowTokens) spec.maxContextWindowTokens = data.maxContextWindowTokens;
  if (data.modelClient || data.modelName) {
    spec.model = { client: data.modelClient || "default", name: data.modelName || null };
  }
  if (data.temperature != null || data.maxTokens != null) {
    spec.default_options = {
      ...(data.temperature != null ? { temperature: data.temperature } : {}),
      ...(data.maxTokens != null ? { max_tokens: data.maxTokens } : {}),
    };
  }
  const middleware = Object.fromEntries(Object.entries(data.middleware ?? {}).filter(([, v]) => v != null));
  if (Object.keys(middleware).length) spec.middleware = middleware;
  const mcp = compiledMcpBindings(data);
  if (mcp) spec.mcp_bindings = mcp;
  const skills = uniq(data.skillIds ?? []);
  if (skills.length) spec.maf_skill_ids = skills;
  const tools = uniq(data.functionTools ?? []);
  if (tools.length) spec.function_tools = tools;
  if (data.responseFormat?.schema || data.responseFormat?.strategy) {
    spec.response_format = {
      strategy: data.responseFormat.strategy || "auto",
      schema: data.responseFormat.schema ?? {},
    };
  }
  const deep = compiledDeepAgent(data);
  if (deep) spec.deep_agent = deep;
  return spec;
}

/* ----------------------------------------------------------- agent spec in */

function parseSubagents(raw: unknown): DeepSubagent[] {
  return asArray<Record<string, unknown>>(raw).map((s) => ({
    name: String(s.name ?? ""),
    description: String(s.description ?? ""),
    prompt: String(s.prompt ?? s.instructions ?? ""),
    tools: asArray<string>(s.tools),
    model: String(s.model ?? ""),
  }));
}

function parsePermissions(raw: unknown): DeepPermission[] {
  return asArray<Record<string, unknown>>(raw).map((p) => ({
    operations: asArray<string>(p.operations),
    paths: asArray<string>(p.paths),
    mode: String(p.mode ?? "ask"),
  }));
}

/** Semantic agent spec → canvas node data. Used by migration and templates. */
export function applySpecToAgentData(
  data: NodeData,
  spec: AgentSpec,
  fallbackModel?: { client?: string; name?: string | null },
): NodeData {
  const next: NodeData = { ...data };
  const runtime = spec.runtime === "harness" ? "deep_agent" : spec.runtime || runtimeOf(data);
  next.runtime = runtime;
  next.paletteType = runtime;
  next.name = String(spec.name || spec.ref || next.name || "Agent");
  next.label = next.name;
  next.instructions = String(spec.instructions ?? "");
  next.description = String(spec.description ?? "");
  const model = asRecord(spec.model ?? fallbackModel);
  if (model.client) next.modelClient = String(model.client);
  if (model.name != null) next.modelName = String(model.name);
  const options = asRecord(spec.default_options);
  if (typeof options.temperature === "number") next.temperature = options.temperature;
  if (typeof options.max_tokens === "number") next.maxTokens = options.max_tokens;
  if (typeof spec.maxContextWindowTokens === "number") next.maxContextWindowTokens = spec.maxContextWindowTokens;

  const middleware: Record<string, Record<string, unknown>> = {};
  for (const [id, config] of Object.entries(asRecord(spec.middleware))) {
    middleware[id] = asRecord(config);
  }
  // Legacy definitions expressed approval as `hitl.approval`; the v2 equivalent
  // is the HumanInTheLoopMiddleware tool list.
  const legacyApproval = uniq(asArray<string>(asRecord(asRecord(spec).hitl).approval));
  if (legacyApproval.length) {
    middleware.human_in_the_loop = {
      ...(middleware.human_in_the_loop ?? {}),
      tools: uniq([...asArray<string>(middleware.human_in_the_loop?.tools), ...legacyApproval]),
    };
  }
  next.middleware = middleware;

  next.mcpBindings = asArray<Record<string, unknown>>(spec.mcp_bindings).map((b) => ({
    serverId: (b.server_id as number) ?? null,
    serverName: String(b.server ?? b.server_name ?? ""),
    tools: asArray<string>(b.tools),
    approval: asArray<string>(b.approval),
  }));
  next.skillIds = uniq(asArray<string>(spec.maf_skill_ids));
  next.functionTools = uniq(asArray<string>(spec.function_tools));

  const response = asRecord(spec.response_format);
  next.responseFormat = Object.keys(response).length
    ? { strategy: String(response.strategy ?? "auto"), schema: response.schema ?? {} }
    : null;

  const deep = asRecord(spec.deep_agent);
  next.deepAgent = Object.keys(deep).length
    ? {
        subagents: parseSubagents(deep.subagents),
        memory: asArray<string>(deep.memory),
        permissions: parsePermissions(deep.permissions),
        interruptOn: Object.fromEntries(
          Object.entries(asRecord(deep.interrupt_on)).map(([k, v]) => [k, Boolean(v)]),
        ),
      }
    : emptyDeepAgent();
  return next;
}

/* ------------------------------------------------------------ graph nodes in */

function routeRows(data: NodeData): RouterRoute[] {
  return (data.routes ?? []).map((r) => ({ name: r.name, description: r.description, to: r.to }));
}

/** A building-block (or agent) canvas node → its `config.graph` node entry. */
export function graphNodeFromCanvas(node: StudioNode): Record<string, unknown> {
  const data = node.data;
  const kind = data.paletteType;
  const base: Record<string, unknown> = { id: node.id, kind, label: data.label || data.name || kind };
  if (isAgentType(kind)) {
    return {
      ...base,
      kind: "agent",
      label: data.name || data.label || "Agent",
      agent: agentSpecFromData(data),
      ...(data.extra ?? {}),
    };
  }
  switch (kind) {
    case "router": {
      const routes = routeRows(data).filter((r) => r.name.trim());
      return {
        ...base,
        routes,
        ...(data.defaultRoute ? { default: data.defaultRoute } : {}),
        ...(typeof data.maxVisits === "number" ? { max_visits: data.maxVisits } : {}),
        ...(data.extra ?? {}),
      };
    }
    case "tool":
      return { ...base, tool: data.toolName ?? "", ...(data.extra ?? {}) };
    case "join":
      return { ...base, strategy: data.strategy ?? "summarize", ...(data.extra ?? {}) };
    case "map":
      return { ...base, over: data.over ?? "items", to: data.to ?? "", ...(data.extra ?? {}) };
    case "human":
      return { ...base, message: data.message ?? "", ...(data.extra ?? {}) };
    case "subgraph":
      return { ...base, ref: data.ref ?? "", ...(data.extra ?? {}) };
    case "set_state":
      return { ...base, values: asRecord(data.values), ...(data.extra ?? {}) };
    default: {
      // Unknown kind: keep only registry-declared keys so the backend can name
      // the problem instead of silently ignoring authored values.
      const extra: Record<string, unknown> = {};
      for (const key of ["routes", "tool", "strategy", "over", "to", "message", "ref", "values"]) {
        if (key in data) extra[key] = data[key];
      }
      return { ...base, ...extra };
    }
  }
}

/** Static edges of a graph spec: canvas edges plus every route and fan-out. */
function graphEdges(
  nodes: StudioNode[],
  edges: StudioEdge[],
): GraphSpec["edges"] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const out: GraphSpec["edges"] = [];
  const seen = new Set<string>();
  const push = (from: string, to: string, when?: string, map?: { over: string; to: string }) => {
    if (!from || !to) return;
    const key = `${from}->${to}:${when ?? ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ from, to, ...(when ? { when } : {}), ...(map ? { map } : {}) });
  };

  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const source = byId.get(edge.source) as StudioNode;
    let when: string | undefined;
    if (source.data.paletteType === "router") {
      when = (source.data.routes ?? []).find((r) => r.to === edge.target)?.name;
    }
    push(edge.source, edge.target, when);
  }
  for (const node of nodes) {
    if (node.data.paletteType === "router") {
      for (const route of node.data.routes ?? []) {
        if (route.to && route.to !== "__end__") push(node.id, route.to, route.name || undefined);
      }
    }
    if (node.data.paletteType === "map" && node.data.to) {
      push(node.id, node.data.to, undefined, { over: node.data.over ?? "items", to: node.data.to });
    }
  }
  return out;
}

export function entryNodeId(nodes: StudioNode[], edges: StudioEdge[]): string {
  const explicit = nodes.find((n) => n.data.entry);
  if (explicit) return explicit.id;
  const targets = new Set(edges.map((e) => e.target));
  const root = [...nodes].sort(byPosition).find((n) => !targets.has(n.id));
  return (root ?? [...nodes].sort(byPosition)[0])?.id ?? "";
}

/* ------------------------------------------------------------------- studio */

function studioLayer(nodes: StudioNode[], edges: StudioEdge[], viewport?: Viewport): DefinitionConfig["studio"] {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: "studio",
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: n.data,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
      targetHandle: e.targetHandle ?? null,
    })),
    ...(viewport ? { viewport } : {}),
  };
}

/* -------------------------------------------------------------- participants */

function reachableAgents(patternId: string, nodes: StudioNode[], edges: StudioEdge[]): StudioNode[] {
  const agentIds = new Set(nodes.filter((n) => isAgentType(n.data.paletteType)).map((n) => n.id));
  const neighbours = new Map<string, string[]>();
  for (const edge of edges) {
    const pairs: [string, string][] = edge.source === patternId
      ? [[patternId, edge.target]]
      : edge.target === patternId
        ? [[patternId, edge.source]]
        : [[edge.source, edge.target]];
    for (const [a, b] of pairs) {
      if (a !== patternId && !agentIds.has(a)) continue;
      if (b !== patternId && !agentIds.has(b)) continue;
      neighbours.set(a, [...(neighbours.get(a) ?? []), b]);
      neighbours.set(b, [...(neighbours.get(b) ?? []), a]);
    }
  }
  const seen = new Set<string>();
  const queue = [...(neighbours.get(patternId) ?? [])];
  while (queue.length) {
    const id = queue.shift() as string;
    if (id === patternId || seen.has(id) || !agentIds.has(id)) continue;
    seen.add(id);
    for (const next of neighbours.get(id) ?? []) queue.push(next);
  }
  return nodes.filter((n) => seen.has(n.id)).sort(byPosition);
}

/** The agent nodes wired to one marker, transitively, in reading order. */
export function participantsFor(nodeId: string, nodes: StudioNode[], edges: StudioEdge[]): StudioNode[] {
  return reachableAgents(nodeId, nodes, edges);
}

export function pickPatternNode(nodes: StudioNode[], edges: StudioEdge[]): StudioNode | null {
  const patterns = nodes.filter((n) => isPatternType(n.data.paletteType)).sort(byPosition);
  if (!patterns.length) return null;
  return patterns.find((p) => reachableAgents(p.id, nodes, edges).length > 0) ?? patterns[0];
}

/* ------------------------------------------------------------------- export */

/** Top-level config keys the canvas owns; anything else is preserved verbatim. */
const CANVAS_KEYS = new Set([
  "kind",
  "schema",
  "description",
  "model",
  "studio",
  "runtime",
  "instructions",
  "middleware",
  "response_format",
  "deep_agent",
  "default_options",
  "maxContextWindowTokens",
  "mcp_bindings",
  "maf_skill_ids",
  "function_tools",
  "pattern",
  "template",
  "participants",
  "manager",
  "manager_agent",
  "start_agent",
  "handoffs",
  "output_mode",
  "parallel_tool_calls",
  "graph",
  "nodes",
  "edges",
  "entry",
  "agents",
]);

/**
 * Keys the editor does not model (`retry`, `timeout`, `recursion_limit`, …) are
 * carried over untouched, so opening and saving a definition never destroys
 * settings authored elsewhere.
 */
export function preservedConfig(config: Record<string, unknown> | undefined): Record<string, unknown> {
  const kept: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config ?? {})) {
    if (!CANVAS_KEYS.has(key)) kept[key] = value;
  }
  return kept;
}

export function graphToConfig(
  nodes: StudioNode[],
  edges: StudioEdge[],
  meta: GraphMeta,
  viewport?: Viewport,
  preserved: Record<string, unknown> = {},
): SerializedGraph {
  const agents = nodes.filter((n) => isAgentType(n.data.paletteType));
  const patterns = nodes.filter((n) => isPatternType(n.data.paletteType));
  const blocks = nodes.filter((n) => !isAgentType(n.data.paletteType) && !isPatternType(n.data.paletteType));
  const studio = studioLayer(nodes, edges, viewport);
  const primary = agents[0];
  const primaryModel = primary ? agentSpecFromData(primary.data).model : undefined;
  // An agent runs on its own connection; the toolbar picker is the flow-level
  // model that participants inherit and only wins for multi-node flows.
  const agentModel = {
    client: primaryModel?.client || meta.modelClient || "default",
    name: primaryModel?.name ?? (meta.modelName || null),
  };
  const flowModel = {
    client: meta.modelClient || primaryModel?.client || "default",
    name: meta.modelName || primaryModel?.name || null,
  };

  if (agents.length <= 1 && !patterns.length && !blocks.length) {
    const spec = primary ? agentSpecFromData(primary.data) : {};
    const config: DefinitionConfig = {
      kind: "agent",
      schema: 2,
      description: meta.description || spec.description || "",
      model: agentModel,
      runtime: spec.runtime ?? "agent",
      instructions: spec.instructions ?? "",
      studio,
    };
    for (const key of [
      "middleware",
      "response_format",
      "deep_agent",
      "default_options",
      "maxContextWindowTokens",
      "mcp_bindings",
      "maf_skill_ids",
      "function_tools",
    ] as const) {
      if (spec[key] != null) (config as Record<string, unknown>)[key] = spec[key];
    }
    return { kind: "agent", config: { ...config, ...preserved } };
  }

  const patternNode = pickPatternNode(nodes, edges);
  const patternId = patternNode?.data.paletteType ?? "graph";
  const participants = patternNode ? reachableAgents(patternNode.id, nodes, edges) : [...agents].sort(byPosition);
  const participantSpecs = participants.map((n) => agentSpecFromData(n.data));

  const config: DefinitionConfig = {
    kind: "workflow",
    schema: 2,
    pattern: patternId === "supervisor" || patternId === "swarm" ? patternId : "graph",
    description: meta.description || "",
    model: flowModel,
    studio,
  };

  if (patternId === "supervisor") {
    config.participants = participantSpecs;
    config.manager = {
      name: patternNode?.data.managerName || "Supervisor",
      instructions: patternNode?.data.managerInstructions || "",
    };
    config.output_mode = patternNode?.data.outputMode || "last_message";
    config.parallel_tool_calls = Boolean(patternNode?.data.parallelToolCalls);
    return { kind: "workflow", config: { ...config, ...preserved } };
  }

  if (patternId === "swarm") {
    const names = new Map(participants.map((n) => [n.id, n.data.name || n.data.label || "Agent"]));
    const allowed = new Set(participants.map((n) => n.id));
    config.participants = participantSpecs;
    config.start_agent = patternNode?.data.startAgent || participantSpecs[0]?.name || "";
    config.handoffs = edges
      .filter((e) => allowed.has(e.source) && allowed.has(e.target))
      .map((e) => ({ from: names.get(e.source) as string, to: names.get(e.target) as string }));
    return { kind: "workflow", config: { ...config, ...preserved } };
  }

  config.template = meta.template || "custom";
  const graphNodes = [...agents, ...blocks].sort(byPosition);
  config.graph = {
    entry: entryNodeId(graphNodes, edges),
    nodes: graphNodes.map(graphNodeFromCanvas),
    edges: graphEdges(graphNodes, edges),
  };
  return { kind: "workflow", config: { ...config, ...preserved } };
}
