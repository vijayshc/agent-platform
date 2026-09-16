import type { RunRow, SpanEvent, TraceSpan, TraceSummary } from "../types";

export const TIMELINE_TYPES = new Set([
  "invoke_agent",
  "chat",
  "execute_tool",
  "skill_load",
  "approval_request",
  "hitl_decision",
  "tool_call",
  "tool_result",
  "error",
]);

export const EVENT_PRIORITY: Record<string, number> = {
  invoke_agent: 0,
  chat: 1,
  tool_call: 2,
  approval_request: 3,
  hitl_decision: 4,
  execute_tool: 5,
  skill_load: 5,
  tool_result: 5,
  error: 6,
};

export interface SpanNode {
  id: string | number;
  event: SpanEvent;
  depth: number;
  children: SpanNode[];
  hasChildren: boolean;
}

export function runParam(): string | null {
  const q = new URLSearchParams(window.location.search);
  return (
    q.get("run") ||
    window.location.pathname.split("/observability/")[1] ||
    window.location.pathname.split("/agent-runs/")[1] ||
    null
  );
}

export function parseTs(value?: string | null): number | null {
  if (!value) return null;
  const raw = String(value).trim();
  const iso = raw.includes("T") ? raw : raw.replace(" ", "T");
  const ms = Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isFinite(ms) ? ms : null;
}

/** Human duration across the whole range a run can take (a hung run is days). */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function elapsedMs(run: RunRow): number | null {
  const start = parseTs(run.started_at);
  if (start == null) return null;
  const end = parseTs(run.finished_at) || Date.now();
  return Math.max(0, end - start);
}

export function latency(run: RunRow): string {
  const ms = elapsedMs(run);
  return ms == null ? "" : formatElapsed(ms);
}

/** The unrounded value, for a `title` tooltip next to the human duration. */
export function latencyExact(run: RunRow): string {
  const ms = elapsedMs(run);
  if (ms == null) return "";
  return `${(ms / 1000).toFixed(1)}s`;
}

