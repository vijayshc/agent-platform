/** Deterministic layered (ranked) auto-layout — "Tidy up", templates, migration.
 *
 * Ranks come from the longest path out of the entry/roots (cycles are cut at
 * back edges, so the evaluator–optimizer loop stays finite). Nodes inside a rank
 * are ordered by a barycenter/median pass to reduce edge crossings, with the
 * previous position as a stable tie-break. Same-rank nodes share a column, so
 * every connector leaves a right handle and enters a left handle.
 */
import type { StudioEdge, StudioNode } from "./types";

export interface LayoutOptions {
  origin?: { x: number; y: number };
  /** Horizontal pitch between ranks (cards are ≤260px wide → 60px+ gaps). */
  columnGap?: number;
  /** Vertical pitch inside a rank (cards are ≤170px tall → 20px+ gaps). */
  rowGap?: number;
}

const DEFAULTS: Required<LayoutOptions> = {
  origin: { x: 64, y: 72 },
  columnGap: 320,
  rowGap: 190,
};

/** Conservative card box used for overlap detection (widest/tallest variant). */
export const CARD_WIDTH = 236;
export const CARD_HEIGHT = 170;

function byPosition(a: StudioNode, b: StudioNode): number {
  return a.position.y - b.position.y || a.position.x - b.position.x || a.id.localeCompare(b.id);
}

/** Do any two node boxes overlap? */
export function hasOverlappingNodes(nodes: StudioNode[]): boolean {
  for (let i = 0; i < nodes.length; i += 1) {
    for (let j = i + 1; j < nodes.length; j += 1) {
      const a = nodes[i].position;
      const b = nodes[j].position;
      if (Math.abs(a.x - b.x) < CARD_WIDTH && Math.abs(a.y - b.y) < CARD_HEIGHT) return true;
    }
  }
  return false;
}

interface Box {
  id: string;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

interface Segment {
  from: string;
  to: string;
  ax: number;
  ay: number;
  bx: number;
  by: number;
}

function boxesOf(nodes: StudioNode[]): Box[] {
  return nodes.map((node) => ({
    id: node.id,
    left: node.position.x,
    top: node.position.y,
    right: node.position.x + CARD_WIDTH,
    bottom: node.position.y + CARD_HEIGHT,
  }));
}

/** Straight-line stand-in for a connector: right handle → left handle. */
function segmentsOf(nodes: StudioNode[], edges: StudioEdge[]): Segment[] {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const segments: Segment[] = [];
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    segments.push({
      from: edge.source,
      to: edge.target,
      ax: source.position.x + CARD_WIDTH,
      ay: source.position.y + CARD_HEIGHT / 2,
      bx: target.position.x,
      by: target.position.y + CARD_HEIGHT / 2,
    });
  }
  return segments;
}

function crossesBox(segment: Segment, box: Box): boolean {
  if (box.id === segment.from || box.id === segment.to) return false;
  // Sampled hit test: cheap, deterministic and accurate enough for a 236×170 box.
  const steps = 24;
  for (let step = 1; step < steps; step += 1) {
    const t = step / steps;
    const x = segment.ax + (segment.bx - segment.ax) * t;
    const y = segment.ay + (segment.by - segment.ay) * t;
    if (x > box.left && x < box.right && y > box.top && y < box.bottom) return true;
  }
  return false;
}

function orientation(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  return Math.sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax));
}

function segmentsCross(a: Segment, b: Segment): boolean {
  if (a.from === b.from || a.from === b.to || a.to === b.from || a.to === b.to) return false;
  return (
    orientation(a.ax, a.ay, a.bx, a.by, b.ax, b.ay) !== orientation(a.ax, a.ay, a.bx, a.by, b.bx, b.by) &&
    orientation(b.ax, b.ay, b.bx, b.by, a.ax, a.ay) !== orientation(b.ax, b.ay, b.bx, b.by, a.bx, a.by)
  );
}

/**
 * Does a stored canvas need re-flowing? True when cards overlap, a connector
 * runs under an unrelated card, or two connectors cross — the three things the
 * author actually sees as "the links are messed up".
 */
export function layoutNeedsReflow(nodes: StudioNode[], edges: StudioEdge[]): boolean {
  if (nodes.length < 2) return false;
  if (hasOverlappingNodes(nodes)) return true;
  const boxes = boxesOf(nodes);
  const segments = segmentsOf(nodes, edges);
  if (segments.some((segment) => boxes.some((box) => crossesBox(segment, box)))) return true;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segmentsCross(segments[i], segments[j])) return true;
    }
  }
  return false;
}

