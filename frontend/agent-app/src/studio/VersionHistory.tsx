import { useCallback, useEffect, useState } from "react";
import type { AgentDef, AgentVersion } from "../types";
import { getVersion, listVersions, rollbackVersion } from "./api";
import "./VersionHistory.css";

interface Props {
  slug: string;
  current: AgentDef | null;
  onClose: () => void;
  onReload: (slug: string) => void;
}

function fmt(ts?: string): string {
  if (!ts) return "—";
  const d = new Date(ts.replace(" ", "T"));
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export function VersionHistory({ slug, current, onClose, onReload }: Props) {
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [previewVer, setPreviewVer] = useState<number | null>(null);
  const [preview, setPreview] = useState<AgentDef | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [confirmVer, setConfirmVer] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listVersions(slug);
      setVersions([...(res.versions || [])].sort((a, b) => b.version - a.version));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && confirmVer == null) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, confirmVer]);

  async function openPreview(ver: number) {
    setPreviewVer(ver);
    setPreview(null);
    setPreviewLoading(true);
    try {
      setPreview(await getVersion(slug, ver));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewLoading(false);
    }
  }

  async function doRestore(ver: number) {
    setRestoring(true);
    setError(null);
    try {
      await rollbackVersion(slug, ver);
      setConfirmVer(null);
      onReload(slug);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRestoring(false);
    }
  }

  const draft = Boolean(current?.has_draft_changes);
  const cfg = preview?.config as Record<string, unknown> | undefined;
  const cfgKeys = cfg ? Object.keys(cfg) : [];
  const cfgJson = cfg ? JSON.stringify(cfg, null, 2) : "";

  return (
    <div
      className="as-overlay"
      data-testid="version-history"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="as-dialog as-dialog-wide"
        role="dialog"
        aria-modal="true"
        aria-label="Version history"
        data-testid="version-history-card"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="as-dialog-head">
          <div className="as-dialog-title">Version history</div>
          <p className="as-dialog-sub">{current?.name || slug}</p>
          <button type="button" className="as-btn-icon as-dialog-close" aria-label="Close history" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="as-dialog-body">
          {draft ? (
            <p className="as-help" data-testid="version-draft-badge">
              Current draft (unpublished) — the canvas holds unsaved or unpublished changes.
            </p>
          ) : (
            <p className="as-help">No unpublished changes — the draft matches the latest published version.</p>
          )}
          {loading ? (
            <p className="as-empty">Loading versions…</p>
          ) : error && !versions.length ? (
            <p className="as-error">{error}</p>
          ) : versions.length === 0 ? (
            <p className="as-empty">No published versions yet. Publish to snapshot v1.</p>
          ) : (
            <ul className="as-menu as-version-list" data-testid="version-list">
              {versions.map((v) => (
                <li key={v.version} className="as-menu-item as-version-row" data-testid="version-row">
                  <span className="as-badge">v{v.version}</span>
                  <span className="as-version-row-main">
                    <strong>{v.name}</strong>
                    <span className="as-muted"> · {v.kind} · {fmt(v.created_at)}</span>
                    {v.created_by_name ? <span className="as-muted"> · {v.created_by_name}</span> : null}
                  </span>
                  <button
                    type="button"
                    className="as-btn as-btn-sm"
                    data-testid={`version-preview-${v.version}`}
                    onClick={() => void openPreview(v.version)}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    className="as-btn as-btn-sm"
                    data-testid={`version-restore-${v.version}`}
                    disabled={restoring}
                    onClick={() => setConfirmVer(v.version)}
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
          {error && versions.length > 0 ? <p className="as-error">{error}</p> : null}
          {previewVer != null ? (
            <div className="as-version-preview" data-testid="version-preview">
              <h3 className="as-dialog-title as-version-preview-title">Preview v{previewVer}</h3>
              {previewLoading ? (
                <p className="as-empty">Loading snapshot…</p>
              ) : preview ? (
                <>
                  <p className="as-help">
                    {preview.name} · {preview.kind} · {cfgKeys.length} config key{cfgKeys.length === 1 ? "" : "s"}
                    {cfgKeys.length ? `: ${cfgKeys.slice(0, 12).join(", ")}` : ""}
                  </p>
                  <pre
                    className="as-version-preview-json"
                    data-testid="version-preview-json"
                  >
                    {cfgJson.slice(0, 4000)}
                  </pre>
                  <div className="as-row as-version-preview-actions">
                    <button
                      type="button"
                      className="as-btn as-btn-primary"
                      data-testid="version-preview-restore"
                      disabled={restoring}
                      onClick={() => setConfirmVer(previewVer)}
                    >
                      Restore this version
                    </button>
                    <button type="button" className="as-btn" onClick={() => { setPreviewVer(null); setPreview(null); }}>
                      Close preview
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="as-dialog-foot as-row">
          <button type="button" className="as-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      {confirmVer != null ? (
        <div className="as-overlay" data-testid="version-restore-confirm" onMouseDown={(e) => { if (e.target === e.currentTarget) setConfirmVer(null); }}>
          <div className="as-dialog" role="alertdialog" aria-modal="true" aria-label="Confirm restore" onMouseDown={(e) => e.stopPropagation()}>
            <div className="as-dialog-head">
              <div className="as-dialog-title">Restore v{confirmVer}?</div>
              <p className="as-dialog-sub">The current draft is replaced with this snapshot. This saves immediately.</p>
            </div>
            <div className="as-dialog-foot as-row">
              <button type="button" className="as-btn" disabled={restoring} onClick={() => setConfirmVer(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="as-btn as-btn-primary"
                data-testid="version-restore-confirm-btn"
                disabled={restoring}
                onClick={() => void doRestore(confirmVer)}
              >
                {restoring ? "Restoring…" : "Restore"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
