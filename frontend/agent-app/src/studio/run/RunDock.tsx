/** Docked test-run panel for the Agent Studio editor.
 *
 * A bottom-docked surface (not a modal) that streams a real `/api/v1/runs`
 * call for the agent being edited: live transcript, the ordered step timeline
 * of every tool call / result / agent switch, HITL approvals through the shared
 * `HitlCard`, the run's workspace files and a link into Observability.
 *
 * All transport lives in `useRunStream`; this file is presentation only.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  AlertTriangle,
  ArrowRightLeft,
  Gauge,
  Info,
  Lightbulb,
  Terminal,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import { HitlCard } from "../../shared/HitlCard";
import { ReasoningTicker } from "../../shared/ReasoningTicker";
import "../../shared/hitl.css";
import type { HitlAction, HitlInterrupt, HitlReviewConfig, HitlDecision } from "../../shared/hitl";
import type { SseEvent } from "../../types";
import { modelClientOptions, patternById, runtimeById } from "../model/catalog";
import type { StudioCatalog } from "../model/types";
import { useRunStream, type RunDraftDefinition, type RunStatus, type RunStepKind } from "./useRunStream";
import "./run.css";

export interface RunDockProps {
  catalog: StudioCatalog | null;
  slug: string | null;
  agentName: string;
  draftDefinition: RunDraftDefinition | null;
  open: boolean;
  onClose: () => void;
}

const STEP_ICON: Record<RunStepKind, LucideIcon> = {
  reasoning: Lightbulb,
  tool_call: Wrench,
  tool_result: Terminal,
  agent_switch: ArrowRightLeft,
  progress: Gauge,
  error: AlertTriangle,
  status: Info,
};

const STATUS_BADGE: Record<RunStatus, { label: string; className: string }> = {
  idle: { label: "Idle", className: "as-badge" },
  streaming: { label: "Streaming", className: "as-badge" },
  awaiting: { label: "Awaiting approval", className: "as-badge as-badge-warn" },
  done: { label: "Done", className: "as-badge as-badge-ok" },
  error: { label: "Error", className: "as-badge as-badge-err" },
};

function asText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return String(value);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toActions(value: unknown): HitlAction[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const actions: HitlAction[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const name = typeof entry.name === "string" ? entry.name : "";
    if (!name) continue;
    const action: HitlAction = { name };
    if (isRecord(entry.args)) action.args = entry.args;
    if (typeof entry.description === "string") action.description = entry.description;
    actions.push(action);
  }
  return actions.length ? actions : undefined;
}

function toReviewConfigs(value: unknown): HitlReviewConfig[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const configs: HitlReviewConfig[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) continue;
    const actionName = typeof entry.action_name === "string" ? entry.action_name : "";
    if (!actionName) continue;
    const config: HitlReviewConfig = { action_name: actionName };
    if (Array.isArray(entry.allowed_decisions)) {
      config.allowed_decisions = entry.allowed_decisions.filter(
        (decision): decision is string => typeof decision === "string",
      );
    }
    configs.push(config);
  }
  return configs.length ? configs : undefined;
}

/** The library interrupt payload arrives flat on the event (current runtime) or
 *  under `request` (documented contract); both render through the same card. */
function toInterrupt(event: SseEvent): HitlInterrupt {
  const nested = isRecord(event.request) ? event.request : {};
  const interrupt: HitlInterrupt = {};
  const actions = toActions(event.action_requests ?? nested.action_requests);
  const configs = toReviewConfigs(event.review_configs ?? nested.review_configs);
  if (actions) interrupt.action_requests = actions;
  if (configs) interrupt.review_configs = configs;
  const type = asText(nested.type) || asText(event.type);
  const message = asText(event.message) || asText(nested.message);
  const plan = asText(event.plan) || asText(nested.plan);
  if (type) interrupt.type = type;
  if (message) interrupt.message = message;
  if (plan) interrupt.plan = plan;
  return interrupt;
}

/** Provider failures the author can act on, in plain language. */
export function explainRunError(raw: string): { summary: string; fix: string } {
  if (/tool_choice/i.test(raw) && /thinking|reasoning/i.test(raw)) {
    return {
      summary: "This model refuses the forced tool call that structured output asked for.",
      fix: "Open the agent's Structured output section and switch the strategy to “Provider — native schema”, or pick a connection whose model supports forced tool calls.",
    };
  }
  if (/tool_choice/i.test(raw)) {
    return {
      summary: "The model rejected the forced tool call used for structured output.",
      fix: "Switch the structured-output strategy to “Provider — native schema”.",
    };
  }
  if (/rate limit|429/i.test(raw)) {
    return { summary: "The model connection is rate limited.", fix: "Retry, or pick another connection in the run dock." };
  }
  if (/unauthorized|401|api key/i.test(raw)) {
    return { summary: "The model connection rejected its credentials.", fix: "Fix the connection in LLM Manager." };
  }
  return { summary: "The run failed.", fix: "" };
}

