import { useEffect, useState, type ReactNode } from "react";
import type { ChatMessage, SseEvent } from "../types";
import type { HitlDecision } from "../shared/hitl";
import { HitlCard } from "../shared/HitlCard";
import "../shared/hitl.css";
import { ReasoningTicker } from "../shared/ReasoningTicker";
import { BubbleBody } from "./BubbleBody";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { activityDuration, activityItems, formatDuration, formatIo, isMeaningfulAgentName, pendingHitl } from "./chatLogic";

const ACTIVITY_PATHS: Record<string, ReactNode> = {
  bulb: (
    <>
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 0 1 6 6c0 2-1 3-2 4s-1 2-1 3h-6c0-1 0-2-1-3s-2-2-2-4a6 6 0 0 1 6-6z" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.35-4.35" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="M21 16l-5-5-5 6" />
    </>
  ),
  book: (
    <>
      <path d="M4 5a2 2 0 0 1 2-2h14v18H6a2 2 0 0 1-2-2z" />
      <path d="M4 17a2 2 0 0 1 2-2h14" />
      <path d="M9 7h6" />
    </>
  ),
  file: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M8 9l3 3-3 3" />
      <path d="M13 15h4" />
    </>
  ),
  db: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>
  ),
  tool: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2.1-2.1z" />,
};

function ActivityIcon({ name, running }: { name: string; running?: boolean }) {
  if (running) {
    return (
      <svg
        className="aa-activity-ico aa-ico-spin"
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      >
        <path d="M12 3a9 9 0 1 0 9 9" />
      </svg>
    );
  }
  return (
    <svg
      className="aa-activity-ico"
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {ACTIVITY_PATHS[name] || ACTIVITY_PATHS.tool}
    </svg>
  );
}

export function MessageBubble({
  msg,
  onDecide,
  streaming,
  agentName,
  onSettle,
  turnStartRef,
}: {
  msg: ChatMessage;
  onDecide: (ev: SseEvent, decisions: HitlDecision[]) => void;
  streaming: boolean;
  agentName?: string;
  onSettle?: (id: string) => void;
  turnStartRef?: React.Ref<HTMLDivElement>;
}) {
  const hitl = msg.role === "assistant" && !msg.hitlResolved ? pendingHitl(msg.events) : null;
  const items = activityItems(msg.events, msg.streaming);
  const isLive = msg.streaming && msg.startedAtMs != null && msg.durationMs == null;
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!isLive) return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [isLive]);
  const dur = activityDuration(msg.events, msg.startedAtMs, msg.streaming, msg.durationMs);
  const activityHeader =
    dur == null ? "Worked" : msg.streaming ? `Working for ${formatDuration(dur)}` : `Worked for ${formatDuration(dur)}`;
  const speakerName = isMeaningfulAgentName(msg.agent) ? msg.agent : agentName;
  const showSpeaker = msg.role === "assistant" && Boolean(speakerName);
  // While the provider is still streaming reasoning, the thinking ticker owns
  // the space under the activity counter. The reply bubble stays out of the
  // way until reasoning settles, then the streamed/previous content takes over.
  const isReasoningLive = Boolean(msg.reasoning && msg.reasoningStreaming);

  return (
    <div ref={turnStartRef} className={`aa-bubble ${msg.role}`} data-testid={`${msg.role}-bubble`}>
      {showSpeaker && (
        <div className="aa-speaker">
          <span className="aa-speaker-name">{speakerName}</span>
        </div>
      )}
      {msg.attachments && msg.attachments.length > 0 && (
        <div className="aa-chips">
          {msg.attachments.map((a) => (
            <span className="aa-chip" data-testid="file-chip" key={a.public_id || a.filename}>
              {a.filename}
            </span>
          ))}
        </div>
      )}
      {items.length === 0 && !msg.streaming && (msg.runId != null || msg.durationMs != null) && msg.role === "assistant" ? (
        // A run whose span log is gone (the sink is in memory and the app was
        // restarted) says so instead of showing an empty step list.
        <p className="aa-muted" data-testid="activity-unavailable">
          Step details are not available for this run — it started before the last app restart.
        </p>
      ) : null}
      {items.length > 0 && (
        <details className="aa-activity" data-testid="activity-summary">
          <summary className="aa-activity-toggle" data-testid="activity-toggle">
            <span className="aa-activity-title">{activityHeader}</span>
            <span className="aa-activity-chevron" aria-hidden="true">
              ⌄
            </span>
          </summary>
          <ol className="aa-activity-steps">
            {items.map((item, i) => {
              const head = (
                <span className="aa-activity-step-head">
                  <span className="aa-activity-icon">
                    <ActivityIcon name={item.icon} running={item.status === "running"} />
                  </span>
                  <span className="aa-activity-label">{item.label}</span>
                </span>
              );
              const expandable =
                item.testid === "reasoning-card" || item.arguments != null || item.result != null;
              return (
                <li key={i} className="aa-activity-step" data-testid={item.testid} title={item.detail}>
                  {expandable ? (
                    <details className="aa-activity-details">
                      <summary className="aa-activity-step-head">
                        <span className="aa-activity-icon">
                          <ActivityIcon name={item.icon} running={item.status === "running"} />
                        </span>
                        <span className="aa-activity-label">{item.label}</span>
                      </summary>
                      <div className="aa-activity-detail">
                        {item.testid === "reasoning-card" && item.detail ? (
                          <div className="aa-activity-detail-block">
                            <div className="aa-activity-detail-label">Thinking</div>
                            <pre className="aa-activity-detail-body">{item.detail}</pre>
                          </div>
                        ) : null}
                        {item.arguments != null && (
                          <div className="aa-activity-detail-block">
                            <div className="aa-activity-detail-label">Arguments</div>
                            <pre className="aa-activity-detail-body">{formatIo(item.arguments)}</pre>
                          </div>
                        )}
                        {item.result != null && (
                          <div className="aa-activity-detail-block">
                            <div className="aa-activity-detail-label">Response</div>
                            <pre className="aa-activity-detail-body">{formatIo(item.result)}</pre>
                          </div>
                        )}
                      </div>
                    </details>
                  ) : item.testid === "assistant-line" && item.detail ? (
                    <span className="aa-activity-step-head">
                      <span className="aa-activity-icon">
                        <ActivityIcon name={item.icon} running={item.status === "running"} />
                      </span>
                      <MarkdownRenderer content={item.detail} />
                    </span>
                  ) : (
                    head
                  )}
                </li>
              );
            })}
          </ol>
        </details>
      )}
      {isReasoningLive ? (
        <ReasoningTicker text={msg.reasoning!} />
      ) : (
        (msg.content || msg.prevContent || msg.streaming) &&
        (msg.role === "assistant" ? (
          <BubbleBody
            content={msg.content}
            prevContent={msg.prevContent}
            streaming={msg.streaming}
            plain={msg.contentUnvalidated}
            toolData={msg.toolData}
            onSettle={() => onSettle?.(msg.id)}
          />
        ) : (
          <div className="aa-bubble-body">{msg.content}</div>
        ))
      )}
      {hitl && <HitlCard interrupt={hitl} busy={streaming} onDecision={(decisions) => onDecide(hitl, decisions)} />}
      {msg.error && <div className="aa-error">{msg.error}</div>}
      {msg.emptyReply && !msg.content ? (
        <div className="aa-empty-reply" data-testid="empty-reply">
          The model returned no text for this turn. Run again, or raise this agent&apos;s
          output limit (max tokens) if it keeps happening.
        </div>
      ) : null}
    </div>
  );
}
