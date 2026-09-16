/** Canvas store: reducer, undo/redo history, selection and clipboard.
 *
 * One reducer owns every mutation so that save/autosave, provenance and the
 * hidden `studio-graph` test hook all observe the same state. History is
 * snapshot based (nodes + edges + meta) and coalesces keystroke-level inspector
 * edits into one undo step.
 */
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import { defaultNodeData } from "./catalog";
import type { GraphMeta, NodeData, StudioCatalog, StudioEdge, StudioNode } from "./types";

export interface Snapshot {
  nodes: StudioNode[];
  edges: StudioEdge[];
  meta: GraphMeta;
}

export interface StudioState {
  nodes: StudioNode[];
  edges: StudioEdge[];
  meta: GraphMeta;
  selected: string[];
  clipboard: { nodes: StudioNode[]; edges: StudioEdge[] } | null;
  past: Snapshot[];
  future: Snapshot[];
  dirty: boolean;
  /** Bumped by every mutation; the autosave effect keys off it. */
  revision: number;
  lastEdit: { key: string; at: number } | null;
}

export const HISTORY_LIMIT = 60;
const COALESCE_MS = 900;

export function emptyMeta(): GraphMeta {
  return { name: "", slug: "", description: "", template: "custom", modelClient: "default", modelName: "" };
}

export function initialState(catalog: StudioCatalog | null = null): StudioState {
  void catalog;
  return {
    nodes: [],
    edges: [],
    meta: emptyMeta(),
    selected: [],
    clipboard: null,
    past: [],
    future: [],
    dirty: false,
    revision: 0,
    lastEdit: null,
  };
}

export function nid(paletteType: string): string {
  return `${paletteType}-${Math.random().toString(36).slice(2, 10)}`;
}

export function createNode(
  paletteType: string,
  position: { x: number; y: number },
  catalog: StudioCatalog | null,
  overrides: Partial<NodeData> = {},
): StudioNode {
  return {
    id: nid(paletteType),
    type: "studio",
    position,
    data: { ...defaultNodeData(catalog, paletteType), ...overrides },
  };
}

function cloneSnapshot(state: StudioState): Snapshot {
  return {
    nodes: state.nodes.map((n) => ({ ...n, position: { ...n.position }, data: { ...n.data } })),
    edges: state.edges.map((e) => ({ ...e })),
    meta: { ...state.meta },
  };
}

function pushHistory(state: StudioState, next: Partial<StudioState>, key?: string): StudioState {
  const now = Date.now();
  const coalesce =
    key != null && state.lastEdit != null && state.lastEdit.key === key && now - state.lastEdit.at < COALESCE_MS;
  const past = coalesce ? state.past : [...state.past, cloneSnapshot(state)].slice(-HISTORY_LIMIT);
  return {
    ...state,
    ...next,
    past,
    future: coalesce ? state.future : [],
    dirty: true,
    revision: state.revision + 1,
    lastEdit: key != null ? { key, at: now } : null,
  };
}

export type StudioAction =
  | { type: "loaded"; nodes: StudioNode[]; edges: StudioEdge[]; meta: GraphMeta }
  | { type: "nodes-change"; changes: NodeChange<StudioNode>[] }
  | { type: "edges-change"; changes: EdgeChange<StudioEdge>[] }
  | { type: "connect"; connection: Connection; edge: StudioEdge }
  | { type: "add-nodes"; nodes: StudioNode[]; edges?: StudioEdge[]; select?: boolean }
  | { type: "replace-graph"; nodes: StudioNode[]; edges: StudioEdge[] }
  | { type: "patch-data"; id: string; patch: Partial<NodeData>; coalesceKey?: string }
  | { type: "patch-meta"; patch: Partial<GraphMeta> }
  | { type: "sync-meta"; patch: Partial<GraphMeta> }
  | { type: "delete-nodes"; ids: string[] }
  | { type: "select"; ids: string[] }
  | { type: "snapshot" }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "copy" }
  | { type: "paste" }
  | { type: "mark-clean" };

