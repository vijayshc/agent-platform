/** Right dock: configure the selected node, read the compile plan, see the report.
 *
 * The plan tab is the v2 signature — it names every shipped builder and
 * middleware a run will use, straight from `POST /api/v1/studio/plan`.
 */
import { useEffect, useRef, useState } from "react";
import { ExternalLink, Trash2 } from "lucide-react";
import type { AgentDef } from "../../types";
import { catalogIcon, nodeKindById, patternById, runtimeById, templateEntries } from "../model/catalog";
import type {
  CompilePlan,
  GraphMeta,
  NodeData,
  PlanAgent,
  PlanNode,
  RouterRoute,
  StudioCatalog,
  StudioNode,
  StudioEdge,
  ValidateReport,
} from "../model/types";
import { participantsFor } from "../model/serialize";

/** Everything the plan payload can carry, in one shape for the renderer. */
export interface PlanLike extends PlanAgent {
  docs?: string;
  entry?: string;
  participants?: PlanAgent[];
  manager?: PlanAgent;
  nodes?: PlanNode[];
  start_agent?: string;
  handoffs?: { from: string; to: string }[];
}
import { AgentForm } from "./AgentForm";
import { Field, Section, Select } from "./Fields";
import { NodeForm } from "./NodeForm";
import { WorkflowForm } from "./WorkflowForm";
import "./inspector.css";

export type InspectorTab = "configure" | "plan" | "report";

/** Warnings that change what a run does: shown with error styling, first. */
const BLOCKING_WARNINGS = new Set(["structured_output_forces_tool_call"]);

export interface InspectorProps {
  nodeId: string | null;
  data: NodeData | null;
  catalog: StudioCatalog | null;
  nodes: StudioNode[];
  edges: StudioEdge[];
  agents: AgentDef[];
  meta: GraphMeta;
  errors: Record<string, string[]>;
  report: ValidateReport | null;
  plan: CompilePlan | null;
  planning: boolean;
  tab: InspectorTab;
  /** Admin module keys the signed-in user may open. */
  modules: string[];
  onTabChange: (tab: InspectorTab) => void;
  /** Errors the last save reported, until the author opens Checks. */
  checksNotice: number | null;
  onChecksSeen: () => void;
  onPatchData: (patch: Partial<NodeData>) => void;
  onRoutesChange: (routes: RouterRoute[], defaultRoute: string) => void;
  onMetaChange: (patch: Partial<GraphMeta>) => void;
  onDelete: () => void;
  onOpenAccess: () => void;
  onSetEntry: (id: string) => void;
  /** Dock width in px (owned by the shell so the canvas can re-fit). */
  width: number;
  /** Overlay mode: the dock floats above the canvas on narrow screens. */
  overlay: boolean;
}

const DOCK_MIN = 320;
const DOCK_MAX = 720;
const DOCK_KEY = "aa_studio_inspector_width";

/** Drag/keyboard-resizable dock edge with a persisted width. */
export function DockGrip({
  width,
  onWidth,
  onCommit,
}: {
  width: number;
  onWidth: (width: number) => void;
  onCommit: () => void;
}) {
  const dragging = useRef(false);
  const clamp = (value: number) => Math.min(DOCK_MAX, Math.max(DOCK_MIN, value));

  useEffect(() => {
    function onMove(event: MouseEvent) {
      if (!dragging.current) return;
      onWidth(clamp(window.innerWidth - event.clientX));
    }
    function onUp() {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.classList.remove("as-resizing");
      onCommit();
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [onCommit, onWidth]);

  return (
    <div
      className="as-dock-grip"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize inspector"
      aria-valuenow={Math.round(width)}
      aria-valuemin={DOCK_MIN}
      aria-valuemax={DOCK_MAX}
      tabIndex={0}
      title="Drag to resize · double-click to reset · arrow keys to nudge"
      data-testid="studio-dock-grip"
      onMouseDown={() => {
        dragging.current = true;
        document.body.classList.add("as-resizing");
      }}
      onDoubleClick={() => {
        onWidth(348);
        onCommit();
      }}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        onWidth(clamp(width + (event.key === "ArrowRight" ? -24 : 24)));
        onCommit();
      }}
    >
      <span className="as-dock-grip-line" aria-hidden="true" />
    </div>
  );
}

