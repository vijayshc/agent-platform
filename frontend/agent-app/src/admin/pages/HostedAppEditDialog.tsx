import { useCallback, useEffect, useState } from "react";
import { adminGet, adminPutJson, AdminError, AdminModal, AdminField } from "../adminShared";

type SizeUnit = "B" | "KB" | "MB";

interface SizeDraft {
  value: string;
  unit: SizeUnit;
}

const UNIT_BYTES: Record<SizeUnit, number> = { B: 1, KB: 1024, MB: 1024 * 1024 };

/** Show a stored byte cap in the unit that expresses it exactly. */
function bytesToDraft(bytes: number): SizeDraft {
  if (bytes % UNIT_BYTES.MB === 0) return { value: String(bytes / UNIT_BYTES.MB), unit: "MB" };
  if (bytes % UNIT_BYTES.KB === 0) return { value: String(bytes / UNIT_BYTES.KB), unit: "KB" };
  return { value: String(bytes), unit: "B" };
}

/** A blank or nonsensical entry means "no limit", not zero. */
function draftToBytes(draft: SizeDraft): number | null {
  const amount = Number(draft.value);
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const bytes = Math.round(amount * UNIT_BYTES[draft.unit]);
  return bytes > 0 ? bytes : null;
}

/** A source-IP rule the backend can enforce: address, `a.b.c.d/n` or `a.b.*`. */
function isValidIpRule(value: string): boolean {
  const rule = value.trim();
  if (!rule) return false;
  const octet = (part: string) => /^\d{1,3}$/.test(part) && Number(part) <= 255;
  if (rule.includes("*")) {
    const parts = rule.split(".");
    return parts.length === 4 && parts.every((part) => part === "*" || octet(part));
  }
  const [address, prefix] = rule.split("/");
  if (!address.split(".").every(octet) || address.split(".").length !== 4) return false;
  if (prefix === undefined) return true;
  return /^\d{1,2}$/.test(prefix) && Number(prefix) <= 32;
}

export interface HostedAppSummary {
  id?: number;
  slug: string;
  name: string;
  description?: string;
  workspace?: string;
  /** Whether the platform starts it when the platform itself boots. */
  autostart?: boolean;
}