export function layoutGraph(
  nodes: StudioNode[],
  edges: StudioEdge[],
  options: LayoutOptions = {},
): StudioNode[] {
  if (!nodes.length) return nodes;
  const { origin, columnGap, rowGap } = { ...DEFAULTS, ...options };
  const ids = new Set(nodes.map((n) => n.id));
  const order = [...nodes].sort(byPosition);

  // A supervisor/swarm marker is a hub, not a step: its participants are peers
  // (handoff edges are not sequencing), so they share one rank with the marker
  // one column to their right. Without this a swarm reads as a 3-column chain.
  const hub = nodes.find((n) => n.data.paletteType === "supervisor" || n.data.paletteType === "swarm");
  const participantIds = new Set<string>();
  if (hub) {
    for (const edge of edges) {
      if (edge.source === hub.id && ids.has(edge.target)) participantIds.add(edge.target);
      if (edge.target === hub.id && ids.has(edge.source)) participantIds.add(edge.source);
    }
  }
  const rankEdges = hub
    ? edges.filter(
        (edge) =>
          edge.source !== hub.id &&
          edge.target !== hub.id &&
          !(participantIds.has(edge.source) && participantIds.has(edge.target)),
      )
    : edges;

  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  for (const node of nodes) {
    outgoing.set(node.id, []);
    incoming.set(node.id, []);
  }
  for (const edge of rankEdges) {
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) continue;
    (outgoing.get(edge.source) as string[]).push(edge.target);
    (incoming.get(edge.target) as string[]).push(edge.source);
  }

  /* ---------------------------------------------------------------- ranking */

  const rank = new Map<string, number>();
  const state = new Map<string, 0 | 1 | 2>();

  function visit(id: string, depth: number): void {
    state.set(id, 1);
    rank.set(id, Math.max(rank.get(id) ?? 0, depth));
    for (const next of outgoing.get(id) ?? []) {
      if (state.get(next) === 1) continue; // back edge: keeps loops finite
      visit(next, depth + 1);
    }
    state.set(id, 2);
  }

  const explicitEntry = order.find((n) => n.data.entry);
  const roots = order.filter((n) => (incoming.get(n.id) ?? []).length === 0);
  const starts = explicitEntry ? [explicitEntry, ...roots] : roots.length ? roots : order;
  for (const node of starts) if (state.get(node.id) !== 2) visit(node.id, 0);
  for (const node of order) if (state.get(node.id) !== 2) visit(node.id, 0);

  if (hub) {
    for (const id of participantIds) rank.set(id, 0);
    rank.set(hub.id, participantIds.size ? 1 : 0);
  }

  const columns = new Map<number, string[]>();
  for (const node of order) {
    const column = rank.get(node.id) ?? 0;
    columns.set(column, [...(columns.get(column) ?? []), node.id]);
  }
  const columnKeys = [...columns.keys()].sort((a, b) => a - b);
  const indexInColumn = new Map<string, number>();
  for (const column of columnKeys) {
    (columns.get(column) as string[]).forEach((id, index) => indexInColumn.set(id, index));
  }

  /* ------------------------------------------------- ordering (barycenter) */

  const median = (values: number[]): number => {
    if (!values.length) return Number.NaN;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };

  for (let pass = 0; pass < 3; pass += 1) {
    const forward = pass % 2 === 0;
    const keys = forward ? columnKeys : [...columnKeys].reverse();
    for (const column of keys) {
      const members = columns.get(column) as string[];
      const weight = new Map<string, number>();
      members.forEach((id) => {
        const base = (forward ? incoming.get(id) : outgoing.get(id)) ?? [];
        const neighbours = hub && id === hub.id ? [...participantIds] : base;
        const positions = neighbours
          .map((other) => indexInColumn.get(other))
          .filter((value): value is number => typeof value === "number");
        weight.set(id, median(positions));
      });
      const stable = new Map(members.map((id, index) => [id, index]));
      members.sort((a, b) => {
        const wa = weight.get(a) as number;
        const wb = weight.get(b) as number;
        const na = Number.isNaN(wa);
        const nb = Number.isNaN(wb);
        if (na && nb) return (stable.get(a) as number) - (stable.get(b) as number);
        if (na) return -1;
        if (nb) return 1;
        return wa - wb || (stable.get(a) as number) - (stable.get(b) as number);
      });
      members.forEach((id, index) => indexInColumn.set(id, index));
    }
  }

  /* -------------------------------------------------------------- placement */

  const positions = new Map<string, { x: number; y: number }>();
  for (const column of columnKeys) {
    const members = columns.get(column) as string[];
    members.forEach((id, index) => {
      positions.set(id, {
        x: Math.round(origin.x + column * columnGap),
        y: Math.round(origin.y + index * rowGap),
      });
    });
  }

  return nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? node.position }));
}
