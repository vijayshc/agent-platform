/** Edge styling, labels, connection rules and the derived wiring the canvas adds.
 *
 * The semantic layer (`config.graph.edges`) always contains a router's route
 * targets and a fan-out's Send target, even when the author never drew them.
 * The canvas renders those derived edges too, plus a terminal marker for routes
 * that end the run, so the picture matches the run.
 */
import { isAgentType, isPatternType, type NodeData, type StudioEdge, type StudioNode } from "../model/types";

export type EdgeVariant = "flow" | "route" | "fanout" | "handoff" | "end";

/** Prefix of the derived terminal markers used by `route.to === "__end__"`. */
export const END_NODE_ID = "__end__";

export function isEndEdge(edge: StudioEdge): boolean {
  return edge.target.startsWith(END_NODE_ID);
}

/** Attach variant + label to every edge from the current canvas state. */
/** Parallel connectors between the same pair get their own lane offset. */
function laneOffsets(edges: StudioEdge[]): Map<string, number> {
  const groups = new Map<string, string[]>();
  for (const edge of edges) {
    const key = [edge.source, edge.target].sort().join("::");
    groups.set(key, [...(groups.get(key) ?? []), edge.id]);
  }
  const lanes = new Map<string, number>();
  for (const ids of groups.values()) {
    ids.forEach((id, index) => lanes.set(id, (index - (ids.length - 1) / 2) * 28));
  }
  return lanes;
}

export function decorateEdges(nodes: StudioNode[], edges: StudioEdge[]): StudioEdge[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const lanes = laneOffsets(edges);
  return edges.map((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    let variant: EdgeVariant = "flow";
    let label = String(edge.label ?? "");
    if (isEndEdge(edge)) {
      variant = "end";
    } else if (source?.data.paletteType === "router") {
      variant = "route";
      label = label || (source.data.routes ?? []).find((r) => r.to === edge.target)?.name || "";
    } else if (source?.data.paletteType === "map") {
      variant = "fanout";
      label = label || (source.data.over ? `each ${source.data.over}` : "fan-out");
    } else if (source && target && isAgentType(source.data.paletteType) && isAgentType(target.data.paletteType)) {
      variant = "handoff";
    }
    return {
      ...edge,
      type: "studio",
      sourceHandle: "out",
      targetHandle: "in",
      label: label || undefined,
      data: { ...(edge.data ?? {}), variant, lane: lanes.get(edge.id) ?? 0 },
    };
  });
}

function derivedEdge(source: string, target: string, label: string, variant: EdgeVariant): StudioEdge {
  return {
    id: `derived-${source}-${target}-${label}`,
    source,
    target,
    type: "studio",
    label,
    data: { derived: true, variant },
  };
}

/** Mark the edges attached to the current selection for emphasis. */
export function emphasiseEdges(edges: StudioEdge[], selected: string[]): StudioEdge[] {
  if (!selected.length) return edges.map((edge) => ({ ...edge, data: { ...(edge.data ?? {}), dim: false } }));
  const chosen = new Set(selected);
  return edges.map((edge) => ({
    ...edge,
    data: { ...(edge.data ?? {}), dim: !chosen.has(edge.source) && !chosen.has(edge.target) },
  }));
}

export interface CanvasEdges {
  /** Canvas edges plus every derived edge and terminal stub, ready to render. */
  edges: StudioEdge[];
  /** Terminal markers the derived stubs point at (never part of the document). */
  terminals: StudioNode[];
  /** Edge count that mirrors `config.graph.edges` (terminal stubs excluded). */
  semantic: number;
}

/**
 * Everything the canvas must draw for the current definition:
 *  - the author's edges,
 *  - a router's `to: "<node>"` routes,
 *  - a fan-out's Send target,
 *  - a stub + terminal marker for every `to: "__end__"` route.
 */
export function canvasEdgeSet(nodes: StudioNode[], edges: StudioEdge[]): CanvasEdges {
  const ids = new Set(nodes.map((n) => n.id));
  const present = new Set(edges.map((e) => `${e.source}->${e.target}`));
  const derived: StudioEdge[] = [];
  const stubs: StudioEdge[] = [];
  const terminals: StudioNode[] = [];

  const add = (source: string, target: string, label: string, variant: EdgeVariant) => {
    const key = `${source}->${target}`;
    if (present.has(key)) return;
    present.add(key);
    derived.push(derivedEdge(source, target, label, variant));
  };

  for (const node of nodes) {
    const kind = node.data.paletteType;
    if (kind === "map") {
      const to = String(node.data.to ?? "");
      if (to && to !== END_NODE_ID && ids.has(to)) {
        add(node.id, to, `each ${String(node.data.over ?? "items")}`, "fanout");
      }
    }
    if (kind === "router") {
      (node.data.routes ?? []).forEach((route, index) => {
        const name = route.name || "route";
        if (route.to === END_NODE_ID) {
          const id = `${END_NODE_ID}-${node.id}-${index}`;
          stubs.push(derivedEdge(node.id, id, name, "end"));
          terminals.push({
            id,
            type: "terminal",
            position: { x: node.position.x + 336, y: node.position.y + index * 64 },
            selectable: false,
            draggable: false,
            deletable: false,
            connectable: false,
            focusable: false,
            data: { paletteType: END_NODE_ID, label: "END", route: name } as NodeData,
          });
        } else if (route.to && ids.has(route.to)) {
          add(node.id, route.to, name, "route");
        }
      });
    }
  }

  return { edges: [...edges, ...derived, ...stubs], terminals, semantic: edges.length + derived.length };
}

/** Can a new connection be drawn between these two nodes? */
export function isValidConnection(nodes: StudioNode[], sourceId: string, targetId: string): boolean {
  if (!sourceId || !targetId || sourceId === targetId) return false;
  const source = nodes.find((n) => n.id === sourceId);
  const target = nodes.find((n) => n.id === targetId);
  if (!source || !target) return false;
  const a = source.data.paletteType;
  const b = target.data.paletteType;
  // Two orchestration markers never wire to each other: a flow has one pattern.
  if (isPatternType(a) && isPatternType(b)) return false;
  // Only an agent can own a supervisor/swarm marker.
  if (isPatternType(b) && !isAgentType(a)) return false;
  return true;
}

/** Why a connection was refused — surfaced as a canvas hint. */
export function connectionRejection(nodes: StudioNode[], sourceId: string, targetId: string): string {
  if (sourceId === targetId) return "A node cannot connect to itself.";
  const source = nodes.find((n) => n.id === sourceId)?.data.paletteType ?? "node";
  const target = nodes.find((n) => n.id === targetId)?.data.paletteType ?? "node";
  if (isPatternType(source) && isPatternType(target)) {
    return "A flow has one orchestration marker — connect agents to it instead.";
  }
  if (isPatternType(target)) return "Only agents can join a supervisor or swarm.";
  return `${source} → ${target} is not a valid connection.`;
}
