import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  currentVariant,
  loadMonaco,
  monacoLanguage,
  type MonacoApi,
  type MonacoEditorInstance,
} from "./monaco";
import "./codeEditor.css";

export interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  /** Monaco language id; inferred from `filename` when omitted. */
  language?: string;
  /** Used for the language default, the a11y label and the status bar. */
  filename?: string;
  label?: string;
  readOnly?: boolean;
  height?: number | string;
  /** Stretch to fill a flex parent instead of using a fixed height. */
  fill?: boolean;
  minHeight?: number;
  wordWrap?: boolean;
  lineNumbers?: boolean;
  /** Ctrl/Cmd+S handler; when absent the keybinding is left to the browser. */
  onSave?: () => void;
  /** Ctrl/Cmd+Enter handler (used by "run query" style editors). */
  onSubmit?: () => void;
  /**
   * Increment after the parent persists the content. Supplying it turns on the
   * saved/modified indicator, which can only be truthful when someone owns the
   * write. Left undefined the status bar shows just the language.
   */
  revision?: number;
  className?: string;
  testId?: string;
  /** Mirrored onto Monaco's editable node so `<label for>` resolves. */
  id?: string;
  name?: string;
}

const LANGUAGE_BY_EXTENSION: Record<string, string> = {
  json: "json", jsonc: "json", md: "markdown", markdown: "markdown", mdx: "markdown",
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript", yml: "yaml", yaml: "yaml", toml: "ini", ini: "ini",
  sh: "shell", bash: "shell", sql: "sql", html: "html", htm: "html", xml: "xml", xsd: "xml",
  css: "css", scss: "scss", txt: "plaintext", csv: "plaintext", log: "plaintext",
};

function inferLanguage(filename: string | undefined, explicit: string | undefined): string {
  if (explicit) return monacoLanguage(explicit);
  const name = (filename || "").split("/").pop() || "";
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  return LANGUAGE_BY_EXTENSION[ext] || "plaintext";
}

function sizeToCss(size: number | string | undefined, fallback: number): string {
  if (typeof size === "number") return `${size}px`;
  return size || `${fallback}px`;
}

/**
 * Monaco-backed editor used everywhere a long or structured document is edited.
 *
 * Monaco is a hard dependency of these surfaces: if its bundle cannot load the
 * component reports the failure instead of silently degrading to a textarea, so
 * a broken deployment cannot look like a working one.
 */