export function Inspector(props: InspectorProps) {
  const {
    nodeId,
    data,
    catalog,
    nodes,
    edges,
    agents,
    meta,
    errors,
    report,
    plan,
    planning,
    tab,
    modules,
    onTabChange,
    checksNotice,
    onChecksSeen,
    onPatchData,
    onRoutesChange,
    onMetaChange,
    onDelete,
    onOpenAccess,
    onSetEntry,
    width,
    overlay,
  } = props;
  const setTab = onTabChange;

  useEffect(() => {
    if (nodeId) setTab("configure");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId]);

  const node = nodeId ? nodes.find((n) => n.id === nodeId) ?? null : null;
  const kind = data?.paletteType ?? "";
  const runtime = runtimeById(catalog, kind);
  const pattern = patternById(catalog, kind);
  const block = nodeKindById(catalog, kind);
  const Icon = catalogIcon(runtime?.icon ?? pattern?.icon ?? block?.icon);
  const nodeErrors = nodeId ? errors[nodeId] ?? [] : [];
  const errorCount = (report?.errors ?? []).length || (checksNotice ?? 0);
  const warningCount = (report?.warnings ?? []).length;
  const planBuilder = plan?.plan?.builder;

  return (
    <aside
      className={`as-inspector${overlay ? " as-inspector-overlay" : ""}`}
      style={{ width }}
      data-testid="studio-inspector"
      data-node-type={kind || "flow"}
    >
      <header className="as-insp-head">
        <span className="as-insp-icon" aria-hidden="true">
          {data ? <Icon size={15} /> : null}
        </span>
        <div className="as-insp-title">
          <span className="as-insp-kicker">{data ? `Editing ${runtime?.label ?? pattern?.label ?? block?.label ?? kind}` : "Flow settings"}</span>
          <h2 title={data ? `Node ${nodeId}` : undefined}>
            {data ? String(data.name || data.label || kind) : `${nodes.length} node${nodes.length === 1 ? "" : "s"}`}
          </h2>
          <p className="as-insp-sub">
            {data ? runtime?.builder ?? pattern?.builder ?? block?.builder ?? "" : `${meta.template} · no node selected`}
          </p>
        </div>
        {data && nodeId ? (
          <div className="as-insp-actions">
            <button
              type="button"
              className="as-btn as-btn-icon as-btn-ghost"
              title="Delete node"
              onClick={onDelete}
              data-testid="inspector-delete"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ) : null}
      </header>

      <nav className="as-insp-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "configure"}
          className={`as-tab${tab === "configure" ? " is-active" : ""}`}
          onClick={() => setTab("configure")}
        >
          Configure
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "plan"}
          className={`as-tab${tab === "plan" ? " is-active" : ""}`}
          onClick={() => setTab("plan")}
          data-testid="inspector-tab-plan"
        >
          What will run
          {planBuilder ? <span className="as-tab-dot" aria-hidden="true" /> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "report"}
          className={`as-tab${tab === "report" ? " is-active" : ""}${
            errorCount ? " has-errors" : warningCount ? " has-warnings" : report ? " is-ok" : ""
          }`}
          onClick={() => {
            setTab("report");
            onChecksSeen();
          }}
          data-testid="inspector-tab-report"
        >
          Checks
          {errorCount ? <span className="as-tab-count">{errorCount}</span> : null}
          {!errorCount && warningCount ? (
            <span className="as-tab-count as-tab-count-warn" data-testid="inspector-tab-warnings">
              {warningCount}
            </span>
          ) : null}
        </button>
      </nav>

      <div className="as-insp-body">
        {tab === "plan" ? <PlanPanel plan={plan} loading={planning} /> : null}
        {tab === "report" ? <ReportPanel report={report} /> : null}

        {tab === "configure" ? (
          <>
            {nodeErrors.length ? (
              <div className="as-insp-errors">
                {nodeErrors.map((message) => (
                  <p className="as-error" key={message}>
                    {message}
                  </p>
                ))}
              </div>
            ) : null}

            {!data ? (
              <FlowSettings
                meta={meta}
                catalog={catalog}
                nodes={nodes}
                onMetaChange={onMetaChange}
                onSetEntry={onSetEntry}
                onOpenAccess={onOpenAccess}
              />
            ) : null}

            {data && (kind === "agent" || kind === "deep_agent") ? (
              <AgentForm data={data} catalog={catalog} modules={modules} onChange={onPatchData} />
            ) : null}

            {data && (kind === "supervisor" || kind === "swarm" || kind === "graph") ? (
              <WorkflowForm
                data={data}
                catalog={catalog}
                participants={nodeId ? participantsFor(nodeId, nodes, edges) : []}
                onChange={onPatchData}
              />
            ) : null}

            {data && kind && !["agent", "deep_agent", "supervisor", "swarm", "graph"].includes(kind) ? (
              <NodeForm
                nodeId={nodeId as string}
                data={data}
                catalog={catalog}
                nodes={nodes}
                agents={agents}
                onChange={onPatchData}
                onRoutesChange={onRoutesChange}
              />
            ) : null}
          </>
        ) : null}
      </div>
    </aside>
  );
}

