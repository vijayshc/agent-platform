import { useEffect, useRef, useState } from "react";
import { apiDelete } from "../api";
import type { AgentDef } from "../types";
import { cloneDefinition } from "./api";

export async function cloneAgent(agent: AgentDef, setNotice: (n: string | null) => void): Promise<AgentDef> {
  const copy = await cloneDefinition(agent.slug);
  setNotice(`Cloned as ${copy.name}.`);
  window.location.href = `/agent-studio/editor?slug=${encodeURIComponent(copy.slug)}`;
  return copy;
}

export function DeleteAgentDialog({
  agent,
  onClose,
  onDeleted,
}: {
  agent: AgentDef;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await apiDelete(`/api/v1/agents/${encodeURIComponent(agent.slug)}`);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="as-overlay"
      data-testid="agent-delete-dialog"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="as-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-label={`Delete ${agent.name}`}
        ref={ref}
        tabIndex={-1}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="as-dialog-head">
          <div className="as-dialog-title">Delete agent?</div>
          <p className="as-dialog-sub">
            {agent.name} ({agent.slug}) will be permanently deleted.
          </p>
        </div>
        <div className="as-dialog-body">
          {error ? <p className="as-error">{error}</p> : <p className="as-help">This cannot be undone.</p>}
        </div>
        <div className="as-dialog-foot as-row">
          <button type="button" className="as-btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="as-btn as-btn-danger"
            data-testid="agent-delete-confirm"
            disabled={busy}
            onClick={() => void confirm()}
          >
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
