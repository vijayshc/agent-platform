import { memo } from "react";
import { MarkdownCore } from "./MarkdownCore";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  asFragment?: boolean;
  /** Never interpret chart/table placeholders. Used for text the server has not
   *  validated (the superseded pre-tool stream), so a raw spec cannot be turned
   *  into a chart. */
  plain?: boolean;
}

/** The chat's markdown entry point. */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = "",
  asFragment,
  plain,
}: MarkdownRendererProps) {
  if (!content) return null;
  return (
    <MarkdownCore content={content} className={className} asFragment={asFragment} rich={!plain} />
  );
});