function FlowSettings({
  meta,
  catalog,
  nodes,
  onMetaChange,
  onSetEntry,
  onOpenAccess,
}: {
  meta: GraphMeta;
  catalog: StudioCatalog | null;
  nodes: StudioNode[];
  onMetaChange: (patch: Partial<GraphMeta>) => void;
  onSetEntry: (id: string) => void;
  onOpenAccess: () => void;
}) {
  const templates = templateEntries(catalog);
  const entry = nodes.find((n) => n.data.entry);
  return (
    <>
      <Section title="Flow" subtitle="Name, description and the model every node inherits.">
        <Field label="Description">
          <textarea
            className="as-textarea"
            rows={2}
            data-testid="studio-description"
            value={meta.description}
            placeholder="What this flow does"
            onChange={(event) => onMetaChange({ description: event.target.value })}
          />
        </Field>
        <Field label="Blueprint" help="Provenance for graph flows. Load a blueprint from the palette or the toolbar.">
          <div className="as-chip-row" data-testid="studio-template-chip">
            <span className="as-chip">
              {templates.find((t) => t.template === meta.template)?.label ?? meta.template}
            </span>
          </div>
        </Field>
        <Field label="Entry node" help="Where a run starts. Marked on the canvas with an Entry badge.">
          <Select
            value={entry?.id ?? ""}
            placeholder="First node without incoming edges"
            options={nodes.map((n) => ({
              value: n.id,
              label: String(n.data.name || n.data.label || n.data.paletteType),
            }))}
            onChange={(id) => onSetEntry(id)}
            testId="studio-entry"
          />
        </Field>
        <button type="button" className="as-btn as-btn-ghost as-btn-sm" onClick={onOpenAccess} data-testid="studio-access">
          Who can run this…
        </button>
      </Section>
    </>
  );
}

function PlanPanel({ plan, loading }: { plan: CompilePlan | null; loading: boolean }) {
  if (loading && !plan) return <p className="as-muted as-plan-loading">Deriving the compile plan…</p>;
  if (!plan) {
    return (
      <p className="as-empty" data-testid="plan-empty">
        Save or edit the canvas — the plan updates on every change.
      </p>
    );
  }
  if (plan.error) return <p className="as-error">{plan.error}</p>;
  const body = (plan.plan ?? {}) as PlanLike;
  return (
    <div className="as-plan" data-testid="studio-plan">
      <div className="as-plan-head">
        <span className="as-chip">{plan.kind}</span>
        {plan.pattern ? <span className="as-chip">{plan.pattern}</span> : null}
        {plan.template ? <span className="as-chip">{String(plan.template).replace(/_/g, " ")}</span> : null}
        {body.docs ? (
          <a className="as-link" href={String(body.docs)} target="_blank" rel="noreferrer">
            docs <ExternalLink size={11} />
          </a>
        ) : null}
      </div>
      <PlanAgentCard title={String(body.name || "Runnable")} agent={body} />

      {body.manager ? (
        <div className="as-plan-group">
          <h4>Manager</h4>
          <PlanAgentCard title={String(body.manager.name || "Manager")} agent={body.manager} />
        </div>
      ) : null}

      {body.participants?.length ? (
        <div className="as-plan-group">
          <h4>Participants</h4>
          {body.participants.map((participant, index) => (
            <PlanAgentCard
              key={`${participant.name}-${index}`}
              title={String(participant.name || `Agent ${index + 1}`)}
              agent={participant}
            />
          ))}
        </div>
      ) : null}

      {body.nodes?.length ? (
        <div className="as-plan-group">
          <h4>
            Nodes{" "}
            {body.entry ? (
              <span className="as-muted" title={String(body.entry)}>
                · entry {body.nodes.find((n) => n.id === body.entry)?.label || body.entry}
              </span>
            ) : null}
          </h4>
          {body.nodes.map((node, index) => (
            <div className="as-plan-card" key={`${node.id}-${index}`}>
              <div className="as-plan-card-head">
                <span className="as-plan-name" title={node.id}>
                  {node.label || node.id}
                </span>
                <span className="as-chip">{node.kind}</span>
              </div>
              {/* An agent node's builder is rendered by its nested agent plan. */}
              {node.builder && !node.agent ? <code className="as-plan-builder">{node.builder}</code> : null}
              {node.error ? <p className="as-error">{node.error}</p> : null}
              {node.agent ? <PlanAgentBody agent={node.agent} /> : null}
            </div>
          ))}
        </div>
      ) : null}

      {body.start_agent ? (
        <p className="as-plan-meta">
          Starts at <strong>{String(body.start_agent)}</strong>
          {body.handoffs?.length ? ` · ${body.handoffs.length} handoff(s)` : ""}
        </p>
      ) : null}
    </div>
  );
}

