/** Generic inspector primitives + the catalog `Field` renderer.
 *
 * Every configurable capability (middleware, node kinds) declares its own
 * `Field[]` in the backend registry; `FieldRenderer` turns one of those into the
 * right control without knowing which capability it belongs to.
 */
import { cloneElement, isValidElement, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { ChevronDown, Plus, Trash2 } from "lucide-react";
import { CodeEditor } from "../../shared/CodeEditor";
import type { CatalogField, RouterRoute } from "../model/types";

export interface SelectOption {
  value: string;
  label: string;
}

let fieldSeq = 0;

/** Give every control a stable id + name so labels bind and autofill works. */
export function Field({
  label,
  help,
  children,
  htmlFor,
}: {
  label: string;
  help?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  const [id] = useState(() => {
    fieldSeq += 1;
    return `as-field-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "field"}-${fieldSeq}`;
  });
  const bound =
    htmlFor ||
    id;
  const child = isValidElement(children)
    ? cloneElement(children as ReactElement<{ id?: string; name?: string }>, {
        id: bound,
        name: (children as ReactElement<{ name?: string }>).props.name ?? bound,
      })
    : children;
  return (
    <div className="as-field">
      <label className="as-label" htmlFor={bound}>
        {label}
      </label>
      {child}
      {help ? <p className="as-help">{help}</p> : null}
    </div>
  );
}

export function Section({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="as-section">
      <header className="as-section-head">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p className="as-section-sub">{subtitle}</p> : null}
        </div>
        {actions ? <div className="as-section-actions">{actions}</div> : null}
      </header>
      <div className="as-section-body">{children}</div>
    </section>
  );
}

export function TextInput({
  value,
  onChange,
  placeholder,
  testId,
  type = "text",
  min,
  max,
  step,
  id,
  name,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
  type?: "text" | "number";
  min?: number;
  max?: number;
  step?: number;
  id?: string;
  name?: string;
}) {
  return (
    <input
      className="as-input"
      id={id}
      name={name}
      type={type}
      value={value}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      data-testid={testId}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function NumberInput({
  value,
  onChange,
  placeholder,
  testId,
  min,
  max,
  step,
  id,
  name,
}: {
  value: number | undefined;
  onChange: (value: number | undefined) => void;
  placeholder?: string;
  testId?: string;
  min?: number;
  max?: number;
  step?: number;
  id?: string;
  name?: string;
}) {
  return (
    <input
      className="as-input"
      id={id}
      name={name}
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      data-testid={testId}
      onChange={(event) => {
        const raw = event.target.value;
        onChange(raw === "" ? undefined : Number(raw));
      }}
    />
  );
}

export function Select({
  value,
  options,
  onChange,
  testId,
  placeholder,
  id,
  name,
}: {
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  testId?: string;
  placeholder?: string;
  id?: string;
  name?: string;
}) {
  return (
    <select
      className="as-select"
      id={id}
      name={name}
      value={value}
      data-testid={testId}
      onChange={(event) => onChange(event.target.value)}
    >
      {placeholder ? <option value="">{placeholder}</option> : null}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  testId,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  testId?: string;
}) {
  return (
    <label className="as-toggle">
      <input
        type="checkbox"
        checked={checked}
        data-testid={testId}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

export function TagsInput({
  value,
  onChange,
  placeholder,
  testId,
  id,
  name,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  testId?: string;
  id?: string;
  name?: string;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const next = draft.trim();
    if (!next) return;
    if (!value.includes(next)) onChange([...value, next]);
    setDraft("");
  }

  return (
    <div className="as-tags">
      {value.map((tag) => (
        <span className="as-chip as-chip-removable" key={tag}>
          {tag}
          <button type="button" aria-label={`Remove ${tag}`} onClick={() => onChange(value.filter((v) => v !== tag))}>
            ×
          </button>
        </span>
      ))}
      <input
        className="as-tags-input"
        id={id}
        name={name}
        value={draft}
        placeholder={placeholder ?? "Type a value, then press Enter"}
        title="Type a value, then press Enter (or click away) to add it"
        data-testid={testId}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            commit();
            return;
          }
          if (event.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
      />
    </div>
  );
}

export function KeyValueInput({
  value,
  onChange,
  keyPlaceholder = "key",
  valuePlaceholder = "value",
}: {
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const rows = Object.entries(value ?? {});
  function rename(from: string, to: string) {
    const next: Record<string, unknown> = {};
    for (const [key, val] of rows) next[key === from ? to : key] = val;
    onChange(next);
  }
  return (
    <div className="as-kv">
      {rows.map(([key, val]) => (
        <div className="as-kv-row" key={key}>
          <input className="as-input" value={key} placeholder={keyPlaceholder} onChange={(e) => rename(key, e.target.value)} />
          <input
            className="as-input"
            value={String(val ?? "")}
            placeholder={valuePlaceholder}
            onChange={(e) => onChange({ ...value, [key]: e.target.value })}
          />
          <button
            type="button"
            className="as-btn as-btn-icon as-btn-ghost"
            title="Remove"
            onClick={() => {
              const next = { ...value };
              delete next[key];
              onChange(next);
            }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      ))}
      <button type="button" className="as-btn as-btn-ghost as-btn-sm" onClick={() => onChange({ ...value, [`key${rows.length + 1}`]: "" })}>
        <Plus size={13} /> Add entry
      </button>
    </div>
  );
}

export function JsonInput({
  value,
  onChange,
  height = 200,
  testId,
  filename = "value.json",
  id,
  name,
}: {
  value: unknown;
  onChange: (next: unknown) => void;
  height?: number;
  testId?: string;
  filename?: string;
  id?: string;
  name?: string;
}) {
  const text = useMemo(() => JSON.stringify(value ?? {}, null, 2), [value]);
  return (
    <CodeEditor
      value={text}
      language="json"
      filename={filename}
      height={height}
      testId={testId}
      id={id}
      name={name}
      onChange={(next) => {
        try {
          onChange(JSON.parse(next));
        } catch {
          // Keep the last valid document; the editor shows the syntax error itself.
        }
      }}
    />
  );
}

export function RoutesEditor({
  routes,
  targets,
  onChange,
  defaultRoute,
  onDefaultChange,
}: {
  routes: RouterRoute[];
  targets: SelectOption[];
  onChange: (next: RouterRoute[]) => void;
  defaultRoute: string;
  onDefaultChange: (next: string) => void;
}) {
  function patch(index: number, next: Partial<RouterRoute>) {
    onChange(routes.map((route, i) => (i === index ? { ...route, ...next } : route)));
  }
  return (
    <div className="as-routes">
      <div className="as-route-row as-route-head">
        <span>Route name</span>
        <span>When it applies</span>
        <span>Goes to</span>
        <span />
      </div>
      {routes.map((route, index) => (
        <div className="as-route-row" key={`${route.name}-${index}`}>
          <input
            className="as-input"
            value={route.name}
            placeholder="billing"
            onChange={(event) => patch(index, { name: event.target.value })}
          />
          <input
            className="as-input"
            value={route.description}
            placeholder="billing questions"
            onChange={(event) => patch(index, { description: event.target.value })}
          />
          <select className="as-select" value={route.to} onChange={(event) => patch(index, { to: event.target.value })}>
            <option value="">Not wired</option>
            {targets.map((target) => (
              <option key={target.value} value={target.value}>
                {target.label}
              </option>
            ))}
            <option value="__end__">End the run</option>
          </select>
          <button
            type="button"
            className="as-btn as-btn-icon as-btn-ghost"
            title="Remove route"
            onClick={() => onChange(routes.filter((_, i) => i !== index))}
          >
            <Trash2 size={13} />
          </button>
        </div>
      ))}
      {!routes.length ? <p className="as-help">No routes yet — a router needs at least one.</p> : null}
      <div className="as-route-actions">
        <button
          type="button"
          className="as-btn as-btn-ghost as-btn-sm"
          onClick={() => onChange([...routes, { name: "", description: "", to: "" }])}
        >
          <Plus size={13} /> Add route
        </button>
        <label className="as-inline-field">
          <span>Fallback</span>
          <select className="as-select" value={defaultRoute} onChange={(event) => onDefaultChange(event.target.value)}>
            <option value="">First route</option>
            {routes
              .filter((route) => route.name)
              .map((route) => (
                <option key={route.name} value={route.to || route.name}>
                  {route.name}
                </option>
              ))}
          </select>
        </label>
      </div>
    </div>
  );
}

/** One registry `Field` → one control. */
export function FieldRenderer({
  field,
  value,
  onChange,
  options,
  targets,
  routesDefault,
  onDefaultChange,
}: {
  field: CatalogField;
  value: unknown;
  onChange: (next: unknown) => void;
  options?: SelectOption[];
  targets?: SelectOption[];
  routesDefault?: string;
  onDefaultChange?: (next: string) => void;
}) {
  const help = field.help;
  switch (field.type) {
    case "boolean":
      return (
        <Field label={field.label} help={help}>
          <Toggle label={field.label} checked={Boolean(value)} onChange={onChange} />
        </Field>
      );
    case "number":
      return (
        <Field label={field.label} help={help}>
          <NumberInput
            value={typeof value === "number" ? value : undefined}
            onChange={(next) => onChange(next)}
          />
        </Field>
      );
    case "select":
      return (
        <Field label={field.label} help={help}>
          <Select
            value={String(value ?? "")}
            options={
              options ??
              (field.options ?? []).map((option) => ({ value: String(option), label: String(option) }))
            }
            onChange={onChange}
          />
        </Field>
      );
    case "tags":
      return (
        <Field label={field.label} help={help}>
          <TagsInput value={Array.isArray(value) ? (value as string[]) : []} onChange={onChange} />
        </Field>
      );
    case "keyvalue":
      return (
        <Field label={field.label} help={help}>
          <KeyValueInput value={(value as Record<string, unknown>) ?? {}} onChange={onChange} />
        </Field>
      );
    case "json":
      return (
        <Field label={field.label} help={help}>
          <JsonInput value={value ?? {}} onChange={onChange} height={170} />
        </Field>
      );
    case "routes":
      return (
        <Field label={field.label} help={help}>
          <RoutesEditor
            routes={Array.isArray(value) ? (value as RouterRoute[]) : []}
            targets={targets ?? []}
            onChange={onChange}
            defaultRoute={routesDefault ?? ""}
            onDefaultChange={onDefaultChange ?? (() => undefined)}
          />
        </Field>
      );
    case "textarea":
      return (
        <Field label={field.label} help={help}>
          <textarea
            className="as-textarea"
            rows={3}
            value={String(value ?? "")}
            onChange={(event) => onChange(event.target.value)}
          />
        </Field>
      );
    default:
      return (
        <Field label={field.label} help={help}>
          <TextInput value={String(value ?? "")} onChange={onChange} />
        </Field>
      );
  }
}

/** Collapsible block used for long middleware/advanced groups. */
export function Collapsible({
  title,
  subtitle,
  children,
  defaultOpen = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="as-collapsible" open={defaultOpen}>
      <summary>
        <ChevronDown size={13} className="as-collapsible-caret" />
        <span>{title}</span>
        {subtitle ? <span className="as-muted">{subtitle}</span> : null}
      </summary>
      <div className="as-collapsible-body">{children}</div>
    </details>
  );
}
