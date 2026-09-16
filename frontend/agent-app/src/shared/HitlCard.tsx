import { useMemo, useState } from "react";
import {
  allowedDecisions,
  type HitlDecision,
  type HitlInterrupt,
} from "./hitl";
import { CodeEditor } from "./CodeEditor";

/** One approval pause, rendered from the library's interrupt payload.
 *
 * Shared by the chat surface and the Studio test-run dock so a run looks the
 * same wherever it is reviewed. Decisions are sent back verbatim as
 * `{decisions: [...]}` — the shape `HumanInTheLoopMiddleware` resumes with.
 */
export function HitlCard({
  interrupt,
  busy,
  onDecision,
  variant,
}: {
  interrupt: HitlInterrupt;
  busy?: boolean;
  onDecision: (decisions: HitlDecision[]) => void;
  /** `studio` renders the card with the Studio's own controls (run dock). */
  variant?: "chat" | "studio";
}) {
  const actions = interrupt.action_requests?.length
    ? interrupt.action_requests
    : [{ name: interrupt.type || "review", args: {}, description: interrupt.message }];
  const [editing, setEditing] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<string[]>(() => actions.map((a) => JSON.stringify(a.args || {}, null, 2)));
  const [rejectNote, setRejectNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const decisions = useMemo(() => allowedDecisions(interrupt), [interrupt]);

  function submit(decision: HitlDecision) {
    onDecision([decision]);
  }

  function submitEdit(index: number) {
    let args: Record<string, unknown> = {};
    try {
      args = JSON.parse(drafts[index] || "{}") as Record<string, unknown>;
    } catch {
      args = {};
    }
    onDecision([{ type: "edit", edited_action: { name: actions[index].name, args } }]);
  }

  return (
    <div className={`aa-hitl-card${variant === "studio" ? " aa-hitl-card-studio" : ""}`} data-testid="hitl-card">
      <div className="aa-hitl-head">
        <span className="aa-hitl-badge">Approval required</span>
        {interrupt.plan ? <span className="aa-hitl-plan">{interrupt.plan}</span> : null}
      </div>
      {actions.map((action, index) => (
        <div className="aa-hitl-action" key={`${action.name}-${index}`}>
          <div className="aa-hitl-name">{action.description || action.name}</div>
          <div className="aa-hitl-tool">
            {action.name}
            {interrupt.review_configs?.length ? (
              <span className="aa-hitl-allowed"> · {decisions.join(" / ")}</span>
            ) : null}
          </div>
          {editing === index ? (
            <div className="aa-hitl-editor">
              <CodeEditor
                value={drafts[index]}
                language="json"
                height={160}
                onChange={(value) =>
                  setDrafts((current) => current.map((d, i) => (i === index ? value : d)))
                }
              />
              <div className="aa-hitl-actions">
                <button type="button" className="aa-btn" onClick={() => setEditing(null)} disabled={busy}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="aa-btn primary"
                  data-testid="hitl-submit-edit"
                  onClick={() => submitEdit(index)}
                  disabled={busy}
                >
                  Send edit
                </button>
              </div>
            </div>
          ) : (
            <pre className="aa-hitl-args">{JSON.stringify(action.args || {}, null, 2)}</pre>
          )}
        </div>
      ))}
      {rejecting ? (
        <div className="aa-hitl-reject">
          <input
            autoFocus
            placeholder="Why is this rejected? (optional)"
            value={rejectNote}
            onChange={(event) => setRejectNote(event.target.value)}
          />
          <div className="aa-hitl-actions">
            <button type="button" className="aa-btn" onClick={() => setRejecting(false)} disabled={busy}>
              Back
            </button>
            <button
              type="button"
              className="aa-btn danger"
              data-testid="hitl-confirm-deny"
              disabled={busy}
              onClick={() => submit({ type: "reject", message: rejectNote || undefined })}
            >
              Reject
            </button>
          </div>
        </div>
      ) : (
        <div className="aa-hitl-actions">
          <button
            type="button"
            className="aa-btn primary"
            data-testid="hitl-approve"
            disabled={busy || !decisions.includes("approve")}
            onClick={() => submit({ type: "approve" })}
          >
            Approve
          </button>
          <button
            type="button"
            className="aa-btn"
            data-testid="hitl-edit"
            disabled={busy || !decisions.includes("edit") || editing !== null}
            onClick={() => setEditing(editing === null ? 0 : null)}
          >
            Edit
          </button>
          <button
            type="button"
            className="aa-btn danger"
            data-testid="hitl-deny"
            disabled={busy || !decisions.includes("reject")}
            onClick={() => setRejecting(true)}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