function PlanAgentCard({ title, agent }: { title: string; agent: PlanLike }) {
  return (
    <div className="as-plan-card">
      <div className="as-plan-card-head">
        <span className="as-plan-name">{title}</span>
      </div>
      <PlanAgentBody agent={agent} />
    </div>
  );
}

function PlanAgentBody({ agent }: { agent: PlanLike }) {
  return (
    <>
      {agent.builder ? <code className="as-plan-builder">{agent.builder}</code> : null}
      <ul className="as-plan-facts">
        {agent.runtime ? <li>runtime {agent.runtime}</li> : null}
        {typeof agent.tools === "number" ? <li>{agent.tools} tool(s)</li> : null}
        {agent.structured_output ? <li>structured output: {agent.structured_output}</li> : null}
        {agent.subagents?.length ? <li>subagents: {agent.subagents.join(", ")}</li> : null}
        {agent.skills?.length ? <li>skill paths: {agent.skills.join(", ")}</li> : null}
        {agent.memory?.length ? <li>memory: {agent.memory.join(", ")}</li> : null}
        {typeof agent.permissions === "number" ? <li>{agent.permissions} permission rule(s)</li> : null}
      </ul>
      {/* Exactly the plan's middleware list: nothing is added at render time. */}
      {agent.middleware?.length ? (
        <div className="as-plan-mw">
          {agent.middleware.map((name) => (
            <span className="as-chip as-chip-mw" key={name}>
              {name}
            </span>
          ))}
        </div>
      ) : (
        <p className="as-plan-none">no guardrails</p>
      )}
    </>
  );
}

function ReportPanel({ report }: { report: ValidateReport | null }) {
  if (!report) {
    return (
      <p className="as-empty" data-testid="report-empty">
        Run <strong>Validate</strong> to check this definition against the compiler.
      </p>
    );
  }
  const compileOk = report.compile?.ok;
  return (
    <div className="as-report" data-testid="studio-report" data-ok={report.ok ? "1" : "0"}>
      <p className={`as-report-summary${report.ok ? " is-ok" : " is-bad"}`}>
        {report.ok ? "Valid" : `${report.errors.length} error${report.errors.length === 1 ? "" : "s"}`}
        {report.warnings.length ? ` · ${report.warnings.length} warning(s)` : ""}
        {compileOk === true ? " · compiles" : compileOk === false ? " · does not compile" : ""}
      </p>
      {report.compile?.error ? <p className="as-error">{String(report.compile.error)}</p> : null}
      {report.errors.map((issue) => (
        <div className="as-report-item is-error" key={`${issue.code}-${issue.message}`}>
          <code>{issue.code}</code>
          <span>{issue.message}</span>
        </div>
      ))}
      {[...report.warnings]
        .sort((a, b) => Number(BLOCKING_WARNINGS.has(b.code)) - Number(BLOCKING_WARNINGS.has(a.code)))
        .map((issue) => (
          <div
            className={`as-report-item ${BLOCKING_WARNINGS.has(issue.code) ? "is-error" : "is-warn"}`}
            key={`${issue.code}-${issue.message}`}
          >
            <code>{issue.code}</code>
            <span>{issue.message}</span>
          </div>
        ))}
    </div>
  );
}
