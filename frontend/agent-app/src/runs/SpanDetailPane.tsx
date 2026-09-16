/** Right-hand inspector for one trace span. */
import { useEffect, useMemo, useState } from "react";

import { MarkdownRenderer } from "../chat/MarkdownRenderer";
import type { TraceSpan, TraceSpanEvent, TraceSpanIO, TraceTurn } from "../types";
import { CopyButton } from "./traceBits";
import { coerceJson, formatDur, formatEventWhen, formatOffset, pretty, roleLabel } from "./runUtils";
import {
  ioValueText,
  traceKindGlyph,
  traceKindSlug,
  traceSpanDuration,
  traceSpanIsError,
  traceSpanLabel,
  traceSpanMeta,
  type TraceBounds,
} from "./traceUtils";
import "./traceDetail.css";

type TabId = "overview" | "attributes" | "raw" | "events";

function ReasoningBlock({ text }: { text: string }) {
  return (
    <details className="aa-tx-reasoning">
      <summary>Reasoning</summary>
      <MarkdownRenderer content={text} />
    </details>
  );
}

function ToolCallBlock({ call }: { call: { name: string; args?: unknown } }) {
  const args = call.args == null || call.args === "" ? "" : pretty(call.args);
  return (
    <div className="aa-tx-toolcall">
      <div className="aa-tx-toolcall-name">{call.name || "tool_call"}</div>
      {args ? <pre>{args}</pre> : null}
    </div>
  );
}

/** One-line summary of a turn, so a collapsed message still says something. */
function turnPreview(turn: TraceTurn): string {
  const calls = (turn.tool_calls || []).map((call) => call.name).filter(Boolean);
  const text = (turn.text || turn.reasoning || "").replace(/\s+/g, " ").trim();
  const parts: string[] = [];
  if (calls.length) parts.push(`→ ${calls.join(", ")}`);
  if (text) parts.push(text);
  return parts.join(" · ").slice(0, 140);
}

function TurnBlock({ turn }: { turn: TraceTurn }) {
  const calls = turn.tool_calls || [];
  const preview = turnPreview(turn);
  return (
    <details className="aa-turn aa-tx-turn" open>
      <summary className="aa-tx-turn-summary">
        <span className="aa-turn-role">{roleLabel(turn.role)}</span>
        {preview ? (
          <span className="aa-tx-turn-preview" title={preview}>
            {preview}
          </span>
        ) : null}
      </summary>
      <div className="aa-tx-turn-body">
        {turn.reasoning ? <ReasoningBlock text={turn.reasoning} /> : null}
        {turn.text ? (
          <div className="aa-turn-body">
            <MarkdownRenderer content={turn.text} />
          </div>
        ) : null}
        {calls.map((call, index) => (
          <ToolCallBlock key={`${call.name}-${index}`} call={call} />
        ))}
      </div>
    </details>
  );
}

function MessageList({ io }: { io?: TraceSpanIO | null }) {
  const turns = io?.turns || [];
  const rawText = ioValueText(io?.value);
  const systemTurns = turns.filter((turn) => (turn.role || "").toLowerCase() === "system");
  const rest = turns.filter((turn) => (turn.role || "").toLowerCase() !== "system");

  if (!turns.length && !rawText) return null;

  return (
    <>
      {systemTurns.map((turn, index) => (
        <details className="aa-turn-system" key={`system-${index}`}>
          <summary>System instructions</summary>
          <div className="aa-turn-body">
            <MarkdownRenderer content={turn.text} />
          </div>
        </details>
      ))}
      {rest.map((turn, index) => (
        <TurnBlock key={`${turn.role}-${index}`} turn={turn} />
      ))}
      {!turns.length && rawText ? (
        <div className="aa-span-field">
          <div className="aa-tx-payload-head">
            <span className="aa-span-field-label" style={{ marginBottom: 0 }}>
              Raw payload
            </span>
            <CopyButton text={rawText} />
          </div>
          <pre className="aa-tx-payload">{rawText}</pre>
        </div>
      ) : null}
    </>
  );
}

