import { useCallback, useEffect, useState } from "react";
import {
  adminGet,
  adminPostJson,
  adminDelete,
  AdminLoading,
  AdminError,
  AdminEmpty,
  AdminModal,
  AdminField,
  AdminStatusPill,
  PageHeader,
} from "../adminShared";
import { AdminDataTable, type Column } from "../AdminDataTable";
import { ActionsMenu } from "../../shared/ActionsMenu";
import { EditHostedAppDialog, HostedAppAccessDialog } from "./HostedAppDialogs";
import { ImportHostedAppDialog, InstallProgressDialog } from "./HostedAppInstall";

interface HostedApp {
  install_step?: string;
  id: number;
  slug: string;
  name: string;
  description?: string;
  version?: string;
  status: string;
  tier: string;
  tier_reason?: string;
  filesystem_isolated: boolean;
  last_error?: string;
  pid?: number | null;
  url: string;
  /** Whether this caller may change the app (owner or administrator). */
  can_manage?: boolean;
  /** Role ids granted access to this app. */
  access?: number[];
}

interface Capabilities {
  userns: boolean;
  seccomp: boolean;
  landlock_abi: number;
  landlock_status: string;
  tier: string | null;
  tier_reason: string;
  best_tier?: string;
}

interface TierGuideEntry {
  key: string;
  label: string;
  tagline: string;
  works: string[];
  blocked: string[];
  caveat: string;
}

interface AppsOrigin {
  separate: boolean;
  url: string;
  text: string;
}

interface ListResponse {
  apps: HostedApp[];
  origin?: AppsOrigin;
  capabilities: Capabilities;
  capabilities_text: string;
  tier_guide: TierGuideEntry[];
  platform_controls: string[];
  configured_tier: string;
}

