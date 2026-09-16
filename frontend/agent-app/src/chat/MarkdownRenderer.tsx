import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./CodeBlock";
import { ChatDataTable } from "./ChatDataTable";
import "./chatMarkdown.css";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  asFragment?: boolean;
}

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className = "",
  asFragment,
}: MarkdownRendererProps) {
  if (!content) return null;

  const md = (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        pre: ({ children }) => <>{children}</>,
        table: ({ children }) => <ChatDataTable>{children}</ChatDataTable>,
        code: ({ className: codeClassName, children, ...props }) => {
          const match = /language-(\w+)/.exec(codeClassName || "");
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
