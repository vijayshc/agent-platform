/** Studio document state machine: catalog, loading, save/validate/publish, plan.
 *
 * Owns everything that talks to the API so `StudioApp` stays a composition
 * layer. The canvas reducer lives here too, because saving, autosaving and the
 * compile plan all read the same nodes/edges/meta.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useReactFlow, type Viewport } from "@xyflow/react";
import { apiGet } from "../api";
import type { AgentDef } from "../types";
import {
  createDefinition,
  compilePlan,
  getDefinition,
  listDefinitions,
  publishDefinition,
  updateDefinition,
  validateDefinition,
} from "./api";
import { fetchCatalog } from "./model/catalog";
import { emptyMeta, initialState, studioReducer } from "./model/graphState";
import { loadDefinition, type LoadedGraph } from "./model/migrate";
import { layoutGraph, layoutNeedsReflow } from "./model/layout";
import { graphToConfig, preservedConfig } from "./model/serialize";
import type {
  CompilePlan,
  DefinitionBody,
  GraphMeta,
  StudioCatalog,
  StudioEdge,
  StudioNode,
  ValidateReport,
} from "./model/types";

/** The viewport a definition was saved with, if any. */
function readStoredViewport(config: Record<string, unknown> | undefined): Viewport | undefined {
  const studio = config?.studio as { viewport?: Viewport } | undefined;
  const viewport = studio?.viewport;
  return viewport && typeof viewport.zoom === "number" ? viewport : undefined;
}

export function slugify(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || "agent"
  );
}

export interface StudioDocument {
  state: ReturnType<typeof studioReducer>;
  dispatch: React.Dispatch<Parameters<typeof studioReducer>[1]>;
  catalog: StudioCatalog | null;
  agents: AgentDef[];
  loadingAgents: boolean;
  current: AgentDef | null;
  status: string;
  error: string | null;
  report: ValidateReport | null;
  plan: CompilePlan | null;
  planning: boolean;
  busy: boolean;
  hint: string | null;
  setHint: (hint: string | null) => void;
  /** What an older canvas lost on load — null when nothing was dropped. */
  migrationNotice: string | null;
  dismissMigrationNotice: () => void;
  /** Set when a stored canvas had to be re-flowed for readability. */
  layoutNotice: string | null;
  dismissLayoutNotice: () => void;
  /** Keys of the admin modules the signed-in user can open. */
  modules: string[];
  /** True when the requested slug could not be loaded (nothing is editable). */
  missing: boolean;
  /** Check-error count from the last save, until the author looks at Checks. */
  checksNotice: number | null;
  clearChecksNotice: () => void;
  /** Opens the Checks tab from the status line. */
  focusChecks: () => void;
  onFocusChecks: (handler: () => void) => void;
  /** Increments when the canvas should be fitted to its nodes. */
  fitToken: number;
  requestFit: () => void;
  /** Increments when a different document lands on the canvas. */
  docId: number;
  setStatus: (status: string) => void;
  setError: (error: string | null) => void;
  setReport: (report: ValidateReport | null) => void;
  setCurrent: (definition: AgentDef | null) => void;
  save: (autosave?: boolean) => Promise<AgentDef | null>;
  validate: () => Promise<ValidateReport | null>;
  publish: () => Promise<void>;
  draftDefinition: () => { name: string; kind: string; config: Record<string, unknown> } | null;
  openSlug: (slug: string) => Promise<void>;
  newAgent: () => void;
  exportJson: () => void;
  importJson: (file: File) => void;
  noteViewportMoved: () => void;
  refreshAgents: () => Promise<void>;
  exportGraph: (nodes: StudioNode[], edges: StudioEdge[], meta: GraphMeta) => DefinitionBody;
}

