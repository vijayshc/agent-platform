import { memo, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./CodeBlock";
import { ChatDataTable } from "./ChatDataTable";
import { ToolDataBlock } from "./ToolDataBlock";
import { parseDataBlock } from "./dataBlock";
import { rehypePairDataRows } from "./dataRowPlugin";
import "./chatMarkdown.css";

interface MarkdownCoreProps {
  content: string;
  className?: string;
  asFragment?: boolean;
  /** Render `#CHART_<ref>` / `#TABLE_<ref>` blocks against the turn's cached
   *  tool data. Off for text the server never validated, so a raw spec cannot
   *  be turned into a chart. */
  rich?: boolean;
}

/** The plain text of a rendered inline fragment (a placeholder is plain text). */
function childrenText(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(childrenText).join("");
  return "";
}

/**
 * Markdown rendering. react-markdown owns the document structure; the only
 * custom pieces are the two the chat adds on top of it: markdown tables become
 * interactive DataTables, and a `#CHART_`/`#TABLE_` placeholder becomes a chart
 * or table card.
 */
export const MarkdownCore = memo(function MarkdownCore({
  content,
  className = "",
  asFragment,
  rich = false,
}: MarkdownCoreProps) {
  if (!content) return null;

  const md = (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={rich ? [rehypePairDataRows] : []}
      components={{
        pre: ({ children }) => <>{children}</>,
        table: ({ children }) => <ChatDataTable>{children}</ChatDataTable>,
        // A bare placeholder on its own line arrives as a paragraph.
        p: ({ children }) => {
          const block = rich ? parseDataBlock(childrenText(children)) : null;
          if (block) return <ToolDataBlock block={block} />;
          return <p>{children}</p>;
        },
        code: ({ className: codeClassName, children, ...props }) => {
          const match = /language-(\w+)/.exec(codeClassName || "");
          const language = match?.[1]?.toLowerCase();
          // A fenced ```chart / ```table / dashboard body arrives as a code element.
          if (
            rich &&
            (language === "chart" ||
              language === "table" ||
              language === "card" ||
              language === "list" ||
              language === "progress" ||
              language === "note")
          ) {
            const block = parseDataBlock(String(children ?? ""));
            if (block) return <ToolDataBlock block={block} />;
          }
          const isMultiLine = String(children || "").includes("\n");
          if (!match && !isMultiLine) {
            return (
              <code className="chat-inline-code" {...props}>
                {children}
              </code>
            );
          }
          return <CodeBlock className={codeClassName}>{children}</CodeBlock>;
        },
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );

  if (asFragment) return md;
  return <div className={`chat-markdown ${className}`}>{md}</div>;
});
