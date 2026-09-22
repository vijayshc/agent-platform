import { memo } from "react";
import { hasRichBlocks } from "./chartProtocol";
import { MarkdownCore } from "./MarkdownCore";
import { RichContent } from "./RichContent";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  asFragment?: boolean;
  /** Never interpret chart/table placeholders. Used for text the server has not
   *  validated (the superseded pre-tool stream), so a raw spec cannot be turned
   *  into a chart. */
  plain?: boolean;
}

/**
 * The chat's markdown entry point. Content that carries chart/table
 * placeholders is routed through :func:`RichContent`; everything else takes the
 * fast plain-markdown path.
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = "",
  asFragment,
  plain,
}: MarkdownRendererProps) {
  if (!content) return null;
  if (!plain && hasRichBlocks(content)) {
    return <RichContent content={content} className={className} asFragment={asFragment} />;
  }
  return <MarkdownCore content={content} className={className} asFragment={asFragment} />;
});