export function EditHostedAppDialog({
  app,
  onClose,
  onSaved,
}: {
  app: HostedAppSummary | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [autostart, setAutostart] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The content policy lives here too: it is a property of this application,
  // saved with the rest of its details.
  const [allowed, setAllowed] = useState<string[]>([]);
  const [blockAttachments, setBlockAttachments] = useState(false);
  const [recommended, setRecommended] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  //: Per-type payload caps, kept as drafts so a half-typed number is not lost.
  const [limitDrafts, setLimitDrafts] = useState<Record<string, SizeDraft>>({});
  const [maxLimitBytes, setMaxLimitBytes] = useState(32 * 1024 * 1024);
  //: Source addresses allowed to open the app; empty means every address.
  const [sourceIps, setSourceIps] = useState<string[]>([]);
  const [ipDraft, setIpDraft] = useState("");
  const [ipError, setIpError] = useState<string | null>(null);

  useEffect(() => {
    setName(app?.name || "");
    setDescription(app?.description || "");
    setAutostart(!!app?.autostart);
    setError(null);
    setDraft("");
    setLimitDrafts({});
    setSourceIps([]);
    setIpDraft("");
    setIpError(null);
  }, [app]);

  useEffect(() => {
    if (!app) return;
    adminGet<{
      policy: {
        allowed_content_types: string[];
        block_attachments: boolean;
        content_type_limits: Record<string, number>;
        source_ip_allowlist: string[];
      };
      recommended: string[];
      max_limit_bytes: number;
    }>(`/admin/api/hosted-apps/${app.slug}/policy`)
      .then((body) => {
        setAllowed(body.policy.allowed_content_types || []);
        setBlockAttachments(!!body.policy.block_attachments);
        setRecommended(body.recommended || []);
        setMaxLimitBytes(body.max_limit_bytes || 32 * 1024 * 1024);
        setSourceIps(body.policy.source_ip_allowlist || []);
        const drafts: Record<string, SizeDraft> = {};
        for (const [type, bytes] of Object.entries(body.policy.content_type_limits || {})) {
          if (Number(bytes) > 0) drafts[type] = bytesToDraft(Number(bytes));
        }
        setLimitDrafts(drafts);
      })
      .catch(() => undefined);
  }, [app]);

  const setLimit = useCallback((type: string, value: string, unit: SizeUnit) => {
    setLimitDrafts((current) => ({ ...current, [type]: { value, unit } }));
  }, []);

  /** Replace the allowlist, dropping the caps of types that are no longer allowed. */
  const replaceAllowed = useCallback((next: string[]) => {
    setAllowed(next);
    setLimitDrafts((current) => {
      const kept: Record<string, SizeDraft> = {};
      for (const type of next) if (current[type]) kept[type] = current[type];
      return kept;
    });
  }, []);

  const addType = useCallback((value: string) => {
    const clean = value.split(";")[0].trim().toLowerCase();
    if (!clean) return;
    setAllowed((current) => (current.includes(clean) ? current : [...current, clean]));
    setLimitDrafts((current) => (current[clean] ? current : { ...current, [clean]: { value: "", unit: "MB" } }));
    setDraft("");
  }, []);

  const addIp = useCallback((value: string) => {
    const clean = value.trim();
    if (!clean) return;
    if (!isValidIpRule(clean)) {
      setIpError(`"${clean}" is not an address, range or wildcard (try 10.0.0.0/24 or 192.168.1.*).`);
      return;
    }
    setIpError(null);
    setSourceIps((current) => (current.includes(clean) ? current : [...current, clean]));
    setIpDraft("");
  }, []);

  const save = useCallback(async () => {
    if (!app) return;
    if (!name.trim()) {
      setError("Name cannot be empty.");
      return;
    }
    const content_type_limits: Record<string, number> = {};
    for (const type of allowed) {
      const draft = limitDrafts[type];
      const bytes = draft ? draftToBytes(draft) : null;
      if (bytes === null) continue;
      if (bytes > maxLimitBytes) {
        setError(
          `The limit for ${type} is larger than the ${Math.floor(maxLimitBytes / (1024 * 1024))} MB ceiling.`,
        );
        return;
      }
      content_type_limits[type] = bytes;
    }
    setBusy(true);
    setError(null);
    try {
      await adminPutJson(`/admin/api/hosted-apps/${app.slug}`, { name, description, autostart });
      await adminPutJson(`/admin/api/hosted-apps/${app.slug}/policy`, {
        allowed_content_types: allowed,
        block_attachments: blockAttachments,
        content_type_limits,
        source_ip_allowlist: sourceIps,
      });
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [app, name, description, autostart, allowed, blockAttachments, limitDrafts, maxLimitBytes,
      sourceIps, onClose, onSaved]);

  return (
    <AdminModal
      title={app ? `Edit ${app.name}` : "Edit application"}
      open={app !== null}
      onClose={onClose}
      wide
      footer={
        <>
          <button type="button" className="aa-btn aa-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="aa-btn aa-btn-primary" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      <AdminField label="Name" hint="Shown in this list. The URL stays /apps/&lt;slug&gt;/.">
        <input
          id="hosted-app-name"
          name="hosted-app-name"
          type="text"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </AdminField>
      <AdminField label="Description">
        <textarea
          id="hosted-app-description"
          name="hosted-app-description"
          rows={3}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </AdminField>
      <label className="aa-hosted-checkbox aa-muted">
        <input
          id="hosted-app-autostart"
          name="hosted-app-autostart"
          type="checkbox"
          checked={autostart}
          onChange={(event) => setAutostart(event.target.checked)}
        />{" "}
        Start automatically when the platform boots
      </label>
      <p className="aa-muted" style={{ marginTop: 0 }}>
        An app that was running is always brought back by a restart. This setting decides the rest:
        with it on, the next boot starts the app even if it is stopped now; with it off, an app you
        stopped stays stopped.
      </p>
      <AdminField
        label="Workspace"
        hint="Where this application lives on disk: its code, virtualenv, install log and run files."
      >
        <input
          id="hosted-app-workspace"
          name="hosted-app-workspace"
          type="text"
          readOnly
          value={app?.workspace || ""}
          onFocus={(event) => event.target.select()}
        />
      </AdminField>
      <h4 className="aa-hosted-log-heading">Content policy</h4>
      <p className="aa-muted" style={{ marginTop: 0 }}>
        What this application may send back to the browser. An empty list allows everything. This is a
        policy control, not a security boundary: the proxy can only judge what an app declares. What
        protects data is the isolation tier and the access list.
      </p>

      <AdminField
        label="Allowed content types"
        hint="Sub-resources matter too: allowing text/html but not text/css, application/javascript and the image types leaves the page unstyled and its scripts unrun."
      >
        <div className="aa-hosted-policy-add">
          <input
            id="hosted-app-content-type"
            name="hosted-app-content-type"
            type="text"
            placeholder="application/pdf"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addType(draft);
              }
            }}
          />
          <button type="button" className="aa-btn aa-btn-ghost" onClick={() => addType(draft)} disabled={!draft.trim()}>
            Add
          </button>
        </div>
      </AdminField>

      <div className="aa-hosted-policy-chips">
        {allowed.length === 0 ? (
          <span className="aa-muted">No restriction: every content type is allowed.</span>
        ) : (
          allowed.map((type) => (
            <span className="aa-hosted-chip" key={type}>
              {type}
              <button
                type="button"
                aria-label={`Remove ${type}`}
                onClick={() => setAllowed((current) => current.filter((item) => item !== type))}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>

      <div className="aa-hosted-policy-actions">
        <button
          type="button"
          className="aa-btn aa-btn-ghost"
          onClick={() => replaceAllowed(recommended)}
          disabled={recommended.length === 0}
        >
          Use the recommended set
        </button>
        <button
          type="button"
          className="aa-btn aa-btn-ghost"
          onClick={() => replaceAllowed([])}
          disabled={!allowed.length}
        >
          Clear
        </button>
      </div>

      <AdminField
        label="Payload limits"
        hint="Cap how much this application may send back of a given type. A response over its cap is refused with 413 before any of it reaches the browser. Leave a row blank for no limit; a streamed type (text/event-stream) should be left unlimited."
      >
        {allowed.length === 0 ? (
          <p className="aa-muted" style={{ margin: 0 }}>
            Add an allowed content type to set a payload limit for it.
          </p>
        ) : (
          <div className="aa-hosted-limits">
            {allowed.map((type) => {
              const draft = limitDrafts[type] || { value: "", unit: "MB" as SizeUnit };
              return (
                <div className="aa-hosted-limit-row" key={type} data-type={type}>
                  <code className="aa-hosted-limit-type">{type}</code>
                  <div className="aa-hosted-limit-input">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      inputMode="decimal"
                      placeholder="No limit"
                      aria-label={`Max response size for ${type}`}
                      value={draft.value}
                      onChange={(event) => setLimit(type, event.target.value, draft.unit)}
                    />
                    <select
                      aria-label={`Unit for ${type}`}
                      value={draft.unit}
                      disabled={!draft.value.trim()}
                      onChange={(event) => setLimit(type, draft.value, event.target.value as SizeUnit)}
                    >
                      <option value="KB">KB</option>
                      <option value="MB">MB</option>
                      <option value="B">B</option>
                    </select>
                  </div>
                </div>
              );
            })}
            <p className="aa-muted" style={{ margin: "8px 0 0" }}>
              A maximum of {Math.floor(maxLimitBytes / (1024 * 1024))} MB each.
            </p>
          </div>
        )}
      </AdminField>

      <label className="aa-hosted-checkbox aa-muted">
        <input
          id="hosted-app-block-attachments"
          name="hosted-app-block-attachments"
          type="checkbox"
          checked={blockAttachments}
          onChange={(event) => setBlockAttachments(event.target.checked)}
        />{" "}
        Block downloads (any response marked <code>Content-Disposition: attachment</code>)
      </label>

      <h4 className="aa-hosted-log-heading">Source IP allowlist</h4>
      <p className="aa-muted" style={{ marginTop: 0 }}>
        Only requests that come from one of these addresses may open this application. Accepts a
        single address, a network range (<code>10.0.0.0/24</code>) or a wildcard (
        <code>192.168.1.*</code>). An empty list allows every address.
      </p>

      <AdminField
        label="Allowed source IPs"
        hint="The platform judges the address the request actually arrived from, not a forwarding header."
      >
        <div className="aa-hosted-policy-add">
          <input
            id="hosted-app-source-ip"
            name="hosted-app-source-ip"
            type="text"
            placeholder="192.168.1.0/24"
            value={ipDraft}
            onChange={(event) => {
              setIpDraft(event.target.value);
              setIpError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addIp(ipDraft);
              }
            }}
          />
          <button type="button" className="aa-btn aa-btn-ghost" onClick={() => addIp(ipDraft)} disabled={!ipDraft.trim()}>
            Add
          </button>
        </div>
      </AdminField>
      {ipError ? <div className="aa-hosted-ip-error">{ipError}</div> : null}

      <div className="aa-hosted-policy-chips">
        {sourceIps.length === 0 ? (
          <span className="aa-muted">No restriction: every source address is allowed.</span>
        ) : (
          sourceIps.map((ip) => (
            <span className="aa-hosted-chip" key={ip} title={ip}>
              {ip}
              <button
                type="button"
                aria-label={`Remove ${ip}`}
                onClick={() => setSourceIps((current) => current.filter((item) => item !== ip))}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>

      {error ? <AdminError message={error} /> : null}
    </AdminModal>
  );
}
