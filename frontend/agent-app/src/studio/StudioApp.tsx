/** Agent Studio shell: left rail | canvas | right inspector | bottom run dock.
 *
 * Composition only — `useStudioDocument` owns the API/state machine, the canvas
 * reducer owns nodes/edges, and each surface owns its own presentation.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";
import { AlertTriangle, LayoutGrid, PanelLeft, PanelRight, X } from "lucide-react";
import { Canvas } from "./canvas/Canvas";
import { canvasEdgeSet } from "./canvas/edges";
import {
  canRedo,
  canUndo,
  createNode,
  edgeTypePairs,
  nodeTypes as paletteTypes,
} from "./model/graphState";
import { patternEntries, templateEntries } from "./model/catalog";
import { CARD_HEIGHT, CARD_WIDTH, layoutGraph } from "./model/layout";
import { participantsFor } from "./model/serialize";
import { graphToConfig } from "./model/serialize";
import { instantiateTemplate } from "./model/templates";
import type { NodeData, RouterRoute, StudioEdge, StudioNode } from "./model/types";
import { DockGrip, Inspector, type InspectorTab } from "./inspector/Inspector";
import { Palette } from "./palette/Palette";
import { RunDock } from "./run/RunDock";
import { AccessDialog } from "./inspector/AccessDialog";
import { StudioToolbar } from "./StudioToolbar";
import { useStudioDocument } from "./useStudioDocument";

/** Attach validate/compile messages to the node whose name they mention. */
function errorsByNode(report: { errors: { message: string }[]; warnings: { message: string }[] } | null, nodes: StudioNode[]) {
  const out: Record<string, string[]> = {};
  if (!report) return out;
  for (const issue of [...report.errors, ...report.warnings]) {
    for (const node of nodes) {
      const label = String(node.data.name || node.data.label || "");
      if (label && issue.message.includes(`'${label}'`)) {
        out[node.id] = [...(out[node.id] ?? []), issue.message];
      }
    }
  }
  return out;
}

