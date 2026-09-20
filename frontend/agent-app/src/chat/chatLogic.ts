import { firstWords } from "../shared/text";
import type { ChatMessage, ChatTurn, Conversation, SseEvent, StoredMessage } from "../types";

export function uid() {
  return Math.random().toString(36).slice(2);
}

/** LangGraph internal node names that should never replace a meaningful agent
 *  label. ``create_react_agent`` names its LLM and tools nodes ``agent``/``tools``
 *  (or ``pre_model_hook`` etc.), and subgraphs surface ``<participant>:<uuid>``
 *  namespaces. These are not user-facing agent names, so we keep the previously
 *  selected agent label instead of showing a raw ``AGENT``/``TOOLS`` chip. */
const INTERNAL_NODE_NAMES = new Set([
  "agent",
  "tools",
  "pre_model_hook",
  "post_model_hook",
  "__start__",
  "__end__",
]);

export function isMeaningfulAgentName(value?: string): boolean {
  if (!value) return false;
  const v = value.trim();
  if (!v) return false;
  if (INTERNAL_NODE_NAMES.has(v.toLowerCase())) return false;
  // A workflow subgraph namespace carries a trailing :uuid; treat it as internal.
  if (v.includes(":")) return false;
  return true;
}

export function asSse(ev: unknown): SseEvent | null {
  if (!ev || typeof ev !== "object") return null;
  const rec = ev as Record<string, unknown>;
  if (rec.type) return rec as SseEvent;
  if (rec.event_type) return { ...rec, type: String(rec.event_type) } as SseEvent;
  return rec as SseEvent;
}

export function hydrateMessages(full: Conversation, defaultAgentName?: string): ChatMessage[] {
  return (full.messages || []).map((m: StoredMessage) => {
    const events: SseEvent[] = [];
    for (const ev of m.events || []) {
      const mapped = asSse(ev);
      if (mapped) events.push(mapped);
    }
    const waiting = m.run_status === "awaiting_approval";
    const pending = waiting ? asSse(m.pending) || asSse(m.meta?.hitl) : null;
    if (pending && pending.type === "approval_request") {
      if (!events.some((e) => e.type === pending.type)) events.push(pending);
    }
    const reasoning = typeof m.meta?.reasoning === "string" ? m.meta.reasoning.trim() : "";
    if (reasoning) {
      events.unshift({ type: "reasoning", content: reasoning, done: true });
    }
    const attachments = m.meta?.attachments || [];
    const toolData = Array.isArray(m.meta?.tool_data) ? m.meta.tool_data : undefined;
    return {
      id: String(m.id),
      role: m.role === "user" ? "user" : "assistant",
      agent: (m.meta as Record<string, unknown> | null)?.agent ? String((m.meta as Record<string, unknown>).agent) : defaultAgentName,
      content: m.content || "",
      runId: m.run_id || undefined,
      runPublicId: m.run_public_id || m.meta?.public_id,
      events,
      attachments: attachments.length ? attachments : undefined,
      toolData,
      hitlResolved: !waiting,
    };
  });
}

/**
 * Group flat messages into conversational turns.
 * A turn begins with a user message and contains any assistant responses that follow.
 * The newest turn is marked isLatest: true so it can receive viewport height reservation.
 */
export function groupMessagesIntoTurns(messages: ChatMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = [];
  let currentTurn: ChatTurn | null = null;

  for (const m of messages) {
    if (m.role === "user") {
      currentTurn = {
        id: m.id,
        messages: [m],
        isLatest: false,
      };
      turns.push(currentTurn);
    } else {
      if (!currentTurn) {
        currentTurn = {
          id: m.id,
          messages: [m],
          isLatest: false,
        };
        turns.push(currentTurn);
      } else {
        currentTurn.messages.push(m);
      }
    }
  }

  if (turns.length > 0) {
    turns[turns.length - 1].isLatest = true;
  }
  return turns;
}

export function settleLiveReasoning(msg: ChatMessage): void {
  const text = (msg.reasoning || "").trim();
  msg.reasoning = undefined;
  msg.reasoningStreaming = false;
  if (!text) return;
  msg.events = [
    ...(msg.events || []),
    { type: "reasoning", content: text, done: true } as SseEvent,
  ];
}

