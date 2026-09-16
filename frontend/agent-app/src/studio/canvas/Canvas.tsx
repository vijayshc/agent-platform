/** ReactFlow host: drop targets, connection validation, keyboard, context menu.
 *
 * The canvas is intentionally thin — every mutation goes back through the
 * reducer in StudioApp, so undo/redo, autosave and serialization see one state.
 * `StudioPage` owns the `ReactFlowProvider`.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent as ReactMouseEvent } from "react";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  MiniMap,
  Panel,
  ReactFlow,
  getSmoothStepPath,
  useNodesInitialized,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type EdgeProps,
  type NodeChange,
} from "@xyflow/react";
import { Crosshair, LayoutGrid, Maximize2, Plus, Trash2 } from "lucide-react";
import { catalogIcon } from "../model/catalog";
import type { StudioCatalog, StudioEdge, StudioNode } from "../model/types";
import { canvasEdgeSet, connectionRejection, decorateEdges, emphasiseEdges, isValidConnection } from "./edges";
import { CanvasContext, NodeCard, TerminalNode } from "./NodeCard";
import "./canvas.css";

const nodeTypes = { studio: NodeCard, terminal: TerminalNode };

function StudioEdgeLine({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  data,
  markerEnd,
}: EdgeProps) {
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
  });
  const variant = String(data?.variant ?? "flow");
  const lane = typeof data?.lane === "number" ? data.lane : 0;
  // An edge whose endpoints share a column (or point backwards) would otherwise
  // loop through the cards between them: route it out into the gutter instead.
  const sameColumn = Math.abs(targetX - sourceX) < 80 && Math.abs(targetY - sourceY) > 40;
  const backwards = targetX < sourceX - 80;
  if (sameColumn || backwards) {
    const gutter = sameColumn
      ? Math.max(sourceX, targetX) + 56
      : Math.min(sourceX, targetX) - 56;
    const direction = gutter > sourceX ? 1 : -1;
    const path = [
      `M ${sourceX} ${sourceY}`,
      `H ${gutter - direction * 10}`,
      `Q ${gutter} ${sourceY} ${gutter} ${sourceY + Math.sign(targetY - sourceY) * 10}`,
      `V ${targetY - Math.sign(targetY - sourceY) * 10}`,
      `Q ${gutter} ${targetY} ${gutter - direction * 10} ${targetY}`,
      `H ${targetX}`,
    ].join(" ");
    return (
      <>
        <BaseEdge id={id} path={path} markerEnd={markerEnd} className={`as-edge as-edge-${variant}${data?.dim ? " is-dim" : ""}`} />
        {label ? (
          <EdgeLabelRenderer>
            <div
              className={`as-edge-label as-edge-label-${variant}${data?.dim ? " is-dim" : ""}`}
              style={{ transform: `translate(-50%, -50%) translate(${gutter}px, ${(sourceY + targetY) / 2}px)` }}
            >
              {String(label)}
            </div>
          </EdgeLabelRenderer>
        ) : null}
      </>
    );
  }
  const [lanedPath, lanedX, lanedY] = lane
    ? getSmoothStepPath({
        sourceX,
        sourceY: sourceY + lane,
        targetX,
        targetY: targetY + lane,
        sourcePosition,
        targetPosition,
        borderRadius: 16,
      })
    : [path, labelX, labelY];
  return (
    <>
      <BaseEdge
        id={id}
        path={lanedPath}
        markerEnd={markerEnd}
        className={`as-edge as-edge-${variant}${data?.dim ? " is-dim" : ""}`}
      />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className={`as-edge-label as-edge-label-${variant}${data?.dim ? " is-dim" : ""}`}
            style={{ transform: `translate(-50%, -50%) translate(${lanedX}px, ${lanedY}px)` }}
          >
            {String(label)}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = { studio: StudioEdgeLine };

export interface CanvasProps {
  nodes: StudioNode[];
  edges: StudioEdge[];
  selected: string[];
  catalog: StudioCatalog | null;
  errors: Record<string, string[]>;
  participants: Record<string, number>;
  onNodesChange: (changes: NodeChange<StudioNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<StudioEdge>[]) => void;
  onConnect: (connection: Connection) => void;
  onSelect: (ids: string[]) => void;
  onDropType: (paletteType: string, position: { x: number; y: number }) => void;
  onDeleteNodes: (ids: string[]) => void;
  onSetEntry: (id: string) => void;
  onDuplicate: (id: string) => void;
  onTidy: () => void;
  onSnapshot: () => void;
  onViewportMoved: () => void;
  /** Fired when ReactFlow has measured the nodes and the pane has a size. */
  onFlowSettled: (size: { width: number; height: number }) => void;
  onSave: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onCopy: () => void;
  onPaste: () => void;
  canPaste: boolean;
  hint: string | null;
  onHint: (hint: string | null) => void;
}

