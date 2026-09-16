import { cloneElement, isValidElement, type ReactNode } from "react";

/* ------------------------------------------------------------------ *
 * Shared admin API helpers (JSON only; legacy HTML POSTs are avoided)
 * ------------------------------------------------------------------ */

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body.error || body.message || res.statusText;
  } catch {
    return res.statusText;
  }
}

function getCsrfToken(): string | null {
  const el = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
  return el ? el.getAttribute("content") : null;
}

export async function adminGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", signal });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function adminPostJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() || "" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function adminPutJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() || "" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function adminDelete<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", credentials: "same-origin", headers: { "X-CSRF-Token": getCsrfToken() || "" } });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function adminPostForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(path, { method: "POST", credentials: "same-origin", body: form });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ *
 * Shared UI primitives
 * ------------------------------------------------------------------ */

export function AdminLoading({ label = "Loading…" }: { label?: string }) {
  return <div className="aa-admin-state">{label}</div>;
}

export function AdminEmpty({ message }: { message: string }) {
  return <div className="aa-admin-state aa-admin-empty">{message}</div>;
}

export function AdminError({ message }: { message: string }) {
  return <div className="aa-admin-state aa-admin-error">{message}</div>;
}

export function AdminStat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className={`aa-admin-stat${tone ? ` ${tone}` : ""}`}>
      <div className="aa-admin-stat-label">{label}</div>
      <div className="aa-admin-stat-value">{value}</div>
    </div>
  );
}

interface ModalProps {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}

export function AdminModal({ title, open, onClose, children, footer, wide }: ModalProps) {
  if (!open) return null;
  return (
    <div className="aa-overlay aa-admin-modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`aa-admin-modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="aa-admin-modal-head">
          <h3>{title}</h3>
          <button type="button" className="aa-admin-modal-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="aa-admin-modal-body">{children}</div>
        {footer && <div className="aa-admin-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function AdminField({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const id = `aa-admin-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  // Only a single form control can own the label's `for`; composite children
  // (repeaters, code editors) must not point it at a non-existent element.
  const controlId = isFormControl(children) ? id : undefined;
  return (
    <div className="aa-admin-field">
      {controlId ? (
        <label className="aa-admin-field-label" htmlFor={controlId}>
          {label}
        </label>
      ) : (
        <span className="aa-admin-field-label">{label}</span>
      )}
      {typeof children === "string" ? (
        <span>{children}</span>
      ) : (
        <CloneWithId id={id}>{children}</CloneWithId>
      )}
      {hint && <span className="aa-admin-field-hint">{hint}</span>}
    </div>
  );
}

function isFormControl(children: ReactNode): boolean {
  return (
    isValidElement(children) &&
    (children.type === "input" || children.type === "select" || children.type === "textarea")
  );
}

/** Clones the first input/select/textarea child and injects id + name for a11y. */
function CloneWithId({ id, children }: { id: string; children: ReactNode }) {
  if (!isValidElement(children)) return <>{children}</>;
  if (children.type !== "input" && children.type !== "select" && children.type !== "textarea") {
    return <>{children}</>;
  }
  return cloneElement(children as React.ReactElement<{ id?: string; name?: string }>, { id, name: id });
}

export function AdminTags({ items }: { items: string[] }) {
  if (!items || items.length === 0) return <span className="aa-muted">—</span>;
  return (
    <div className="aa-chips">
      {items.map((t, i) => (
        <span className="aa-chip" key={`${t}-${i}`}>
          {t}
        </span>
      ))}
    </div>
  );
}

export function AdminStatusPill({ status }: { status: string }) {
  return <span className={`aa-status-pill ${status}`}>{status}</span>;
}

/** Page-level header with title on the left and action buttons on the right. */
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="aa-page-header">
      <div className="aa-page-header-copy">
        <h2 className="aa-page-header-title">{title}</h2>
        {subtitle && <div className="aa-page-header-sub">{subtitle}</div>}
      </div>
      {actions && <div className="aa-page-header-actions">{actions}</div>}
    </div>
  );
}
