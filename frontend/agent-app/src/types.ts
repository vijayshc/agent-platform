import type { HitlInterrupt } from "./shared/hitl";

export type AgentKind = "agent" | "workflow";

export interface AgentAccessRole {
  role_id: number;
  role_name: string;
}

export type AgentAccess = AgentAccessRole;

export interface AgentDef {
  id: number;
  slug: string;
  name: string;
  kind: AgentKind;
  description: string;
  /** Blueprint a graph flow was built from (mirrors `config.template`). */
  template?: string | null;
  tags?: string[];
  /** Model the definition pins, if any (design-time field, read-only in v2). */
  model?: { client?: string; name?: string | null } | null;
  published: boolean;
  version?: number;
  /** Only published rows carry counts; drafts do not. */
  tool_count?: number;
  skill_count?: number;
  pattern?: string | null;
  config?: Record<string, unknown>;
  created_by?: number | null;
  updated_by?: number | null;
  created_by_name?: string | null;
  updated_by_name?: string | null;
  created_at?: string;
  updated_at?: string;
  access?: AgentAccess[];
  /** Server verdict: the caller may delete this agent / change its grants. */
  can_manage?: boolean;
}

export interface McpToolDetail {
  name: string;
  description?: string;
  parameters?: Record<string, unknown>;
}

export interface StudioMcpServer {
  id: number;
  name: string;
  description?: string;
  server_type?: string;
  tools: string[];
  tool_details?: McpToolDetail[];
  tools_error?: string | null;
}

export interface LlmModel {
  id: string;
  name: string;
  model_name?: string | null;
  is_default?: boolean;
}

export interface StudioResources {
  mcp_servers: StudioMcpServer[];
  skills: SkillPackage[];
  function_tools: { name: string; description: string }[];
  model_clients?: { id: string; label: string; model_name?: string | null; is_default?: boolean }[];
  plugins: { type_id: string; kind: string; label: string; icon?: string; schema?: Record<string, unknown> }[];
}

export interface SkillPackage {
  name: string;
  description?: string;
  path?: string;
  enabled?: boolean;
  has_skill_md?: boolean;
  instructions?: string;
  scripts?: { filename: string; content: string }[];
  skill_md?: string;
}

export interface ValidateReport {
  ok: boolean;
  errors: { code: string; message: string }[];
  warnings: { code: string; message: string }[];
}

export interface Conversation {
  id: number;
  public_id: string;
  title: string;
  agent_slug?: string | null;
  updated_at?: string;
  created_at?: string;
  messages?: StoredMessage[];
}

export interface StoredMessage {
  id: number;
  conversation_id: number;
  role: string;
  content: string;
  run_id?: number | null;
  run_public_id?: string;
  run_status?: string;
  events?: SseEvent[];
  pending?: SseEvent | Record<string, unknown> | null;
  meta?: {
    attachments?: AttachmentMeta[];
    hitl?: SseEvent;
    pending?: boolean;
    public_id?: string;
    /** Full reasoning captured for this assistant turn (chat reload). */
    reasoning?: string;
  } | null;
}

export interface AttachmentMeta {
  public_id?: string;
  filename: string;
  path?: string;
  content_type?: string;
}

export interface SseEvent extends HitlInterrupt {
  type: string;
  content?: string;
  agent?: string;
  run_id?: number;
  public_id?: string;
  message?: string;
  tool_name?: string;
  call_id?: string;
  arguments?: unknown;
  result?: string;
  reply?: string;
  error?: string;
  status?: string;
  skill?: unknown;
  final?: boolean;
  reasoning_id?: string;
  done?: boolean;
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  agent?: string;
  attachments?: AttachmentMeta[];
  runId?: number | string;
  runPublicId?: string;
  streaming?: boolean;
  events?: SseEvent[];
  /** Live provider reasoning while its model call is still streaming. */
  reasoning?: string;
  reasoningStreaming?: boolean;
  error?: string;
  hitlResolved?: boolean;
  startedAtMs?: number;
  durationMs?: number;
  /** Snapshot of the last intermediate bubble text. Held in-flow until new
   *  content arrives, then overlay-crossfaded out. */
  prevContent?: string;
  /** True while prev and current content are both present and the overlay
   *  crossfade is in progress. Not reset on unrelated SSE events. */
  swapping?: boolean;
  /** The run finished successfully but the model returned no text at all. */
  emptyReply?: boolean;
}