export function CodeEditor({
  value,
  onChange,
  language,
  filename,
  label,
  readOnly = false,
  height = 260,
  fill = false,
  minHeight,
  wordWrap = true,
  lineNumbers = true,
  onSave,
  onSubmit,
  revision,
  className,
  testId,
  id,
  name,
}: CodeEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const editorRef = useRef<MonacoEditorInstance | null>(null);
  const changeRef = useRef(onChange);
  const saveRef = useRef(onSave);
  const submitRef = useRef(onSubmit);
  const valueRef = useRef(value);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const resolvedLanguage = useMemo(() => inferLanguage(filename, language), [filename, language]);

  changeRef.current = onChange;
  saveRef.current = onSave;
  submitRef.current = onSubmit;

  const flush = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const next = editor.getValue();
    if (next === valueRef.current) return;
    valueRef.current = next;
    changeRef.current(next);
    setDirty(false);
  }, []);

  // Create the Monaco instance once per mounted editor. Keybindings are
  // registered here and nowhere else: Monaco's `addCommand` has no disposal API,
  // so re-registering on every render leaked dynamic keybindings indefinitely.
  useEffect(() => {
    let disposed = false;
    let subscription: { dispose(): void } | null = null;
    loadMonaco()
      .then((api) => {
        if (disposed || !hostRef.current) return;
        const editor = api.editor.create(hostRef.current, {
          value,
          language: resolvedLanguage,
          theme: currentVariant() === "dark" ? "dsh-dark" : "dsh-light",
          automaticLayout: false,
          readOnly,
          // `readOnly` must be the *only* thing that makes a field unwritable.
          // Left at its default, Monaco 0.55 turns on the experimental
          // EditContext input path in Chromium, where a click never gives the
          // editor text focus (focus stays on a `readonly` hidden helper
          // textarea), so every field built on this component silently behaved
          // like a read-only viewer. Pin the classic textarea input instead.
          editContext: false,
          domReadOnly: false,
          wordWrap: wordWrap ? "on" : "off",
          lineNumbers: lineNumbers ? "on" : "off",
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          renderLineHighlight: "line",
          fontSize: 13,
          tabSize: 2,
          insertSpaces: true,
          smoothScrolling: true,
          scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10, useShadows: false },
          padding: { top: 10, bottom: 10 },
          fixedOverflowWidgets: true,
          ariaLabel: label || filename || "Code editor",
          overviewRulerLanes: 0,
          hideCursorInOverviewRuler: true,
          contextmenu: true,
          bracketPairColorization: { enabled: true },
          guides: { indentation: true },
          stickyScroll: { enabled: false },
          renderWhitespace: "selection",
          unicodeHighlight: { ambiguousCharacters: false },
        });
        editorRef.current = editor;
        // The bundle may resolve *after* the parent already pushed content, in
        // which case the value-sync effect ran while `editorRef` was still null.
        // Seed from the always-current ref so nothing is silently dropped.
        if (valueRef.current !== editor.getValue()) editor.setValue(valueRef.current);
        subscription = editor.onDidChangeModelContent(() => {
          const next = editor.getValue();
          if (next === valueRef.current) return;
          valueRef.current = next;
          setDirty(true);
          changeRef.current(next);
        });
        editor.addCommand(api.KeyMod.CtrlCmd | api.KeyCode.KeyS, () => saveRef.current?.());
        editor.addCommand(api.KeyMod.CtrlCmd | api.KeyCode.Enter, () => submitRef.current?.());
        if (testId) {
          // Playwright (and screen readers) need the editable node itself, which
          // Monaco creates internally -- the wrapper div is not fillable.
          const editable = hostRef.current.querySelector("textarea");
          editable?.setAttribute("data-testid", testId);
          // A <label for> must resolve: mirror the wrapper id onto the editable.
          if (editable && id) {
            editable.setAttribute("id", id);
            editable.setAttribute("name", name ?? id);
          }
        }
        setReady(true);
      })
      .catch((error: unknown) => {
        if (!disposed) setFailed(error instanceof Error ? error.message : String(error));
      });
    return () => {
      disposed = true;
      subscription?.dispose();
      editorRef.current?.dispose();
      editorRef.current = null;
      setReady(false);
    };
    // Recreating on language change keeps model ownership simple and avoids
    // leaking models when the selected artifact switches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedLanguage]);

  // Push external value changes into the editor without clobbering typing.
  useEffect(() => {
    const editor = editorRef.current;
    valueRef.current = value;
    if (!editor) return;
    if (editor.getValue() === value) return;
    editor.setValue(value);
    setDirty(false);
  }, [value, ready]);

  useEffect(() => {
    editorRef.current?.updateOptions({ readOnly, wordWrap: wordWrap ? "on" : "off", lineNumbers: lineNumbers ? "on" : "off" });
  }, [readOnly, wordWrap, lineNumbers]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const sub = editor.onDidBlurEditorText(() => flush());
    return () => sub.dispose();
  }, [ready, flush]);

  // A new revision means the parent persisted the content: clear the dirty flag.
  useEffect(() => {
    setDirty(false);
  }, [revision]);

  // Monaco cannot measure itself inside a flex or hidden container.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !ready) return;
    const measure = () => editorRef.current?.layout();
    const raf = requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    window.addEventListener("resize", measure);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [ready]);

  const style = fill
    ? { flex: "1 1 auto", minHeight: minHeight ? `${minHeight}px` : undefined, height: "auto" }
    : { height: sizeToCss(height, 260), minHeight: minHeight ? `${minHeight}px` : undefined };

  if (failed) {
    return (
      <div className={`aa-code-editor aa-code-editor-failed ${className || ""}`} style={style} data-testid={testId}>
        <div className="aa-code-editor-error" role="alert">
          Editor failed to load: {failed}
        </div>
      </div>
    );
  }

  return (
    <div
      className={`aa-code-editor ${className || ""}`}
      style={style}
      data-editor={testId || undefined}
      data-ready={ready ? "true" : "false"}
    >
      <div ref={hostRef} className="aa-code-editor-host" />
      {!ready && <div className="aa-code-editor-loading">Loading editor…</div>}
      {(label || filename) && (
        <div className="aa-code-editor-status">
          <span>{filename || label}</span>
          <span className="aa-code-editor-status-right">
            {revision !== undefined ? `${dirty ? "modified" : "saved"} · ` : ""}
            {resolvedLanguage}
          </span>
        </div>
      )}
    </div>
  );
}
