/**
 * Lazy loader + theme bridge for the vendored Monaco build.
 *
 * Monaco is ~16 MB on disk, so it is *never* part of the initial bundle: the
 * AMD loader is injected the first time a `CodeEditor` mounts, and every later
 * mount reuses the same promise. The library is served from
 * `/static/vendor/monaco-editor/<version>/min/vs`, which the AMD build resolves
 * on its own -- including the worker URLs, which it derives from
 * `require.toUrl("../assets/<lang>.worker-*.js")`.
 *
 * Theming: the app switches themes by toggling `theme-*` classes on
 * `<html>`/`<body>`. We mirror that into two Monaco themes whose colours are
 * sampled from the live CSS custom properties, so the editor always matches the
 * surrounding design system instead of hardcoding palettes.
 */

export interface MonacoDisposable {
  dispose(): void;
}

export interface MonacoEditorInstance {
  getValue(): string;
  setValue(value: string): void;
  getModel(): { getLanguageId(): string } | null;
  onDidChangeModelContent(listener: () => void): MonacoDisposable;
  onDidBlurEditorText(listener: () => void): MonacoDisposable;
  addCommand(keybinding: number, handler: () => void): void;
  updateOptions(options: Record<string, unknown>): void;
  layout(): void;
  focus(): void;
  dispose(): void;
  setPosition(position: { lineNumber: number; column: number }): void;
  revealLine(line: number): void;
  getPosition(): { lineNumber: number; column: number } | null;
}

export interface MonacoApi {
  editor: {
    create(element: HTMLElement, options: Record<string, unknown>): MonacoEditorInstance;
    defineTheme(name: string, theme: Record<string, unknown>): void;
    setTheme(name: string): void;
    setModelLanguage(model: unknown, language: string): void;
    getModels(): unknown[];
    setModelMarkers(model: unknown, owner: string, markers: unknown[]): void;
  };
  KeyMod: { CtrlCmd: number; Shift: number; Alt: number };
  KeyCode: { KeyS: number; Enter: number };
}

const MONACO_VERSION = "0.55.1";
const MONACO_BASE = `/static/vendor/monaco-editor/${MONACO_VERSION}/min/vs`;
const THEME_DARK = "dsh-dark";
const THEME_LIGHT = "dsh-light";

type ThemeVariant = "dark" | "light";

let monacoPromise: Promise<MonacoApi> | null = null;
let activeVariant: ThemeVariant | null = null;
const themeListeners = new Set<(variant: ThemeVariant) => void>();
let observerAttached = false;

/** Load (once) and resolve the Monaco namespace. */
export function loadMonaco(): Promise<MonacoApi> {
  if (monacoPromise) return monacoPromise;
  monacoPromise = new Promise<MonacoApi>((resolve, reject) => {
    ensureStylesheet();
    loadAmdLoader()
      .then((amdRequire) => {
        amdRequire.config({ paths: { vs: MONACO_BASE } });
        amdRequire(["vs/editor/editor.main"], () => {
          const api = (window as unknown as { monaco?: MonacoApi }).monaco;
          if (!api) {
            reject(new Error("Monaco loaded without exposing window.monaco"));
            return;
          }
          applyTheme(api);
          attachThemeObserver(api);
          resolve(api);
        }, (error: unknown) => reject(error instanceof Error ? error : new Error(String(error))));
      })
      .catch(reject);
  });
  return monacoPromise;
}

/** Subscribe to app theme changes; returns an unsubscribe function. */
export function onThemeChange(listener: (variant: ThemeVariant) => void): () => void {
  themeListeners.add(listener);
  return () => themeListeners.delete(listener);
}

export function currentVariant(): ThemeVariant {
  return detectVariant();
}

function detectVariant(): ThemeVariant {
  const classes = document.documentElement.classList;
  if (classes.contains("theme-light") || classes.contains("theme-lightColored")) return "light";
  return "dark";
}