export interface ChatTurn {
  id: string;
  messages: ChatMessage[];
  isLatest: boolean;
}

export interface RunRow {
  id: number;
  public_id: string;
  agent_slug?: string | null;
  task?: string;
  status?: string;
  started_at?: string;
  finished_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
  final_reply?: string | null;
  error?: string | null;
  user_id?: number | null;
  username?: string | null;
  events?: SpanEvent[];
  /** Phoenix correlation ids captured when the run's root span opened. */
  trace_id?: string | null;
  session_id?: string | null;
  root_span_id?: string | null;
  phoenix_project?: string | null;
}

export interface SpanEvent {
  /** Present on persisted spans; the UI keys rows by `span_id` and falls back. */
  id?: number;
  run_id: number;
  ts?: string;
  event_type: string;
  source?: string;
  agent_name?: string | null;
  tool_name?: string | null;
  span_name?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  duration_ms?: number | null;
  detail?: Record<string, unknown> | string | null;
  prompt?: unknown;
  response?: unknown;
  arguments?: unknown;
  result?: unknown;
  model?: string | null;
}

/** A normalized chat turn extracted from a span's input or output payload. */
export interface TraceTurn {
  role: string;
  text: string;
  tool_calls?: { name: string; args?: unknown }[];
  reasoning?: string;
}

export interface TraceSpanIO {
  mime_type: string;
  value: string;
  turns: TraceTurn[];
}

export interface TraceSpanEvent {
  name: string;
  message: string;
  timestamp?: string;
  attributes?: Record<string, unknown>;
}

/** One span of a run's Phoenix trace, as served by GET /api/v1/runs/<id>/trace. */
export interface TraceSpan {
  id: string;
  parent_id?: string | null;
  name: string;
  /** AGENT | CHAIN | LLM | TOOL | RETRIEVER | EMBEDDING | RERANKER | ... */
  kind: string;
  start_time?: string | null;
  end_time?: string | null;
  duration_ms?: number | null;
  status?: string;
  error?: boolean;
  status_message?: string;
  model?: string | null;
  tool_name?: string | null;
  tool_description?: string | null;
  agent_name?: string | null;
  node?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  /** Whether the span has payloads (set on lightweight waterfall rows). */
  has_io?: boolean;
  input?: TraceSpanIO | null;
  output?: TraceSpanIO | null;
  attributes?: Record<string, unknown>;
  events?: TraceSpanEvent[];
}

export interface TraceSummary {
  span_count: number;
  error_count: number;
  duration_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost?: number | null;
  models: string[];
  kinds: Record<string, number>;
  llm_calls: number;
  tool_calls: number;
  slowest_span_ms?: number | null;
  start_time?: string | null;
  end_time?: string | null;
  session_id?: string | null;
  root_span_id?: string | null;
  root_span_name?: string | null;
}

export interface RunTrace {
  available: boolean;
  reason?: string | null;
  message?: string | null;
  trace_id?: string | null;
  session_id?: string | null;
  project?: string | null;
  resolved_from?: string | null;
  summary?: TraceSummary;
  spans: TraceSpan[];
}

/** One span's full payloads, fetched on selection (see GET .../trace/spans/<id>). */
export interface RunSpanDetail {
  available: boolean;
  reason?: string | null;
  message?: string | null;
  trace_id?: string | null;
  project?: string | null;
  span: TraceSpan | null;
}

export interface EvalItem {
  prompt: string;
  keyword?: string;
  expected_tool_calls?: { name: string; arguments?: Record<string, unknown> }[];
}

export interface EvalDefinition {
  id: number;
  definition_id: number;
  name: string;
  items: EvalItem[];
  created_at?: string;
}

export interface EvalCheck {
  name: string;
  score: number;
  passed: boolean;
  reason?: string | null;
}

export interface EvalResultRow {
  id: number;
  item_index: number;
  passed: boolean;
  score: number;
  status: string;
  output?: string | null;
  error?: string | null;
  checks: EvalCheck[];
  run_public_id?: string | null;
  trace_id?: string | null;
  created_at?: string;
}

export interface EvalRunOutcome {
  eval_id?: number;
  name?: string;
  summary: { passed: number; failed: number; errored: number; score: number };
  results: {
    item_index: number;
    prompt: string;
    passed: boolean;
    score: number;
    status: string;
    output?: string | null;
    error?: string | null;
    checks: EvalCheck[];
    run_public_id?: string | null;
    link?: string;
  }[];
}
