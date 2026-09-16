/** Catalog access: one fetch, lookups, and defaults derived from the registry.
 *
 * No capability list is written down here. Labels, icons, builders, field
 * schemas and defaults all come from `GET /api/v1/studio/catalog`, so shipping a
 * new runtime/middleware/node kind is a backend-only change.
 */
import { apiGet } from "../../api";
import type { StudioResources } from "../../types";
import {
  AlertTriangle,
  ArrowRight,
  Bot,
  Brain,
  Circle,
  Eraser,
  FileText,
  Filter,
  FlaskConical,
  FolderSearch,
  Gauge,
  GitFork,
  GitMerge,
  ListChecks,
  Lock,
  Network,
  Package,
  Repeat,
  RotateCw,
  Route,
  Scroll,
  Search,
  Share2,
  ShieldCheck,
  Shuffle,
  Terminal,
  UserCheck,
  Workflow,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import {
  emptyDeepAgent,
  type CatalogField,
  type CatalogMiddleware,
  type CatalogNodeKind,
  type CatalogPattern,
  type CatalogRuntime,
  type NodeData,
  type StudioCatalog,
} from "./types";

const DEFAULT_CONTEXT_WINDOW = 32000;

/** Canvas keys that differ from the catalog field name (camelCase rule). */
const NODE_FIELD_ALIASES: Record<string, Record<string, string>> = {
  router: { default: "defaultRoute", max_visits: "maxVisits" },
  tool: { tool: "toolName" },
};

export function nodeFieldKey(nodeKind: string, fieldName: string): string {
  return NODE_FIELD_ALIASES[nodeKind]?.[fieldName] ?? fieldName;
}

export async function fetchCatalog(signal?: AbortSignal): Promise<StudioCatalog> {
  const raw = await apiGet<Omit<StudioCatalog, "resources"> & { resources?: StudioResources }>(
    "/api/v1/studio/catalog",
    signal,
  );
  return {
    ...raw,
    resources: raw.resources ?? {
      mcp_servers: [],
      skills: [],
      function_tools: [],
      model_clients: [],
      plugins: [],
    },
    loadedAt: Date.now(),
  };
}

export function runtimeById(catalog: StudioCatalog | null, id: string): CatalogRuntime | null {
  return catalog?.runtimes.find((r) => r.id === id) ?? null;
}

export function patternById(catalog: StudioCatalog | null, id: string): CatalogPattern | null {
  return catalog?.patterns.find((p) => p.id === id) ?? null;
}

export function nodeKindById(catalog: StudioCatalog | null, id: string): CatalogNodeKind | null {
  return catalog?.node_kinds.find((n) => n.id === id) ?? null;
}

export function middlewareById(catalog: StudioCatalog | null, id: string): CatalogMiddleware | null {
  return catalog?.middleware.find((m) => m.id === id) ?? null;
}

/** Value a freshly added field starts from, straight out of the registry. */
export function fieldDefault(field: CatalogField): unknown {
  if (field.default !== undefined) return field.default;
  switch (field.type) {
    case "boolean":
      return false;
    case "number":
      return undefined;
    case "select":
      return field.options?.length ? field.options[0] : "";
    case "tags":
    case "routes":
      return [];
    case "json":
    case "keyvalue":
      return {};
    default:
      return "";
  }
}

export function fieldDefaults(fields: CatalogField[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const value = fieldDefault(field);
    if (value !== undefined) out[field.name] = value;
  }
  return out;
}

/** Middleware config seeded from the registry entry's field defaults. */
export function middlewareDefaults(entry: CatalogMiddleware): Record<string, unknown> {
  return fieldDefaults(entry.fields);
}

/** The catalog fields that belong to a node kind, in registry order. */
export function nodeKindFields(catalog: StudioCatalog | null, nodeKind: string): CatalogField[] {
  return nodeKindById(catalog, nodeKind)?.fields ?? [];
}

/** Values a *new* node is authored with. They are author-owned the moment the
 *  node exists, so they are never re-invented when a definition is loaded:
 *  doing so would change what a saved agent runs (a legacy token budget maps to
 *  SummarizationMiddleware on the backend). */
const CREATION_ONLY_KEYS = ["maxContextWindowTokens", "modelClient", "modelName"] as const;

/** Defaults for a node that came from a saved definition or an import. */
export function loadedNodeData(catalog: StudioCatalog | null, paletteType: string): NodeData {
  const data = defaultNodeData(catalog, paletteType);
  for (const key of CREATION_ONLY_KEYS) delete data[key];
  return data;
}

export function defaultNodeData(catalog: StudioCatalog | null, paletteType: string): NodeData {
  const runtime = runtimeById(catalog, paletteType);
  if (runtime) {
    const defaults = catalog?.resources?.model_clients?.find((c) => c.is_default);
    return {
      paletteType,
      label: runtime.label,
      name: runtime.label,
      instructions: "",
      description: "",
      runtime: runtime.id,
      modelClient: defaults?.id || "default",
      modelName: defaults?.model_name || "",
      mcpBindings: [],
      skillIds: [],
      functionTools: [],
      middleware: {},
      responseFormat: null,
      deepAgent: emptyDeepAgent(),
      maxContextWindowTokens: DEFAULT_CONTEXT_WINDOW,
    };
  }

  const pattern = patternById(catalog, paletteType);
  if (pattern) {
    const data: NodeData = { paletteType, label: pattern.label };
    if (paletteType === "supervisor") {
      data.name = pattern.label;
      data.managerName = pattern.label;
      data.managerInstructions = "Delegate work to specialists, then return a final answer.";
      data.outputMode = "last_message";
      data.parallelToolCalls = false;
    }
    if (paletteType === "swarm") {
      data.name = pattern.label;
      data.startAgent = "";
    }
    return data;
  }

  const kind = nodeKindById(catalog, paletteType);
  if (kind) {
    const data: NodeData = { paletteType, label: kind.label };
    for (const [fieldName, value] of Object.entries(fieldDefaults(kind.fields))) {
      data[nodeFieldKey(kind.id, fieldName)] = value;
    }
    if (kind.id === "map") data.over = data.over || "items";
    if (kind.id === "set_state" && !data.values) data.values = {};
    return data;
  }

  return { paletteType, label: paletteType };
}

export function runtimeEntries(catalog: StudioCatalog | null): CatalogRuntime[] {
  return catalog?.runtimes ?? [];
}

export function patternEntries(catalog: StudioCatalog | null): CatalogPattern[] {
  return catalog?.patterns ?? [];
}

export function nodeKindEntries(catalog: StudioCatalog | null): CatalogNodeKind[] {
  return catalog?.node_kinds ?? [];
}

export function middlewareEntries(catalog: StudioCatalog | null): CatalogMiddleware[] {
  return catalog?.middleware ?? [];
}

/** Graph blueprints the toolbar template picker offers (Custom first). */
export function templateEntries(catalog: StudioCatalog | null): CatalogPattern[] {
  const custom = patternEntries(catalog).filter((p) => p.template === "custom");
  const rest = patternEntries(catalog).filter(
    (p) => p.template !== "custom" && p.id !== "supervisor" && p.id !== "swarm",
  );
  return [...custom, ...rest];
}

export function templateById(catalog: StudioCatalog | null, templateId: string): CatalogPattern | null {
  return patternEntries(catalog).find((p) => p.template === templateId) ?? null;
}

export interface SelectOption {
  value: string;
  label: string;
}

/** Pattern labels, worded exactly like `patterns[].label` in the catalog. */
export const PATTERN_LABELS: Record<string, string> = {
  supervisor: "Supervisor",
  swarm: "Swarm",
  graph: "Custom graph",
};

/** Blueprint labels (the graph templates), same wording as the catalog. */
export const TEMPLATE_LABELS: Record<string, string> = {
  sequential: "Prompt chaining",
  parallel: "Parallelization",
  routing: "Routing",
  orchestrator_worker: "Orchestrator–worker",
  evaluator_optimizer: "Evaluator–optimizer",
  custom: "Custom graph",
};

/** The most specific label a row can show for a saved definition. */
export function blueprintLabel(pattern?: string | null, template?: string | null): string {
  const named = template ? TEMPLATE_LABELS[template] : "";
  if (named && (pattern === "graph" || !pattern)) return named;
  return (pattern ? PATTERN_LABELS[pattern] : "") || named || "";
}

export function modelClientOptions(catalog: StudioCatalog | null): SelectOption[] {
  const clients = catalog?.resources?.model_clients ?? [];
  return clients.map((c) => ({
    value: c.id,
    label: c.model_name ? `${c.label || c.id} · ${c.model_name}` : c.label || c.id,
  }));
}

export function functionToolOptions(catalog: StudioCatalog | null): SelectOption[] {
  return (catalog?.resources?.function_tools ?? []).map((t) => ({ value: t.name, label: t.name }));
}

export function skillOptions(catalog: StudioCatalog | null): SelectOption[] {
  return (catalog?.resources?.skills ?? []).map((s) => ({ value: s.name, label: s.name }));
}

/* ------------------------------------------------------------------- icons */

/** Catalog icon names → lucide components; unknown names get a neutral dot. */
const ICONS: Record<string, LucideIcon> = {
  bot: Bot,
  brain: Brain,
  sitemap: Network,
  "share-2": Share2,
  "arrow-right": ArrowRight,
  "git-fork": GitFork,
  route: Route,
  network: Network,
  repeat: Repeat,
  workflow: Workflow,
  wrench: Wrench,
  "git-merge": GitMerge,
  "user-check": UserCheck,
  package: Package,
  edit: FileText,
  scroll: Scroll,
  eraser: Eraser,
  "list-checks": ListChecks,
  "shield-check": ShieldCheck,
  gauge: Gauge,
  shuffle: Shuffle,
  "rotate-cw": RotateCw,
  "alert-triangle": AlertTriangle,
  filter: Filter,
  search: Search,
  lock: Lock,
  terminal: Terminal,
  "folder-search": FolderSearch,
  flask: FlaskConical,
};

export function catalogIcon(name: string | undefined): LucideIcon {
  return (name && ICONS[name]) || Circle;
}
