import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentDef } from "../types";

const FOCUSABLE = 'input, button, [href], textarea, select, [tabindex]:not([tabindex="-1"])';

export function Spotlight({
  agents,
  open,
  onClose,
  onSelect,
  returnFocusRef,
}: {
  agents: AgentDef[];
  open: boolean;
  onClose: () => void;
  onSelect: (agent: AgentDef) => void;
  returnFocusRef?: { readonly current: HTMLElement | null };
}) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);
  const indexRef = useRef(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => {
      const hay = `${a.name} ${a.slug} ${a.description || ""} ${a.kind}`.toLowerCase();
      return hay.includes(q);
    });
  }, [agents, query]);

  indexRef.current = index;

  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      setQuery("");
      setIndex(0);
      const t = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(t);
    }
    if (wasOpen.current) {
      wasOpen.current = false;
      const el = returnFocusRef?.current;
      window.setTimeout(() => el?.focus(), 0);
    }
  }, [open, returnFocusRef]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [index, filtered]);

  useEffect(() => {
    if (!open) return;

    function focusables(): HTMLElement[] {
      const root = dialogRef.current;
      if (!root) return [];
      return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => !el.hasAttribute("disabled") && el.tabIndex !== -1,
      );
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setIndex((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        const tag = (e.target as HTMLElement | null)?.tagName;
        if (tag === "BUTTON") return;
        e.preventDefault();
        const agent = filtered[indexRef.current];
        if (agent) onSelect(agent);
        return;
      }
      if (e.key === "Tab") {
        const list = focusables();
        if (!list.length) {
          e.preventDefault();
          return;
        }
        const first = list[0];
        const last = list[list.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (e.shiftKey && (active === first || !dialogRef.current?.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && (active === last || !dialogRef.current?.contains(active))) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, filtered, onClose, onSelect]);

  if (!open) return null;

  const activeId = filtered[index] ? `aa-sp-${filtered[index].slug}` : undefined;

  return createPortal(
    <div
      className="aa-overlay"
      data-testid="spotlight"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="aa-spotlight"
        role="dialog"
        aria-modal="true"
        aria-label="Select agent"
      >
        <input
          ref={inputRef}
          className="aa-spotlight-search"
          data-testid="spotlight-search"
          placeholder="Search agents…"
          value={query}
          role="combobox"
          aria-expanded="true"
          aria-controls="aa-spotlight-list"
          aria-activedescendant={activeId}
          autoComplete="off"
          onChange={(e) => setQuery(e.target.value)}
        />
        <div
          ref={listRef}
          id="aa-spotlight-list"
          className="aa-spotlight-list"
          role="listbox"
        >
          {filtered.length === 0 && <div className="aa-muted" style={{ padding: 16 }}>No agents match.</div>}
          {filtered.map((agent, i) => (
            <button
              key={agent.slug}
              id={`aa-sp-${agent.slug}`}
              type="button"
              role="option"
              aria-selected={i === index}
              className={`aa-spotlight-item${i === index ? " active" : ""}`}
              data-testid="spotlight-item"
              data-slug={agent.slug}
              onMouseEnter={() => setIndex(i)}
              onClick={() => onSelect(agent)}
            >
              <div className="aa-avatar">{(agent.name || "?").slice(0, 1).toUpperCase()}</div>
              <div>
                <div className="aa-sp-name-row">
                  <div className="aa-sp-name">{agent.name}</div>
                  <span className="aa-badge">{agent.kind === "workflow" ? "Team" : "Agent"}</span>
                </div>
                <div className="aa-sp-desc">{agent.description}</div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