export function formatWhen(iso?: string | null): string {
  const ms = parseTs(iso);
  if (ms == null) return iso || "";
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatEventClock(iso?: string | null): string {
  const ms = parseTs(iso);
  if (ms == null) return "";
  return new Date(ms).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatEventWhen(iso?: string | null): string {
  const ms = parseTs(iso);
  if (ms == null) return iso || "";
  return new Date(ms).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatOffset(iso?: string | null, origin = 0): string {
  const ms = parseTs(iso);
  if (ms == null || !origin) return "";
  const delta = Math.max(0, ms - origin);
  if (delta < 1000) return `+${Math.round(delta)}ms`;
  return `+${(delta / 1000).toFixed(2)}s`;
}

export function formatDur(ms?: number | null): string {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return "";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)}s` : `${Math.round(n)}ms`;
}

export function statusLabel(status?: string | null): string {
  return (status || "unknown").replace(/_/g, " ");
}

export function userLabel(run: RunRow): string {
  if (run.username) return run.username;
  if (run.user_id != null) return `#${run.user_id}`;
  return "—";
}

export function eventKind(ev: SpanEvent): string {
  const type = ev.event_type;
  if (type === "execute_tool" || type === "tool_call" || type === "tool_result") return "Tool";
  if (type === "chat") return "Chat";
  if (type === "invoke_agent") return "Run";
  if (type === "agent_switch") return "Switch";
  if (type === "skill_load") return "Skill";
  if (isHitl(ev)) return "HITL";
  if (type === "error") return "Error";
  return (type || "event").replace(/_/g, " ");
}

export function isHitl(ev: SpanEvent) {
  return ["approval_request", "hitl_decision"].includes(ev.event_type);
}

export function spanCaption(ev: SpanEvent): { title: string; extra: string } {
  const type = ev.event_type;
  const tool = (ev.tool_name || "").trim();
  const agent = (ev.agent_name || "").trim();
  const raw = (ev.span_name || type || "").replace(/_/g, " ").trim();
  let title = raw;
  let extra = "";
  if (type === "execute_tool" || type === "tool_result") {
    title = tool ? `Tool Result: ${tool}` : raw || "Tool Result";
    extra = agent || "Tool";
  } else if (type === "tool_call") {
    title = tool ? `Tool Call: ${tool}` : raw || "Tool Call";
    extra = agent || "Tool";
  } else if (type === "chat") {
    title = agent ? `LLM Call: ${agent}` : (raw.startsWith("LLM Call") ? raw : (raw.startsWith("chat") ? `LLM Call: ${raw.replace(/^chat\s*/i, "")}` : "LLM Call"));
    extra = "";
    extra = "Run";
  } else if (type === "agent_switch") {
    title = agent ? `Switch: ${agent}` : "Switch";
    extra = "Switch";
  } else if (type === "skill_load") {
    title = tool ? `Skill: ${tool}` : raw || "Skill";
    extra = agent || "Skill";
  } else if (isHitl(ev)) {
    if (type === "hitl_decision") {
      const detail = asRecord(ev.detail);
      const approved = detail.decision === "approved";
      title = approved ? "Approval: Approved" : "Approval: Denied";
      extra = agent || raw;
    } else {
      title = "Approval requested";
      extra = agent || raw;
    }
  }
  if (extra && title.toLowerCase().includes(extra.toLowerCase())) extra = "";
  return { title, extra };
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function pretty(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 1) {
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

export function coerceJson(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && trimmed.length > 1) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }
  return value;
}

export type Turn = { role: string; text: string };

export function partText(part: unknown): string {
  if (typeof part === "string") return part;
  const rec = asRecord(part);
  const ptype = String(rec.type || "").toLowerCase();

  if (ptype === "tool_call" || ptype === "function_call" || ptype === "function" || ptype === "tool_use" || rec.function || rec.functionCall || rec.function_call) {
    const fn = asRecord(rec.function || rec.functionCall || rec.function_call);
    const name = String(rec.name || fn.name || rec.tool || "");
    const rawArgs = rec.arguments ?? fn.arguments ?? rec.args ?? fn.args ?? rec.input;
    const argsStr = typeof rawArgs === "object" && rawArgs !== null ? JSON.stringify(rawArgs) : String(rawArgs || "");
    return name ? `[tool ${name}] ${argsStr}`.trim() : argsStr;
  }

  if (ptype === "tool_call_response" || ptype === "function_result" || ptype === "tool_result") {
    const res = rec.response ?? rec.result ?? rec.content ?? "";
    return typeof res === "object" && res !== null ? JSON.stringify(res) : String(res);
  }

  if (typeof rec.content === "string" && rec.content) return rec.content;
  if (typeof rec.text === "string" && rec.text) return rec.text;
  if (typeof rec.thought === "string" && rec.thought) return rec.thought;
  if (typeof rec.reasoning === "string" && rec.reasoning) return rec.reasoning;

  return "";
}

export function messageToTurn(msg: unknown): Turn | null {
  if (typeof msg === "string") {
    const text = msg.trim();
    return text ? { role: "message", text } : null;
  }
  const rec = asRecord(msg);
  let role = String(rec.role || rec.author || "").toLowerCase();
  if (role === "model") role = "assistant";

  const chunks: string[] = [];

  if (typeof rec.content === "string" && rec.content.trim()) {
    chunks.push(rec.content.trim());
  } else if (Array.isArray(rec.content)) {
    const txt = rec.content.map(partText).filter(Boolean).join("\n\n");
    if (txt.trim()) chunks.push(txt.trim());
  }

  if (Array.isArray(rec.parts)) {
    const txt = rec.parts.map(partText).filter(Boolean).join("\n\n");
    if (txt.trim()) chunks.push(txt.trim());
  }

  if (!chunks.length) {
    if (Array.isArray(rec.tool_calls) && rec.tool_calls.length) {
      const txt = rec.tool_calls.map(partText).filter(Boolean).join("\n\n");
      if (txt.trim()) chunks.push(txt.trim());
    } else if (rec.function_call) {
      const txt = partText({ function_call: rec.function_call });
      if (txt.trim()) chunks.push(txt.trim());
    }
  }

  if (typeof rec.text === "string" && rec.text.trim() && !chunks.length) {
    chunks.push(rec.text.trim());
  }
  if (typeof rec.message === "string" && rec.message.trim() && !chunks.length) {
    chunks.push(rec.message.trim());
  }

  const thought = String(rec.thought || rec.reasoning || "").trim();
  if (thought && !chunks.some((c) => c.includes(thought))) {
    chunks.unshift(thought);
  }

  const text = chunks.join("\n\n").trim();
  if (!role && !text) return null;
  return { role: role || "assistant", text };
}

export function parseRoleDump(src: string): Turn[] | null {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const roleLine = /^(system|user|assistant|tool|developer|function|model)\s*:\s*(.*)$/i;
  const turns: Turn[] = [];
  let cur: Turn | null = null;
  for (const line of lines) {
    const match = line.match(roleLine);
    if (match) {
      if (cur) turns.push(cur);
      const r = match[1].toLowerCase();
      cur = { role: r === "model" ? "assistant" : r, text: match[2] || "" };
      continue;
    }
    if (!cur) continue;
    const wrapped = line.match(/^\s*message:\s*(.*)$/i);
    if (wrapped && !cur.text.trim()) {
      cur.text = wrapped[1] || "";
      continue;
    }
    cur.text = cur.text ? `${cur.text}\n${line}` : line;
  }
  if (cur) turns.push(cur);
  const cleaned = turns
    .map((t) => {
      let txt = t.text.trim();
      txt = txt.replace(/(\n|^)(\[tool\s)/g, (match, prefix, tag) => (prefix ? `\n\n${tag}` : tag));
      return { ...t, text: txt };
    })
    .filter((t) => t.role && t.text);
  return cleaned.length ? cleaned : null;
}

export function parseTurns(value: unknown): Turn[] | null {
  if (value == null || value === "") return null;
  const parsed = coerceJson(value);
  if (Array.isArray(parsed)) {
    const turns = parsed.map(messageToTurn).filter((t): t is Turn => !!t && !!t.text);
    return turns.length ? turns : null;
  }
  if (parsed && typeof parsed === "object") {
    const rec = asRecord(parsed);
    if (Array.isArray(rec.messages)) return parseTurns(rec.messages);
    if (Array.isArray(rec.choices)) {
      const msgs = (rec.choices as Record<string, unknown>[]).map((c) => c.message || c.delta || c);
      return parseTurns(msgs);
    }
    const one = messageToTurn(parsed);
    if (one?.text) return [one];
  }
  if (typeof value === "string") {
    const fromDump = parseRoleDump(value);
    if (fromDump) return fromDump;
    const trimmed = value.trim();
    if (trimmed) return [{ role: "assistant", text: trimmed }];
  }
  if (typeof parsed === "string") {
    const fromDump = parseRoleDump(parsed);
    if (fromDump) return fromDump;
    const trimmed = parsed.trim();
    if (trimmed) return [{ role: "assistant", text: trimmed }];
  }
  return null;
}

export function kvEntries(value: unknown): [string, string][] | null {
  const parsed = coerceJson(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const rec = asRecord(parsed);
  const keys = Object.keys(rec);
  if (!keys.length || keys.length > 24) return null;
  return keys.map((k) => [k, typeof rec[k] === "string" ? rec[k] : pretty(rec[k])]);
}

export function roleLabel(role: string): string {
  if (role === "system") return "System";
  if (role === "user") return "User";
  if (role === "assistant" || role === "model") return "Assistant";
  if (role === "tool" || role === "function") return "Tool";
  if (role === "developer") return "Developer";
  return role || "Message";
}

export function pickField(span: SpanEvent, keys: string[]): unknown {
  const detail = asRecord(span.detail);
  const attrs = asRecord(detail.attributes);
  const top = span as unknown as Record<string, unknown>;
  for (const key of keys) {
    for (const src of [top, detail, attrs]) {
      const value = src[key];
      if (value != null) {
        if (typeof value === "string") {
          const trimmed = value.trim();
          if (trimmed && trimmed !== "assistant:" && trimmed !== "user:" && trimmed !== "system:") {
            return value;
          }
        } else {
          return value;
        }
      }
    }
  }
  return undefined;
}

export function callIdOf(span: SpanEvent): string {
  const detail = asRecord(span.detail);
  const attrs = asRecord(detail.attributes);
  const top = span as unknown as Record<string, unknown>;
  for (const src of [detail, attrs, top]) {
    for (const key of ["call_id", "gen_ai.tool.call.id"]) {
      const value = src[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  return "";
}

export function toolOf(span: SpanEvent): string {
  const detail = asRecord(span.detail);
  if (span.tool_name && span.tool_name.trim()) return span.tool_name.trim();
  if (typeof detail.tool_name === "string" && detail.tool_name.trim()) return detail.tool_name.trim();
  return "";
}

export function fillFromSiblings(span: SpanEvent, events: SpanEvent[], keys: string[]): unknown {
  const own = pickField(span, keys);
  if (own != null && own !== "") return own;
  const callId = callIdOf(span);
  const tool = toolOf(span);
  for (const ev of events) {
    if (ev.id === span.id) continue;
    const match = (callId && callIdOf(ev) === callId) || (tool && toolOf(ev) === tool);
    if (!match) continue;
    const value = pickField(ev, keys);
    if (value != null && value !== "") return value;
  }
  return own;
}

export function coveredByExecuteTool(ev: SpanEvent, timeline: SpanEvent[]): boolean {
  return false;
}

export function hitlDecision(span: SpanEvent): string | null {
  const detail = asRecord(span.detail);
  if (detail.decision != null && detail.decision !== "") return String(detail.decision);
  return null;
}

export function sortEventsChronologically(a: SpanEvent, b: SpanEvent): number {
  const ta = parseTs(a.ts) ?? 0;
  const tb = parseTs(b.ts) ?? 0;
  if (ta !== tb) return ta - tb;
  const pa = EVENT_PRIORITY[a.event_type] ?? 99;
  const pb = EVENT_PRIORITY[b.event_type] ?? 99;
  if (pa !== pb) return pa - pb;
  return Number(a.id || 0) - Number(b.id || 0);
}

export function buildSpanTree(events: SpanEvent[]): SpanNode[] {
  const bySpanId = new Map<string, SpanEvent>();
  for (const ev of events) {
    if (ev.span_id) bySpanId.set(ev.span_id, ev);
  }

  const childrenByParent = new Map<string, SpanEvent[]>();
  const roots: SpanEvent[] = [];

  for (const ev of events) {
    const parentId = ev.parent_span_id;
    if (parentId && bySpanId.has(parentId)) {
      const list = childrenByParent.get(parentId) || [];
      list.push(ev);
      childrenByParent.set(parentId, list);
    } else {
      roots.push(ev);
    }
  }

  roots.sort(sortEventsChronologically);
  for (const list of childrenByParent.values()) {
    list.sort(sortEventsChronologically);
  }

  // A span row without a server id still needs a stable React key: the event's
  // position in the payload is stable for a given response.
  const idByEvent = new Map<SpanEvent, string>();
  events.forEach((ev, index) => idByEvent.set(ev, String(ev.span_id || ev.id || `span-${index}`)));

  function createNode(ev: SpanEvent, depth = 0): SpanNode {
    const rawChildren = childrenByParent.get(ev.span_id || "") || [];
    const children = rawChildren.map((c) => createNode(c, depth + 1));
    return {
      id: idByEvent.get(ev) as string,
      event: ev,
      depth,
      children,
      hasChildren: children.length > 0,
    };
  }

  return roots.map((r) => createNode(r, 0));
}

export function flattenSpanTree(
  nodes: SpanNode[],
  collapsedIds: Set<string | number> = new Set()
): SpanNode[] {
  const flat: SpanNode[] = [];
  function walk(node: SpanNode) {
    flat.push(node);
    if (!collapsedIds.has(node.id)) {
      for (const child of node.children) {
        walk(child);
      }
    }
  }
  for (const root of nodes) {
    walk(root);
  }
  return flat;
}

export function isToolishTurn(t: Turn): boolean {
  return t.role === "tool" || t.role === "function";
}

export function splitTurns(turns: Turn[], kind?: "prompt" | "response") {
  const system = kind === "prompt" ? turns.filter((t) => t.role === "system") : [];
  let rest = turns.filter((t) => t.role !== "system");
  if (kind === "prompt") {
    const userTurns = rest.filter((t) => t.role === "user");
    rest = userTurns.length ? userTurns : rest.filter((t) => t.role !== "assistant" && t.role !== "model");
  }
  if (kind === "response") {
    const assistantTurns = rest.filter((t) => t.role === "assistant" || t.role === "model");
    rest = assistantTurns.length ? assistantTurns : rest.filter((t) => t.role !== "user" && t.role !== "system");
    if (!rest.length && turns.length) {
      rest = turns.filter((t) => t.role !== "system");
    }
  }
  return { rest, system };
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function formatRichText(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br/>");
}

/* ------------------------------------------------------------------ *
 * Phoenix trace spans (GET /api/v1/runs/<id>/trace)
 * ------------------------------------------------------------------ */

/** Sibling ordering in the waterfall. */
export function listingRows(text: string): { kind: string; name: string }[] | null {
  const lines = text.replace(/\\t/g, "\t").trim().split(/\n/);
  if (!lines.length) return null;
  const rows: { kind: string; name: string }[] = [];
  for (const line of lines) {
    const match = line.match(/^(dir|file|lnk)\t+(.+)$/i);
    if (!match) return null;
    rows.push({ kind: match[1].toLowerCase(), name: match[2] });
  }
  return rows.length ? rows : null;
}
