/** Live run state for the Agent Studio test-run dock.
 *
 * One hook owns the whole `/api/v1/runs` conversation: the streamed transcript
 * (tokens), the ordered timeline of non-token events (tool calls, tool results,
 * agent switches, progress, errors, status), the pending HITL interrupt, the
 * run's workspace files and the local transport status.
 *
 * Stream ownership is per-run: every `start`/`decide` aborts the previous
 * controller and bumps a generation counter, so a superseded reader can never
 * write into the new run's state; `cancel` and unmount abort the live reader.
 * A paused run ends its stream with `status`/`done` carrying
 * `awaiting_approval` — that is the only state in which `pending` is set.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { cancelRun, getRun, resumeRun, startRun, type RunStreamRequest } from "../api";
import { readSse } from "../../sse";
import type { SseEvent } from "../../types";
import type { HitlDecision } from "../../shared/hitl";
import { firstWords } from "../../shared/text";

export type RunStatus = "idle" | "streaming" | "awaiting" | "done" | "error";

/** Bookkeeping file the runtime writes into every run workspace. */
const WORKSPACE_BASELINE = ".agent-workspace-baseline.json";

export type RunStepKind =
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "agent_switch"
  | "progress"
  | "error"
  | "status";

/** One event on the run's ordered timeline (tokens are the transcript, not steps). */
export interface RunStep {
  id: string;
  kind: RunStepKind;
  title: string;
  /** Collapsed timeline preview (currently used for the Think step). */
  preview?: string;
  detail?: string;
  agent?: string;
  ts: number;
  /** 1-based run counter: the dock groups the timeline by run. */
  run: number;
}

/** Namespaced actor labels carry a thread/subgraph UUID (`Writer:8b36…`); they
 *  are internal identifiers, so the UI shows the human part only. */
export function cleanActor(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "";
  const withoutUuid = trimmed.replace(
    /:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}.*$/i,
    "",
  );
  return (withoutUuid || trimmed).replace(/:+$/, "").trim();
}

/** The inline definition of an unsaved draft, exactly as `POST /runs` takes it. */
export interface RunDraftDefinition {
  name: string;
  kind: string;
  config: Record<string, unknown>;
}

export interface StartPayload {
  agentId?: string | null;
  definition?: RunDraftDefinition | null;
  input: string;
  /** Connection for this run; "default" (or empty) uses the deployment default. */
  modelClient?: string;
}

export interface RunStream {
  transcript: string;
  reasoning: string;
  reasoningStreaming: boolean;
  steps: RunStep[];
  pending: SseEvent | null;
  files: string[];
  status: RunStatus;
  error: string | null;
  runId: string;
  busy: boolean;
  start: (payload: StartPayload) => Promise<void>;
  decide: (decisions: HitlDecision[]) => Promise<void>;
  cancel: () => void;
  reset: () => void;
}

/** Step details are a UI preview; the server already caps each payload at 16 KB. */
const DETAIL_CAP = 2000;
/** Coalescing window for the workspace-file poll during tool traffic. */
const FILES_POLL_MS = 700;

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

function clip(text: string, cap = DETAIL_CAP): string {
  const trimmed = text.trim();
  if (trimmed.length <= cap) return trimmed;
  return `${trimmed.slice(0, cap)}…`;
}

