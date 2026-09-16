import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiGet } from "../api";
import type { LlmModel } from "../types";

const STORAGE_KEY = "aa_chat_model_id";

function readStoredModelId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredModelId(id: string) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* ignore */
  }
}

export function pickModelId(models: LlmModel[], preferred?: string | null): string | null {
  if (preferred && models.some((m) => m.id === preferred)) return preferred;
  const stored = readStoredModelId();
  if (stored && models.some((m) => m.id === stored)) return stored;
  const def = models.find((m) => m.is_default);
  return def?.id || models[0]?.id || null;
}

export function useChatModels() {
  const [models, setModels] = useState<LlmModel[]>([]);
  const [modelId, setModelIdState] = useState<string | null>(() => readStoredModelId());
  const [open, setOpen] = useState(false);

  useEffect(() => {
    apiGet<{ models: LlmModel[] }>("/api/v1/models")
      .then((r) => {
        const list = r.models || [];
        setModels(list);
        setModelIdState((prev) => pickModelId(list, prev));
      })
      .catch(() => setModels([]));
  }, []);

  function setModelId(id: string) {
    setModelIdState(id);
    writeStoredModelId(id);
    setOpen(false);
  }

  const selected = models.find((m) => m.id === modelId) || null;
  return { models, modelId, setModelId, selected, open, setOpen };
}

export function ModelPicker({
  models,
  modelId,
  open,
  onClose,
  onSelect,
}: {
  models: LlmModel[];
  modelId: string | null;
  open: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [index, setIndex] = useState(0);
  const indexRef = useRef(0);

  useEffect(() => {
    if (!open) return;
    const selectedIndex = Math.max(0, models.findIndex((m) => m.id === modelId));
    setIndex(selectedIndex);
    const t = window.setTimeout(() => {
      listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.focus();
    }, 0);
    return () => window.clearTimeout(t);
  }, [open, modelId, models]);

  indexRef.current = index;

  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [index]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setIndex((i) => Math.min(i + 1, Math.max(models.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setIndex((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const model = models[indexRef.current];
        if (model) onSelect(model.id);
      }
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, models, onClose, onSelect]);

  if (!open) return null;

  return createPortal(
    <div
      className="aa-overlay"
      data-testid="model-menu"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="aa-spotlight aa-model-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Select model"
      >
        <div className="aa-model-picker-title">Select a model</div>
        <div ref={listRef} className="aa-spotlight-list" role="listbox">
          {models.length === 0 && (
            <div className="aa-muted aa-model-picker-empty">
              No models configured. Add a connection in LLM Manager.
            </div>
          )}
          {models.map((model, i) => (
            <button
              key={model.id}
              type="button"
              role="option"
              aria-selected={i === index}
              className={`aa-spotlight-item${i === index ? " active" : ""}`}
              data-testid="model-item"
              data-model-id={model.id}
              onMouseEnter={() => setIndex(i)}
              onClick={() => onSelect(model.id)}
            >
              <div>
                <div className="aa-sp-name-row">
                  <div className="aa-sp-name">{model.name}</div>
                  {model.is_default ? <span className="aa-badge">Default</span> : null}
                  {model.id === modelId ? <span className="aa-badge">Selected</span> : null}
                </div>
                {model.model_name ? <div className="aa-sp-desc">{model.model_name}</div> : null}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
