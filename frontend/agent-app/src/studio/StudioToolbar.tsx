/** Studio top bar: identity, status and the save / validate / publish actions.
 *
 * Presentational — every action is provided by `useStudioDocument`.
 */
import { useEffect, useRef, useState } from "react";
import {
  Download,
  Eye,
  LayoutGrid,
  Maximize2,
  MoreHorizontal,
  Play,
  Redo2,
  Save,
  ShieldCheck,
  Undo2,
  Upload,
} from "lucide-react";
import type { AgentDef } from "../types";
import type { GraphMeta } from "./model/types";

/** The theme the app is currently showing (class is the source of truth). */
function currentThemeName(): string {
  const classes = document.documentElement.classList;
  if (classes.contains("theme-lightColored")) return "lightColored";
  if (classes.contains("theme-light")) return "light";
  return "dark";
}

/** Apply a theme through the app's ThemeManager so both storages stay in sync. */
function applyAppTheme(name: string): void {
  const manager = (window as unknown as { themeManager?: { applyTheme?: (theme: string) => void } }).themeManager;
  if (manager?.applyTheme) {
    manager.applyTheme(name);
    return;
  }
  const root = document.documentElement;
  [...root.classList, ...document.body.classList]
    .filter((token) => token.startsWith("theme-"))
    .forEach((token) => {
      root.classList.remove(token);
      document.body.classList.remove(token);
    });
  root.classList.add(`theme-${name}`);
  document.body.classList.add(`theme-${name}`);
  sessionStorage.setItem("selectedTheme", name);
  localStorage.setItem("selectedTheme", name);
}

export interface StudioToolbarProps {
  meta: GraphMeta;
  current: AgentDef | null;
  statusText: string;
  error: string | null;
  /** Check errors the last validate/save reported (0 when clean). */
  checks: number;
  /** Check warnings (surfaced, never blocking). */
  warnings: number;
  onOpenChecks: () => void;
  busy: boolean;
  /** The loaded slug does not exist: nothing may be written. */
  missing: boolean;
  canUndo: boolean;
  canRedo: boolean;
  dirty: boolean;
  onTitle: (name: string) => void;
  onSave: () => void;
  onValidate: () => void;
  onPublish: () => void;
  onTest: () => void;
  onUndo: () => void;
  onRedo: () => void;
  onTidy: () => void;
  onFit: () => void;
  onExport: () => void;
  onImport: (file: File) => void;
  onAccess: () => void;
}