function humanize(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  if (!spaced) return "";
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function makeStep(args: {
  id: string;
  kind: RunStepKind;
  title: string;
  ts: number;
  run: number;
  detail?: string;
  agent?: string;
}): RunStep {
  const step: RunStep = { id: args.id, kind: args.kind, title: args.title, ts: args.ts, run: args.run };
  const detail = args.detail?.trim();
  if (detail) step.detail = detail;
  if (args.agent) step.agent = args.agent;
  return step;
}

/** Every non-token event becomes exactly one step. */
function stepFromEvent(event: SseEvent, id: string, run: number): RunStep {
  const ts = Date.now();
  const agent = cleanActor(asText(event.agent)) || undefined;
  switch (event.type) {
    case "tool_call":
      return makeStep({
        id,
        kind: "tool_call",
        title: event.tool_name ? `Call ${event.tool_name}` : "Tool call",
        ts,
        run,
        detail: clip(asText(event.arguments)),
        agent,
      });
    case "tool_result":
      return makeStep({
        id,
        kind: "tool_result",
        title: event.tool_name ? `Result from ${event.tool_name}` : "Tool result",
        ts,
        run,
        detail: clip(asText(event.result)),
        agent,
      });
    case "agent_switch":
      return makeStep({
        id,
        kind: "agent_switch",
        title: `Switched to ${agent ?? "the next agent"}`,
        ts,
        run,
        agent,
      });
    case "progress": {
      // Two shapes reach here: the runtime's phase/elapsed/chunks heartbeat and
      // a model-authored `message` (custom stream writer) — show whichever came.
      const phase = asText(event.phase);
      const message = asText(event.message);
      const stats: string[] = [];
      if (typeof event.elapsed === "number") stats.push(`${event.elapsed}s`);
      if (typeof event.chunks === "number") stats.push(`${event.chunks} chunks`);
      return makeStep({
        id,
        kind: "progress",
        title: phase ? `Progress · ${phase}` : clip(message, 120) || "Progress",
        ts,
        run,
        detail: phase ? [message, ...stats].filter(Boolean).join(" · ") : stats.join(" · "),
        agent,
      });
    }
    case "error": {
      const message = asText(event.message) || asText(event.error) || "Run failed";
      return makeStep({ id, kind: "error", title: clip(message, 120), ts, run, agent });
    }
    case "approval_request":
      return makeStep({
        id,
        kind: "status",
        title: "Approval requested",
        ts,
        run,
        detail: clip(asText(event.action_requests ?? event.request ?? event.message)),
        agent,
      });
    case "skill_load":
      return makeStep({
        id,
        kind: "status",
        title: "Skill loaded",
        ts,
        run,
        detail: clip(asText(event.skill)),
        agent,
      });
    case "chat":
      return makeStep({
        id,
        kind: "status",
        title: "Model reply",
        ts,
        run,
        detail: clip(asText(event.content)),
        agent,
      });
    case "status":
      return makeStep({
        id,
        kind: "status",
        title: humanize(asText(event.message) || asText(event.status)) || "Status",
        ts,
        run,
        agent,
      });
    case "done": {
      const failure = asText(event.error);
      if (failure) {
        return makeStep({ id, kind: "status", title: "Run failed", ts, run, detail: clip(failure), agent });
      }
      if (asText(event.status) === "awaiting_approval") {
        return makeStep({ id, kind: "status", title: "Paused for approval", ts, run, agent });
      }
      return makeStep({ id, kind: "status", title: "Run complete", ts, run, agent });
    }
    default:
      return makeStep({
        id,
        kind: "status",
        title: humanize(event.type) || "Event",
        ts,
        run,
        detail: clip(asText(event.message)),
        agent,
      });
  }
}

export function useRunStream(): RunStream {
  const [transcript, setTranscript] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [reasoningStreaming, setReasoningStreaming] = useState(false);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [pending, setPending] = useState<SseEvent | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState("");

  const aliveRef = useRef(true);
  const runIdRef = useRef("");
  const pendingRef = useRef<SseEvent | null>(null);
  const seenToolRef = useRef<Set<string>>(new Set());
  /** Terminal status latched by the events of the run currently being read. */
  const terminalRef = useRef<RunStatus | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  /** Bumped per stream so a superseded reader cannot touch the new run. */
  const generationRef = useRef(0);
  const stepSeqRef = useRef(0);
  /** 1-based counter of the runs shown in this dock (timeline grouping). */
  const runSeqRef = useRef(0);
  const filesTimerRef = useRef<number | null>(null);
  const reasoningRef = useRef("");
  const reasoningAgentRef = useRef<string | undefined>(undefined);
  const reasoningIdRef = useRef("");

  const fail: (message: string) => never = useCallback((message: string): never => {
    setError(message);
    setStatus("error");
    throw new Error(message);
  }, []);

  const rememberRunId = useCallback((next: string) => {
    if (!next || next === runIdRef.current) return;
    runIdRef.current = next;
    setRunId(next);
  }, []);

  const finalizeReasoning = useCallback(() => {
    const text = reasoningRef.current.trim();
    const agent = reasoningAgentRef.current;
    reasoningRef.current = "";
    reasoningAgentRef.current = undefined;
    reasoningIdRef.current = "";
    setReasoning("");
    setReasoningStreaming(false);
    if (!text) return;
    stepSeqRef.current += 1;
    setSteps((prev) => [
      ...prev,
      {
        id: `step-${stepSeqRef.current}`,
        kind: "reasoning",
        title: "Think",
        preview: firstWords(text, 9),
        detail: text,
        agent,
        ts: Date.now(),
        run: runSeqRef.current,
      },
    ]);
  }, []);

  const setPendingInterrupt = useCallback((event: SseEvent | null) => {
    pendingRef.current = event;
    setPending(event);
  }, []);

  const clearFilesTimer = useCallback(() => {
    if (filesTimerRef.current === null) return;
    window.clearTimeout(filesTimerRef.current);
    filesTimerRef.current = null;
  }, []);

  const refreshFiles = useCallback(async (id: string) => {
    if (!id) return;
    try {
      const run = await getRun(id);
      if (!aliveRef.current || runIdRef.current !== id) return;
      setFiles((run.workspace_files ?? []).filter((name) => !name.endsWith(WORKSPACE_BASELINE)));
    } catch {
      /* a file poll must never break the run view */
    }
  }, []);

  const scheduleFilesRefresh = useCallback(
    (id: string, delay = FILES_POLL_MS) => {
      if (!id || filesTimerRef.current !== null) return;
      filesTimerRef.current = window.setTimeout(() => {
        filesTimerRef.current = null;
        void refreshFiles(id);
      }, delay);
    },
    [refreshFiles],
  );

  const applyEvent = useCallback(
    (event: SseEvent) => {
      const eventRunId = asText(event.public_id) || asText(event.runPublicId) || asText(event.run_id);
      if (eventRunId) rememberRunId(eventRunId);

      if (event.type === "reasoning") {
        const id = asText(event.reasoning_id);
        if (id && reasoningIdRef.current && id !== reasoningIdRef.current) finalizeReasoning();
        const chunk = asText(event.content) || asText(event.delta);
        if (chunk) {
          reasoningIdRef.current = id || reasoningIdRef.current;
          reasoningRef.current += chunk;
          reasoningAgentRef.current = cleanActor(asText(event.agent)) || reasoningAgentRef.current;
          setReasoning(reasoningRef.current);
          setReasoningStreaming(true);
        }
        return;
      }
      // Heartbeats can land mid-reasoning; every real model/flow event means
      // the thinking line has ended and belongs in the timeline.
      if (event.type !== "progress" && event.type !== "status") finalizeReasoning();

      switch (event.type) {
        case "token": {
          const chunk = asText(event.content) || asText(event.delta);
          if (chunk) setTranscript((prev) => prev + chunk);
          return;
        }
        case "tool_result": {
          // A tool may have written a file: refresh the list as soon as the
          // result lands instead of waiting for the next poll.
          setPendingInterrupt(pendingRef.current);
          scheduleFilesRefresh(runIdRef.current, 250);
          break;
        }
        case "done": {
          // A write that is the run's last step can land a moment after `done`:
          // refresh now and once more shortly after.
          void refreshFiles(runIdRef.current);
          scheduleFilesRefresh(runIdRef.current, 900);
          const reply = asText(event.reply);
          if (reply.trim()) setTranscript(reply);
          const failure = asText(event.error);
          if (failure) {
            setError(failure);
            terminalRef.current = "error";
            setStatus("error");
          } else if (asText(event.status) === "awaiting_approval") {
            terminalRef.current = "awaiting";
            setStatus("awaiting");
          } else {
            terminalRef.current = "done";
            setStatus("done");
            setPendingInterrupt(null);
          }
          break;
        }
        case "approval_request":
          setPendingInterrupt(event);
          terminalRef.current = "awaiting";
          break;
        case "status":
          if (asText(event.message) === "awaiting_approval") terminalRef.current = "awaiting";
          break;
        case "error":
          setError(asText(event.message) || asText(event.error) || "Run failed");
          terminalRef.current = "error";
          break;
        case "tool_call":
          scheduleFilesRefresh(eventRunId || runIdRef.current);
          break;
        default:
          break;
      }

      stepSeqRef.current += 1;
      const step = stepFromEvent(event, `step-${stepSeqRef.current}`, runSeqRef.current);
      // The runtime can report the same tool event twice (one per stream mode);
      // the timeline keeps the first occurrence instead of a phantom second run.
      if (step.kind === "tool_call" || step.kind === "tool_result") {
        const key = `${step.kind}:${asText(event.call_id) || asText(event.tool_name)}`;
        if (key !== `${step.kind}:`) {
          if (seenToolRef.current.has(key)) return;
          seenToolRef.current.add(key);
        }
      }
      setSteps((prev) => [...prev, step]);
    },
    [finalizeReasoning, rememberRunId, scheduleFilesRefresh, setPendingInterrupt],
  );

  /** Open one SSE stream: the previous reader is aborted before the next begins. */
  const openStream = useCallback(
    async (open: (signal: AbortSignal) => Promise<Response>) => {
      controllerRef.current?.abort();
      clearFilesTimer();

      const controller = new AbortController();
      controllerRef.current = controller;
      generationRef.current += 1;
      const generation = generationRef.current;
      terminalRef.current = null;
      reasoningRef.current = "";
      reasoningAgentRef.current = undefined;
      reasoningIdRef.current = "";
      setReasoning("");
      setReasoningStreaming(false);
      setPendingInterrupt(null);
      setError(null);
      setFiles([]);
      setStatus("streaming");

      const onEvent = (event: SseEvent) => {
        if (generationRef.current === generation) applyEvent(event);
      };

      try {
        const response = await open(controller.signal);
        if (generationRef.current !== generation) return;
        await readSse(response, onEvent, controller.signal);
      } catch (err) {
        if (generationRef.current === generation && !controller.signal.aborted) {
          setError(err instanceof Error ? err.message : String(err));
          terminalRef.current = "error";
        }
      }

      if (generationRef.current !== generation || !aliveRef.current) return;
      if (controllerRef.current === controller) controllerRef.current = null;
      setStatus(terminalRef.current ?? (pendingRef.current ? "awaiting" : "done"));
      await refreshFiles(runIdRef.current);
    },
    [applyEvent, clearFilesTimer, refreshFiles, setPendingInterrupt],
  );

  const start = useCallback(
    async (payload: StartPayload) => {
      const input = payload.input.trim();
      const agentId = (payload.agentId ?? "").trim();
      const definition = payload.definition ?? null;
      if (!input) return fail("Enter a message to run.");
      if (!agentId && !definition) {
        return fail("This agent has no saved slug yet and the draft is empty — nothing to run.");
      }
      // A new run owns the dock: the previous run id no longer labels it, the
      // file list starts empty and the timeline opens a new group.
      runIdRef.current = "";
      setRunId("");
      runSeqRef.current += 1;
      // "Deployment default" sends no override: the run then uses the
      // definition's own model and the runtime's default-resolution chain.
      // Picking a connection sends `{model:{client}}` for this run only.
      const chosen = (payload.modelClient || "").trim();
      const request: RunStreamRequest = {
        input,
        stream: true,
        ...(chosen && chosen !== "default" ? { model: { client: chosen } } : {}),
      };
      if (agentId) {
        await openStream((signal) => startRun({ ...request, agent_id: agentId }, signal));
        return;
      }
      if (definition) {
        await openStream((signal) => startRun({ ...request, definition }, signal));
      }
    },
    [fail, openStream],
  );

  const decide = useCallback(
    async (decisions: HitlDecision[]) => {
      const id = runIdRef.current;
      if (!id) return fail("No run is waiting for a decision.");
      if (!decisions.length) return fail("A decision is required to resume the run.");
      // HumanInTheLoopMiddleware wants exactly one decision per action request.
      // HitlCard decides the whole card, so a single approve/reject is repeated
      // for every action the interrupt asks about (same rule as the chat page).
      const pending = pendingRef.current;
      const actions = Array.isArray(pending?.action_requests) ? pending.action_requests.length : 0;
      const batch =
        decisions.length === 1 && actions > 1 && (decisions[0].type === "approve" || decisions[0].type === "reject")
          ? Array.from({ length: actions }, () => decisions[0])
          : decisions;
      // `openStream` clears the pending interrupt while the resume is in flight.
      await openStream((signal: AbortSignal) => resumeRun(id, batch, signal));
    },
    [fail, openStream],
  );

  const cancel = useCallback(() => {
    const id = runIdRef.current;
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearFilesTimer();
    setPendingInterrupt(null);
    setStatus((prev) => (prev === "streaming" ? "done" : prev));
    if (!id) return;
    void cancelRun(id).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [clearFilesTimer, setPendingInterrupt]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearFilesTimer();
    terminalRef.current = null;
    pendingRef.current = null;
    seenToolRef.current = new Set();
    runIdRef.current = "";
    stepSeqRef.current = 0;
    reasoningRef.current = "";
    reasoningAgentRef.current = undefined;
    reasoningIdRef.current = "";
    setTranscript("");
    setReasoning("");
    setReasoningStreaming(false);
    setSteps([]);
    setPending(null);
    setFiles([]);
    setStatus("idle");
    setError(null);
    setRunId("");
  }, [clearFilesTimer]);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      generationRef.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
      if (filesTimerRef.current !== null) window.clearTimeout(filesTimerRef.current);
    };
  }, []);

  return {
    transcript,
    reasoning,
    reasoningStreaming,
    steps,
    pending,
    files,
    status,
    error,
    runId,
    busy: status === "streaming",
    start,
    decide,
    cancel,
    reset,
  };
}
