/** Guardrails: install/remove OOTB LangChain middleware, each with its fields.
 *
 * The add list and every field come from the registry entry, and each installed
 * card shows the real `langchain.agents.middleware.*` class it compiles to.
 */
import { useMemo, useState } from "react";
import { Plus, Search, Trash2 } from "lucide-react";
import { catalogIcon, middlewareDefaults, middlewareEntries } from "../model/catalog";
import type { NodeData, StudioCatalog } from "../model/types";
import { FieldRenderer, Section } from "./Fields";

export function MiddlewareForm({
  data,
  catalog,
  onChange,
}: {
  data: NodeData;
  catalog: StudioCatalog | null;
  onChange: (patch: Partial<NodeData>) => void;
}) {
  const entries = middlewareEntries(catalog);
  const installed = data.middleware ?? {};
  const [picking, setPicking] = useState(false);
  const [query, setQuery] = useState("");

  const available = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return entries
      .filter((entry) => !(entry.id in installed))
      .filter((entry) =>
        needle ? `${entry.label} ${entry.summary ?? ""} ${entry.id}`.toLowerCase().includes(needle) : true,
      );
  }, [entries, installed, query]);

  function install(id: string) {
    const entry = entries.find((e) => e.id === id);
    if (!entry) return;
    onChange({ middleware: { ...installed, [id]: middlewareDefaults(entry) } });
    setPicking(false);
    setQuery("");
  }

  function remove(id: string) {
    const next = { ...installed };
    delete next[id];
    onChange({ middleware: next });
  }

  function patch(id: string, fieldName: string, value: unknown) {
    onChange({ middleware: { ...installed, [id]: { ...(installed[id] ?? {}), [fieldName]: value } } });
  }

  return (
    <Section
      title="Guardrails"
      subtitle="Shipped LangChain middleware, one instance per entry."
      actions={
        <button type="button" className="as-btn as-btn-sm" onClick={() => setPicking((v) => !v)} data-testid="middleware-add">
          <Plus size={13} /> Add
        </button>
      }
    >
      {picking ? (
        <div className="as-picker">
          <div className="as-picker-search">
            <Search size={13} />
            <input
              autoFocus
              className="as-input"
              placeholder="Search guardrails"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="as-picker-list">
            {available.map((entry) => {
              const Icon = catalogIcon(entry.icon);
              return (
                <button
                  type="button"
                  className="as-picker-item"
                  key={entry.id}
                  onClick={() => install(entry.id)}
                  data-testid={`middleware-option-${entry.id}`}
                  title={entry.summary}
                >
                  <Icon size={14} />
                  <span className="as-picker-text">
                    <span className="as-picker-label">{entry.label}</span>
                    <span className="as-picker-hint">{entry.summary}</span>
                  </span>
                </button>
              );
            })}
            {!available.length ? <p className="as-muted">Every guardrail is installed.</p> : null}
          </div>
        </div>
      ) : null}

      {!Object.keys(installed).length ? (
        <p className="as-empty" data-testid="middleware-empty">
          No guardrails. Add summarization, retries, limits, PII rules or a human approval gate.
        </p>
      ) : null}

      {Object.entries(installed).map(([id, config]) => {
        const entry = entries.find((e) => e.id === id);
        if (!entry) {
          return (
            <div className="as-mw-card" key={id}>
              <div className="as-mw-head">
                <span className="as-mw-title">{id}</span>
                <button type="button" className="as-btn as-btn-icon as-btn-ghost" title="Remove" onClick={() => remove(id)}>
                  <Trash2 size={13} />
                </button>
              </div>
              <p className="as-error">Unknown middleware id “{id}” — it is not in the capability registry.</p>
            </div>
          );
        }
        const Icon = catalogIcon(entry.icon);
        const builder = entry.builder || entry.class;
        return (
          <div className="as-mw-card" key={id} data-testid={`middleware-${id}`}>
            <div className="as-mw-head">
              <span className="as-mw-icon">
                <Icon size={14} />
              </span>
              <span className="as-mw-title">{entry.label}</span>
              <code className="as-mw-class" title={builder}>
                {builder.split(".").pop()}
              </code>
              <button
                type="button"
                className="as-btn as-btn-icon as-btn-ghost"
                title="Remove guardrail"
                onClick={() => remove(id)}
                data-testid={`middleware-remove-${id}`}
              >
                <Trash2 size={13} />
              </button>
            </div>
            {entry.fields.length ? (
              <div className="as-mw-fields">
                {entry.fields.map((field) => (
                  <FieldRenderer
                    key={field.name}
                    field={field}
                    value={(config ?? {})[field.name]}
                    onChange={(value) => patch(id, field.name, value)}
                  />
                ))}
              </div>
            ) : (
              <p className="as-muted as-mw-noopts">No options — it runs with library defaults.</p>
            )}
          </div>
        );
      })}
    </Section>
  );
}