function MessageSection({ label, io }: { label: string; io?: TraceSpanIO | null }) {
  const turns = io?.turns || [];
  const hasTurns = Boolean(turns.length);
  const hasValue = Boolean(ioValueText(io?.value));
  if (!hasTurns && !hasValue) {
    return <div className="aa-tx-muted-block">No {label.toLowerCase()} captured</div>;
  }
  const systems = turns.filter((turn) => (turn.role || "").toLowerCase() === "system").length;
  return (
    <details className="aa-tx-msg-section" open>
      <summary className="aa-tx-msg-summary">
        <span className="aa-span-field-label" style={{ marginBottom: 0 }}>
          {label}
        </span>
        <span className="aa-muted aa-tx-msg-count">
          {hasTurns
            ? `${turns.length} message${turns.length === 1 ? "" : "s"}${systems ? ` · ${systems} system` : ""}`
            : "raw payload"}
        </span>
      </summary>
      <div className="aa-tx-msg-body">
        <MessageList io={io} />
      </div>
    </details>
  );
}

function StructuredValue({ value }: { value: unknown }) {
  const text = pretty(value);
  return (
    <div className="aa-span-field">
      <div className="aa-tx-payload-head">
        <div className="aa-span-field-label" style={{ marginBottom: 0 }}>
          Arguments
        </div>
        <CopyButton text={text} />
      </div>
      <pre className="aa-tx-payload">{text || "—"}</pre>
    </div>
  );
}

function ResultBlock({ label, value }: { label: string; value: unknown }) {
  const structured = value != null && typeof value === "object";
  const text = pretty(value);
  return (
    <div className="aa-span-field">
      <div className="aa-tx-payload-head">
        <div className="aa-span-field-label" style={{ marginBottom: 0 }}>
          {label}
        </div>
        <CopyButton text={text} />
      </div>
      {structured || !text ? (
        <pre className="aa-tx-payload">{text || "—"}</pre>
      ) : (
        <div className="aa-tx-payload">
          <MarkdownRenderer content={text} />
        </div>
      )}
    </div>
  );
}

const ARG_KEYS = [
  "tool.parameters",
  "tool.arguments",
  "gen_ai.tool.call.arguments",
  "arguments",
  "args",
  "input.value",
  "input",
];

const RESULT_KEYS = [
  "tool.result",
  "gen_ai.tool.call.result",
  "output.value",
  "result",
  "result_preview",
  "output",
];

function fromAttributes(span: TraceSpan, keys: string[]): { value: unknown; present: boolean } {
  const attrs = span.attributes || {};
  for (const key of keys) {
    const value = attrs[key];
    if (value != null && value !== "") return { value: coerceJson(value), present: true };
  }
  return { value: null, present: false };
}

function toolArguments(span: TraceSpan): { value: unknown; present: boolean } {
  const io = span.input;
  if (io?.value && io.value.trim()) return { value: coerceJson(io.value), present: true };
  for (const turn of io?.turns || []) {
    const call = (turn.tool_calls || [])[0];
    if (call) return { value: call.args ?? "", present: true };
    if (turn.text?.trim()) return { value: turn.text, present: true };
  }
  return fromAttributes(span, ARG_KEYS);
}

function toolResult(span: TraceSpan): { value: unknown; present: boolean } {
  const io = span.output;
  if (io?.value && io.value.trim()) return { value: coerceJson(io.value), present: true };
  for (const turn of io?.turns || []) {
    if (turn.text?.trim()) return { value: turn.text, present: true };
  }
  return fromAttributes(span, RESULT_KEYS);
}

