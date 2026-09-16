/** Load any saved definition onto the canvas (docs/agent-studio-v2.md §6).
 *
 * Order of preference:
 *  1. `config.studio.nodes` — the author's own canvas (the normal path).
 *  2. `config.participants` — a supervisor/swarm saved from a canvas.
 *  3. `config.graph.nodes` — a declarative flow (including blueprints that were
 *     submitted through the API without ever touching the editor).
 *  4. the whole config as a single agent.
 *
 * Legacy canvas vocabulary is rewritten here: `harness` → `deep_agent`, MCP /
 * skill / tool / approval satellite nodes fold into the agent they are wired to,
 * and `hitl.approval` becomes `middleware.human_in_the_loop`.
 */
import { defaultNodeData, loadedNodeData } from "./catalog";
import { nid } from "./graphState";
import { layoutGraph } from "./layout";
import { instantiateTemplate } from "./templates";
import { applySpecToAgentData } from "./serialize";
import {
  isAgentType,
  isPatternType,
  type AgentSpec,
  type DefinitionConfig,
  type GraphMeta,
  type McpBinding,
  type NodeData,
  type StudioCatalog,
  type StudioEdge,
  type StudioNode,
} from "./types";

export interface LoadedGraph {
  nodes: StudioNode[];
  edges: StudioEdge[];
  meta: GraphMeta;
  /** Legacy components removed because v2 has no equivalent (never silent). */
  dropped: string[];
  /** True when the stored positions need the ranked layout (the caller applies
   *  it as a separate, undoable step). */
  reflowed?: boolean;
}

const LEGACY_RUNTIME: Record<string, string> = { harness: "deep_agent", deep_agent: "deep_agent", agent: "agent" };
/** Legacy satellite nodes that map onto a v2 agent field. */
const LEGACY_FOLDED_TYPES = new Set([
  "mcp-stdio",
  "mcp-http",
  "mcp-ws",
  "skill-file",
  "function-tool",
  "hitl-approval",
]);

/** Legacy satellite nodes the v2 canvas has no equivalent for. */
const LEGACY_DROPPED_TYPES = new Set(["skill-inline", "skill-mcp", "hitl-request-info"]);

