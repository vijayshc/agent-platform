import { useEffect, useLayoutEffect, useRef, useState, type AnimationEvent } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { parseDataBlock } from "./dataBlock";
import { blockLayout } from "./toolDataTypes";

type Chunk = { id: number; text: string };

function isFenceLine(line: string): boolean {
  return line.startsWith("```");
}

/** Split streaming markdown so fences are atomic: never cut on `\n\n` inside a
 *  fence, keep an open fence entirely in live, and freeze a closed fence as one
 *  block as soon as it closes (no wait for a trailing blank line). */
function splitStreaming(content: string): { frozenBlocks: string[]; live: string; liveIsFence: boolean } {
  if (!content) return { frozenBlocks: [], live: "", liveIsFence: false };

  const lines = content.split("\n");
  const frozenBlocks: string[] = [];
  let para: string[] = [];
  let fence: string[] | null = null;

  function flushPara() {
    const text = para.join("\n");
    para = [];
    if (text) frozenBlocks.push(text);
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (fence) {
      fence.push(line);
      if (isFenceLine(line)) {
        frozenBlocks.push(fence.join("\n"));
        fence = null;
      }
      continue;
    }

    if (isFenceLine(line)) {
      flushPara();
      fence = [line];
      continue;
    }

    if (line === "") {
      flushPara();
      continue;
    }

    para.push(line);
  }

  if (fence) return { frozenBlocks, live: fence.join("\n"), liveIsFence: true };
  return { frozenBlocks, live: para.join("\n"), liveIsFence: false };
}

function shownText(solid: string, chunks: Chunk[], pending: string): string {
  return solid + chunks.map((c) => c.text).join("") + pending;
}

/** The ```language of an open fence, when it is a chart/table/dashboard block. */
function dataFenceKind(live: string): "chart" | "table" | "card" | "list" | "progress" | "note" | null {
  const first = live.split("\n")[0].trim().toLowerCase();
  if (/^```chart\b/.test(first)) return "chart";
  if (/^```table\b/.test(first)) return "table";
  if (/^```card\b/.test(first)) return "card";
  if (/^```list\b/.test(first)) return "list";
  if (/^```progress\b/.test(first)) return "progress";
  if (/^```note\b/.test(first)) return "note";
  return null;
}

/** Placeholder shown while a chart/table/dashboard fence is still streaming, so the raw
 *  JSON never flashes in the transcript. */
function DataFenceSkeleton({ kind, layout }: { kind: string; layout?: string }) {
  return (
    <div
      className="td-card td-card-skeleton"
      data-layout={layout || "full"}
      data-testid={`${kind}-skeleton`}
      aria-busy="true"
    >
      <div className="td-card-head">
        <span className="td-card-title">Preparing {kind}…</span>
      </div>
      <div className="td-skeleton-body" aria-hidden="true">
        <span className="td-skeleton-bar" />
        <span className="td-skeleton-bar td-skeleton-short" />
        <span className="td-skeleton-bar" />
      </div>
    </div>
  );
}

/** Narrow (`half`/`third`/`quarter`) dashboard layout of a frozen markdown
 *  block, or `null` when the block is prose, a full-width block, or not a
 *  data block at all. Mirrors `narrowBlock` in dataRowPlugin, which is what
 *  the settled render uses — streaming must group the same way or narrow
 *  cards stack one-per-row until the turn finishes. */
function narrowLayoutOf(blockText: string): string | null {
  let block = null;
  try {
    block = parseDataBlock(blockText);
    if (!block) {
      // Frozen fence blocks still carry their ``` markers; the renderer
      // strips them before parsing, so do the same here.
      const lines = blockText.split("\n");
      if (lines.length >= 2 && lines[0].trim().startsWith("```")) {
        const end = lines.lastIndexOf("```");
        const inner = (end > 0 ? lines.slice(1, end) : lines.slice(1)).join("\n");
        block = parseDataBlock(inner);
      }
    }
  } catch {
    return null;
  }
  if (!block) return null;
  const layout = blockLayout(block);
  return layout === "full" ? null : layout;
}

/** Best-effort layout of a still-streaming data fence. The spec JSON is
 *  incomplete, but `"layout"` is often already streamed; otherwise fall back
 *  to the layout of the narrow run it continues so the skeleton reserves the
 *  right column instead of flashing full width. */
function liveFenceLayout(live: string, fallback: string | null): string | null {
  const match = /"layout"\s*:\s*"(half|third|quarter|full)"/.exec(live);
  if (match) return match[1] === "full" ? null : match[1];
  return fallback;
}

