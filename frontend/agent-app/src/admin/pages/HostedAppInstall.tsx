import { useCallback, useEffect, useRef, useState } from "react";
import { adminGet, adminPostJson, AdminError, AdminModal, AdminField } from "../adminShared";

/**
 * Follow one app's install until it finishes.
 *
 * Used both for a fresh upload and for retrying an app whose install did not
 * complete: the steps, the log and the outcome are identical either way.
 */
export function InstallProgressDialog({
  slug,
  startWhenReady,
  onClose,
  onFinished,
  title,
}: {
  slug: string;
  startWhenReady: boolean;
  onClose: () => void;
  onFinished: () => void;
  title?: string;
}) {
  const [phase, setPhase] = useState<"installing" | "ready" | "failed">("installing");
  const [step, setStep] = useState("queued");
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const state = await adminGet<{ status: string; step: string; error: string; log: string }>(
          `/admin/api/hosted-apps/${slug}/install?lines=400`,
        );
        if (cancelled) return;
        setLog(state.log || "");
        setStep(state.step || "");
        if (state.status === "error") {
          setPhase("failed");
          setError(state.error || "Environment setup failed.");
          onFinished();
        } else if (state.status !== "installing") {
          if (startWhenReady) {
            try {
              await adminPostJson(`/admin/api/hosted-apps/${slug}/start`, {});
            } catch {
              /* the list shows why it did not start */
            }
          }
          if (!cancelled) {
            setPhase("ready");
            onFinished();
          }
        }
      } catch {
        /* transient: keep polling */
      }
    };
    void tick();
    const timer = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [slug, startWhenReady, onFinished]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [log]);

  return (
    <AdminModal
      title={title || (phase === "installing" ? `Installing ${slug}` : phase === "ready" ? `${slug} is ready` : `${slug} could not be installed`)}
      open
      onClose={phase === "installing" ? () => undefined : onClose}
      wide
      footer={
        phase === "installing" ? (
          <span className="aa-muted">Installing dependencies… this can take a few minutes.</span>
        ) : (
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
              Close
            </button>
            {phase === "ready" ? (
              <a className="aa-btn aa-btn-primary" href={`/apps/${slug}/`} target="_blank" rel="noreferrer">
                Open application
              </a>
            ) : null}
            {phase === "failed" ? (
              <button type="button" className="aa-btn aa-btn-primary" onClick={onClose}>
                Close and retry from the list
              </button>
            ) : null}
          </>
        )
      }
    >
      <p className="aa-muted" style={{ marginTop: 0 }}>
        {phase === "installing" ? (
          <>
            <strong>{step || "working"}</strong> — creating the virtualenv, installing Python and Node
            packages, then running the build.
          </>
        ) : phase === "ready" ? (
          "Installed. The log below is the full record of what was done."
        ) : (
          <span className="aa-error">{error}</span>
        )}
      </p>
      <pre ref={logRef} className="aa-hosted-install-log">
        {log.trim() || "waiting for the first step…"}
      </pre>
    </AdminModal>
  );
}


/** Upload an archive, then hand over to the progress view. */
export function ImportHostedAppDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [startWhenReady, setStartWhenReady] = useState(true);
  const [slug, setSlug] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setFile(null);
    setSlug("");
    setError(null);
    setBusy(false);
    if (fileInput.current) fileInput.current.value = "";
  }, [open]);

  const submit = useCallback(async () => {
    if (!file) {
      setError("Choose a .zip archive containing app.json and requirements.txt.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (startWhenReady) form.append("start", "1");
      const meta = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
      const response = await fetch("/admin/api/hosted-apps/import", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": meta?.getAttribute("content") || "" },
        body: form,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || `Import failed (${response.status})`);
      setSlug(body.app?.slug || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [file, startWhenReady]);

  if (slug) {
    return (
      <InstallProgressDialog
        slug={slug}
        startWhenReady={startWhenReady}
        onClose={onClose}
        onFinished={onImported}
      />
    );
  }

  return (
    <AdminModal
      title="Import application"
      open={open}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
            Close
          </button>
          <button type="button" className="aa-btn aa-btn-primary" disabled={busy} onClick={submit}>
            {busy ? "Uploading…" : "Import"}
          </button>
        </>
      }
    >
      <AdminField
        label="Application archive (.zip)"
        hint="Must contain app.json (name and entry point) and requirements.txt declaring the app's Python dependencies. A package.json adds Node packages and an optional build step."
      >
        <input
          ref={fileInput}
          id="hosted-app-archive"
          name="hosted-app-archive"
          type="file"
          accept=".zip,application/zip"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </AdminField>
      <AdminField label="After install">
        <label className="aa-hosted-checkbox aa-muted" style={{ marginTop: 0 }}>
          <input
            id="hosted-app-start-immediately"
            name="hosted-app-start-immediately"
            type="checkbox"
            checked={startWhenReady}
            onChange={(event) => setStartWhenReady(event.target.checked)}
          />{" "}
          Start it immediately under the best available isolation
        </label>
      </AdminField>
      {error ? <AdminError message={error} /> : null}
    </AdminModal>
  );
}