export function applyEvent(msg: ChatMessage, ev: SseEvent): ChatMessage {
  const next: ChatMessage = { ...msg, events: msg.events ? [...msg.events] : [] };
  if (ev.run_id) next.runId = ev.run_id;
  if (ev.public_id) next.runPublicId = String(ev.public_id);
  if (ev.type === "reasoning") {
    next.reasoning = `${next.reasoning || ""}${ev.content || ""}`;
    if (isMeaningfulAgentName(ev.agent)) next.agent = ev.agent;
    next.reasoningStreaming = true;
    return next;
  }
  settleLiveReasoning(next);
  const hasPrev = Boolean((next.prevContent || "").trim());
  switch (ev.type) {
    case "token": {
      next.content = (next.content || "") + (ev.content || "");
      if (hasPrev && next.content) next.swapping = true;
      if (isMeaningfulAgentName(ev.agent)) next.agent = ev.agent;
      break;
    }
    case "chat": {
      if (ev.intermediate) {
        if (next.content) {
          next.events!.push({
            type: "assistant_delta",
            content: next.content,
            agent: ev.agent || next.agent,
          });
          next.prevContent = next.content;
          next.content = "";
          next.swapping = false;
        }
      } else if (hasPrev && next.content) {
        next.swapping = true;
      }
      next.events!.push(ev);
      break;
    }
    case "agent_switch":
      if (isMeaningfulAgentName(ev.agent)) next.agent = ev.agent;
      next.events!.push(ev);
      break;
    case "tool_call": {
      if (next.content) {
        next.events!.push({
          type: "assistant_delta",
          content: next.content,
          agent: ev.agent || next.agent,
        });
        next.prevContent = next.content;
        next.content = "";
        next.swapping = false;
      }
      next.events!.push(ev);
      break;
    }
    case "approval_request":
      next.hitlResolved = false;
      next.streaming = false;
      next.events!.push(ev);
      break;
    case "skill_load":
    case "tool_result":
    case "progress":
    case "error":
    case "status":
      next.events!.push(ev);
      if (ev.type === "error") next.error = ev.message || ev.error;
      break;
    case "done":
      next.streaming = false;
      // The server's reply is authoritative: it is redacted as a whole (host
      // paths removed), whereas the token stream can split a path across chunks
      // so client-side accumulation may still contain one.
      if (ev.reply) next.content = String(ev.reply);
      // Charts/tables in the reply are resolved against the cached tables the
      // server sends with the final event.
      if (Array.isArray(ev.tool_data)) next.toolData = ev.tool_data;
      if (hasPrev && next.content) {
        next.swapping = true;
      } else if (hasPrev && !next.content) {
        next.content = next.prevContent || "";
        next.prevContent = undefined;
        next.swapping = false;
      }
      // A run can legitimately finish with no text (a reasoning model under a
      // tight token cap returns an empty message). Saying so beats an empty
      // bubble that looks like a UI failure.
      if (!next.content && !next.error && ev.status !== "awaiting_approval") {
        next.emptyReply = true;
      }
      break;
    default:
      next.events!.push(ev);
  }
  return next;
}

export function pendingHitl(events: SseEvent[] | undefined): SseEvent | null {
  if (!events) return null;
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "approval_request") return events[i];
  }
  return null;
}

export function titleFromInput(text: string, files: File[]): string {
  const line = text.trim().split("\n")[0].slice(0, 80);
  if (line) return line;
  if (files.length) return files[0].name;
  return "New chat";
}

export function conversationTitle(c: Conversation): string {
  const t = (c.title || "").trim();
  return t || "New chat";
}