const LEGACY_BIND_TYPES = new Set([...LEGACY_FOLDED_TYPES, ...LEGACY_DROPPED_TYPES]);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function uniq(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function mergeBinding(into: McpBinding[], extra: McpBinding): void {
  const index = into.findIndex(
    (b) =>
      (extra.serverId != null && b.serverId === extra.serverId) ||
      (!!extra.serverName && b.serverName === extra.serverName),
  );
  if (index < 0) {
    into.push(extra);
    return;
  }
  const current = into[index];
  into[index] = {
    serverId: current.serverId ?? extra.serverId,
    serverName: current.serverName || extra.serverName,
    tools: uniq([...current.tools, ...extra.tools]),
    approval: uniq([...current.approval, ...extra.approval]),
  };
}

/** Fold legacy satellite nodes (MCP / skills / tools / approvals) into agents.
 *
 * Anything that cannot be folded — a type with no v2 equivalent, or a satellite
 * that was never wired to an agent — is reported in `dropped` so the editor can
 * tell the author instead of losing it quietly. */
function foldLegacyNodes(
  nodes: StudioNode[],
  edges: StudioEdge[],
): { nodes: StudioNode[]; edges: StudioEdge[]; dropped: string[] } {
  const bindNodes = nodes.filter((n) => LEGACY_BIND_TYPES.has(n.data.paletteType));
  const bindIds = new Set(bindNodes.map((n) => n.id));
  if (!bindIds.size) return { nodes, edges, dropped: [] };
  const consumed = new Set<string>();

  const next = nodes.map((n) => ({ ...n, data: { ...n.data } }));
  const byId = new Map(next.map((n) => [n.id, n]));
  for (const agent of next.filter((n) => isAgentType(n.data.paletteType))) {
    const neighbours = edges
      .filter((e) => e.source === agent.id || e.target === agent.id)
      .map((e) => byId.get(e.source === agent.id ? e.target : e.source))
      .filter((n): n is StudioNode => Boolean(n) && LEGACY_BIND_TYPES.has((n as StudioNode).data.paletteType));
    if (!neighbours.length) continue;
    const bindings = [...(agent.data.mcpBindings ?? [])];
    const skills = [...(agent.data.skillIds ?? [])];
    const tools = [...(agent.data.functionTools ?? [])];
    const approval = new Set<string>();
    for (const source of neighbours) {
      if (!LEGACY_FOLDED_TYPES.has(source.data.paletteType)) continue;
      consumed.add(source.id);
      const data = asRecord(source.data);
      if (source.data.paletteType.startsWith("mcp")) {
        mergeBinding(bindings, {
          serverId: (data.serverId as number) ?? null,
          serverName: String(data.serverName ?? ""),
          tools: asArray<string>(data.tools),
          approval: asArray<string>(data.approval),
        });
      }
      if (source.data.paletteType === "skill-file") skills.push(...asArray<string>(data.skillIds));
      if (source.data.paletteType === "function-tool" && data.functionName) tools.push(String(data.functionName));
      if (source.data.paletteType === "hitl-approval") {
        for (const tool of asArray<string>(data.approval)) approval.add(tool);
      }
    }
    agent.data.mcpBindings = bindings;
    agent.data.skillIds = uniq(skills);
    agent.data.functionTools = uniq(tools);
    if (approval.size) {
      const human = asRecord((agent.data.middleware ?? {}).human_in_the_loop);
      agent.data.middleware = {
        ...(agent.data.middleware ?? {}),
        human_in_the_loop: { ...human, tools: uniq([...asArray<string>(human.tools), ...approval]) },
      };
    }
  }
  return {
    nodes: next.filter((n) => !bindIds.has(n.id)),
    edges: edges.filter((e) => !bindIds.has(e.source) && !bindIds.has(e.target)),
    dropped: bindNodes.filter((n) => !consumed.has(n.id)).map((n) => n.data.paletteType),
  };
}

function normalizeNodeData(raw: NodeData, catalog: StudioCatalog | null): NodeData {
  const paletteType = LEGACY_RUNTIME[raw.paletteType] ?? raw.paletteType;
  // `loadedNodeData` keeps every authored value and adds none of the
  // new-node defaults, so loading + saving cannot change what runs.
  const data: NodeData = { ...loadedNodeData(catalog, paletteType), ...raw, paletteType };
  data.label = raw.label || data.label;
  if (raw.runtime) data.runtime = LEGACY_RUNTIME[raw.runtime] ?? raw.runtime;
  if (paletteType === "deep_agent" || paletteType === "agent") data.runtime = paletteType;
  // Only deep agents carry this block; materialising an empty one on every other
  // node would change the canvas layer on a no-edit save.
  if ((paletteType === "deep_agent") && !data.deepAgent) {
    data.deepAgent = { subagents: [], memory: [], permissions: [], interruptOn: {} };
  }
  return data;
}

/** Old canvases can hold the same wiring twice (differing only by id). */
function dedupeEdges(edges: StudioEdge[]): StudioEdge[] {
  const seen = new Set<string>();
  return edges.filter((edge) => {
    const key = `${edge.source}->${edge.target}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function metaFromDefinition(
  name: string,
  slug: string,
  config: DefinitionConfig,
  template: string,
): GraphMeta {
  const model = asRecord(config.model);
  return {
    name: name || "Untitled",
    slug,
    description: String(config.description ?? ""),
    template,
    modelClient: String(model.client ?? "default"),
    modelName: String(model.name ?? ""),
  };
}

/** Node keys that describe graph wiring, never the agent behind a node.
 *  Mirrors `GRAPH_ONLY_KEYS` in the backend's graph spec.py. */
const GRAPH_ONLY_KEYS = new Set([
  "id", "kind", "label", "position", "agent", "type", "x", "y", "width", "height",
  "selected", "dragging", "routes", "default", "tool", "strategy", "over", "to",
  "message", "ref", "values",
]);

/** Agent-spec keys the canvas models; anything else an author wrote is kept. */
const AGENT_SPEC_KEYS = new Set([
  "id", "ref", "name", "instructions", "description", "runtime", "model",
  "default_options", "maxContextWindowTokens", "middleware", "response_format",
  "deep_agent", "mcp_bindings", "maf_skill_ids", "function_tools", "hitl",
]);

/**
 * The agent behind a graph node. Nodes written by the current canvas nest the
 * spec under `agent`; nodes written by the pre-v2 canvas carry it on the node
 * itself (`instructions`, `mcp_bindings`, `model`, …). Both shapes are read and
 * unmodelled keys are preserved in `extra`, so a load + save never drops an
 * authored setting.
 */
function agentSpecFromGraphNode(raw: Record<string, unknown>): { spec: AgentSpec; extra: Record<string, unknown> } {
  const nested = asRecord(raw.agent);
  const folded: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (GRAPH_ONLY_KEYS.has(key)) continue;
    folded[key] = value;
  }
  Object.assign(folded, nested);
  if (!folded.name) folded.name = String(raw.label ?? raw.id ?? "Agent");
  const extra = Object.fromEntries(Object.entries(folded).filter(([key]) => !AGENT_SPEC_KEYS.has(key)));
  return { spec: folded as AgentSpec, extra };
}

/** Keys of a participant/single-agent spec the canvas does not model. */
function unmodelledSpecKeys(spec: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(spec).filter(([key]) => !AGENT_SPEC_KEYS.has(key)));
}

function graphNodesFromSpec(
  catalog: StudioCatalog | null,
  config: DefinitionConfig,
): { nodes: StudioNode[]; edges: StudioEdge[] } {
  // Mirror the backend's `raw_graph()`: `config.graph`, then the legacy
  // top-level `nodes`/`edges`/`entry` shape, then the template blueprint. Miss
  // one and an old flow would be flattened into a single agent on save.
  const spec = asRecord(config.graph);
  const graphNodes = asArray<Record<string, unknown>>(spec.nodes);
  const graphEdges = asArray<Record<string, unknown>>(spec.edges);
  const rawNodes = graphNodes.length ? graphNodes : asArray<Record<string, unknown>>(config.nodes);
  const rawEdges = graphEdges.length ? graphEdges : asArray<Record<string, unknown>>(config.edges);
  const rawEntry = String(spec.entry ?? config.entry ?? "");
  const nodes: StudioNode[] = rawNodes.map((raw) => {
    const kind = String(raw.kind ?? "agent");
    const id = String(raw.id ?? nid(kind));
    if (kind === "agent") {
      const { spec: agentSpec, extra } = agentSpecFromGraphNode(raw);
      const runtime = agentSpec.runtime === "harness" ? "deep_agent" : agentSpec.runtime || "agent";
      const base = loadedNodeData(catalog, runtime);
      const data = applySpecToAgentData(base, agentSpec);
      if (Object.keys(extra).length) data.extra = extra;
      return { id, type: "studio", position: { x: 0, y: 0 }, data: { ...data, label: String(raw.label ?? data.name) } };
    }
    const data: NodeData = { ...defaultNodeData(catalog, kind), ...raw, paletteType: kind };
    data.label = String(raw.label ?? data.label);
    if (kind === "router") {
      data.defaultRoute = String(raw.default ?? "");
      if (typeof raw.max_visits === "number") data.maxVisits = raw.max_visits;
    }
    if (kind === "tool") data.toolName = String(raw.tool ?? "");
    if (kind === "map") {
      data.over = String(raw.over ?? "items");
      data.to = String(raw.to ?? "");
    }
    if (kind === "set_state") data.values = asRecord(raw.values);
    // Keys the canvas does not model survive a load → save round trip.
    const modelled = new Set([
      "id", "kind", "label", "name", "routes", "default", "max_visits", "tool",
      "strategy", "over", "to", "message", "ref", "values", "model", "agent",
    ]);
    const extra = Object.fromEntries(Object.entries(raw).filter(([key]) => !modelled.has(key)));
    if (Object.keys(extra).length) data.extra = extra;
    return { id, type: "studio", position: { x: 0, y: 0 }, data };
  });

  const known = new Set(nodes.map((n) => n.id));
  const edges: StudioEdge[] = rawEdges
    .filter((e) => known.has(String(e.from ?? e.source)) && known.has(String(e.to ?? e.target)))
    .map((e) => ({
      id: `e-${String(e.from ?? e.source)}-${String(e.to ?? e.target)}`,
      source: String(e.from ?? e.source),
      target: String(e.to ?? e.target),
    }));

  const entry = rawEntry;
  if (entry && known.has(entry)) {
    for (const node of nodes) node.data = { ...node.data, entry: node.id === entry };
  }
  return { nodes, edges };
}

function participantNodes(
  catalog: StudioCatalog | null,
  config: DefinitionConfig,
  patternId: string,
): { nodes: StudioNode[]; edges: StudioEdge[] } {
  const participants = asArray<AgentSpec>(config.participants);
  const nodes: StudioNode[] = [];
  const edges: StudioEdge[] = [];
  const patternData = defaultNodeData(catalog, patternId);
  if (patternId === "supervisor") {
    const manager = asRecord(config.manager);
    patternData.managerName = String(manager.name ?? patternData.managerName ?? "Supervisor");
    patternData.managerInstructions = String(manager.instructions ?? patternData.managerInstructions ?? "");
    patternData.outputMode = String(config.output_mode ?? patternData.outputMode ?? "last_message");
    patternData.parallelToolCalls = Boolean(config.parallel_tool_calls);
  }
  if (patternId === "swarm") patternData.startAgent = String(config.start_agent ?? "");
  const pattern: StudioNode = {
    id: nid(patternId),
    type: "studio",
    position: { x: 0, y: 0 },
    data: patternData,
  };
  nodes.push(pattern);

  participants.forEach((spec) => {
    const runtime = spec.runtime === "harness" ? "deep_agent" : spec.runtime || "agent";
    const base = loadedNodeData(catalog, runtime);
    const data = applySpecToAgentData(base, spec);
    const extra = unmodelledSpecKeys(spec as Record<string, unknown>);
    if (Object.keys(extra).length) data.extra = extra;
    const agent: StudioNode = { id: nid(runtime), type: "studio", position: { x: 0, y: 0 }, data };
    nodes.push(agent);
    edges.push({ id: `e-${agent.id}-${pattern.id}`, source: agent.id, target: pattern.id });
  });

  if (patternId === "swarm") {
    const byName = new Map(nodes.filter((n) => n.id !== pattern.id).map((n) => [n.data.name, n.id]));
    for (const handoff of asArray<Record<string, unknown>>(config.handoffs)) {
      const source = byName.get(String(handoff.from));
      const target = byName.get(String(handoff.to));
      if (source && target) edges.push({ id: `e-${source}-${target}`, source, target });
    }
  }
  return { nodes, edges };
}

export function loadDefinition(
  catalog: StudioCatalog | null,
  definition: { name?: string; slug?: string; kind?: string; config?: Record<string, unknown> },
): LoadedGraph {
  const config = (definition.config ?? {}) as DefinitionConfig;
  const kind = String(definition.kind ?? config.kind ?? "agent");
  const template = String(config.template ?? (kind === "workflow" ? patternTemplate(config) : "custom"));

  const studio = asRecord(config.studio);
  const studioNodes = asArray<{ id: string; position?: { x: number; y: number }; data?: NodeData }>(studio.nodes);
  if (studioNodes.length) {
    const nodes: StudioNode[] = studioNodes.map((raw) => ({
      id: String(raw.id),
      type: "studio",
      position: raw.position ?? { x: 0, y: 0 },
      data: normalizeNodeData(raw.data ?? { paletteType: "agent", label: "Agent" }, catalog),
    }));
    const edges: StudioEdge[] = asArray<Record<string, unknown>>(studio.edges).map((e) => ({
      id: String(e.id ?? `e-${String(e.source)}-${String(e.target)}`),
      source: String(e.source),
      target: String(e.target),
      sourceHandle: (e.sourceHandle as string | null) ?? undefined,
      targetHandle: (e.targetHandle as string | null) ?? undefined,
    }));
    const folded = foldLegacyNodes(nodes, edges);
    return {
      nodes: folded.nodes,
      edges: dedupeEdges(folded.edges),
      meta: metaFromDefinition(definition.name ?? "", definition.slug ?? "", config, template),
      dropped: folded.dropped,
    };
  }

  const pattern = String(config.pattern ?? "");
  if (kind === "workflow" && (pattern === "supervisor" || pattern === "swarm")) {
    const built = participantNodes(catalog, config, pattern);
    return {
      nodes: layoutGraph(built.nodes, built.edges),
      edges: built.edges,
      meta: metaFromDefinition(definition.name ?? "", definition.slug ?? "", config, template),
      dropped: [],
    };
  }

  if (kind === "workflow") {
    let built = graphNodesFromSpec(catalog, config);
    // A definition that only names a blueprint still runs that blueprint
    // (the backend materialises it), so the canvas must show it too.
    if (!built.nodes.length && template && template !== "custom") {
      const blueprint = instantiateTemplate(catalog, template);
      if (blueprint.nodes.length) built = { nodes: blueprint.nodes, edges: blueprint.edges };
    }
    if (built.nodes.length) {
      return {
        nodes: layoutGraph(built.nodes, built.edges),
        edges: built.edges,
        meta: metaFromDefinition(definition.name ?? "", definition.slug ?? "", config, template),
        dropped: [],
      };
    }
  }

  const spec: AgentSpec = {
    name: definition.name || "Agent",
    instructions: String(config.instructions ?? ""),
    description: String(config.description ?? ""),
    runtime: String(config.runtime ?? "agent"),
    model: asRecord(config.model) as AgentSpec["model"],
    default_options: asRecord(config.default_options) as AgentSpec["default_options"],
    maxContextWindowTokens: config.maxContextWindowTokens as number | undefined,
    middleware: asRecord(config.middleware) as AgentSpec["middleware"],
    response_format: (config.response_format ?? null) as AgentSpec["response_format"],
    deep_agent: asRecord(config.deep_agent) as AgentSpec["deep_agent"],
    mcp_bindings: asArray<NonNullable<AgentSpec["mcp_bindings"]>[number]>(config.mcp_bindings),
    maf_skill_ids: asArray<string>(config.maf_skill_ids),
    function_tools: asArray<string>(config.function_tools),
  };
  // `hitl.approval` is the pre-v2 spelling of the human-in-the-loop tool list.
  if (asRecord(config.hitl).approval) (spec as Record<string, unknown>).hitl = asRecord(config.hitl);
  const runtime = spec.runtime === "harness" ? "deep_agent" : spec.runtime || "agent";
  const base = loadedNodeData(catalog, runtime);
  const data = applySpecToAgentData(base, spec);
  const extra = unmodelledSpecKeys(spec as Record<string, unknown>);
  if (Object.keys(extra).length) data.extra = extra;
  return {
    nodes: [{ id: nid(runtime), type: "studio", position: { x: 80, y: 80 }, data }],
    edges: [],
    meta: metaFromDefinition(definition.name ?? "", definition.slug ?? "", config, template),
    dropped: [],
  };
}

function patternTemplate(config: DefinitionConfig): string {
  const pattern = String(config.pattern ?? "");
  if (pattern && pattern !== "graph") return pattern;
  return String(config.template ?? "custom");
}