export function useStudioDocument(): StudioDocument {
  const [state, dispatch] = useReducer(studioReducer, undefined, () => initialState());
  const [catalog, setCatalog] = useState<StudioCatalog | null>(null);
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [loadingAgents, setLoadingAgents] = useState(true);
  const [current, setCurrent] = useState<AgentDef | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<ValidateReport | null>(null);
  const [plan, setPlan] = useState<CompilePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [migrationNotice, setMigrationNotice] = useState<string | null>(null);
  /** Modules this user may open (skills are authored outside the Studio). */
  const [modules, setModules] = useState<string[]>([]);
  const [missing, setMissing] = useState(false);
  const [layoutNotice, setLayoutNotice] = useState<string | null>(null);
  const [checksNotice, setChecksNotice] = useState<number | null>(null);
  // Bumped whenever a document lands on (or is replaced on) the canvas; the
  // shell fits the viewport to it so nothing loads off-screen.
  const [fitToken, setFitToken] = useState(0);
  const requestFit = useCallback(() => setFitToken((token) => token + 1), []);
  const dismissMigrationNotice = useCallback(() => setMigrationNotice(null), []);
  useEffect(() => {
    void apiGet<{ admin_menu?: { key?: string }[] }>("/api/v1/me")
      .then((me) => setModules((me.admin_menu ?? []).map((item) => String(item.key ?? "")).filter(Boolean)))
      .catch(() => setModules([]));
  }, []);
  const dismissLayoutNotice = useCallback(() => setLayoutNotice(null), []);
  /** Old canvases can store cards on top of each other: re-flow and say so. */
  const settleGraph = useCallback((graph: LoadedGraph): LoadedGraph => {
    const laid = layoutGraph(graph.nodes, graph.edges);
    const changed = graph.nodes.some((node, index) => {
      const next = laid[index];
      return !next || next.position.x !== node.position.x || next.position.y !== node.position.y;
    });
    if (!changed || !layoutNeedsReflow(graph.nodes, graph.edges)) {
      setLayoutNotice(null);
      return graph;
    }
    setLayoutNotice(
      "Layout re-flowed for readability — overlapping cards or crossing links were re-ranked. Undo restores the stored positions.",
    );
    return { ...graph, reflowed: true };
  }, []);
  const clearChecksNotice = useCallback(() => setChecksNotice(null), []);
  const focusChecksRef = useRef<(() => void) | null>(null);
  const onFocusChecks = useCallback((handler: () => void) => {
    focusChecksRef.current = handler;
  }, []);
  const focusChecks = useCallback(() => {
    focusChecksRef.current?.();
    setChecksNotice(null);
  }, []);
  const reportMigration = useCallback((dropped: string[]) => {
    if (!dropped.length) {
      setMigrationNotice(null);
      return;
    }
    const counts = new Map<string, number>();
    for (const type of dropped) counts.set(type, (counts.get(type) ?? 0) + 1);
    const listed = [...counts.entries()].map(([type, count]) => (count > 1 ? `${type} ×${count}` : type)).join(", ");
    const total = dropped.length;
    setMigrationNotice(
      `${total} component${total === 1 ? "" : "s"} from an older canvas version ${
        total === 1 ? "was" : "were"
      } removed because the v2 runtime has no equivalent: ${listed}. ` +
        "Re-add them with Skills or MCP tools on the agent.",
    );
  }, []);
  // Identity of the document on the canvas: a new document resets the run dock.
  const [docId, setDocId] = useState(0);
  const nextDocument = useCallback(() => {
    setDocId((id) => id + 1);
    requestFit();
  }, [requestFit]);
  const preserved = useRef<Record<string, unknown>>({});
  /** Viewport as stored, and whether the author moved it in this session. */
  const storedViewport = useRef<Viewport | undefined>(undefined);
  const viewportMoved = useRef(false);
  const { getViewport } = useReactFlow();
  const { nodes, edges, meta } = state;

  /** A flow the author never named borrows the first agent's name. */
  const derivedName = useCallback((nextNodes: StudioNode[], typed: string): string => {
    if (typed.trim()) return typed.trim();
    const named = nextNodes.find((n) => n.data.paletteType === "agent" || n.data.paletteType === "deep_agent");
    const label = String(named?.data.name ?? "").trim() || String(named?.data.label ?? "").trim();
    return label || "Untitled agent";
  }, []);

  // The canvas reports a move only after the author pans or zooms.
  const noteViewportMoved = useCallback(() => {
    viewportMoved.current = true;
  }, []);

  const exportGraph = useCallback(
    (nextNodes: StudioNode[], nextEdges: StudioEdge[], nextMeta: GraphMeta, autosave = false): DefinitionBody => {
      const name = derivedName(nextNodes, nextMeta.name);
      const viewport = viewportMoved.current ? getViewport() : storedViewport.current;
      const compiled = graphToConfig(nextNodes, nextEdges, { ...nextMeta, name }, viewport, preserved.current);
      return {
        name,
        // A draft's slug follows its name; a saved definition keeps its slug so
        // existing links and API clients do not break on a rename.
        slug: current?.slug || slugify(name),
        kind: compiled.kind,
        config: compiled.config,
        autosave,
      };
    },
    [current?.slug, derivedName, getViewport],
  );

  const catalogRef = useRef<StudioCatalog | null>(null);
  useEffect(() => {
    catalogRef.current = catalog;
  }, [catalog]);

  const openSlug = useCallback(async (slug: string) => {
    setError(null);
    try {
      const definition = await getDefinition(slug);
      setMissing(false);
      preserved.current = preservedConfig(definition.config);
      storedViewport.current = readStoredViewport(definition.config);
      viewportMoved.current = false;
      const activeCatalog = catalogRef.current ?? (await fetchCatalog());
      setCatalog(activeCatalog);
      const loaded = settleGraph(
        loadDefinition(activeCatalog, {
          name: definition.name,
          slug: definition.slug,
          kind: definition.kind,
          config: definition.config,
        }),
      );
      dispatch({ type: "loaded", nodes: loaded.nodes, edges: loaded.edges, meta: loaded.meta });
      if (loaded.reflowed) {
        dispatch({ type: "replace-graph", nodes: layoutGraph(loaded.nodes, loaded.edges), edges: loaded.edges });
        // The re-flow is undoable but must not autosave: opening a document is
        // not an edit (the author can Save or Undo explicitly).
        dispatch({ type: "mark-clean" });
      }
      setCurrent(definition);
      reportMigration(loaded.dropped);
      setStatus(`Loaded ${definition.name} v${definition.version ?? 1}`);
      nextDocument();
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError));
    }
  }, [nextDocument, reportMigration, settleGraph]);

  const refreshAgents = useCallback(async () => {
    setLoadingAgents(true);
    try {
      const listed = await listDefinitions();
      setAgents(listed.agents);
    } catch (listError) {
      setError(listError instanceof Error ? listError.message : String(listError));
    } finally {
      setLoadingAgents(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await fetchCatalog();
        setCatalog(loaded);
        const slug = new URLSearchParams(window.location.search).get("slug");
        if (slug) {
          const definition = await getDefinition(slug);
          setMissing(false);
          preserved.current = preservedConfig(definition.config);
          storedViewport.current = readStoredViewport(definition.config);
          viewportMoved.current = false;
          const graph = settleGraph(
            loadDefinition(loaded, {
              name: definition.name,
              slug: definition.slug,
              kind: definition.kind,
              config: definition.config,
            }),
          );
          dispatch({ type: "loaded", nodes: graph.nodes, edges: graph.edges, meta: graph.meta });
          if (graph.reflowed) {
            dispatch({ type: "replace-graph", nodes: layoutGraph(graph.nodes, graph.edges), edges: graph.edges });
            dispatch({ type: "mark-clean" });
          }
          setCurrent(definition);
          reportMigration(graph.dropped);
          setStatus(`Loaded ${definition.name} v${definition.version ?? 1}`);
          nextDocument();
        }
      } catch (bootError) {
        setError(bootError instanceof Error ? bootError.message : String(bootError));
      }
    })();
  }, []);

  useEffect(() => {
    void refreshAgents();
  }, [refreshAgents]);

  /* ------------------------------------------------------------------- plan */

  useEffect(() => {
    if (!nodes.length) {
      setPlan(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setPlanning(true);
      compilePlan(exportGraph(nodes, edges, meta))
        .then((next) => {
          if (!cancelled) setPlan(next);
        })
        .catch((planError: unknown) => {
          if (cancelled) return;
          setPlan({
            kind: "agent",
            error: planError instanceof Error ? planError.message : String(planError),
            plan: {},
          });
        })
        .finally(() => {
          if (!cancelled) setPlanning(false);
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [nodes, edges, meta, exportGraph]);

  /* ------------------------------------------------------------ save et al */

  const payload = useCallback(() => exportGraph(nodes, edges, meta), [edges, exportGraph, meta, nodes]);

  const save = useCallback(
    async (autosave = false): Promise<AgentDef | null> => {
      setError(null);
      setBusy(true);
      try {
        const body = exportGraph(nodes, edges, meta, autosave);
        const row = current?.id ? await updateDefinition(current.slug, body) : await createDefinition(body);
        setCurrent(row);
        dispatch({ type: "sync-meta", patch: { slug: row.slug, name: row.name } });
        dispatch({ type: "mark-clean" });
        // Saving a definition the checks reject must never be silent: validate
        // alongside the write and say so in the status line.
        const check = autosave ? null : await validateDefinition(row.slug, body).catch(() => null);
        if (check) setReport(check);
        const fresh = check && !check.ok ? check.errors.length : 0;
        const known = fresh || (report && !report.ok ? report.errors.length : 0);
        const suffix = known ? ` · ${known} check error${known === 1 ? "" : "s"} — open Checks` : "";
        setStatus(`${autosave ? `Draft autosaved v${row.version}` : `Saved v${row.version}`}${suffix}`);
        setChecksNotice(known || null);
        window.history.replaceState({}, "", `/agent-studio/editor?slug=${encodeURIComponent(row.slug)}`);
        void refreshAgents();
        return row;
      } catch (saveError) {
        setError(saveError instanceof Error ? saveError.message : String(saveError));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [current, edges, exportGraph, meta, nodes, refreshAgents],
  );

  const validate = useCallback(async (): Promise<ValidateReport | null> => {
    setError(null);
    setBusy(true);
    try {
      const result = await validateDefinition(current?.slug ?? null, payload());
      setReport(result);
      const warn = result.warnings.length
        ? ` · ${result.warnings.length} warning${result.warnings.length === 1 ? "" : "s"} — open Checks`
        : "";
      setStatus(result.ok ? `Valid${warn}` : `Validation failed · ${result.errors.length} error(s)${warn}`);
      return result;
    } catch (validateError) {
      setError(validateError instanceof Error ? validateError.message : String(validateError));
      return null;
    } finally {
      setBusy(false);
    }
  }, [current, payload]);

  const publish = useCallback(async () => {
    const saved = await save(false);
    if (!saved) return;
    const result = await validate();
    if (result && !result.ok) return;
    setBusy(true);
    try {
      const row = await publishDefinition(saved.slug, true);
      setCurrent(row);
      setStatus("Published");
      void refreshAgents();
    } catch (publishError) {
      setError(publishError instanceof Error ? publishError.message : String(publishError));
    } finally {
      setBusy(false);
    }
  }, [refreshAgents, save, validate]);

  useEffect(() => {
    if (!state.dirty || !current) return;
    const timer = window.setTimeout(() => void save(true), 1800);
    return () => window.clearTimeout(timer);
  }, [state.dirty, state.revision, current, save]);

  useEffect(() => {
    if (!hint) return;
    const timer = window.setTimeout(() => setHint(null), 3200);
    return () => window.clearTimeout(timer);
  }, [hint]);

  const newAgent = useCallback(() => {
    preserved.current = {};
    storedViewport.current = undefined;
    viewportMoved.current = false;
    dispatch({ type: "loaded", nodes: [], edges: [], meta: emptyMeta() });
    setCurrent(null);
    setReport(null);
    setMissing(false);
    setStatus("New agent");
    setMigrationNotice(null);
    setLayoutNotice(null);
    window.history.replaceState({}, "", "/agent-studio/editor");
    nextDocument();
  }, [nextDocument]);

  const exportJson = useCallback(() => {
    const body = payload();
    const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${body.slug || "agent"}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }, [payload]);

  const importJson = useCallback(
    (file: File) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const json = JSON.parse(String(reader.result || "{}")) as {
            name?: string;
            slug?: string;
            kind?: string;
            config?: Record<string, unknown>;
          };
          preserved.current = preservedConfig(json.config);
          storedViewport.current = readStoredViewport(json.config);
          viewportMoved.current = false;
          const loaded = settleGraph(loadDefinition(catalog, json));
          dispatch({ type: "loaded", nodes: loaded.nodes, edges: loaded.edges, meta: loaded.meta });
          if (loaded.reflowed) {
            dispatch({ type: "replace-graph", nodes: layoutGraph(loaded.nodes, loaded.edges), edges: loaded.edges });
            dispatch({ type: "mark-clean" });
          }
          setCurrent(null);
          setReport(null);
          reportMigration(loaded.dropped);
          setStatus("Imported — review, then Save");
          nextDocument();
        } catch (importError) {
          setError(importError instanceof Error ? importError.message : String(importError));
        }
      };
      reader.readAsText(file);
    },
    [catalog, nextDocument, reportMigration, settleGraph],
  );

  const draftDefinition = useCallback(() => {
    if (current) return null;
    const body = exportGraph(nodes, edges, meta);
    return { name: body.name, kind: body.kind, config: body.config as Record<string, unknown> };
  }, [current, edges, exportGraph, meta, nodes]);

  return useMemo(
    () => ({
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
      requestFit,
      docId,
      setStatus,
      setError,
      setReport,
      setCurrent,
      save,
      validate,
      publish,
      draftDefinition,
      openSlug,
      newAgent,
      exportJson,
      importJson,
      refreshAgents,
      exportGraph,
      noteViewportMoved,
    }),
    [
      state,
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
      requestFit,
      docId,
      save,
      validate,
      publish,
      draftDefinition,
      openSlug,
      newAgent,
      exportJson,
      importJson,
      refreshAgents,
      exportGraph,
      noteViewportMoved,
    ],
  );
}
