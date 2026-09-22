/** Parse the model's chart/table placeholders out of a markdown reply.
 *
 * Two authoring forms are understood, and they can be mixed:
 *
 *   #TABLE_<call_id>                          (bare token — table, auto)
 *   #CHART_<call_id>  {"type":"bar", ...}     (bare token + inline JSON)
 *
 *   ```chart
 *   #CHART_<call_id>
 *   {"type":"bar", "x":"month", "y":["revenue"]}
 *   ```
 *
 *   ```table
 *   #TABLE_<call_id>
 *   {"title":"Revenue by region", "pageLength": 25}
 *   ```
 *
 * Everything that is not a block is returned as markdown segments, in order, so
 * the renderer can lay charts and prose out exactly as the model wrote them.
 */
import type { ChartSpec, RichBlock, RichSegment, TableSpec } from "./toolDataTypes";

const FENCE_OPEN = /^(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^(`{3,}|~{3,})\s*$/;
const TOKEN = /^\s*#(CHART|TABLE)_([A-Za-z0-9][A-Za-z0-9_-]*)\s*(.*)$/;
const TOKEN_ANY = /#(?:CHART|TABLE)_[A-Za-z0-9]/;
const FENCE_ANY = /(?:`{3,}|~{3,})\s*(?:chart|table)\b/i;

/** The first info word of a fence opener (`chart`, `python`, …), lower-cased. */
function fenceLanguage(info: string): string {
  return (info.trim().split(/[\s{]/)[0] || "").toLowerCase();
}

/** True when `line` closes a fence opened with `marker` repeated `length`. */
function closesFence(line: string, marker: string, length: number): boolean {
  const match = FENCE_CLOSE.exec(line.trim());
  return Boolean(match && match[1][0] === marker && match[1].length >= length);
}

/** Cheap pre-check so ordinary markdown never pays for the full parser. */
export function hasRichBlocks(content: string | undefined): boolean {
  if (!content) return false;
  return TOKEN_ANY.test(content) || FENCE_ANY.test(content);
}

/** The first balanced `{…}` object in `text`, or `null`. */
function balancedJson(text: string): string | null {
  const start = text.indexOf("{");
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return null;
}

function parseObject(text: string): Record<string, unknown> {
  const candidate = balancedJson(text);
  if (!candidate) return {};
  try {
    const parsed = JSON.parse(candidate);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function collectJson(lines: string[], startIdx: number, first: string): { json: string; endIdx: number } {
  let acc = first;
  let idx = startIdx;
  while (balancedJson(acc) === null && idx + 1 < lines.length) {
    idx += 1;
    acc += "\n" + lines[idx];
  }
  return { json: acc, endIdx: idx };
}

function blockFromBody(body: string[]): RichBlock | null {
  let start = -1;
  for (let i = 0; i < body.length; i++) {
    if (body[i].trim()) {
      start = i;
      break;
    }
  }
  if (start < 0) return null;
  const match = TOKEN.exec(body[start]);
  if (!match) {
    // Not a placeholder block: the fence is opaque and renders as code. The only
    // documented forms are a placeholder on the first line of a chart/table
    // fence, or a bare placeholder line; anything else is prose.
    return null;
  }
  const isChart = match[1].toUpperCase() === "CHART";
  const rest = [match[3] ?? "", ...body.slice(start + 1)].join("\n");
  const spec = parseObject(rest);
  return isChart
    ? { kind: "chart", callId: match[2], spec: asSpec<ChartSpec>(spec) }
    : { kind: "table", callId: match[2], spec: asSpec<TableSpec>(spec) };
}

/**
 * The parsed JSON as its wire contract. The server validates and normalises
 * every block before the reply is streamed or stored, so a block that is
 * rendered against data always carries a complete spec; this cast only crosses
 * the JSON boundary, it does not paper over a missing field.
 */
function asSpec<T>(value: Record<string, unknown>): T {
  return value as unknown as T;
}

/** A block written with no spec: a bare `#CHART_D1` / `#TABLE_D1` line. */
function isBareBlock(segment: RichSegment): boolean {
  return segment.kind !== "markdown" && Object.keys(segment.spec ?? {}).length === 0;
}

/**
 * Drop a bare placeholder that the model also wrote as the first line of the
 * immediately following fenced block — a common "lead-in then spec" habit that
 * would otherwise render the same result twice.
 */
function collapseBareLeadIns(segments: RichSegment[]): RichSegment[] {
  const cleaned: RichSegment[] = [];
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    const next = segments[i + 1];
    if (
      segment.kind !== "markdown" &&
      isBareBlock(segment) &&
      next &&
      next.kind === segment.kind &&
      next.callId === segment.callId
    ) {
      continue;
    }
    cleaned.push(segment);
  }
  return cleaned;
}

export function splitRichContent(content: string): RichSegment[] {
  const lines = (content ?? "").replace(/\r\n?/g, "\n").split("\n");
  const segments: RichSegment[] = [];
  let buffer: string[] = [];

  const flush = () => {
    if (!buffer.length) return;
    const text = buffer.join("\n").trim();
    buffer = [];
    if (text) segments.push({ kind: "markdown", text });
  };

  for (let i = 0; i < lines.length; i++) {
    const opener = FENCE_OPEN.exec(lines[i].trim());
    if (opener) {
      const marker = opener[1][0];
      const length = opener[1].length;
      const language = fenceLanguage(opener[2] ?? "");
      let end = i + 1;
      let closed = false;
      for (; end < lines.length; end++) {
        if (closesFence(lines[end], marker, length)) {
          closed = true;
          break;
        }
      }
      if (language === "chart" || language === "table") {
        // A chart/table fence is read as a block; when the closing fence never
        // arrived (a truncated stream) the rest of the reply is its body, so a
        // valid spec still renders instead of leaking as code.
        const body = closed ? lines.slice(i + 1, end) : lines.slice(i + 1);
        const block = blockFromBody(body);
        if (block) {
          flush();
          segments.push(block);
          i = closed ? end : lines.length - 1;
          continue;
        }
      }
      // Every other fence -- and a chart/table fence with no resolvable block --
      // is opaque: its body is copied verbatim, so a `#CHART_D1` written as an
      // example inside a code block is never mistaken for a real placeholder.
      const stop = closed ? end : lines.length - 1;
      for (let k = i; k <= stop; k++) buffer.push(lines[k]);
      i = stop;
      continue;
    }

    const token = TOKEN.exec(lines[i]);
    if (token) {
      const isChart = token[1].toUpperCase() === "CHART";
      const callId = token[2];
      let json = (token[3] ?? "").trim();
      if (!json && i + 1 < lines.length && lines[i + 1].trim().startsWith("{")) {
        const collected = collectJson(lines, i + 1, lines[i + 1]);
        json = collected.json;
        i = collected.endIdx;
      } else if (json) {
        const collected = collectJson(lines, i, json);
        json = collected.json;
        i = collected.endIdx;
      }
      const spec = parseObject(json);
      flush();
      segments.push(
        isChart
          ? { kind: "chart", callId, spec: asSpec<ChartSpec>(spec) }
          : { kind: "table", callId, spec: asSpec<TableSpec>(spec) },
      );
      continue;
    }

    buffer.push(lines[i]);
  }

  flush();
  return collapseBareLeadIns(segments);
}