export function StudioApp() {
  const doc = useStudioDocument();
  const {
    state,
    dispatch,
    catalog,
    agents,
    loadingAgents,
    current,
    status,
    error,
    report,
    plan,
    planning,
    busy,
    hint,
    setHint,
    migrationNotice,
    dismissMigrationNotice,
    modules,
    missing,
    layoutNotice,
    dismissLayoutNotice,
    checksNotice,
    clearChecksNotice,
    focusChecks,
    onFocusChecks,
    fitToken,
    docId,
    setStatus,
    save,
    validate,
    publish,
    draftDefinition,
    openSlug,
    newAgent,
    exportJson,
    importJson,
    exportGraph,
    noteViewportMoved,
    refreshAgents,
  } = doc;
  const { nodes, edges, meta, selected } = state;
  const [paletteOpen, setPaletteOpen] = useState(true);
  const [paletteAutoCollapsed, setPaletteAutoCollapsed] = useState(false);
  const SMART_MIN_ZOOM = 0.62;
  /** Loading a document must never leave a card outside the viewport — even
   *  when the author has squeezed the canvas with an open palette and an overlay
   *  dock. Manual "Fit" keeps the readable floor (0.62) instead. */
  const FIT_FLOOR = 0.2;
  /** Conservative card box for the fit maths (cards are <=236x190). */
  const FIT_CARD_WIDTH = 236;
  const FIT_CARD_HEIGHT = 200;
  const DOCK_OVERLAY_MAX = 1100;
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("configure");
  const [dockWidth, setDockWidth] = useState<number>(() => {
    const stored = Number(window.localStorage.getItem("aa_studio_inspector_width"));
    return Number.isFinite(stored) && stored >= 320 && stored <= 720 ? stored : 348;
  });

  useEffect(() => {
    window.localStorage.setItem("aa_studio_inspector_width", String(dockWidth));
  }, [dockWidth]);

  const [accessOpen, setAccessOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const { getViewport, setCenter, setViewport, screenToFlowPosition } = useReactFlow();

  const [dockOverlay, setDockOverlay] = useState(() => window.innerWidth < DOCK_OVERLAY_MAX);
  const [dockOpen, setDockOpen] = useState(true);
  /** A fit is owed to the author until the canvas has a final measured size. */
  const pendingFit = useRef<number | null>(null);
  const settledRef = useRef({ width: 0, height: 0 });
  /** A fit issued before ReactFlow is ready is dropped: verify and retry. */
  const fitAttempts = useRef(0);
  const [settleTick, setSettleTick] = useState(0);

  /**
   * Readability first: fit the flow, but never below 0.62 zoom. If it cannot fit
   * there, centre on the entry node at that zoom — a readable partial view beats
   * an unreadable whole (the minimap and the Fit button give orientation).
   */
  const edgeSet = useMemo(() => canvasEdgeSet(nodes, edges), [nodes, edges]);

  const fitSmart = useCallback(
    async (floor: number = FIT_FLOOR, duration = 0): Promise<number | null> => {
      const canvas = document.querySelector(".as-canvas");
      if (!canvas || !nodes.length) return null;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (!width || !height) return null;
      // A floating inspector covers part of the canvas: fit and centre on the
      // area the author can actually see.
      const occlusion = dockOpen && dockOverlay ? Math.min(dockWidth + 12, width * 0.6) : 0;
      const visibleWidth = Math.max(80, width - occlusion);
      // Closed form from the canvas state: the same maths every time, so a load,
      // a resize and a manual fit cannot disagree.
      const boxes = [...nodes, ...edgeSet.terminals].map((node) => ({
        left: node.position.x,
        top: node.position.y,
        right: node.position.x + FIT_CARD_WIDTH,
        bottom: node.position.y + FIT_CARD_HEIGHT,
      }));
      const left = Math.min(...boxes.map((b) => b.left));
      const top = Math.min(...boxes.map((b) => b.top));
      const right = Math.max(...boxes.map((b) => b.right));
      const bottom = Math.max(...boxes.map((b) => b.bottom));
      const padding = 0.06;
      const wanted = Math.min(
        (visibleWidth * (1 - padding)) / Math.max(1, right - left),
        (height * (1 - padding)) / Math.max(1, bottom - top),
      );
      const zoom = Math.max(floor, Math.min(1, wanted));
      const centreX = (left + right) / 2;
      const centreY = (top + bottom) / 2;
      await setViewport(
        {
          x: width / 2 - occlusion / 2 - centreX * zoom,
          y: height / 2 - centreY * zoom,
          zoom,
        },
        { duration },
      );
      return zoom;
    },
    [dockOpen, dockOverlay, dockWidth, edgeSet.terminals, nodes, setViewport],
  );

  /** The canvas re-fits after a dock resize so the flow never hides behind it. */
  // A wide dock on a narrow screen would leave a thumbnail-sized canvas: give the
  // space back by folding the component palette away (the toolbar toggle restores
  // it, and widening the window restores it automatically).


  useEffect(() => {
    const apply = () => {
      const width = window.innerWidth;
      setDockOverlay(width < DOCK_OVERLAY_MAX);
      // Below 1600px a wide dock costs too much canvas; below 1200px the palette
      // goes away regardless so the flow keeps a usable width.
      const tight = width < 1200 || (width < 1600 && dockWidth > 420);
      if (tight && !paletteAutoCollapsed) {
        setPaletteOpen(false);
        setPaletteAutoCollapsed(true);
        return;
      }
      if (paletteAutoCollapsed && !tight) {
        setPaletteOpen(true);
        setPaletteAutoCollapsed(false);
      }
    };
    apply();
    window.addEventListener("resize", apply);
    return () => window.removeEventListener("resize", apply);
  }, [dockWidth, paletteAutoCollapsed]);

  /** Manual toggles win until the geometry changes again. */
  const togglePalette = useCallback(() => setPaletteOpen((open) => !open), []);

  const refitSoon = useCallback(() => {
    window.setTimeout(() => void fitSmart(pendingFit.current ?? FIT_FLOOR), 120);
  }, [fitSmart]);

  const selectedId = selected[0] ?? null;
  const selectedData: NodeData | null = useMemo(
    () => nodes.find((n) => n.id === selectedId)?.data ?? null,
    [nodes, selectedId],
  );
  const nodeErrors = useMemo(() => errorsByNode(report, nodes), [report, nodes]);
  const participants = useMemo(() => {
    const out: Record<string, number> = {};
    for (const node of nodes) {
      if (!["supervisor", "swarm", "graph"].includes(node.data.paletteType)) continue;
      out[node.id] = participantsFor(node.id, nodes, edges).length;
    }
    return out;
  }, [nodes, edges]);
  const templates = templateEntries(catalog);
  const patternNode = useMemo(() => nodes.find((n) => ["supervisor", "swarm"].includes(n.data.paletteType)) ?? null, [nodes]);

  useEffect(() => {
    onFocusChecks(() => setInspectorTab("report"));
  }, [onFocusChecks]);

  // Fit whenever a document or a blueprint lands on the canvas, so a saved flow
  // never opens with its nodes outside the viewport.
  // Opening a document always fits (floor 0.45 so nothing is left off-canvas).
  useEffect(() => {
    if (!fitToken) return;
    pendingFit.current = FIT_FLOOR;
    fitAttempts.current = 0;
  }, [fitToken]);

  // The shell owns the canvas measurement: ReactFlow's own "nodes initialised"
  // signal is not reliable enough to gate a fit on (a call issued before the
  // instance is ready is dropped, which is how polish-loop never fitted).
  const onFlowSettled = useCallback((size: { width: number; height: number }) => {
    const previous = settledRef.current;
    const changed =
      Math.abs(previous.width - size.width) > 1 || Math.abs(previous.height - size.height) > 1;
    settledRef.current = size;
    if (changed) setSettleTick((tick) => tick + 1);
  }, []);

  useEffect(() => {
    const canvas = document.querySelector(".as-canvas");
    if (!canvas || typeof ResizeObserver === "undefined") return;
    const read = () => {
      const box = canvas.getBoundingClientRect();
      if (box.width && box.height) onFlowSettled({ width: box.width, height: box.height });
    };
    const observer = new ResizeObserver(read);
    observer.observe(canvas);
    read();
    return () => observer.disconnect();
  }, [onFlowSettled]);

  useEffect(() => {
    const size = settledRef.current;
    if (!size.width || !size.height) return;
    // Nothing to fit yet (the document is still loading): stay owed.
    if (!nodes.length) return;
    const owed = pendingFit.current;
    if (owed != null) pendingFit.current = null;
    const floor = owed ?? FIT_FLOOR;
    // A resize that no fit has caught up with (dock grew for an approval, window
    // changed, palette toggled) always re-fits the current document.
    void fitSmart(floor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settleTick, nodes.length, fitSmart]);

  // Dock open/close and palette toggles change the canvas height: re-fit.
  useEffect(() => {
    pendingFit.current = FIT_FLOOR;
    setSettleTick((tick) => tick + 1);
  }, [runOpen, dockOpen, paletteOpen]);

  /** Manual "Fit": everything visible first, then the reading floor. */
  const fitNow = useCallback(() => {
    pendingFit.current = SMART_MIN_ZOOM;
    setSettleTick((tick) => tick + 1);
  }, []);
  const compiled = useMemo(() => graphToConfig(nodes, edges, meta), [nodes, edges, meta]);

  /* ------------------------------------------------------ canvas operations */

  /** Cards are at most ~236×170: scan right/down from the requested point for a
   *  free slot so a palette click or a drop never lands on an existing node. */
  const freeSlot = useCallback(
    (wanted: { x: number; y: number }, visible?: { x: number; y: number; width: number; height: number }) => {
      const width = 260;
      const height = 190;
      const free = (candidate: { x: number; y: number }) =>
        !nodes.some(
          (n) => Math.abs(n.position.x - candidate.x) < width && Math.abs(n.position.y - candidate.y) < height,
        );
      if (free(wanted)) return wanted;
      // Prefer a free slot the author can actually see, scanning the visible
      // flow rectangle from its top-left corner.
      if (visible) {
        for (let row = 0; row < 6; row += 1) {
          for (let column = 0; column < 6; column += 1) {
            const candidate = {
              x: visible.x + column * width,
              y: visible.y + row * height,
            };
            if (candidate.x + CARD_WIDTH > visible.x + visible.width) continue;
            if (candidate.y + CARD_HEIGHT > visible.y + visible.height) continue;
            if (free(candidate)) return candidate;
          }
        }
      }
      for (let row = 0; row < 24; row += 1) {
        for (let column = 0; column < 8; column += 1) {
          const candidate = { x: wanted.x + column * width, y: wanted.y + row * height };
          if (free(candidate)) return candidate;
        }
      }
      return { x: wanted.x, y: wanted.y + nodes.length * height };
    },
    [nodes],
  );

  const addComponent = useCallback(
    (paletteType: string, position?: { x: number; y: number }) => {
      const fallback = { x: 96 + (nodes.length % 4) * 48, y: 104 + nodes.length * 28 };
      const canvas = document.querySelector(".as-canvas");
      const visible = canvas
        ? (() => {
            const rect = canvas.getBoundingClientRect();
            const topLeft = screenToFlowPosition({ x: rect.left, y: rect.top });
            const bottomRight = screenToFlowPosition({ x: rect.right, y: rect.bottom });
            return {
              x: topLeft.x + 16,
              y: topLeft.y + 16,
              width: bottomRight.x - topLeft.x - 32,
              height: bottomRight.y - topLeft.y - 32,
            };
          })()
        : undefined;
      const node = createNode(paletteType, freeSlot(position ?? fallback, visible), catalog);
      // If the chosen slot is outside what the author can see, bring it into view.
      const offscreen =
        visible &&
        (node.position.x < visible.x ||
          node.position.y < visible.y ||
          node.position.x + CARD_WIDTH > visible.x + visible.width ||
          node.position.y + CARD_HEIGHT > visible.y + visible.height);
      // A supervisor/swarm marker adopts the agents already on the canvas (and a
      // new agent joins an existing marker), so the team is wired either way.
      // The custom-graph marker is a mode switch, not a hub: it stays unwired.
      const markers = nodes.filter((n) => n.data.paletteType === "supervisor" || n.data.paletteType === "swarm");
      const agents = nodes.filter((n) => n.data.paletteType === "agent" || n.data.paletteType === "deep_agent");
      const wire = (source: string, target: string): StudioEdge => ({
        id: `e-${source}-${target}`,
        source,
        target,
        type: "studio",
      });
      let edges: StudioEdge[] = [];
      if (paletteType === "supervisor" || paletteType === "swarm") {
        edges = agents.map((agent) => wire(agent.id, node.id));
      } else if (paletteType === "agent" || paletteType === "deep_agent") {
        edges = markers.map((marker) => wire(node.id, marker.id));
      }
      dispatch({ type: "add-nodes", nodes: [node], edges, select: true });
      if (offscreen) {
        void setCenter(node.position.x + CARD_WIDTH / 2, node.position.y + CARD_HEIGHT / 2, {
          zoom: getViewport().zoom,
          duration: 250,
        });
      }
      setStatus(edges.length ? `Added ${node.data.label} and wired ${edges.length} connection(s)` : `Added ${node.data.label}`);
    },
    [catalog, dispatch, freeSlot, getViewport, nodes, screenToFlowPosition, setCenter, setStatus],
  );

  const applyTemplate = useCallback(
    (templateId: string, label?: string) => {
      const instantiated = instantiateTemplate(catalog, templateId);
      const name = label ?? templates.find((t) => t.template === templateId)?.label ?? templateId;
      if (!instantiated.nodes.length) {
        dispatch({ type: "patch-meta", patch: { template: templateId } });
        setStatus(`${name}: empty blueprint — the canvas stays yours`);
        return;
      }
      dispatch({ type: "replace-graph", nodes: instantiated.nodes, edges: instantiated.edges });
      dispatch({ type: "patch-meta", patch: { template: templateId } });
      doc.requestFit();
      setStatus(`Loaded the ${name} blueprint · ⌘Z to undo`);
    },
    [catalog, dispatch, doc, setStatus, templates],
  );

  /** Add (or adopt) the supervisor/swarm marker, wiring every agent already there. */
  const applyPattern = useCallback(
    (patternId: string) => {
      const existing = nodes.find((n) => n.data.paletteType === patternId);
      if (!existing) {
        addComponent(patternId);
        return;
      }
      // Already on the canvas: adopt the agents that are not wired to it yet.
      const wired = new Set(
        edges.filter((e) => e.target === existing.id).map((e) => e.source),
      );
      const missing = nodes.filter(
        (n) => (n.data.paletteType === "agent" || n.data.paletteType === "deep_agent") && !wired.has(n.id),
      );
      dispatch({ type: "select", ids: [existing.id] });
      if (!missing.length) {
        setStatus(`${patternId} already has every agent connected`);
        return;
      }
      dispatch({
        type: "add-nodes",
        nodes: [],
        edges: missing.map((agent) => ({
          id: `e-${agent.id}-${existing.id}`,
          source: agent.id,
          target: existing.id,
          type: "studio",
        })),
      });
      setStatus(`Connected ${missing.length} agent(s) to the ${patternId}`);
    },
    [addComponent, dispatch, edges, nodes, setStatus],
  );

  const patchData = useCallback(
    (patch: Partial<NodeData>) => {
      if (!selectedId) return;
      dispatch({ type: "patch-data", id: selectedId, patch });
    },
    [dispatch, selectedId],
  );

  const onRoutesChange = useCallback(
    (routes: RouterRoute[], defaultRoute: string) => {
      if (!selectedId) return;
      dispatch({ type: "patch-data", id: selectedId, patch: { routes, defaultRoute } });
      const wanted = new Set(routes.map((r) => r.to).filter((to) => to && to !== "__end__"));
      const stale = edges.filter((e) => e.source === selectedId && !wanted.has(e.target));
      if (stale.length) {
        dispatch({ type: "edges-change", changes: stale.map((e) => ({ type: "remove", id: e.id })) });
      }
      const missing = [...wanted].filter(
        (to) => !edges.some((e) => e.source === selectedId && e.target === to) && nodes.some((n) => n.id === to),
      );
      if (missing.length) {
        dispatch({
          type: "add-nodes",
          nodes: [],
          edges: missing.map((to) => ({
            id: `e-${selectedId}-${to}`,
            source: selectedId as string,
            target: to,
            type: "studio",
          })),
        });
      }
    },
    [dispatch, edges, nodes, selectedId],
  );

  const tidy = useCallback(() => {
    dispatch({ type: "replace-graph", nodes: layoutGraph(nodes, edges), edges });
    setStatus("Tidied up");
  }, [dispatch, edges, nodes, setStatus]);

  const setEntry = useCallback(
    (id: string) => {
      if (!id) return;
      dispatch({
        type: "replace-graph",
        nodes: nodes.map((n) => ({ ...n, data: { ...n.data, entry: n.id === id } })),
        edges,
      });
      setStatus("Entry node updated");
    },
    [dispatch, edges, nodes, setStatus],
  );

  const duplicate = useCallback(
    (id: string) => {
      const source = nodes.find((n) => n.id === id);
      if (!source) return;
      const copy = createNode(
        source.data.paletteType,
        { x: source.position.x + 40, y: source.position.y + 40 },
        catalog,
        source.data,
      );
      const label = String(source.data.name ?? source.data.label ?? "Node");
      copy.data = { ...source.data, name: `${label} copy`, label: `${label} copy`, entry: false };
      dispatch({ type: "add-nodes", nodes: [copy], select: true });
    },
    [catalog, dispatch, nodes],
  );

  const draft = draftDefinition();
  const onConnect = useCallback(
    (connection: { source?: string | null; target?: string | null; sourceHandle?: string | null; targetHandle?: string | null }) => {
      if (!connection.source || !connection.target) return;
      dispatch({
        type: "connect",
        connection: { source: connection.source, target: connection.target, sourceHandle: connection.sourceHandle ?? null, targetHandle: connection.targetHandle ?? null },
        edge: {
          id: `e-${connection.source}-${connection.target}`,
          source: connection.source,
          target: connection.target,
          sourceHandle: connection.sourceHandle ?? undefined,
          targetHandle: connection.targetHandle ?? undefined,
          type: "studio",
        },
      });
    },
    [dispatch],
  );

  return (
    <div className="as-studio aa-root" data-testid="agent-studio">
      <StudioToolbar
        meta={meta}
        current={current}
        statusText={error || status || (state.dirty ? "Unsaved changes" : "")}
        error={error}
        checks={(report?.ok === false ? report.errors.length : 0) || (checksNotice ?? 0)}
        warnings={report?.warnings.length ?? 0}
        onOpenChecks={focusChecks}
        busy={busy}
        missing={missing}
        canUndo={canUndo(state)}
        canRedo={canRedo(state)}
        dirty={state.dirty}
        onTitle={(name) => dispatch({ type: "patch-meta", patch: { name } })}
        onSave={() => void save(false)}
        onValidate={() => void validate()}
        onPublish={() => void publish()}
        onTest={() => setRunOpen(true)}
        onUndo={() => dispatch({ type: "undo" })}
        onRedo={() => dispatch({ type: "redo" })}
        onTidy={tidy}
        onFit={fitNow}
        onExport={exportJson}
        onImport={importJson}
        onAccess={() => setAccessOpen(true)}
      />

      <div
        hidden
        data-testid="studio-graph"
        data-node-types={paletteTypes(nodes)}
        data-node-count={nodes.length}
        data-edges={edgeTypePairs(nodes, edgeSet.edges)}
        data-kind={compiled.kind}
        data-pattern={String(compiled.config.pattern ?? "")}
        data-template={meta.template}
        data-selected={selectedId ?? ""}
      />

      {layoutNotice ? (
        <div className="as-notice as-notice-info" role="status" data-testid="studio-layout-notice">
          <LayoutGrid size={14} aria-hidden="true" />
          <span>{layoutNotice}</span>
          <button
            type="button"
            className="as-btn as-btn-icon as-btn-ghost as-notice-close"
            title="Dismiss"
            onClick={dismissLayoutNotice}
            data-testid="studio-layout-dismiss"
          >
            <X size={14} />
          </button>
        </div>
      ) : null}

      {migrationNotice ? (
        <div className="as-notice" role="status" data-testid="studio-migration-notice">
          <AlertTriangle size={14} aria-hidden="true" />
          <span>{migrationNotice}</span>
          <button
            type="button"
            className="as-btn as-btn-icon as-btn-ghost as-notice-close"
            title="Dismiss"
            onClick={dismissMigrationNotice}
            data-testid="studio-migration-dismiss"
          >
            <X size={14} />
          </button>
        </div>
      ) : null}

      <div className="as-body">
        {paletteOpen ? (
        <Palette
          catalog={catalog}
          agents={agents}
          activeSlug={current?.slug ?? null}
          highlightSlug={String(selectedData?.ref ?? "") || null}
          loading={loadingAgents}
          onOpenAgent={(slug) => void openSlug(slug)}
          onNewAgent={newAgent}
          onAddComponent={(paletteType) => addComponent(paletteType)}
          onApplyTemplate={applyTemplate}
          onDragComponent={(event, paletteType) => {
            event.dataTransfer.setData("application/studio-node", paletteType);
            event.dataTransfer.effectAllowed = "move";
          }}
          onCollapse={() => setPaletteOpen(false)}
        />
        ) : null}

        <main className="as-center">
          <div className="as-canvas-toolbar">
            <label className="as-toolbar-field">
              <span>Blueprint</span>
              <select
                className="as-select as-select-sm"
                value={patternNode?.data.paletteType ?? meta.template}
                data-testid="studio-template-toolbar"
                onChange={(event) => {
                  const value = event.target.value;
                  if (value === "supervisor" || value === "swarm") {
                    applyPattern(value);
                    return;
                  }
                  dispatch({ type: "patch-meta", patch: { template: value } });
                  setStatus("Blueprint updated — Apply loads its nodes");
                }}
              >
                {templates.map((template) => (
                  <option key={template.template} value={template.template}>
                    {template.label}
                  </option>
                ))}
                {patternEntries(catalog)
                  .filter((pattern) => pattern.id === "supervisor" || pattern.id === "swarm")
                  .map((pattern) => (
                    <option key={pattern.id} value={pattern.id}>
                      {pattern.label}
                    </option>
                  ))}
              </select>
            </label>
            <button
              type="button"
              className="as-btn as-btn-sm"
              onClick={() => {
                const chosen = patternNode?.data.paletteType ?? meta.template;
                if (chosen === "supervisor" || chosen === "swarm") applyPattern(chosen);
                else applyTemplate(chosen);
              }}
              data-testid="studio-apply-template"
            >
              Apply
            </button>
            <button
              type="button"
              className="as-btn as-btn-sm as-btn-icon"
              title={dockOpen ? "Hide the inspector panel" : "Show the inspector panel"}
              aria-pressed={dockOpen}
              onClick={() => setDockOpen((open) => !open)}
              data-testid="studio-toggle-dock"
            >
              <PanelRight size={14} />
            </button>
            <button
              type="button"
              className="as-btn as-btn-sm as-btn-icon"
              title={paletteOpen ? "Hide the component palette" : "Show the component palette"}
              aria-pressed={paletteOpen}
              onClick={togglePalette}
              data-testid="studio-toggle-palette"
            >
              <PanelLeft size={14} />
            </button>
            <span className="as-toolbar-spacer" />
            <span className="as-muted as-toolbar-count" data-testid="studio-counts">
              {nodes.length} node{nodes.length === 1 ? "" : "s"} · {edgeSet.semantic} edge
              {edgeSet.semantic === 1 ? "" : "s"}
              {edgeSet.terminals.length
                ? ` · ${edgeSet.terminals.length} end${edgeSet.terminals.length === 1 ? "" : "s"}`
                : ""}
            </span>
          </div>

          <Canvas
            nodes={nodes}
            edges={edges}
            selected={selected}
            catalog={catalog}
            errors={nodeErrors}
            participants={participants}
            onNodesChange={(changes) => dispatch({ type: "nodes-change", changes })}
            onEdgesChange={(changes) => dispatch({ type: "edges-change", changes })}
            onConnect={onConnect}
            onSelect={(ids) => dispatch({ type: "select", ids })}
            onDropType={addComponent}
            onDeleteNodes={(ids) => dispatch({ type: "delete-nodes", ids })}
            onSetEntry={setEntry}
            onDuplicate={duplicate}
            onTidy={tidy}
            onSnapshot={() => dispatch({ type: "snapshot" })}
            onViewportMoved={noteViewportMoved}
            onFlowSettled={onFlowSettled}
            onSave={() => void save(false)}
            onUndo={() => dispatch({ type: "undo" })}
            onRedo={() => dispatch({ type: "redo" })}
            onCopy={() => dispatch({ type: "copy" })}
            onPaste={() => dispatch({ type: "paste" })}
            canPaste={Boolean(state.clipboard?.nodes.length)}
            hint={hint}
            onHint={setHint}
          />
        </main>

        {dockOpen ? <DockGrip width={dockWidth} onWidth={setDockWidth} onCommit={refitSoon} /> : null}

        {dockOpen ? (
        <Inspector
          overlay={dockOverlay}
          width={dockWidth}
          nodeId={selectedId}
          data={selectedData}
          catalog={catalog}
          nodes={nodes}
          edges={edges}
          agents={agents}
          meta={meta}
          errors={nodeErrors}
          report={report}
          plan={plan}
          planning={planning}
          tab={inspectorTab}
          onTabChange={setInspectorTab}
          checksNotice={checksNotice}
          onChecksSeen={clearChecksNotice}
          modules={modules}
          onPatchData={patchData}
          onRoutesChange={onRoutesChange}
          onMetaChange={(patch) => dispatch({ type: "patch-meta", patch })}
          onDelete={() => selectedId && dispatch({ type: "delete-nodes", ids: [selectedId] })}
          onOpenAccess={() => setAccessOpen(true)}
          onSetEntry={setEntry}
        />
        ) : null}
      </div>

      {runOpen ? (
        <RunDock
          key={docId}
          catalog={catalog}
          slug={current?.slug ?? null}
          agentName={meta.name || "Untitled"}
          draftDefinition={draft}
          open={runOpen}
          onClose={() => setRunOpen(false)}
        />
      ) : null}

      {accessOpen && current ? <AccessDialog agent={current} onClose={() => setAccessOpen(false)} /> : null}
      {accessOpen && !current ? (
        <div className="as-overlay" onMouseDown={() => setAccessOpen(false)}>
          <div className="as-dialog" role="dialog" aria-modal="true" aria-label="Access">
            <div className="as-dialog-head">
              <h2 className="as-dialog-title">Access</h2>
              <p className="as-dialog-sub">Save this agent first — access is granted per saved definition.</p>
            </div>
            <div className="as-dialog-foot">
              <button type="button" className="as-btn" onClick={() => setAccessOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
