/** Human-in-the-loop payloads.
 *
 * The runtime uses LangChain's `HumanInTheLoopMiddleware` interrupt contract:
 * an interrupt carries `action_requests` (what the agent wants to do) and
 * `review_configs` (which decisions a reviewer may take). A resume sends
 * `{decisions: [...]}` back. Nothing here is app-specific — the shapes are the
 * library's.
 */

export interface HitlAction {
  name: string;
  args?: Record<string, unknown>;
  description?: string;
}

export interface HitlReviewConfig {
  action_name: string;
  allowed_decisions?: string[];
}

export interface HitlInterrupt {
  action_requests?: HitlAction[];
  review_configs?: HitlReviewConfig[];
  /** Legacy/simple interrupts (plan review, single question) may carry these. */
  type?: string;
  message?: string;
  plan?: string;
}

export type HitlDecision =
  | { type: "approve" }
  | { type: "reject"; message?: string }
  | { type: "edit"; edited_action: { name: string; args: Record<string, unknown> } };

export const DEFAULT_DECISIONS = ["approve", "edit", "reject"];

export function allowedDecisions(
  interrupt: HitlInterrupt | null | undefined,
  actionName?: string,
): string[] {
  const configs = interrupt?.review_configs || [];
  const match = actionName
    ? configs.find((c) => c.action_name === actionName)
    : configs[0];
  const allowed = match?.allowed_decisions;
  return allowed && allowed.length ? allowed : DEFAULT_DECISIONS;
}
