import { useMemo, type ReactNode } from "react";
import { splitRichContent } from "./chartProtocol";
import { MarkdownCore } from "./MarkdownCore";
import { ToolDataBlock } from "./ToolDataBlock";
import { blockLayout, type RichBlock, type RichSegment } from "./toolDataTypes";
import "./toolData.css";

/**
 * Renders a reply that mixes markdown with `#CHART_<id>` / `#TABLE_<id>`
 * placeholders. Consecutive `half` blocks pair into a two-column row; anything
 * else is full width.
 */
export function RichContent({
  content,
  className = "",
  asFragment,
}: {
  content: string;
  className?: string;
  asFragment?: boolean;
}) {
  const segments = useMemo(() => splitRichContent(content), [content]);
  const nodes = useMemo(() => renderSegments(segments), [segments]);
  if (asFragment) return <>{nodes}</>;
  return <div className={`chat-markdown ${className}`}>{nodes}</div>;
}

function renderSegments(segments: RichSegment[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  let index = 0;
  while (index < segments.length) {
    const segment = segments[index];
    if (segment.kind === "markdown") {
      nodes.push(<MarkdownCore key={`md-${index}`} content={segment.text} asFragment />);
      index += 1;
      continue;
    }
    const run: RichBlock[] = [];
    while (index < segments.length && segments[index].kind !== "markdown") {
      run.push(segments[index] as RichBlock);
      index += 1;
    }
    nodes.push(...renderRun(run));
  }
  return nodes;
}

function renderRun(run: RichBlock[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  let index = 0;
  while (index < run.length) {
    const block = run[index];
    const next = run[index + 1];
    if (blockLayout(block) === "half" && next && blockLayout(next) === "half") {
      nodes.push(
        <div className="td-row" key={`half-${index}-${block.callId}`}>
          <ToolDataBlock block={block} />
          <ToolDataBlock block={next} />
        </div>,
      );
      index += 2;
      continue;
    }
    nodes.push(<ToolDataBlock key={`block-${index}-${block.callId}`} block={block} />);
    index += 1;
  }
  return nodes;
}
