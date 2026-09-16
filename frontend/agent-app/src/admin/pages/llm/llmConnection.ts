/** Types and form <-> payload translation for the admin LLM Manager. */

export interface LlmConnection {
  id: number;
  name: string;
  base_url: string;
  api_key?: string;
  api_key_masked: boolean;
  model_name: string;
  system_instruction?: string;
  /** Every provider argument beyond endpoint/key/model, forwarded as extra_body. */
  extra_body?: Record<string, unknown>;
  http_headers?: Record<string, unknown>;
  verify_ssl?: boolean;
  is_default: boolean;
  enabled: boolean;
  /** Owning user; null marks a deployment-level (shared) connection. */
  created_by?: number | null;
  /** Server-computed: the caller is the owner or an administrator. */
  can_manage?: boolean;
  /** Server-computed: the caller is an administrator and may move the global default. */
  can_set_default?: boolean;
}

export interface LlmListResponse {
  status: string;
  data?: LlmConnection[];
  /** True only for administrators (same for every row, and present with no rows). */
  can_set_default?: boolean;
}

export interface LlmActionResponse {
  status: string;
  message?: string;
  id?: number;
}

export interface LlmTestResponse {
  status: string;
  message?: string;
  reply?: string;
  /** Endpoint and model the app really called, echoed by the backend. */
  endpoint?: string | null;
  model?: string | null;
  [key: string]: unknown;
}

/** One row of the free-form HTTP header editor. */
export interface HeaderRow {
  name: string;
  value: string;
}

export interface LlmForm {
  id?: number;
  name: string;
  base_url: string;
  model_name: string;
  api_key: string;
  system_instruction: string;
  /** Every model/provider argument, edited as one free-form JSON object. */
  extraBodyText: string;
  headers: HeaderRow[];
  verify_ssl: boolean;
  enabled: boolean;
  is_default: boolean;
}

export const EMPTY_FORM: LlmForm = {
  name: "",
  base_url: "",
  model_name: "",
  api_key: "",
  system_instruction: "",
  extraBodyText: "",
  headers: [],
  verify_ssl: true,
  enabled: true,
  is_default: false,
};

/** Starting point offered to an operator creating their first connection. */
export const EXTRA_BODY_EXAMPLE = `{
  "temperature": 1.0,
  "max_tokens": 81920,
  "top_p": 0.95,
  "presence_penalty": 0.0,
  "top_k": 20,
  "chat_template_kwargs": { "enable_thinking": false }
}`;

/** Request-body keys the app sets per call, so a connection must not own them. */
const RESERVED_PARAM_KEYS = ["model", "messages", "stream", "extra_body"];

export function toForm(c: LlmConnection): LlmForm {
  const extra = c.extra_body && Object.keys(c.extra_body).length > 0 ? JSON.stringify(c.extra_body, null, 2) : "";
  return {
    id: c.id,
    name: c.name || "",
    base_url: c.base_url || "",
    model_name: c.model_name || "",
    api_key: "",
    system_instruction: c.system_instruction || "",
    extraBodyText: extra,
    headers: Object.entries(c.http_headers || {}).map(([name, value]) => ({
      name,
      value: value === null || value === undefined ? "" : String(value),
    })),
    verify_ssl: c.verify_ssl !== false,
    enabled: c.enabled,
    is_default: c.is_default,
  };
}

/** Parse the model-parameters editor into the object sent to the API. */
export function parseExtraBody(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    throw new Error(`Model parameters must be valid JSON: ${e instanceof Error ? e.message : String(e)}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error('Model parameters must be a JSON object, e.g. {"temperature": 0.7, "top_k": 20}.');
  }
  const params = parsed as Record<string, unknown>;
  const reserved = RESERVED_PARAM_KEYS.filter((key) => key in params);
  if (reserved.length > 0) {
    throw new Error(
      `Model parameters cannot set ${reserved.join(", ")} — the app manages them per call. ` +
        "Put the contents of extra_body directly in this object instead.",
    );
  }
  return params;
}

/** Collapse header rows into the object the API stores (blank names dropped). */
export function headersToObject(rows: HeaderRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const name = row.name.trim();
    if (!name) continue;
    out[name] = row.value.trim();
  }
  return out;
}

export function validateForm(form: LlmForm): string | null {
  if (!form.name.trim()) return "Name is required.";
  if (!form.base_url.trim()) return "Base URL is required.";
  if (!form.model_name.trim()) return "Model is required.";
  const duplicate = firstDuplicateHeader(form.headers);
  if (duplicate) return `Duplicate header "${duplicate}" — keep one row per header name.`;
  return null;
}

function firstDuplicateHeader(rows: HeaderRow[]): string | null {
  const seen = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim().toLowerCase();
    if (!name) continue;
    if (seen.has(name)) return row.name.trim();
    seen.add(name);
  }
  return null;
}

/** Build the save payload; masked secrets are re-sent untouched. */
export function formToPayload(form: LlmForm): Record<string, unknown> {
  return {
    ...(form.id ? { id: form.id } : {}),
    name: form.name.trim(),
    base_url: form.base_url.trim(),
    model_name: form.model_name.trim(),
    system_instruction: form.system_instruction.trim() || undefined,
    api_key: form.api_key.trim() || undefined,
    extra_body: parseExtraBody(form.extraBodyText),
    http_headers: headersToObject(form.headers),
    verify_ssl: form.verify_ssl,
    enabled: form.enabled,
    is_default: form.is_default,
  };
}
