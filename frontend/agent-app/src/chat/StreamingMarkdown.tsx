import { useEffect, useLayoutEffect, useRef, useState, type AnimationEvent } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";

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

export function StreamingMarkdown({ content, streaming }: { content: string; streaming?: boolean }) {
  if (!streaming) return <MarkdownRenderer content={content} />;

  const { frozenBlocks, live, liveIsFence } = splitStreaming(content);
  const tail = live ? <LiveTail text={live} /> : null;
  const caret = <span className="aa-caret" aria-hidden="true" />;

  return (
    <div className="chat-markdown">
      {frozenBlocks.map((block, i) => (
        <div key={i} className="aa-md-block">
          <MarkdownRenderer content={block} asFragment />
        </div>
      ))}
      {liveIsFence ? (
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