/** What the dock is about to run — the registry label of the draft's shape. */
function targetLabel(
  catalog: StudioCatalog | null,
  draft: RunDraftDefinition | null,
  saved: boolean,
): string {
  if (!draft) return saved ? "Saved agent" : "Nothing to run";
  const config = draft.config;
  const pattern = typeof config.pattern === "string" ? config.pattern : "";
  const runtime = typeof config.runtime === "string" ? config.runtime : "";
  const label = (pattern ? patternById(catalog, pattern)?.label : "") ||
    (runtime ? runtimeById(catalog, runtime)?.label : "");
  if (label) return label;
  return draft.kind === "workflow" ? "Workflow" : "Agent";
}

function clipLine(text: string, cap = 140): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > cap ? `${line.slice(0, cap)}…` : line;
}

function clockTime(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function RunDock({
  catalog,
  slug,
  agentName,
  draftDefinition,
  open,
  onClose,
}: RunDockProps) {
  const {
    transcript,
    reasoning,
    reasoningStreaming,
    steps,
    pending,
    files,
    status,
    error,
    runId,
    busy,
    start,
    decide,
    cancel,
    reset,
  } = useRunStream();
  const [input, setInput] = useState("");
  const [modelClient, setModelClient] = useState("default");
  const transcriptRef = useRef<HTMLPreElement | null>(null);
  const stepsRef = useRef<HTMLDivElement | null>(null);
  const stepsFollowRef = useRef(true);

  const canRun = Boolean(slug || draftDefinition);
  // A finished run with no text, no error and nothing pending is a real (if
  // annoying) model outcome — say so instead of showing a blank panel.
  const finishedWithNoReply =
    !busy && !transcript && !error && !pending && status !== "awaiting" && status !== "idle" && Boolean(runId);
  const meta = useMemo(
    () => targetLabel(catalog, draftDefinition, Boolean(slug)),
    [catalog, draftDefinition, slug],
  );

  // Closing the dock ends the session: the local reader is aborted and the
  // transcript is dropped, so the next open starts from a clean panel.
  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [transcript]);

  // An approval pause is the one thing the author must not have to hunt for:
  // scroll the card into view when it appears.
  useEffect(() => {
    if (!pending) return;
    document.querySelector('[data-testid="hitl-card"]')?.scrollIntoView({ block: "nearest" });
  }, [pending]);

  // The step timeline follows the newest step unless the author scrolled up.
  useEffect(() => {
    const node = stepsRef.current;
    if (!node || stepsFollowRef.current === false) return;
    node.scrollTop = node.scrollHeight;
  }, [steps]);

  async function send() {
    const text = input.trim();
    if (!text || busy || !canRun) return;
    setInput("");
    try {
      await start({ agentId: slug, definition: draftDefinition, input: text, modelClient });
    } catch {
      // start() recorded the reason in the hook's error state; keep the prompt.
      setInput(text);
    }
  }

  function onDecision(decisions: HitlDecision[]) {
    void decide(decisions).catch(() => {
      /* decide() records the failure in the hook's error state */
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    void send();
  }

  if (!open) return null;
  const badge = STATUS_BADGE[status];

  return (
    <section
      className="as-run"
      data-testid="testrun-drawer"
      data-streaming={busy ? "1" : "0"}
      data-pending={pending ? "1" : "0"}
      data-run-id={runId}
      aria-label="Test run"
    >
      <header className="as-run-header">
        <div className="as-run-title">
          <span>Test run</span>
          <span className="as-run-meta">
            {agentName || "Untitled agent"} · {meta}
          </span>
        </div>
        <div className="as-run-actions">
          {/* Models are chosen per run, not in the agent definition. */}
          <label className="as-run-model">
            <span className="as-muted">Model</span>
            <select
              className="as-select as-select-sm"
              value={modelClient}
              disabled={busy}
              data-testid="testrun-model"
              title="Connection used for this test run only"
              onChange={(event) => setModelClient(event.target.value)}
            >
              <option value="default">Deployment default</option>
              {modelClientOptions(catalog).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          {!slug && draftDefinition ? <span className="as-badge">Draft</span> : null}
          {runId ? <span className="as-chip">{runId}</span> : null}
          {runId ? (
            <a className="as-link" href={`/observability?run=${encodeURIComponent(runId)}`}>
              Observability
            </a>
          ) : null}
          <button
            type="button"
            className="as-btn as-btn-icon"
            data-testid="testrun-close"
            aria-label="Close test run"
            onClick={onClose}
          >
            <X aria-hidden="true" size={16} />
          </button>
        </div>
      </header>

      <div className="as-run-body">
        <div className="as-run-transcript-pane">
          {reasoning && reasoningStreaming ? <ReasoningTicker text={reasoning} /> : null}
          <pre className="as-run-transcript" data-testid="testrun-transcript" ref={transcriptRef}>
            {transcript}
            {transcript || busy ? null : finishedWithNoReply ? (
              <span className="as-muted" data-testid="testrun-empty-reply">
                The model returned no text for this turn. Run again, or lower the temperature / raise max tokens for this
                agent.
              </span>
            ) : (
              <span className="as-empty">Run the agent to watch its reply stream here.</span>
            )}
            {busy ? <span className="as-run-streaming" aria-hidden="true" /> : null}
          </pre>
        </div>

        <div
          className="as-run-steps"
          data-testid="testrun-steps"
          ref={stepsRef}
          onScroll={(event) => {
            const el = event.currentTarget;
            stepsFollowRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
          }}
        >
          {steps.length ? (
            steps.map((step, index) => {
              const Icon = STEP_ICON[step.kind];
              const newRun = index === 0 || steps[index - 1].run !== step.run;
              return (
                <Fragment key={step.id}>
                {newRun ? (
                  <div className="as-run-group" data-testid="testrun-run-group" data-run={step.run}>
                    Run {step.run}
                    {step.run === steps[steps.length - 1].run ? " · current" : ""}
                  </div>
                ) : null}
                <div
                  className="as-run-step"
                  data-testid="testrun-step"
                  data-kind={step.kind}
                  data-run={step.run}
                >
                  <div className="as-run-step-head">
                    <Icon aria-hidden="true" size={14} />
                    <span className="as-run-step-title">{step.title}</span>
                    {step.agent ? <span className="as-chip">{step.agent}</span> : null}
                    <span className="as-muted">{clockTime(step.ts)}</span>
                  </div>
                  {step.detail ? (
                    <details className="as-run-step-detail">
                      <summary className="as-muted">{step.preview || clipLine(step.detail)}</summary>
                      <pre>{step.detail}</pre>
                    </details>
                  ) : null}
                </div>
                </Fragment>
              );
            })
          ) : (
            <div className="as-empty">
              Tool calls, results, agent switches and progress appear here in order.
            </div>
          )}
        </div>

        <div className="as-run-files" data-testid="workspace-files">
          <div className="as-muted">Workspace files</div>
          {files.length ? (
            files.map((path) => (
              <div className="as-run-file" key={path} data-testid="workspace-file" data-path={path}>
                {path}
              </div>
            ))
          ) : (
            <div className="as-empty">No files produced yet</div>
          )}
        </div>
      </div>

      {pending ? (
        <div className="as-run-approval" data-testid="testrun-approval">
          <HitlCard variant="studio" interrupt={toInterrupt(pending)} busy={busy} onDecision={onDecision} />
        </div>
      ) : null}

      {error ? (
        <div className="as-error" data-testid="testrun-error">
          <span>{explainRunError(error).summary}</span>
          {explainRunError(error).fix ? (
            <span className="as-muted"> {explainRunError(error).fix}</span>
          ) : null}
          <details className="as-run-raw">
            <summary className="as-muted">Provider response</summary>
            <pre>{error}</pre>
          </details>
        </div>
      ) : null}

      <div className="as-run-status">
        <span className={badge.className}>{badge.label}</span>
        <span className="as-muted">
          {steps.length} {steps.length === 1 ? "step" : "steps"} · {files.length}{" "}
          {files.length === 1 ? "file" : "files"}
        </span>
        {pending ? <span className="as-badge as-badge-warn">Waiting on you</span> : null}
      </div>

      <div className="as-run-inputrow">
        <textarea
          className="as-input"
          data-testid="testrun-input"
          rows={2}
          placeholder={canRun ? "Describe the task to run…" : "Save this agent before test running it"}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="as-btn as-btn-primary"
          data-testid="testrun-send"
          disabled={busy || !canRun}
          onClick={() => void send()}
        >
          Send
        </button>
        {busy ? (
          <button
            type="button"
            className="as-btn as-btn-ghost"
            data-testid="testrun-cancel"
            onClick={cancel}
          >
            Cancel
          </button>
        ) : null}
      </div>
    </section>
  );
}