/** One tier: what it is, what works, what is blocked, and what to watch for. */
function TierCard({ entry, current }: { entry: TierGuideEntry; current: boolean }) {
  return (
    <div className={`aa-hosted-tier${current ? " is-current" : ""}`}>
      <div className="aa-hosted-tier-head">
        <AdminStatusPill status={entry.key} />
        <span>{entry.label}</span>
        {current ? <span className="aa-muted" style={{ fontSize: "0.78em", fontWeight: 400 }}>this host</span> : null}
      </div>
      <p className="aa-hosted-tier-tagline">{entry.tagline}</p>
      <ul className="aa-hosted-tier-list is-works">
        {entry.works.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <ul className="aa-hosted-tier-list is-blocked">
        {entry.blocked.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p className="aa-hosted-tier-caveat">{entry.caveat}</p>
    </div>
  );
}

function TierGuide({ guide, controls, currentTier }: {
  guide: TierGuideEntry[];
  controls: string[];
  currentTier: string | null;
}) {
  const ordered = [...guide].sort((a, b) => (a.key === currentTier ? -1 : b.key === currentTier ? 1 : 0));
  return (
    <>
      <p className="aa-muted" style={{ marginTop: 0, fontSize: "0.85em" }}>
        Every app is bound by these, whichever tier it runs under:
      </p>
      <ul className="aa-hosted-controls">
        {controls.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <div className="aa-hosted-guide">
        {ordered.map((entry) => (
          <TierCard key={entry.key} entry={entry} current={entry.key === currentTier} />
        ))}
      </div>
    </>
  );
}

/** One-line explanation of what the current tier actually guarantees. */
function tierTone(tier: string): string {
  if (tier === "A") return "ok";
  if (tier === "B+") return "warn";
  return "danger";
}

function getCsrfToken(): string {
  const el = document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]');
  return el?.getAttribute("content") || "";
}

export function HostedAppsPage() {
  const [data, setData] = useState<ListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  //: Importing/deploying is administrator-only; the server enforces it, and
  //: this keeps the control out of everyone else's way.
  const [isAdmin, setIsAdmin] = useState(false);
  const [logsFor, setLogsFor] = useState<HostedApp | null>(null);
  const [logs, setLogs] = useState<{ install: string; app: string }>({ install: "", app: "" });
  const [logTab, setLogTab] = useState<"app" | "install">("app");
  const [deleting, setDeleting] = useState<HostedApp | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [retrying, setRetrying] = useState<{ slug: string; start: boolean } | null>(null);
  const [editing, setEditing] = useState<HostedApp | null>(null);
  const [managingAccess, setManagingAccess] = useState<HostedApp | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await adminGet<ListResponse>("/admin/api/hosted-apps");
      setData(body);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    adminGet<{ is_admin?: boolean }>("/api/v1/me")
      .then((me) => {
        if (!cancelled) setIsAdmin(!!me.is_admin);
      })
      .catch(() => {
        if (!cancelled) setIsAdmin(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const control = useCallback(
    async (app: HostedApp, action: "start" | "stop" | "restart") => {
      setBusy(`${app.slug}:${action}`);
      try {
        await adminPostJson(`/admin/api/hosted-apps/${app.slug}/${action}`, {});
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  const retryInstall = useCallback(async (app: HostedApp) => {
    setBusy(`${app.slug}:reinstall`);
    try {
      await adminPostJson(`/admin/api/hosted-apps/${app.slug}/reinstall`, {});
      setRetrying({ slug: app.slug, start: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }, []);

  const openLogs = useCallback(async (app: HostedApp) => {
    setLogsFor(app);
    setLogTab(app.status === "installing" || app.status === "error" ? "install" : "app");
    setLogs({ install: "Loading…", app: "Loading…" });
    const fetchLog = async (source: "install" | "app") => {
      try {
        const body = await adminGet<{ log: string }>(
          `/admin/api/hosted-apps/${app.slug}/logs?lines=400&source=${source}`,
        );
        return body.log?.trim() || "";
      } catch (err) {
        return `Could not read the log: ${err instanceof Error ? err.message : String(err)}`;
      }
    };
    // Both are fetched: during an install the interesting output is in the
    // install log, and once it runs it is in the app log.
    const [install, appLog] = await Promise.all([fetchLog("install"), fetchLog("app")]);
    setLogs({ install, app: appLog });
  }, []);

  // While an install is running, keep the log dialog live: the whole point of
  // opening it is to watch the steps, and a frozen snapshot would look stuck.
  const logsSlug = logsFor?.slug;
  const logsStatus = logsFor?.status;
  useEffect(() => {
    if (!logsSlug || logsStatus !== "installing") return;
    const timer = setInterval(async () => {
      try {
        const body = await adminGet<ListResponse>("/admin/api/hosted-apps");
        const fresh = body.apps.find((candidate) => candidate.slug === logsSlug);
        if (fresh) {
          setData(body);
          setLogsFor(fresh);
          const [install, appLog] = await Promise.all([
            adminGet<{ log: string }>(`/admin/api/hosted-apps/${logsSlug}/logs?lines=400&source=install`),
            adminGet<{ log: string }>(`/admin/api/hosted-apps/${logsSlug}/logs?lines=400&source=app`),
          ]);
          setLogs({ install: install.log || "", app: appLog.log || "" });
        }
      } catch {
        /* transient: the next tick tries again */
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [logsSlug, logsStatus]);

  const remove = useCallback(async () => {
    if (!deleting) return;
    setBusy(`${deleting.slug}:delete`);
    try {
      await adminDelete(`/admin/api/hosted-apps/${deleting.slug}`);
      setDeleting(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }, [deleting, load]);

  const columns: Column<HostedApp>[] = [
    {
      key: "name",
      header: "Application",
      render: (app) => (
        // Wrapped and width-capped on purpose: table cells are nowrap, so an
        // uncapped description would force this column wide and push the
        // STATUS/ISOLATION/ACTIONS columns out of view.
        <div style={{ maxWidth: 420, whiteSpace: "normal" }}>
          <div>{app.name}</div>
          <div className="aa-muted" style={{ fontSize: "0.85em" }}>
            {app.description || app.slug}
          </div>
        </div>
      ),
      sortValue: (app) => app.name,
    },
    {
      key: "url",
      header: "URL",
      render: (app) => (
        <a href={app.url} target="_blank" rel="noreferrer">
          {app.url}
        </a>
      ),
      sortValue: (app) => app.url,
    },
    {
      key: "status",
      header: "Status",
      render: (app) => (
        <span>
          <AdminStatusPill status={app.status} />
          {app.last_error ? (
            <div className="aa-muted" style={{ fontSize: "0.8em", maxWidth: 220, whiteSpace: "normal" }}>
              {app.last_error.slice(0, 160)}
            </div>
          ) : null}
        </span>
      ),
      sortValue: (app) => app.status,
    },
    {
      key: "tier",
      header: "Isolation",
      render: (app) => (
        <span
          style={{ whiteSpace: "normal", display: "inline-block", maxWidth: 200 }}
          title={data?.tier_guide?.find((entry) => entry.key === app.tier)?.tagline || ""}
        >
          <AdminStatusPill status={app.tier || "unknown"} />
          <div className="aa-muted" style={{ fontSize: "0.8em" }}>
            {!app.tier
              ? "not run yet"
              : app.status !== "running"
                ? `last run: ${app.filesystem_isolated ? "isolated" : "no isolation"}`
                : app.tier === "A"
                  ? "platform files hidden (namespaces)"
                  : app.tier === "B+"
                    ? "platform files denied (Landlock)"
                    : "no filesystem isolation"}
          </div>
        </span>
      ),
      sortValue: (app) => app.tier,
    },
    {
      key: "actions",
      header: "Actions",
      className: "aa-table-actions",
      render: (app) => {
        // A role granted access may open the app but not manage it; the server
        // refuses either way, and this keeps the menu honest about it.
        const canManage = app.can_manage !== false;
        return (
          <ActionsMenu
            testId={`hosted-app-actions-${app.slug}`}
            items={[
              {
                key: "open",
                label: "Open",
                disabled: app.status !== "running",
                onSelect: () => window.open(app.url, "_blank", "noreferrer"),
              },
              ...(canManage
                ? [
                    { key: "edit", label: "Edit details", onSelect: () => setEditing(app) },
                    { key: "access", label: "Access", onSelect: () => setManagingAccess(app) },
                    app.status === "installing"
                      ? { key: "installing", label: "Installing…", disabled: true, onSelect: () => undefined }
                      : app.status === "running"
                        ? { key: "stop", label: "Stop", disabled: busy !== null, onSelect: () => control(app, "stop") }
                        : { key: "start", label: "Start", disabled: busy !== null, onSelect: () => control(app, "start") },
                    {
                      key: "restart",
                      label: "Restart",
                      disabled: app.status !== "running" || busy !== null,
                      onSelect: () => control(app, "restart"),
                    },
                    {
                      key: "reinstall",
                      label: app.status === "error" ? "Retry install" : "Reinstall dependencies",
                      disabled: app.status === "installing" || busy !== null,
                      onSelect: () => retryInstall(app),
                    },
                    { key: "logs", label: "Logs", onSelect: () => openLogs(app) },
                    { key: "delete", label: "Delete", danger: true, onSelect: () => setDeleting(app) },
                  ]
                : []),
            ]}
          />
        );
      },
    },
  ];

  if (!data && !error) return <AdminLoading label="Loading hosted apps…" />;
  if (!data && error) return <AdminError message={error} />;

  const caps = data!.capabilities;
  const apps = data!.apps;

  return (
    <div className="aa-admin-page">
      <PageHeader
        title="Hosted Apps"
        actions={
          isAdmin ? (
            <button type="button" className="aa-btn aa-btn-primary" onClick={() => setImportOpen(true)}>
              Import application
            </button>
          ) : null
        }
      />

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">Isolation on this host</span>
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setGuideOpen(true)}>
              What do the badges mean?
            </button>
            <AdminStatusPill status={caps.tier || "unavailable"} />
          </span>
        </div>
        <div style={{ padding: "0 16px 16px" }}>
          <p className="aa-muted" style={{ marginTop: 0 }}>{data!.capabilities_text}</p>
          <p className="aa-muted" style={{ fontSize: "0.85em" }}>
            user namespaces: {caps.userns ? "available" : "unavailable"} · landlock: {caps.landlock_status} ·
            seccomp: {caps.seccomp ? "available" : "unavailable"} · configured tier: {data!.configured_tier}
          </p>
          {data!.origin ? (
            <p className="aa-muted" style={{ fontSize: "0.85em" }}>
              {data!.origin.separate ? (
                <>
                  apps origin: <strong>{data!.origin.url}</strong> — app content runs on its own origin, so
                  an app&rsquo;s JavaScript cannot call platform APIs as the signed-in user. The platform&rsquo;s own{" "}
                  <code>/apps/&lt;name&gt;/</code> links redirect there.
                </>
              ) : (
                <>
                  apps origin: <strong>the platform&rsquo;s own origin</strong> —{" "}
                  <span style={{ color: "var(--aa-danger, #b42318)" }}>
                    insecure migration mode: a hosted app can act as the signed-in user.
                  </span>{" "}
                  Set <code>HOSTED_APPS_ORIGIN_PORT</code> (default 5001) or <code>HOSTED_APPS_ORIGIN</code>.
                </>
              )}
            </p>
          ) : null}
          {!caps.tier ? <AdminError message={caps.tier_reason} /> : null}
        </div>
      </div>

      {error ? <AdminError message={error} /> : null}

      <div className="aa-admin-panel">
        <div className="aa-admin-panel-head">
          <span className="aa-muted">
            {apps.length} application{apps.length === 1 ? "" : "s"}
          </span>
        </div>
        {apps.length === 0 ? (
          <AdminEmpty message="No hosted applications yet. Import a .zip that contains app.json." />
        ) : (
          <AdminDataTable<HostedApp>
            columns={columns}
            rows={apps}
            rowKey={(app) => app.slug}
            searchText={(app) => `${app.name} ${app.slug} ${app.description || ""}`}
            searchPlaceholder="Search applications…"
          />
        )}
      </div>

      {isAdmin ? (
        <ImportHostedAppDialog open={importOpen} onClose={() => setImportOpen(false)} onImported={load} />
      ) : null}
      {retrying ? (
        <InstallProgressDialog
          slug={retrying.slug}
          startWhenReady={retrying.start}
          onClose={() => setRetrying(null)}
          onFinished={load}
          title={`Installing ${retrying.slug}`}
        />
      ) : null}
      <EditHostedAppDialog app={editing} onClose={() => setEditing(null)} onSaved={load} />
      <HostedAppAccessDialog
        app={managingAccess}
        onClose={() => setManagingAccess(null)}
        onSaved={load}
      />

      <AdminModal
        title="What the isolation badges mean"
        open={guideOpen}
        onClose={() => setGuideOpen(false)}
        wide
      >
        {data!.tier_guide?.length ? (
          <TierGuide
            guide={data!.tier_guide}
            controls={data!.platform_controls || []}
            currentTier={caps.tier}
          />
        ) : null}
      </AdminModal>

      <AdminModal
        title={logsFor ? `Logs · ${logsFor.name}` : "Logs"}
        open={logsFor !== null}
        onClose={() => setLogsFor(null)}
        wide
      >
        {/* The install log only exists while an install is running or after it
            failed. Once an app is up it is finished business, so the app's own
            output gets the whole dialog. */}
        <div className="aa-hosted-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={logTab === "app"}
            className={logTab === "app" ? "is-active" : ""}
            onClick={() => setLogTab("app")}
          >
            Application log
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={logTab === "install"}
            className={logTab === "install" ? "is-active" : ""}
            onClick={() => setLogTab("install")}
          >
            Install log
          </button>
          {logsFor?.status === "installing" ? (
            <span className="aa-hosted-tab-note">
              installing — {logsFor.install_step || "working"}
            </span>
          ) : null}
        </div>

        {logTab === "app" ? (
          <pre className="aa-hosted-install-log is-tall">
            {logs.app.trim() ||
              (logsFor?.status === "installing"
                ? "Nothing yet — the app starts once its environment is ready."
                : "No output yet. The platform writes a banner here when it starts the app.")}
          </pre>
        ) : (
          <pre className="aa-hosted-install-log is-tall">
            {logs.install.trim() || "No install log: this app was installed before logs were kept."}
          </pre>
        )}
      </AdminModal>

      <AdminModal
        title="Remove application"
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        footer={
          <>
            <button type="button" className="aa-btn aa-btn-ghost" onClick={() => setDeleting(null)}>
              Cancel
            </button>
            <button type="button" className="aa-btn aa-btn-danger" disabled={busy !== null} onClick={remove}>
              Remove
            </button>
          </>
        }
      >
        <p>
          Remove <strong>{deleting?.name}</strong> and delete its files? The application is stopped first.
        </p>
      </AdminModal>
    </div>
  );
}