export function studioReducer(state: StudioState, action: StudioAction): StudioState {
  switch (action.type) {
    case "loaded":
      return {
        ...initialState(),
        nodes: action.nodes,
        edges: action.edges,
        meta: action.meta,
        revision: state.revision + 1,
      };

    case "nodes-change": {
      const structural = action.changes.some((c) => c.type === "remove");
      const nodes = applyNodeChanges(action.changes, state.nodes) as StudioNode[];
      const next = { ...state, nodes, revision: state.revision + 1 };
      return structural ? pushHistory(state, { nodes, revision: next.revision }) : next;
    }

    case "edges-change": {
      const structural = action.changes.some((c) => c.type === "remove");
      const edges = applyEdgeChanges(action.changes, state.edges) as StudioEdge[];
      const next = { ...state, edges, revision: state.revision + 1 };
      return structural ? pushHistory(state, { edges, revision: next.revision }) : next;
    }

    case "connect":
      return pushHistory(state, { edges: addEdge(action.edge, state.edges) as StudioEdge[] });

    case "add-nodes": {
      const edges = (action.edges ?? []).reduce(
        (acc, edge) => addEdge(edge, acc) as StudioEdge[],
        state.edges,
      );
      const next = pushHistory(state, { nodes: [...state.nodes, ...action.nodes], edges });
      return action.select ? { ...next, selected: action.nodes.map((n) => n.id) } : next;
    }

    case "replace-graph":
      return pushHistory(state, { nodes: action.nodes, edges: action.edges, selected: [] });

    case "patch-data": {
      const nodes = state.nodes.map((n) => (n.id === action.id ? { ...n, data: { ...n.data, ...action.patch } } : n));
      return pushHistory(state, { nodes }, action.coalesceKey ?? `data:${action.id}`);
    }

    case "patch-meta":
      return pushHistory(state, { meta: { ...state.meta, ...action.patch } }, "meta");

    // Server-confirmed identity (slug/name after a save): never dirty, no undo step.
    case "sync-meta":
      return { ...state, meta: { ...state.meta, ...action.patch } };

    case "delete-nodes": {
      const ids = new Set(action.ids);
      return pushHistory(state, {
        nodes: state.nodes.filter((n) => !ids.has(n.id)),
        edges: state.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target)),
        selected: state.selected.filter((id) => !ids.has(id)),
      });
    }

    case "select":
      return { ...state, selected: action.ids };

    case "snapshot":
      return pushHistory(state, {});

    case "undo": {
      const previous = state.past[state.past.length - 1];
      if (!previous) return state;
      return {
        ...state,
        nodes: previous.nodes,
        edges: previous.edges,
        meta: previous.meta,
        past: state.past.slice(0, -1),
        future: [cloneSnapshot(state), ...state.future].slice(0, HISTORY_LIMIT),
        selected: state.selected.filter((id) => previous.nodes.some((n) => n.id === id)),
        dirty: true,
        revision: state.revision + 1,
        lastEdit: null,
      };
    }

    case "redo": {
      const next = state.future[0];
      if (!next) return state;
      return {
        ...state,
        nodes: next.nodes,
        edges: next.edges,
        meta: next.meta,
        past: [...state.past, cloneSnapshot(state)].slice(-HISTORY_LIMIT),
        future: state.future.slice(1),
        dirty: true,
        revision: state.revision + 1,
        lastEdit: null,
      };
    }

    case "copy": {
      const ids = new Set(state.selected);
      if (!ids.size) return state;
      return {
        ...state,
        clipboard: {
          nodes: state.nodes.filter((n) => ids.has(n.id)).map((n) => ({ ...n })),
          edges: state.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ ...e })),
        },
      };
    }

    case "paste": {
      const clip = state.clipboard;
      if (!clip?.nodes.length) return state;
      const remap = new Map<string, string>();
      const copies = clip.nodes.map((n) => {
        const id = nid(n.data.paletteType);
        remap.set(n.id, id);
        return {
          ...n,
          id,
          position: { x: n.position.x + 32, y: n.position.y + 32 },
          selected: true,
          data: { ...n.data },
        } as StudioNode;
      });
      const edges = clip.edges.map((e) => ({
        ...e,
        id: `e-${remap.get(e.source)}-${remap.get(e.target)}`,
        source: remap.get(e.source) as string,
        target: remap.get(e.target) as string,
      }));
      return pushHistory(
        { ...state, nodes: state.nodes.map((n) => ({ ...n, selected: false })) },
        {
          nodes: [...state.nodes.map((n) => ({ ...n, selected: false })), ...copies],
          edges: [...state.edges, ...edges],
          selected: copies.map((n) => n.id),
        },
      );
    }

    case "mark-clean":
      return { ...state, dirty: false, lastEdit: null };

    default:
      return state;
  }
}

export function canUndo(state: StudioState): boolean {
  return state.past.length > 0;
}

export function canRedo(state: StudioState): boolean {
  return state.future.length > 0;
}

/** Comma-separated `type->type` pairs of every edge (studio-graph test hook). */
export function edgeTypePairs(nodes: StudioNode[], edges: StudioEdge[]): string {
  const byId = new Map(nodes.map((n) => [n.id, n.data.paletteType]));
  return edges
    .map((e) => {
      const source = byId.get(e.source);
      const target = byId.get(e.target);
      return source && target ? `${source}->${target}` : "";
    })
    .filter(Boolean)
    .join(",");
}

/** Comma-separated palette types of every node (studio-graph test hook). */
export function nodeTypes(nodes: StudioNode[]): string {
  return nodes.map((n) => n.data.paletteType).join(",");
}