export function StudioToolbar(props: StudioToolbarProps) {
  const {
    meta,
    current,
    statusText,
    error,
    checks,
    warnings,
    onOpenChecks,
    busy,
    missing,
    canUndo,
    canRedo,
    onTitle,
    onSave,
    onValidate,
    onPublish,
    onTest,
    onUndo,
    onRedo,
    onTidy,
    onFit,
    onExport,
    onImport,
    onAccess,
  } = props;
  const [moreOpen, setMoreOpen] = useState(false);
  const [theme, setTheme] = useState<string>(() => currentThemeName());
  const fileRef = useRef<HTMLInputElement>(null);

  // The theme is an app setting: the Studio mirrors the app control and applies
  // it through the app's own ThemeManager. It never writes storage by itself.
  useEffect(() => {
    const sync = () => setTheme(currentThemeName());
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    <header className="as-topbar">
      <div className="as-topbar-left">
        <input
          className="as-input as-title-input"
          id="studio-agent-name"
          name="agent-name"
          aria-label="Agent name"
          data-testid="studio-title"
          value={meta.name}
          title={meta.name || "Name this flow"}
          placeholder="Untitled agent"
          onChange={(event) => onTitle(event.target.value)}
        />
        <span className={`as-badge${current?.published ? " as-badge-ok" : ""}`}>
          {current?.published ? "Published" : "Draft"}
        </span>
        {current ? <span className="as-muted as-slug">{current.slug}</span> : null}
        {current?.version ? <span className="as-muted">v{current.version}</span> : null}
        {(checks > 0 || warnings > 0) && !error ? (
          <button
            type="button"
            className={`as-status ${checks > 0 ? "as-status-alert" : "as-status-warn"}`}
            title={`${statusText} — click to open Checks`}
            onClick={onOpenChecks}
            data-testid="studio-status"
          >
            {statusText}
          </button>
        ) : (
          <span
            className={error ? "as-error as-status" : "as-muted as-status"}
            title={error || statusText}
            data-testid="studio-status"
          >
            {statusText}
          </span>
        )}
      </div>

      <div className="as-topbar-right">
        <button
          type="button"
          className="as-btn as-btn-icon"
          title="Undo (⌘Z)"
          onClick={onUndo}
          disabled={!canUndo}
          data-testid="studio-undo"
        >
          <Undo2 size={15} />
        </button>
        <button
          type="button"
          className="as-btn as-btn-icon"
          title="Redo (⇧⌘Z)"
          onClick={onRedo}
          disabled={!canRedo}
          data-testid="studio-redo"
        >
          <Redo2 size={15} />
        </button>
        <button type="button" className="as-btn as-btn-icon" title="Tidy up the canvas" onClick={onTidy} data-testid="studio-layout">
          <LayoutGrid size={15} />
        </button>
        <button type="button" className="as-btn as-btn-icon" title="Fit to view" onClick={onFit}>
          <Maximize2 size={15} />
        </button>
        <span className="as-divider" />
        <button type="button" className="as-btn" onClick={onValidate} disabled={busy} data-testid="studio-validate">
          <ShieldCheck size={14} /> Validate
        </button>
        <button
          type="button"
          className="as-btn as-btn-primary"
          onClick={onSave}
          disabled={busy || missing}
          data-testid="studio-save"
        >
          <Save size={14} /> Save
        </button>
        <button type="button" className="as-btn" onClick={onPublish} disabled={busy || missing} data-testid="studio-publish">
          <Upload size={14} /> Publish
        </button>
        <button type="button" className="as-btn" onClick={onTest} disabled={missing} data-testid="studio-testrun">
          <Play size={14} /> Test run
        </button>
        <div className="as-overflow">
          <button
            type="button"
            className="as-btn as-btn-icon"
            title="More actions"
            aria-expanded={moreOpen}
            onMouseDown={(event) => {
              event.stopPropagation();
              setMoreOpen((open) => !open);
            }}
            data-testid="studio-more"
          >
            <MoreHorizontal size={15} />
          </button>
          {moreOpen ? (
            <div className="as-menu as-menu-right" role="menu">
              <button
                type="button"
                className="as-menu-item"
                onClick={() => {
                  onExport();
                  setMoreOpen(false);
                }}
                data-testid="studio-export"
              >
                <Download size={13} /> Export JSON
              </button>
              <button
                type="button"
                className="as-menu-item"
                onClick={() => {
                  fileRef.current?.click();
                  setMoreOpen(false);
                }}
              >
                <Upload size={13} /> Import JSON
              </button>
              <button
                type="button"
                className="as-menu-item"
                onClick={() => {
                  onAccess();
                  setMoreOpen(false);
                }}
                data-testid="studio-access-menu"
              >
                <Eye size={13} /> Access…
              </button>
              <div className="as-menu-sep" role="separator" />
              <div className="as-menu-label">Theme</div>
              {["light", "dark", "lightColored"].map((name) => (
                <button
                  type="button"
                  key={name}
                  className="as-menu-item"
                  aria-checked={theme === name}
                  role="menuitemradio"
                  data-testid={`studio-theme-${name}`}
                  onClick={() => {
                    applyAppTheme(name);
                    setTheme(name);
                  }}
                >
                  <span className="as-menu-check">{theme === name ? "✓" : ""}</span>
                  {name === "lightColored" ? "Light (colored)" : name === "light" ? "Light" : "Dark"}
                </button>
              ))}
              <button
                type="button"
                className="as-menu-item"
                onClick={() => {
                  window.open("/observability", "_blank");
                  setMoreOpen(false);
                }}
              >
                <Eye size={13} /> Observability
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept="application/json"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onImport(file);
          event.target.value = "";
        }}
      />
    </header>
  );
}
