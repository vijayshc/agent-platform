/** Typed node card: kind colour, icon, title, subtitle, chips, handles, provenance.
 *
 * The provenance strip is a signature of the v2 editor: every card names the
 * shipped builder it compiles to (`create_agent`, `create_supervisor`,
 * `Command`, …) so the author can always see what will run.
 */
import { createContext, useContext } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { catalogIcon, nodeKindById, patternById, runtimeById } from "../model/catalog";
import { isAgentType, isPatternType, type NodeData, type StudioCatalog, type StudioNode } from "../model/types";

export interface CanvasContextValue {
  catalog: StudioCatalog | null;
  /** Validation messages keyed by canvas node id (matched by label/name). */
  errors: Record<string, string[]>;
  /** Agents currently wired to each pattern node, keyed by pattern node id. */
  participants: Record<string, number>;
}

export const CanvasContext = createContext<CanvasContextValue>({
  catalog: null,
  errors: {},
  participants: {},
});

export function useCanvasContext(): CanvasContextValue {
  return useContext(CanvasContext);
}

function shortBuilder(builder: string | undefined): string {
  if (!builder) return "";
  const cleaned = builder
    .replace("langchain.agents.", "")
    .replace("langgraph_supervisor.", "")
    .replace("langgraph_swarm.", "")
    .replace("langgraph.prebuilt.", "")
    .replace("langgraph.types.", "")
    .replace("langgraph.graph.", "")
    .replace("deepagents.", "")
    .replace("langchain.agents.middleware.", "")
    .replace(" (subgraph)", "");
  return cleaned.length > 34 ? `${cleaned.slice(0, 33)}…` : cleaned;
}

function firstLines(text: string | undefined, max = 76): string {
  const line = (text || "").trim().split("\n").find((l) => l.trim()) || "";
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

function groupOf(paletteType: string): string {
  if (paletteType === "agent") return "agent";
  if (paletteType === "deep_agent") return "deep";
  if (isPatternType(paletteType)) return "pattern";
  return "block";
}

function NodeChips({ data, catalog }: { data: NodeData; catalog: StudioCatalog | null }) {
  const chips: string[] = [];
  if (isAgentType(data.paletteType)) {
    const tools = (data.mcpBindings ?? []).reduce((n, b) => n + b.tools.length, 0) + (data.functionTools ?? []).length;
    if (tools) chips.push(`${tools} tool${tools === 1 ? "" : "s"}`);
    if (data.skillIds?.length) chips.push(`${data.skillIds.length} skill${data.skillIds.length === 1 ? "" : "s"}`);
    const middleware = Object.keys(data.middleware ?? {}).length;
    if (middleware) chips.push(`${middleware} guardrail${middleware === 1 ? "" : "s"}`);
    if (data.responseFormat?.schema || data.responseFormat?.strategy) chips.push("structured");
    if (data.runtime === "deep_agent" && data.deepAgent?.subagents?.length) {
      chips.push(`${data.deepAgent.subagents.length} subagent${data.deepAgent.subagents.length === 1 ? "" : "s"}`);
    }
  }
  if (data.paletteType === "router") {
    const routes = (data.routes ?? []).length;
    chips.push(routes ? `${routes} route${routes === 1 ? "" : "s"}` : "no routes yet");
  }
  if (data.paletteType === "join") chips.push(String(data.strategy || "summarize"));
  if (data.paletteType === "map") chips.push(`${data.over || "items"} →`);
  if (data.paletteType === "tool") chips.push(String(data.toolName || "no tool"));
  if (data.paletteType === "subgraph") chips.push(String(data.ref || "no flow"));
  if (data.paletteType === "human") chips.push(firstLines(data.message, 28) || "no question");
  if (data.paletteType === "supervisor") {
    chips.push(String(data.managerName || "manager"));
    chips.push(String(data.outputMode || "last_message").replace("_", " "));
  }
  if (data.paletteType === "swarm") chips.push(String(data.startAgent || "start: first agent"));
  if (!chips.length) return null;
  return (
    <div className="as-node-chips">
      {chips.slice(0, 3).map((chip) => (
        <span className="as-node-chip" key={chip}>
          {chip}
        </span>
      ))}
    </div>
  );
}

/** Terminal marker for a router route that ends the run (`to: "__end__"`). */
export function TerminalNode({ data }: NodeProps<StudioNode>) {
  return (
    <div className="as-node-terminus" data-testid="studio-node-end" title="This route ends the run">
      <span className="as-node-terminus-dot" aria-hidden="true" />
      {String(data.route || "END")} → END
    </div>
  );
}

export function NodeCard({ id, data, selected }: NodeProps<StudioNode>) {
  const { catalog, errors, participants } = useCanvasContext();
  const kind = data.paletteType;
  const group = groupOf(kind);
  const runtime = runtimeById(catalog, kind);
  const pattern = patternById(catalog, kind);
  const nodeKind = nodeKindById(catalog, kind);
  const label = runtime?.label ?? pattern?.label ?? nodeKind?.label ?? kind;
  const Icon = catalogIcon(runtime?.icon ?? pattern?.icon ?? nodeKind?.icon ?? (kind === "agent" ? "bot" : undefined));
  const builder = shortBuilder(runtime?.builder ?? pattern?.builder ?? nodeKind?.builder);
  const middlewareCount = isAgentType(kind) ? Object.keys(data.middleware ?? {}).length : 0;
  const title = kind === "supervisor"
    ? String(data.managerName || data.label)
    : String(data.name || data.label || label);
  const subtitle = isAgentType(kind)
    ? firstLines(data.instructions) || "No instructions yet"
    : isPatternType(kind)
      ? pattern?.summary ?? ""
      : nodeKind?.summary ?? "";
  const nodeErrors = errors[id] ?? [];

  return (
    <div
      className={`as-node as-node-${group}${selected ? " selected" : ""}${nodeErrors.length ? " has-error" : ""}`}
      data-testid={`studio-node-${kind}`}
      data-palette-type={kind}
      data-node-label={title}
    >
      <Handle id="in" type="target" position={Position.Left} className="as-handle" />
      <div className="as-node-head">
        <span className="as-node-icon" aria-hidden="true">
          <Icon size={15} />
        </span>
        <span className="as-node-kicker">{label}</span>
        {data.entry ? <span className="as-node-flag as-node-entry">Entry</span> : null}
        {nodeErrors.length ? (
          <span className="as-node-flag as-node-flag-error" title={nodeErrors.join("\n")}>
            {nodeErrors.length}
          </span>
        ) : null}
      </div>
      <div className="as-node-title" title={title}>
        {title}
      </div>
      {subtitle ? <div className="as-node-sub">{subtitle}</div> : null}
      {isPatternType(kind) && kind !== "graph" ? (
        <div className={`as-node-needs${(participants[id] ?? 0) ? " is-met" : ""}`}>
          {(participants[id] ?? 0) > 0
            ? `${participants[id]} agent${participants[id] === 1 ? "" : "s"} connected`
            : `Connect ${pattern?.min_agents ?? 1}+ agents to run this`}
        </div>
      ) : null}
      <NodeChips data={data} catalog={catalog} />
      {builder ? (
        <div className="as-node-provenance" title={`Compiles to ${runtime?.builder ?? pattern?.builder ?? nodeKind?.builder}`}>
          <code>{builder}</code>
          {middlewareCount ? <span className="as-node-mw">{middlewareCount} mw</span> : null}
        </div>
      ) : null}
      <Handle id="out" type="source" position={Position.Right} className="as-handle" />
    </div>
  );
}