export function Canvas(props: CanvasProps) {
  const {
    nodes,
    edges,
    selected,
    catalog,
    errors,
    participants,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onSelect,
    onDropType,
    onDeleteNodes,
    onSetEntry,
    onDuplicate,
    onTidy,
    onSnapshot,
    onViewportMoved,
    onFlowSettled,
    onSave,
    onUndo,
    onRedo,
    onCopy,
    onPaste,
    canPaste,
    hint,
    onHint,
  } = props;
  const { screenToFlowPosition, fitView } = useReactFlow();
  const [menu, setMenu] = useState<{ x: number; y: number; nodeId: string | null } | null>(null);
  const [canvasHeight, setCanvasHeight] = useState(800);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  // ReactFlow only fits correctly once it has measured the nodes; report every
  // settled size so the shell can fit against the real (final) canvas box.
  const measured = useNodesInitialized();

  // Derived wiring (router routes, fan-out Send, end-of-run markers) is computed
  // here so the canvas always shows exactly what the definition will run.
  const canvasEdges = useMemo(() => canvasEdgeSet(nodes, edges), [nodes, edges]);
  // ReactFlow renders the selection ring from the node objects, so the app's
  // selection list is projected onto them (one source of truth).
  const rendered = useMemo(
    () =>
      [...nodes.map((node) => ({ ...node, selected: selected.includes(node.id) })), ...canvasEdges.terminals],
    [nodes, canvasEdges.terminals, selected],
  );
  const decorated = useMemo(
    () => emphasiseEdges(decorateEdges(rendered, canvasEdges.edges), selected),
    [rendered, canvasEdges.edges, selected],
  );
  const contextValue = useMemo(() => ({ catalog, errors, participants }), [catalog, errors, participants]);

  const closeMenu = useCallback(() => setMenu(null), []);

  // The minimap is an overlay: on a short canvas (run dock open) it would cover
  // cards, so it only renders when there is room for it.
  useEffect(() => {
    const element = canvasRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      const height = box?.height ?? 0;
      if (height) setCanvasHeight(height);
      if (box && measured) onFlowSettled({ width: box.width, height: box.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [measured, onFlowSettled]);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element || !measured) return;
    const box = element.getBoundingClientRect();
    if (box.width && box.height) onFlowSettled({ width: box.width, height: box.height });
  }, [measured, nodes.length, onFlowSettled]);

  useEffect(() => {
    if (!menu) return;
    const onDown = () => closeMenu();
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", closeMenu);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", closeMenu);
    };
  }, [menu, closeMenu]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing =
        !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        onSave();
        return;
      }
      if (mod && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) onRedo();
        else onUndo();
        return;
      }
      if (mod && event.key.toLowerCase() === "y") {
        event.preventDefault();
        onRedo();
        return;
      }
      if (typing) return;
      if (mod && event.key.toLowerCase() === "c") {
        onCopy();
        return;
      }
      if (mod && event.key.toLowerCase() === "v") {
        if (canPaste) onPaste();
        return;
      }
      if (event.key === "Escape") {
        onSelect([]);
        return;
      }
      if ((event.key === "Delete" || event.key === "Backspace") && selected.length) {
        event.preventDefault();
        onDeleteNodes(selected);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [canPaste, onCopy, onDeleteNodes, onPaste, onRedo, onSave, onSelect, onUndo, selected]);

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      const paletteType = event.dataTransfer.getData("application/studio-node");
      if (!paletteType) return;
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      onDropType(paletteType, { x: Math.round(position.x), y: Math.round(position.y) });
    },
    [onDropType, screenToFlowPosition],
  );

  const openMenu = useCallback((event: ReactMouseEvent, nodeId: string | null) => {
    event.preventDefault();
    setMenu({ x: event.clientX, y: event.clientY, nodeId });
  }, []);

  const menuNode = menu?.nodeId ? nodes.find((n) => n.id === menu.nodeId) ?? null : null;

  return (
    <div
      className="as-canvas"
      ref={canvasRef}
      data-testid="studio-canvas"
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }}
      onDrop={handleDrop}
      onContextMenu={(event) => openMenu(event, null)}
    >
      <CanvasContext.Provider value={contextValue}>
        <ReactFlow
          nodes={rendered}
          edges={decorated}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeDragStart={onSnapshot}
          onMoveEnd={(_event, viewport) => {
            if (viewport.zoom || viewport.x || viewport.y) onViewportMoved();
          }}
          onNodeClick={(event, node) => onSelect(event.shiftKey ? [...selected, node.id] : [node.id])}
          onNodeContextMenu={(event, node) => {
            onSelect([node.id]);
            openMenu(event, node.id);
          }}
          onPaneClick={() => {
            onSelect([]);
            closeMenu();
          }}
          onNodesDelete={(deleted) => {
            const ids = deleted.map((n) => n.id).filter((id) => !id.startsWith("__end__"));
            if (ids.length) onDeleteNodes(ids);
          }}
          isValidConnection={(connection) =>
            isValidConnection(nodes, String(connection.source ?? ""), String(connection.target ?? ""))
          }
          onConnectEnd={(_event, state) => {
            const from = (state as { fromNode?: { id: string } | null }).fromNode;
            const to = (state as { toNode?: { id: string } | null }).toNode;
            if (state?.isValid === false && from && to) {
              onHint(connectionRejection(nodes, from.id, to.id));
            }
          }}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          defaultViewport={{ x: 32, y: 32, zoom: 1 }}
          minZoom={0.3}
          maxZoom={1.75}
          snapToGrid
          snapGrid={[8, 8]}
          deleteKeyCode={null}
          multiSelectionKeyCode="Shift"
          selectionOnDrag
          panOnDrag={[1, 2]}
          elevateNodesOnSelect
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          {nodes.length && canvasHeight > 420 ? (
            <MiniMap pannable zoomable position="bottom-right" className="as-minimap" style={{ width: 152, height: 102 }} />
          ) : null}
          <Controls showInteractive={false} className="as-controls" />
          {hint ? (
            <Panel position="top-center" className="as-canvas-hint">
              {hint}
            </Panel>
          ) : null}
        </ReactFlow>
      </CanvasContext.Provider>

      {!nodes.length ? <EmptyCanvas catalog={catalog} /> : null}

      {menu ? (
        <div className="as-menu" style={{ left: menu.x, top: menu.y }} role="menu">
          {menuNode ? (
            <>
              <button
                type="button"
                className="as-menu-item"
                role="menuitem"
                onClick={() => {
                  onDuplicate(menuNode.id);
                  closeMenu();
                }}
              >
                <Plus size={13} /> Duplicate
              </button>
              <button
                type="button"
                className="as-menu-item"
                role="menuitem"
                disabled={Boolean(menuNode.data.entry)}
                onClick={() => {
                  onSetEntry(menuNode.id);
                  closeMenu();
                }}
              >
                <Crosshair size={13} /> Set as entry
              </button>
              <button
                type="button"
                className="as-menu-item as-menu-danger"
                role="menuitem"
                onClick={() => {
                  onDeleteNodes([menuNode.id]);
                  closeMenu();
                }}
              >
                <Trash2 size={13} /> Delete
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="as-menu-item"
                role="menuitem"
                disabled={!canPaste}
                onClick={() => {
                  onPaste();
                  closeMenu();
                }}
              >
                Paste
              </button>
              <button
                type="button"
                className="as-menu-item"
                role="menuitem"
                onClick={() => {
                  onTidy();
                  closeMenu();
                }}
              >
                <LayoutGrid size={13} /> Tidy up
              </button>
              <button
                type="button"
                className="as-menu-item"
                role="menuitem"
                onClick={() => {
                  fitView({ padding: 0.25, duration: 250 });
                  closeMenu();
                }}
              >
                <Maximize2 size={13} /> Fit to view
              </button>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

function EmptyCanvas({ catalog }: { catalog: StudioCatalog | null }) {
  const runtimes = catalog?.runtimes ?? [];
  const patterns = catalog?.patterns ?? [];
  return (
    <div className="as-empty-canvas" data-testid="studio-empty">
      <div className="as-empty-card">
        <h3>Start a flow</h3>
        <p>
          Drag an <strong>Agent</strong> from the left rail, or load a blueprint. Every node shows the shipped builder
          it compiles to.
        </p>
        <ul className="as-empty-list">
          {runtimes.slice(0, 1).map((runtime) => {
            const Icon = catalogIcon(runtime.icon);
            return (
              <li key={runtime.id}>
                <Icon size={13} /> {runtime.label} → <code>{runtime.builder}</code>
              </li>
            );
          })}
          {patterns.slice(0, 2).map((pattern) => {
            const Icon = catalogIcon(pattern.icon);
            return (
              <li key={pattern.id}>
                <Icon size={13} /> {pattern.label} → <code>{pattern.builder}</code>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
