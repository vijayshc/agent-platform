/** Instantiate a catalog blueprint (pattern card / empty canvas) as canvas state.
 *
 * A template ships `key`-addressed nodes and edges; this module assigns canvas
 * ids, rewrites every reference (`routes[].to`, `map.to`, `entry`) to those ids
 * and lays the result out deterministically.
 */
import { applySpecToAgentData } from "./serialize";
import { defaultNodeData, nodeFieldKey, nodeKindFields } from "./catalog";
import { nid } from "./graphState";
import { layoutGraph } from "./layout";
import type { AgentSpec, NodeData, StudioCatalog, StudioEdge, StudioNode, TemplateNode } from "./types";

export interface InstantiatedTemplate {
  nodes: StudioNode[];
  edges: StudioEdge[];
  entry: string;
  template: string;
}

export function instantiateTemplate(
  catalog: StudioCatalog | null,
  templateId: string,
): InstantiatedTemplate {
  const template = catalog?.templates?.[templateId];
  if (!template) {
    return { nodes: [], edges: [], entry: "", template: templateId };
  }

  const idByKey = new Map<string, string>();
  for (const raw of template.nodes as TemplateNode[]) idByKey.set(raw.key, nid(raw.kind));
  const resolve = (key: string | undefined): string => {
    if (!key) return "";
    if (key === "__end__") return "__end__";
    return idByKey.get(key) ?? key;
  };

  const nodes: StudioNode[] = (template.nodes as TemplateNode[]).map((raw) => {
    const id = idByKey.get(raw.key) as string;
    const rawRuntime = String((raw.agent?.runtime as string) || "agent");
    const paletteType = raw.kind === "agent" ? (rawRuntime === "harness" ? "deep_agent" : rawRuntime) : raw.kind;
    let data: NodeData = { ...defaultNodeData(catalog, paletteType), label: raw.label };
    if (raw.kind === "agent") {
      data = applySpecToAgentData(data, { name: raw.label, ...(raw.agent ?? {}) } as AgentSpec);
    }
    // Every key the blueprint emits is carried over: the canvas vocabulary is a
    // subset of the semantic node, and dropping the rest silently changes the
    // flow (this is how `max_visits` used to disappear). Registry field names
    // land on their canvas key; anything else is kept verbatim in `extra`.
    const blueprintKeys = new Set(["key", "kind", "label", "agent", "model"]);
    const fieldNames = new Set(nodeKindFields(catalog, raw.kind).map((field) => field.name));
    const extra: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(raw)) {
      if (blueprintKeys.has(key)) continue;
      if (key === "routes") {
        data.routes = ((value as TemplateNode["routes"]) ?? []).map((r) => ({
          name: r.name,
          description: r.description ?? "",
          to: resolve(r.to),
        }));
      } else if (key === "values") {
        data.values = { ...((value as Record<string, unknown>) ?? {}) };
      } else if (fieldNames.has(key)) {
        const canvasKey = nodeFieldKey(raw.kind, key);
        if (canvasKey === "defaultRoute" || key === "to") {
          (data as Record<string, unknown>)[canvasKey] = resolve(value as string);
        } else {
          (data as Record<string, unknown>)[canvasKey] = value;
        }
      } else {
        (data as Record<string, unknown>)[key] = value;
        extra[key] = value;
      }
    }
    if (Object.keys(extra).length) data.extra = extra;
    return { id, type: "studio", position: { x: 0, y: 0 }, data };
  });

  const edges: StudioEdge[] = template.edges.map((edge) => ({
    id: `e-${resolve(edge.from)}-${resolve(edge.to)}`,
    source: resolve(edge.from),
    target: resolve(edge.to),
  }));

  const entry = resolve(template.entry);
  if (entry) {
    for (const node of nodes) node.data = { ...node.data, entry: node.id === entry };
  }

  return { nodes: layoutGraph(nodes, edges), edges, entry, template: templateId };
}
