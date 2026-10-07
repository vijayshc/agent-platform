import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, CheckCircle2, Info, Lightbulb, Megaphone } from "lucide-react";
import { CodeBlock } from "./CodeBlock";
import type { NoteSpec } from "./toolDataTypes";

const STYLE_ICON = {
  title: Megaphone,
  insight: Lightbulb,
  info: Info,
  warning: AlertTriangle,
  success: CheckCircle2,
} as const;

/** A free-text dashboard box: section header, takeaway, or alert. */
export function ToolNote({ spec }: { spec: NoteSpec }) {
  const style = spec.style || "info";
  const Icon = STYLE_ICON[style] ?? Info;
  if (style === "title") {
    return (
      <div className="td-note-title" data-layout={spec.layout || "full"} data-testid="note-title">
        <div className="td-note-title-text">{spec.title || "Section"}</div>
        {spec.body ? (
          <div className="td-note-title-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{spec.body}</ReactMarkdown>
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <figure
      className={`td-card td-note td-note-${style}`}
      data-layout={spec.layout || "full"}
      data-testid={`note-${style}`}
    >
      <div className="td-note-body">
        <span className="td-note-icon" aria-hidden="true">
          <Icon size={15} strokeWidth={2} />
        </span>
        <div className="td-note-main">
          {spec.title ? <span className="td-note-heading">{spec.title}</span> : null}
          {spec.body ? (
            <div className="td-note-text">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  pre: ({ children }) => <>{children}</>,
                  code: ({ className, children, ...props }) => {
                    const match = /language-(\w+)/.exec(className || "");
                    if (!match && !String(children || "").includes("\n")) {
                      return (
                        <code className="chat-inline-code" {...props}>
                          {children}
                        </code>
                      );
                    }
                    return <CodeBlock className={className}>{children}</CodeBlock>;
                  },
                  a: ({ href, children }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {spec.body}
              </ReactMarkdown>
            </div>
          ) : null}
        </div>
      </div>
    </figure>
  );
}
