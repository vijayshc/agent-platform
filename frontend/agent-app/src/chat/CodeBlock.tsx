import { useCallback, useMemo, useState } from "react";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("js", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("ts", typescript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("py", python);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("json", json);
hljs.registerLanguage("bash", bash);
hljs.registerLanguage("sh", bash);
hljs.registerLanguage("html", xml);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("css", css);
hljs.registerLanguage("yaml", yaml);
hljs.registerLanguage("yml", yaml);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("md", markdown);

interface CodeBlockProps {
  children?: React.ReactNode;
  className?: string;
}

export function CodeBlock({ children, className = "" }: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const rawCode = String(children || "").replace(/\n$/, "");

  const langMatch = /language-(\w+)/.exec(className || "");
  const lang = (langMatch ? langMatch[1] : "").toLowerCase();

  const highlightedHtml = useMemo(() => {
    if (!rawCode) return "";
    try {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(rawCode, { language: lang }).value;
      }
      const auto = hljs.highlightAuto(rawCode, [
        "python",
        "sql",
        "javascript",
        "typescript",
        "json",
        "bash",
      ]);
      return auto.value;
    } catch {
      return rawCode
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }
  }, [rawCode, lang]);

  const onCopy = useCallback(() => {
    navigator.clipboard.writeText(rawCode).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [rawCode]);

  return (
    <div className="chat-code-card">
      <div className="chat-code-header">
        <span className="chat-code-lang">{lang || "code"}</span>
        <button
          type="button"
          className={`chat-copy-btn${copied ? " copied" : ""}`}
          onClick={onCopy}
          title="Copy code to clipboard"
          aria-live="polite"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="chat-code-pre">
        <code
          className={`hljs ${className}`}
          dangerouslySetInnerHTML={{ __html: highlightedHtml }}
        />
      </pre>
    </div>
  );
}
