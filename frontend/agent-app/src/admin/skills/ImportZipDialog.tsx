import { useCallback, useEffect, useRef, useState } from "react";
import { AdminModal } from "../adminShared";
import { importSkillZip, type ImportResult } from "./skillsApi";

interface Props {
  open: boolean;
  onClose: () => void;
  onImported: (result: ImportResult) => void;
}

/**
 * ZIP upload for complete skill packages.
 *
 * Drop (or pick) an archive produced by `zip -r`, GitHub's "Download ZIP", or
 * any skill folder export. The server discovers every `SKILL.md` root inside
 * the archive, so one upload can carry a single skill or a whole collection.
 */
export function ImportZipDialog({ open, onClose, onImported }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const reset = useCallback(() => {
    setFile(null);
    setReplace(false);
    setDragging(false);
    setBusy(false);
    setResult(null);
    setError(null);
  }, []);

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  function accept(candidate: File | undefined | null) {
    if (!candidate) return;
    if (!candidate.name.toLowerCase().endsWith(".zip")) {
      setError("Only .zip archives are supported.");
      return;
    }
    setError(null);
    setResult(null);
    setFile(candidate);
  }

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const res = await importSkillZip(file, replace);
      setResult(res);
      onImported(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const conflicts = result?.conflicts || [];
  const imported = result?.imported || [];

  return (
    <AdminModal
      title="Import Skill ZIP"
      open={open}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
            Close
          </button>
          <button
            type="button"
            className="aa-btn aa-btn-primary"
            onClick={() => void submit()}
            disabled={!file || busy}
          >
            {busy ? "Importing…" : replace && conflicts.length ? "Replace & import" : "Import"}
          </button>
        </>
      }
    >
      <p className="aa-muted" style={{ marginTop: 0 }}>
        Upload the skill folder as a ZIP. Everything inside is preserved — <code>SKILL.md</code>,{" "}
        <code>scripts/</code>, <code>references/</code>, <code>assets/</code> and any nested folders or binary files.
        An archive may contain several skills; each <code>SKILL.md</code> becomes its own package.
      </p>

      <div
        className={`aa-zip-drop${dragging ? " dragging" : ""}${file ? " has-file" : ""}`}
        data-testid="zip-dropzone"
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          accept(event.dataTransfer?.files?.[0]);
        }}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
      >
        <div className="aa-zip-drop-icon">🗂️</div>
        {file ? (
          <>
            <div className="aa-zip-drop-title">{file.name}</div>
            <div className="aa-muted">{(file.size / 1024).toFixed(1)} KB · click to choose a different archive</div>
          </>
        ) : (
          <>
            <div className="aa-zip-drop-title">Drop a skill .zip here</div>
            <div className="aa-muted">or click to browse</div>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          data-testid="zip-input"
          onChange={(event) => accept(event.target.files?.[0])}
        />
      </div>

      <label className="aa-zip-replace">
        <input type="checkbox" checked={replace} onChange={(event) => setReplace(event.target.checked)} />
        <span>Replace existing skills with the same name</span>
      </label>

      {error && <div className="aa-error">{error}</div>}

      {result && (
        <div className="aa-admin-state" style={{ marginTop: 12 }} data-testid="zip-result">
          {imported.length > 0 ? (
            <>
              <div>
                Imported <strong>{imported.length}</strong> skill{imported.length === 1 ? "" : "s"} ·{" "}
                {result.file_count} files
              </div>
              <ul className="aa-zip-list">
                {imported.map((item) => (
                  <li key={item.name}>
                    <strong>{item.name}</strong> <span className="aa-muted">{item.file_count} files · {item.path}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <div>Nothing was imported.</div>
          )}
          {conflicts.length > 0 && (
            <div className="aa-error" style={{ marginTop: 8 }}>
              {conflicts.length} skill{conflicts.length === 1 ? "" : "s"} already exist
              {conflicts.map((conflict) => conflict.name).join(", ")}. Tick “Replace existing skills” and import again.
            </div>
          )}
        </div>
      )}
    </AdminModal>
  );
}