function applyTheme(api: MonacoApi) {
  const variant = detectVariant();
  api.editor.defineTheme(THEME_DARK, buildTheme("vs-dark"));
  api.editor.defineTheme(THEME_LIGHT, buildTheme("vs"));
  api.editor.setTheme(variant === "dark" ? THEME_DARK : THEME_LIGHT);
  activeVariant = variant;
}

function buildTheme(base: string): Record<string, unknown> {
  const sample = getComputedStyle(document.body);
  const read = (name: string, fallback: string) => {
    const value = sample.getPropertyValue(name).trim();
    return value || fallback;
  };
  const background = read("--input-bg", base === "vs-dark" ? "#2a2a2a" : "#ffffff");
  const foreground = read("--text-primary", base === "vs-dark" ? "#ffffff" : "#1f2937");
  const border = read("--border-color", base === "vs-dark" ? "#2d2d2d" : "#d1d5db");
  const accent = read("--info-color", "#3b82f6");
  const muted = read("--text-muted", base === "vs-dark" ? "#808080" : "#9ca3af");
  return {
    base,
    inherit: true,
    rules: [],
    colors: {
      "editor.background": background,
      "editor.foreground": foreground,
      "editorGutter.background": background,
      "editorLineNumber.foreground": muted,
      "editorLineNumber.activeForeground": foreground,
      "editor.lineHighlightBackground": read("--hover-bg", base === "vs-dark" ? "#2a2a2a" : "#f3f4f6"),
      "editor.selectionBackground": accent,
      "editor.inactiveSelectionBackground": accent,
      "editorCursor.foreground": accent,
      "editorIndentGuide.background1": border,
      "editorIndentGuide.activeBackground1": muted,
      "editorWidget.background": read("--card-bg", background),
      "editorWidget.border": border,
      "editorSuggestWidget.background": read("--card-bg", background),
      "editorSuggestWidget.border": border,
      "input.background": background,
      "input.border": border,
      "scrollbar.shadow": "transparent",
      "scrollbarSlider.background": border,
      "scrollbarSlider.hoverBackground": muted,
      "scrollbarSlider.activeBackground": muted,
      "editorOverviewRuler.border": "transparent",
      "minimap.background": background,
    },
  };
}

function attachThemeObserver(api: MonacoApi) {
  if (observerAttached) return;
  observerAttached = true;
  const sync = () => {
    const variant = detectVariant();
    if (variant === activeVariant) return;
    activeVariant = variant;
    applyTheme(api);
    themeListeners.forEach((listener) => listener(variant));
  };
  new MutationObserver(sync).observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(sync).observe(document.body, { attributes: true, attributeFilter: ["class"] });
}

function ensureStylesheet() {
  const marker = "data-monaco-css";
  if (document.querySelector(`link[${marker}]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `${MONACO_BASE}/editor/editor.main.css`;
  link.setAttribute(marker, "true");
  document.head.appendChild(link);
}

interface AmdRequire {
  (modules: string[], onLoad: () => void, onError?: (error: unknown) => void): void;
  config(options: { paths: Record<string, string> }): void;
}

function loadAmdLoader(): Promise<AmdRequire> {
  const existing = (window as unknown as { require?: AmdRequire }).require;
  if (existing) return Promise.resolve(existing);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${MONACO_BASE}/loader.js`;
    script.async = true;
    script.onload = () => {
      const amd = (window as unknown as { require?: AmdRequire }).require;
      if (amd) resolve(amd);
      else reject(new Error("Monaco AMD loader did not initialise"));
    };
    script.onerror = () => reject(new Error("Could not load the Monaco editor bundle"));
    document.head.appendChild(script);
  });
}

/** Languages Monaco can highlight with its bundled tokenizers. */
export function monacoLanguage(language: string | undefined, fallback = "plaintext"): string {
  const value = (language || "").trim().toLowerCase();
  return value || fallback;
}