function LiveTail({ text }: { text: string }) {
  const [solid, setSolid] = useState("");
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const solidRef = useRef("");
  const chunksRef = useRef<Chunk[]>([]);
  const pendingRef = useRef("");
  const endedRef = useRef(new Set<number>());
  const rafRef = useRef(0);
  const genRef = useRef(0);

  function commit(nextSolid: string, nextChunks: Chunk[]) {
    solidRef.current = nextSolid;
    chunksRef.current = nextChunks;
    setSolid(nextSolid);
    setChunks(nextChunks);
  }

  function flushEnded() {
    let nextSolid = solidRef.current;
    const next: Chunk[] = [];
    let merging = true;
    for (const chunk of chunksRef.current) {
      if (merging && endedRef.current.has(chunk.id)) {
        nextSolid += chunk.text;
        endedRef.current.delete(chunk.id);
      } else {
        merging = false;
        next.push(chunk);
      }
    }
    if (nextSolid !== solidRef.current || next.length !== chunksRef.current.length) {
      commit(nextSolid, next);
    }
  }

  function resetTo(next: string) {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    pendingRef.current = "";
    endedRef.current.clear();
    if (!next) {
      commit("", []);
      return;
    }
    genRef.current += 1;
    commit("", [{ id: genRef.current, text: next }]);
  }

  function enqueue(suffix: string) {
    pendingRef.current += suffix;
    if (rafRef.current) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = 0;
      const add = pendingRef.current;
      pendingRef.current = "";
      if (!add) return;
      genRef.current += 1;
      commit(solidRef.current, [...chunksRef.current, { id: genRef.current, text: add }]);
    });
  }

  useLayoutEffect(() => {
    const shown = shownText(solidRef.current, chunksRef.current, pendingRef.current);
    if (text === shown) return;
    if (!shown || !text.startsWith(shown)) resetTo(text);
  }, [text]);

  useEffect(() => {
    const shown = shownText(solidRef.current, chunksRef.current, pendingRef.current);
    if (text === shown) return;
    if (!text.startsWith(shown)) return;
    enqueue(text.slice(shown.length));
  }, [text]);

  useEffect(
    () => () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  function onChunkEnd(id: number, e: AnimationEvent<HTMLSpanElement>) {
    if (e.target !== e.currentTarget) return;
    endedRef.current.add(id);
    flushEnded();
  }

  return (
    <>
      {solid ? <span className="aa-tok-solid">{solid}</span> : null}
      {chunks.map((chunk) => (
        <span key={chunk.id} className="aa-tok-in" onAnimationEnd={(e) => onChunkEnd(chunk.id, e)}>
          {chunk.text}
        </span>
      ))}
    </>
  );
}

export function StreamingMarkdown({
  content,
  streaming,
  plain,
}: {
  content: string;
  streaming?: boolean;
  plain?: boolean;
}) {
  if (!streaming) return <MarkdownRenderer content={content} plain={plain} />;

  const { frozenBlocks, live, liveIsFence } = splitStreaming(content);
  const tail = live ? <LiveTail text={live} /> : null;
  const caret = <span className="aa-caret" aria-hidden="true" />;
  const fenceKind = liveIsFence ? dataFenceKind(live) : null;

  // Group consecutive narrow frozen blocks into shared `.td-row` grids, the
  // same grouping the settled render gets from `rehypePairDataRows`. Each
  // frozen block is rendered in isolation (so a closed fence appears
  // immediately), which would otherwise hide adjacency from the row plugin
  // and stack every card one-per-row until `done`. Every narrow run — even a
  // lone card — gets its own row so the parent stays stable as siblings
  // arrive (a lone narrow block keeps its span per the `.td-row` CSS) and
  // already-mounted cards are never moved to a new parent.
  const narrowLayouts = frozenBlocks.map(narrowLayoutOf);
  const segments: Array<{ type: "single"; index: number } | { type: "row"; indices: number[] }> = [];
  for (let i = 0; i < frozenBlocks.length; i++) {
    if (!narrowLayouts[i]) {
      segments.push({ type: "single", index: i });
      continue;
    }
    let j = i;
    while (j + 1 < frozenBlocks.length && narrowLayouts[j + 1]) j++;
    segments.push({ type: "row", indices: Array.from({ length: j - i + 1 }, (_, k) => i + k) });
    i = j;
  }
  const trailingRun: number[] = [];
  for (let i = frozenBlocks.length - 1; i >= 0 && narrowLayouts[i]; i--) {
    trailingRun.unshift(i);
  }
  const lastRunLayouts = trailingRun.map((idx) => narrowLayouts[idx]);
  const uniformFallback =
    lastRunLayouts.length > 0 && lastRunLayouts.every((l) => l === lastRunLayouts[0])
      ? lastRunLayouts[0]
      : null;
  const skeletonLayout = fenceKind ? liveFenceLayout(live, uniformFallback) : null;
  // A live data fence continuing a narrow run joins that run's row so the
  // skeleton holds its column; otherwise it renders standalone.
  const skeletonJoinsRow = Boolean(
    fenceKind && skeletonLayout && uniformFallback && trailingRun.length > 0,
  );
  const bodySegments = skeletonJoinsRow ? segments.slice(0, -1) : segments;

  return (
    <div className="chat-markdown">
      {bodySegments.map((seg, si) =>
        seg.type === "single" ? (
          <div key={`b${seg.index}`} className="aa-md-block">
            <MarkdownRenderer content={frozenBlocks[seg.index]} asFragment plain={plain} />
          </div>
        ) : (
          <div key={`r${seg.indices[0]}`} className="td-row" data-testid="td-row-streaming">
            {seg.indices.map((idx) => (
              <MarkdownRenderer key={`b${idx}`} content={frozenBlocks[idx]} asFragment plain={plain} />
            ))}
          </div>
        ),
      )}
      {fenceKind ? (
        skeletonJoinsRow ? (
          <div className="td-row" data-testid="td-row-streaming">
            {trailingRun.map((idx) => (
              <MarkdownRenderer key={`b${idx}`} content={frozenBlocks[idx]} asFragment plain={plain} />
            ))}
            <DataFenceSkeleton kind={fenceKind} layout={skeletonLayout || undefined} />
          </div>
        ) : (
          <DataFenceSkeleton kind={fenceKind} layout={skeletonLayout || undefined} />
        )
      ) : liveIsFence ? (
        <pre className="aa-md-pre aa-live-fence">
          {tail}
          {caret}
        </pre>
      ) : tail ? (
        <span className="aa-live">
          {tail}
          {caret}
        </span>
      ) : (
        caret
      )}
    </div>
  );
}
