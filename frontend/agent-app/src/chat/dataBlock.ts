/** The `#CHART_<ref>` / `#TABLE_<ref>` placeholder protocol.
 *
 * Markdown structure — fences, paragraphs, tables — is react-markdown's job.
 * This module only turns a placeholder (plus the JSON spec that follows it)
 * into the block the renderer needs; it deliberately knows nothing about
 * fences or how markdown is laid out.
 */
import type {
  CardSpec,
  ChartSpec,
  ListSpec,
  NoteSpec,
  ProgressSpec,
  RichBlock,
  TableSpec,
} from "./toolDataTypes";

const TOKEN = /^\s*#(CHART|TABLE|CARD|LIST|PROGRESS)_([A-Za-z0-9][A-Za-z0-9_-]*)\s*([\s\S]*)$/;
const NOTE_TOKEN = /^\s*#NOTE\s*([\s\S]*)$/;
/** Cheap presence check, used only to widen a bubble that carries a block. */
const HAS_BLOCK = /#(?:CHART|TABLE|CARD|LIST|PROGRESS)_[A-Za-z0-9]|#NOTE\b|(?:`{3,}|~{3,})\s*(?:chart|table|card|list|progress|note)\b/i;

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

/**
 * The chart/table block a placeholder names, or `null` when `text` is not one.
 *
 * Accepts both documented forms — a bare `#TABLE_D1` line, or a placeholder
 * that starts a fenced `chart`/`table` body. A placeholder followed by prose is
 * prose, not a block, so the text is never swallowed by mistake.
 */
export function parseDataBlock(text: string | null | undefined): RichBlock | null {
  const raw = String(text ?? "").replace(/\r\n?/g, "\n");
  const lines = raw.split("\n");
  const start = lines.findIndex((line) => line.trim());
  if (start < 0) return null;
  const first = lines[start];
  const note = NOTE_TOKEN.exec(first);
  if (note) {
    const rest = [note[1] ?? "", ...lines.slice(start + 1)].join("\n").trim();
    if (rest && balancedJson(rest) === null) return null;
    const spec = parseObject(rest);
    return { kind: "note", callId: "", spec: spec as unknown as NoteSpec };
  }
  const match = TOKEN.exec(first);
  if (!match) return null;
  const rest = [match[3] ?? "", ...lines.slice(start + 1)].join("\n").trim();
  if (rest && balancedJson(rest) === null) return null;
  const spec = parseObject(rest);
  const kind = match[1].toUpperCase();
  if (kind === "CHART") return { kind: "chart", callId: match[2], spec: spec as unknown as ChartSpec };
  if (kind === "CARD") return { kind: "card", callId: match[2], spec: spec as unknown as CardSpec };
  if (kind === "LIST") return { kind: "list", callId: match[2], spec: spec as unknown as ListSpec };
  if (kind === "PROGRESS")
    return { kind: "progress", callId: match[2], spec: spec as unknown as ProgressSpec };
  return { kind: "table", callId: match[2], spec: spec as unknown as TableSpec };
}

/** True when `text` mentions a chart/table block (used to size the bubble). */
export function hasDataBlocks(text: string | null | undefined): boolean {
  return Boolean(text) && HAS_BLOCK.test(String(text));
}