function AttrValue({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > 180 || text.split("\n").length > 4;
  return (
    <div>
      <div className={`aa-kv-v aa-kv-code${isLong ? " aa-tx-longval" : ""}${isLong && expanded ? " expanded" : ""}`}>
        {text || "—"}
      </div>
      {isLong ? (
        <button type="button" className="aa-tx-kv-toggle" onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </div>
  );
}

function ErrorCallout({ span }: { span: TraceSpan }) {
  const messages = [span.status_message || ""];
  for (const event of span.events || []) {
    if (event.message) messages.push(event.message);
  }
  const details = messages.map((message) => message.trim()).filter(Boolean);
  return (
    <div className="aa-tx-error-callout" data-testid="span-error-callout">
      <div className="aa-tx-error-title">⚠ Span failed</div>
      {details.length ? (
        details.map((detail, index) => (
          <div className="aa-tx-error-body" key={index}>
            {detail}
          </div>
        ))
      ) : (
        <div className="aa-tx-error-body">The span reported an error but no message was captured.</div>
      )}
    </div>
  );
}

function EventsTab({ events }: { events: TraceSpanEvent[] }) {
  return (
    <div>
      {events.map((event, index) => (
        <div className="aa-tx-toolcall" key={`${event.name}-${index}`}>
          <div className="aa-tx-payload-head">
            <span className="aa-tx-toolcall-name">{event.name || "event"}</span>
            <span className="aa-muted" style={{ fontSize: 11 }}>
              {formatEventWhen(event.timestamp)}
            </span>
          </div>
          {event.message ? <pre className="aa-tx-payload">{event.message}</pre> : null}
          {event.attributes && Object.keys(event.attributes).length ? (
            <div className="aa-kv" style={{ marginTop: 6 }}>
              {Object.entries(event.attributes).map(([key, value]) => (
                <div className="aa-kv-row" key={key}>
                  <div className="aa-kv-k">{key}</div>
                  <div className="aa-kv-v aa-kv-code">{typeof value === "string" ? value : pretty(value)}</div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function OverviewTab({ span }: { span: TraceSpan }) {
  const kind = (span.kind || "UNKNOWN").toUpperCase();
  if (kind === "TOOL") {
    const args = toolArguments(span);
    const result = toolResult(span);
    return (
      <div>
        {span.tool_description ? (
          <div className="aa-span-field">
            <div className="aa-span-field-label">Tool description</div>
            <div className="aa-turn-body">
              <MarkdownRenderer content={span.tool_description} />
            </div>
          </div>
        ) : null}
        {args.present ? (
          <StructuredValue value={args.value} />
        ) : (
          <div className="aa-tx-muted-block">No arguments captured</div>
        )}
        {result.present ? (
          <ResultBlock label="Result" value={result.value} />
        ) : (
          <div className="aa-tx-muted-block">No result captured</div>
        )}
      </div>
    );
  }
  return (
    <div>
      <MessageSection label="Input" io={span.input} />
      <MessageSection label="Output" io={span.output} />
    </div>
  );
}

function attrText(value: unknown): string {
  if (typeof value === "string") {
    const coerced = coerceJson(value);
    return coerced === value ? value : pretty(coerced);
  }
  return pretty(value);
}

const CORE_ATTR_KEYS = new Set([
  "name", "kind", "span_id", "parent_id", "status", "start_time", "end_time",
  "duration", "model", "tool_name", "node", "agent_name",
  "prompt_tokens", "completion_tokens", "total_tokens",
]);

interface AttrGroup {
  name: string;
  rows: [string, unknown][];
}

function buildAttrGroups(span: TraceSpan): AttrGroup[] {
  const duration = traceSpanDuration(span);
  const core: [string, unknown][] = [
    ["name", span.name],
    ["kind", (span.kind || "").toUpperCase()],
    ["span_id", span.id],
    ["parent_id", span.parent_id || ""],
    ["status", span.status || ""],
    ["start_time", span.start_time || ""],
    ["end_time", span.end_time || ""],
    ["duration", formatDur(duration) || "0ms"],
    ["model", span.model || ""],
    ["tool_name", span.tool_name || ""],
    ["node", span.node || ""],
    ["agent_name", span.agent_name || ""],
    ["prompt_tokens", span.prompt_tokens ? String(span.prompt_tokens) : ""],
    ["completion_tokens", span.completion_tokens ? String(span.completion_tokens) : ""],
    ["total_tokens", span.total_tokens ? String(span.total_tokens) : ""],
  ];
  const groups: AttrGroup[] = [{ name: "span", rows: core.filter(([, value]) => value !== "" && value != null) }];
  const byPrefix = new Map<string, [string, unknown][]>();
  for (const [key, value] of Object.entries(span.attributes || {})) {
    if (CORE_ATTR_KEYS.has(key)) continue;
    const dot = key.indexOf(".");
    const prefix = dot > 0 ? key.slice(0, dot) : "other";
    const list = byPrefix.get(prefix) || [];
    list.push([key, value]);
    byPrefix.set(prefix, list);
  }
  for (const name of Array.from(byPrefix.keys()).sort()) {
    groups.push({ name, rows: byPrefix.get(name) as [string, unknown][] });
  }
  return groups;
}

function AttributesTab({ span }: { span: TraceSpan }) {
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => buildAttrGroups(span), [span]);
  const needle = filter.trim().toLowerCase();
  const visible = useMemo(
    () =>
      groups
        .map((group) => ({
          ...group,
          rows: needle
            ? group.rows.filter(([key, value]) => key.toLowerCase().includes(needle) || attrText(value).toLowerCase().includes(needle))
            : group.rows,
        }))
        .filter((group) => group.rows.length),
    [groups, needle],
  );
  const total = groups.reduce((sum, group) => sum + group.rows.length, 0);
  const shown = visible.reduce((sum, group) => sum + group.rows.length, 0);

  return (
    <div>
      <div className="aa-tx-attr-bar">
        <input
          id="span-attr-filter"
          name="span-attr-filter"
          className="aa-search"
          placeholder="Filter attributes…"
          aria-label="Filter attributes"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <span className="aa-muted">
          {shown === total ? `${total} attributes` : `${shown} of ${total} attributes`}
        </span>
      </div>
      {visible.map((group) => (
        <details className="aa-tx-attr-group" key={group.name} open>
          <summary>
            {group.name} <span className="aa-muted">{group.rows.length}</span>
          </summary>
          <div className="aa-kv">
            {group.rows.map(([key, value]) => (
              <div className="aa-kv-row" key={key}>
                <div className="aa-kv-k">{key}</div>
                <div className="aa-tx-attr-val">
                  <AttrValue text={attrText(value)} />
                  <CopyButton text={attrText(value)} className="aa-tx-copy-btn aa-tx-attr-copy" title="Copy value" />
                </div>
              </div>
            ))}
          </div>
        </details>
      ))}
      {!visible.length ? <div className="aa-tx-muted-block">No attributes match “{filter}”.</div> : null}
    </div>
  );
}

export function SpanDetailPane({
  span,
  bounds,
  loading = false,
}: {
  span: TraceSpan | null;
  bounds: TraceBounds;
  loading?: boolean;
}) {
  const [tab, setTab] = useState<TabId>("overview");
  const [decodeEscapes, setDecodeEscapes] = useState(false);

  useEffect(() => {
    setTab("overview");
    setDecodeEscapes(false);
  }, [span?.id]);

  if (!span) {
    return (
      <div className="aa-detail" data-testid="span-detail">
        <div className="aa-tx-muted-block" style={{ marginTop: 24 }}>
          Select a span in the waterfall to inspect its payloads, attributes and raw JSON.
        </div>
      </div>
    );
  }

  const kind = (span.kind || "UNKNOWN").toUpperCase();
  const slug = traceKindSlug(span.kind);
  const error = traceSpanIsError(span);
  const duration = traceSpanDuration(span);
  const events = span.events || [];
  const raw = JSON.stringify(span, null, 2);
  // The gutter and the body must be the *same* text, or decoded newlines make
  // the line numbers lie.
  const rawText = decodeEscapes ? raw.replace(/\\n/g, "\n").replace(/\\t/g, "\t") : raw;
  const rawLines = rawText.split("\n");
  const offset = formatOffset(span.start_time, bounds.start);
  const meta = traceSpanMeta(span);

  const tabs: { id: TabId; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "attributes", label: "Attributes" },
    { id: "raw", label: "Raw JSON" },
  ];
  if (events.length) tabs.push({ id: "events", label: `Events (${events.length})` });
  const activeTab: TabId = tab === "events" && !events.length ? "overview" : tab;

  return (
    <div className="aa-detail" data-testid="span-detail">
      <div className="aa-tx-detail-head">
        <div className="aa-tx-detail-title">
          <div style={{ minWidth: 0 }}>
            <h1 className="aa-tx-detail-name">{traceSpanLabel(span)}</h1>
            <div className="aa-tx-detail-meta">
              <span className={`aa-tx-badge k-${slug}`} aria-hidden="true">
                {traceKindGlyph(span.kind)}
              </span>
              <span className="aa-span-kind">{kind}</span>
              <span className={`aa-status-pill ${error ? "error" : "success"}`}>
                {error ? "Error" : span.status && span.status !== "UNSET" ? span.status : "OK"}
              </span>
              <span className="aa-chip-meta">{formatDur(duration) || "0ms"}</span>
              {span.model && meta !== span.model ? <span className="aa-chip-meta">{span.model}</span> : null}
              {meta ? <span className="aa-chip-meta">{meta}</span> : null}
              {offset ? <span className="aa-chip-meta">starts {offset}</span> : null}
              <CopyButton text={span.id} label={`id ${span.id.slice(0, 8)}`} title="Copy span id" />
            </div>
          </div>
        </div>
        <div
          className="aa-tx-tabs"
          role="tablist"
          aria-label="Span detail views"
          onKeyDown={(event) => {
            if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
            event.preventDefault();
            const index = tabs.findIndex((entry) => entry.id === activeTab);
            const delta = event.key === "ArrowRight" ? 1 : tabs.length - 1;
            setTab(tabs[(index + delta) % tabs.length].id);
          }}
        >
          {tabs.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              id={`span-tab-${entry.id}`}
              aria-selected={activeTab === entry.id}
              aria-controls={`span-panel-${entry.id}`}
              tabIndex={activeTab === entry.id ? 0 : -1}
              className={`aa-btn aa-tx-tab${activeTab === entry.id ? " active" : ""}`}
              onClick={() => setTab(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      </div>

      {error ? <ErrorCallout span={span} /> : null}

      {activeTab === "overview" ? (
        <div role="tabpanel" id="span-panel-overview" aria-labelledby="span-tab-overview">
          {loading ? (
            <div className="aa-tx-muted-block">Loading span payloads…</div>
          ) : (
            <OverviewTab span={span} />
          )}
        </div>
      ) : null}

      {activeTab === "attributes" ? (
        <div role="tabpanel" id="span-panel-attributes" aria-labelledby="span-tab-attributes">
          <AttributesTab span={span} />
        </div>
      ) : null}

      {activeTab === "raw" ? (
        <div role="tabpanel" id="span-panel-raw" aria-labelledby="span-tab-raw" className="aa-span-field">
          <div className="aa-tx-payload-head">
            <div className="aa-span-field-label" style={{ marginBottom: 0 }}>
              Span JSON <span className="aa-muted">· {rawLines.length} lines</span>
            </div>
            <div className="aa-tx-payload-actions">
              <label className="aa-check" title="Render escaped \\n and \\t as real line breaks">
                <input
                  id="span-decode-escapes"
                  name="span-decode-escapes"
                  type="checkbox"
                  checked={decodeEscapes}
                  onChange={(event) => setDecodeEscapes(event.target.checked)}
                />
                Decode
              </label>
              <CopyButton text={raw} label="Copy JSON" />
            </div>
          </div>
          <div className="aa-tx-json">
            <div className="aa-tx-json-gutter" aria-hidden="true">
              {rawLines.map((_, index) => (
                <div key={index}>{index + 1}</div>
              ))}
            </div>
            <pre className="aa-tx-json-body">{rawText}</pre>
          </div>
        </div>
      ) : null}

      {activeTab === "events" ? (
        <div role="tabpanel" id="span-panel-events" aria-labelledby="span-tab-events">
          <EventsTab events={events} />
        </div>
      ) : null}
    </div>
  );
}
