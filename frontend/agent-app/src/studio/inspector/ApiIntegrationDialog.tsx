import { useEffect, useMemo, useRef, useState } from "react";
import { CodeEditor } from "../../shared/CodeEditor";
import type { AgentDef } from "../../types";
import { buildSamples, SAMPLE_TABS, type SampleTab } from "./apiSamples";

interface Props {
  agent: AgentDef;
  onClose: () => void;
}

const NO_CHANGE = (): void => undefined;

function CopyButton({ value, label }: { value: string; label: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setState("ok");
    } catch {
      setState("fail");
    }
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setState("idle"), 1600);
  }

  return (
    <button
      type="button"
      className="as-btn as-btn-ghost"
      aria-live="polite"
      onClick={() => void copy()}
    >
      {state === "ok" ? "✓ Copied" : state === "fail" ? "Copy failed" : label}
    </button>
  );
}

/**
 * Shows how to call one agent over the public HTTP API. The parent mounts it
 * only while the dialog is open, so closing is the single exit path.
 */
export function ApiIntegrationDialog({ agent, onClose }: Props) {
  const [tab, setTab] = useState<SampleTab>("python");
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const origin = useMemo(() => window.location.origin, []);
  const loginUrl = `${origin}/login`;
  const runsUrl = `${origin}/api/v1/runs`;
  const agentUrl = `${origin}/api/v1/agents/${encodeURIComponent(agent.slug)}`;
  const invokeUrl = `${agentUrl}/invoke`;
  const resumeUrl = `${runsUrl}/{run_id}/resume`;
  const samples = useMemo(
    () => buildSamples({ baseUrl: origin, loginUrl, runsUrl, invokeUrl }, agent.slug),
    [origin, loginUrl, runsUrl, invokeUrl, agent.slug],
  );
  const active = SAMPLE_TABS.find((entry) => entry.id === tab) ?? SAMPLE_TABS[0];

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="as-overlay"
      data-testid="api-integration-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="as-dialog as-dialog-api"
        data-testid="api-integration-card"
        role="dialog"
        aria-modal="true"
        aria-label="API integration"
        ref={dialogRef}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="as-dialog-head">
          <div>
            <div className="as-dialog-title">API integration</div>
            <div className="as-dialog-sub">
              {agent.name} · {agent.published ? "Published" : "Draft"}
              {agent.version != null ? ` v${agent.version}` : ""}
            </div>
          </div>
          <button
            type="button"
            className="as-btn-icon as-dialog-close"
            aria-label="Close API integration dialog"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="as-dialog-body">
          <div className="as-api-grid">
            <div className="as-col as-api-info">
              <div className="as-row">
                <span className="as-muted">Agent id</span>
                <span className="as-chip" data-testid="api-integration-slug">
                  {agent.slug}
                </span>
              </div>

              <div className="as-list">
                <div className="as-list-row">
                  <div className="as-list-main">
                    <div className="as-list-title">
                      <span className="as-api-method">POST</span>
                      {loginUrl}
                    </div>
                    <div className="as-list-sub">
                      Get a Bearer token with your username and password. Body{" "}
                      {'{"username": "<user>", "password": "<pass>"}'} → access_token, a JWT valid
                      for 12 hours.
                    </div>
                  </div>
                  <div className="as-list-actions">
                    <CopyButton value={loginUrl} label="Copy URL" />
                  </div>
                </div>

                <div className="as-list-row">
                  <div className="as-list-main">
                    <div className="as-list-title">
                      <span className="as-api-method">POST</span>
                      {runsUrl}
                    </div>
                    <div className="as-list-sub">
                      Run {agent.slug} with Authorization: Bearer &lt;token&gt;. Body{" "}
                      {`{"agent_id": "${agent.slug}", "input": "<text>", "stream": true}`} → a
                      Server-Sent Events stream.
                    </div>
                  </div>
                  <div className="as-list-actions">
                    <CopyButton value={runsUrl} label="Copy URL" />
                  </div>
                </div>

                <div className="as-list-row">
                  <div className="as-list-main">
                    <div className="as-list-title">
                      <span className="as-api-method">POST</span>
                      {invokeUrl}
                    </div>
                    <div className="as-list-sub">
                      Non-streamed run. Body {'{"input": "<text>"}'} → one JSON reply{" "}
                      {'{"run", "events", "reply", "status"}'}. Published agents only.
                    </div>
                  </div>
                  <div className="as-list-actions">
                    <CopyButton value={invokeUrl} label="Copy URL" />
                  </div>
                </div>

                <div className="as-list-row">
                  <div className="as-list-main">
                    <div className="as-list-title">
                      <span className="as-api-method">POST</span>
                      {resumeUrl}
                    </div>
                    <div className="as-list-sub">
                      Answer a pending pause. Body {'{"decisions": [{"type": "approve"}]}'} — one
                      decision per action request.
                    </div>
                  </div>
                  <div className="as-list-actions">
                    <CopyButton value={resumeUrl} label="Copy URL" />
                  </div>
                </div>
              </div>

              <p className="as-api-note">
                Post your platform credentials to <b>POST /login</b> to mint a Bearer JWT, then send
                it as <b>Authorization: Bearer &lt;token&gt;</b>. The token identifies you, so this
                agent&apos;s role-based access is enforced on every request. Add{" "}
                <b>&quot;stream&quot;: true</b> to POST /api/v1/runs for Server-Sent Events, or call
                POST /api/v1/agents/{agent.slug}/invoke for a single JSON reply. When the agent
                pauses on an approval_request, resume it at POST
                /api/v1/runs/&#123;run_id&#125;/resume with one decision per action_requests entry.
              </p>
            </div>

            <div className="as-col as-api-code">
              <div className="as-tabs" role="tablist" aria-label="Request samples">
                {SAMPLE_TABS.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === entry.id}
                    className={`as-tab${tab === entry.id ? " is-active" : ""}`}
                    data-testid={`api-sample-${entry.id}`}
                    onClick={() => setTab(entry.id)}
                  >
                    {entry.label}
                  </button>
                ))}
              </div>

              <div className="as-col" role="tabpanel" aria-label={`${active.label} sample`}>
                <CodeEditor
                  key={active.id}
                  value={samples[active.id]}
                  onChange={NO_CHANGE}
                  language={active.language}
                  filename={active.filename}
                  label={`${active.label} example for ${agent.slug}`}
                  readOnly
                  height="min(68vh, 700px)"
                  testId={`api-sample-${active.id}-code`}
                />
                <div className="as-row">
                  <CopyButton value={samples[active.id]} label="Copy snippet" />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="as-dialog-foot">
          <button type="button" className="as-btn as-btn-primary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