export function parseWhen(iso?: string): Date | null {
  if (!iso) return null;
  const raw = iso.trim();
  const normalized = raw.includes("T") ? raw : raw.replace(" ", "T");
  const hasZone = /[zZ]|[+-]\d{2}:?\d{2}$/.test(normalized);
  const d = new Date(hasZone ? normalized : `${normalized}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

const DATE_TIME_FORMAT: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
};

export function formatChatWhen(iso?: string): string {
  const d = parseWhen(iso);
  if (!d) return "—";
  return d.toLocaleString(undefined, DATE_TIME_FORMAT);
}

function startOfLocalDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function groupLabel(iso?: string): "Today" | "Yesterday" | "Older" {
  const d = parseWhen(iso);
  if (!d) return "Older";
  const diff = startOfLocalDay(new Date()) - startOfLocalDay(d);
  if (diff === 0) return "Today";
  if (diff === 86_400_000) return "Yesterday";
  return "Older";
}

export function groupedConversations(rows: Conversation[]): { label: string; items: Conversation[] }[] {
  const buckets: Record<"Today" | "Yesterday" | "Older", Conversation[]> = {
    Today: [],
    Yesterday: [],
    Older: [],
  };
  for (const row of rows) {
    buckets[groupLabel(row.updated_at || row.created_at)].push(row);
  }
  return (["Today", "Yesterday", "Older"] as const)
    .map((label) => ({ label, items: buckets[label] }))
    .filter((g) => g.items.length > 0);
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function parseArgs(raw: unknown): Record<string, unknown> {
  if (typeof raw === "string") {
    try {
      return asRecord(JSON.parse(raw));
    } catch {
      return raw ? { value: raw } : {};
    }
  }
  return asRecord(raw);
}

function firstString(rec: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const v = rec[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return "";
}

export function skillName(ev: SseEvent): string {
  if (typeof ev.skill === "string" && ev.skill.trim()) return ev.skill;
  const rec = asRecord(ev.skill);
  const args = parseArgs(rec.arguments ?? ev.arguments);
  return firstString({ ...rec, ...args }, ["name", "skill", "id", "skill_name"]) || "skill";
}

export function toolName(ev: SseEvent): string {
  if (ev.type === "skill_load") return skillName(ev);
  if (ev.tool_name && ev.tool_name.trim() && ev.tool_name !== "tool") return ev.tool_name;
  const rec = asRecord(ev);
  const args = parseArgs(ev.arguments);
  return firstString({ ...rec, ...args }, ["tool_name", "name", "tool"]) || "tool";
}

export function shortText(value: unknown, max = 240): string {
  const s = typeof value === "string" ? value : value == null ? "" : String(value);
  const t = s.trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}…`;
}

export function toolDetail(ev: SseEvent): string {
  if (ev.type === "tool_result") return shortText(ev.result);
  const args = parseArgs(ev.arguments);
  const target = firstString(args, ["path", "filename", "file", "target", "dest"]);
  const bits = Object.entries(args)
    .filter(([, v]) => v != null && typeof v !== "object")
    .slice(0, 4)
    .map(([k, v]) => `${k} ${v}`);
  if (target) return target;
  return bits.join(" · ");
}

export function capitalize(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export function parseTs(value: unknown): number | null {
  if (typeof value !== "string") return null;
  const d = parseWhen(value);
  return d ? d.getTime() : null;
}

export interface ActivityItem {
  icon: string;
  label: string;
  detail?: string;
  arguments?: unknown;
  result?: unknown;
  status?: "running" | "done";
  testid?: string;
  name?: string;
}

export function formatIo(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    const t = value.trim();
    if ((t[0] === "{" && t.endsWith("}")) || (t[0] === "[" && t.endsWith("]"))) {
      try {
        return JSON.stringify(JSON.parse(value), null, 2);
      } catch {
        return value;
      }
    }
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function activityDuration(
  events: SseEvent[] | undefined,
  startedAtMs?: number,
  streaming?: boolean,
  durationMs?: number,
): number | null {
  if (durationMs != null && durationMs > 0) return durationMs;
  if (startedAtMs != null) return Math.max(0, Date.now() - startedAtMs);
  const ts = (events || []).map((e) => parseTs(e.ts)).filter((n): n is number => n != null);
  if (ts.length >= 2) return Math.max(...ts) - Math.min(...ts);
  const sum = (events || []).reduce((acc, e) => acc + (Number(e.duration_ms) || 0), 0);
  return sum > 0 ? sum : null;
}

export function formatDuration(ms: number): string {
  const s = Math.max(1, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m}m`;
}

function activityIcon(name: string, evType?: string): string {
  if (evType === "status") return "bulb";
  if (evType === "skill_load") return "book";
  const n = (name || "").toLowerCase();
  if (/image|img|vision|photo|screenshot/.test(n)) return "image";
  if (/search|web|browse|retrieve|lookup/.test(n)) return "search";
  if (/file|read|write|code|editor|patch|list|dir|glob|ls/.test(n)) return "file";
  if (/run|shell|bash|terminal|command|exec/.test(n)) return "terminal";
  if (/db|sql|query|table|schema/.test(n)) return "db";
  return "tool";
}

function toolActivityLabel(ev: SseEvent, name: string): string {
  const args = parseArgs(ev.arguments);
  const query = firstString(args, ["query", "q", "prompt", "topic", "search", "url", "keyword"]);
  const n = (name || "").toLowerCase();
  if (/search|web|browse|retrieve/.test(n)) {
    const hasImage = /image|img|vision|photo/.test(n) || Boolean(args.image || args.image_url);
    return hasImage
      ? query
        ? `Searched images ${query}`
        : "Searched images"
      : query
        ? `Searched ${query}`
        : `Ran ${name}`;
  }
  const detail = toolDetail(ev);
  const pretty = n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  return detail ? `${pretty} · ${detail}` : pretty;
}

export function activityItems(events: SseEvent[] | undefined, streaming?: boolean): ActivityItem[] {
  const list = events || [];
  const hasSpans = list.some((e) => e.type === "execute_tool");
  const out: ActivityItem[] = [];
  const byCall = new Map<string, ActivityItem>();
  const seenSkill = new Set<string>();
  const pending: Record<string, ActivityItem[]> = {};
  let interimBuf = "";
  const flushInterim = () => {
    if (!interimBuf.trim()) {
      interimBuf = "";
      return;
    }
    out.push({
      icon: "bulb",
      label: "Assistant",
      detail: interimBuf.trim(),
      testid: "assistant-line",
      name: "assistant",
    });
    interimBuf = "";
  };
  for (const ev of list) {
    if (ev.type === "assistant_delta") {
      // Accumulate consecutive interim model fragments; flush when a tool call
      // or another event interrupts them.
      interimBuf += String(ev.content || "");
      continue;
    }
    flushInterim();
    if (ev.type === "reasoning") {
      const text = String(ev.content || "").trim();
      if (!text) continue;
      out.push({
        icon: "bulb",
        label: `Think · ${firstWords(text)}`,
        detail: text,
        testid: "reasoning-card",
        name: "reasoning",
      });
    } else if (ev.type === "status" || ev.type === "progress") {
      const msg = String(ev.message || ev.state || "").trim();
      if (!msg) continue;
      out.push({ icon: "bulb", label: capitalize(msg) });
    } else if (ev.type === "skill_load") {
      const name = skillName(ev);
      if (!name || name === "tool" || seenSkill.has(name)) continue;
      seenSkill.add(name);
      out.push({
        icon: "book",
        label: `Loaded ${name}`,
        arguments: ev.arguments != null ? ev.arguments : ev.skill,
        result: ev.result,
        testid: "skill-card",
      });
    } else if (ev.type === "execute_tool" && hasSpans) {
      const name = String(ev.tool_name || "").trim();
      if (!name) continue;
      const item: ActivityItem = {
        icon: activityIcon(name),
        label: toolActivityLabel(ev, name),
        detail: toolDetail(ev),
        arguments: ev.arguments,
        result: ev.result,
        testid: "tool-card",
      };
      if (item.result == null) {
        const r = list.find((x) => x.type === "tool_result" && toolName(x) === name);
        if (r) item.result = r.result;
      }
      out.push(item);
    } else if (ev.type === "tool_call" && !hasSpans) {
      const name = toolName(ev);
      const key = ev.call_id ? String(ev.call_id) : null;
      const chunk =
        ev.arguments != null && typeof ev.arguments === "string" && ev.arguments !== "" ? ev.arguments : null;
      let item: ActivityItem | undefined = key ? byCall.get(key) : undefined;
      if (!item && name === "tool" && chunk) {
        item = [...out].reverse().find((x) => x.testid === "tool-card" && x.result == null);
      }
      if (!item && name !== "tool") {
        const created: ActivityItem = {
          icon: activityIcon(name),
          label: toolActivityLabel(ev, name),
          detail: toolDetail(ev),
          arguments: ev.arguments != null && ev.arguments !== "" ? ev.arguments : null,
          testid: "tool-card",
          name,
        };
        if (key) byCall.set(key, created);
        out.push(created);
        (pending[name] = pending[name] || []).push(created);
      } else if (item && chunk && (item.arguments == null || typeof item.arguments === "string")) {
        item.arguments = item.arguments == null ? chunk : `${item.arguments}${chunk}`;
      }
    } else if (ev.type === "tool_result" && !hasSpans) {
      const name = toolName(ev);
      const key = ev.call_id ? String(ev.call_id) : null;
      let item: ActivityItem | undefined = key ? byCall.get(key) : undefined;
      if (!item && name !== "tool") {
        const q = pending[name];
        if (q && q.length) item = q.shift();
      }
      if (!item) item = [...out].reverse().find((x) => x.testid === "tool-card" && x.result == null);
      if (item) {
        item.result = ev.result;
        if (item.name) {
          const argsEv = { ...ev, type: "tool_call", arguments: item.arguments };
          item.detail = toolDetail(argsEv);
          item.label = toolActivityLabel(argsEv, item.name);
        }
      }
    }
  }
  flushInterim();
  if (streaming && out.length) out[out.length - 1].status = "running";
  return out;
}
