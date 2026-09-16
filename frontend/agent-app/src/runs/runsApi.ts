/** Runs API client.
 *
 * The built-in runs API is the tenant-scoped observability surface: the server
 * returns only the runs the caller may see.  These helpers surface the HTTP
 * status so a deep link to a run the caller cannot see renders a clean
 * "not available" state instead of throwing into a blank page.
 */
import type { RunRow, RunSpanDetail, RunTrace, SpanEvent } from "../types";

export interface RunDetail extends RunRow {
  events?: SpanEvent[];
  spans?: SpanEvent[];
  workspace_files?: string[];
}

export interface ApiResult<T> {
  status: number;
  data: T | null;
  error: string | null;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: string; message?: string };
    return body.error || body.message || res.statusText;
  } catch {
    return res.statusText;
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<ApiResult<T>> {
  try {
    const res = await fetch(path, { credentials: "same-origin", signal });
    if (!res.ok) return { status: res.status, data: null, error: await readError(res) };
    return { status: res.status, data: (await res.json()) as T, error: null };
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    return { status: 0, data: null, error: err instanceof Error ? err.message : "request failed" };
  }
}

export function fetchRuns(limit = 50, signal?: AbortSignal): Promise<ApiResult<{ runs: RunRow[]; total?: number }>> {
  return request<{ runs: RunRow[]; total?: number }>(`/api/v1/runs?limit=${encodeURIComponent(String(limit))}`, signal);
}

export function fetchRun(runId: string, signal?: AbortSignal): Promise<ApiResult<RunDetail>> {
  return request<RunDetail>(`/api/v1/runs/${encodeURIComponent(runId)}`, signal);
}

/** The run's interactions, read live from Phoenix (never from a local table). */
export function fetchRunTrace(runId: string, signal?: AbortSignal): Promise<ApiResult<RunTrace>> {
  return request<RunTrace>(`/api/v1/runs/${encodeURIComponent(runId)}/trace`, signal);
}

/** One span's payloads, fetched only when the inspector selects it. */
export function fetchRunSpanDetail(
  runId: string,
  spanId: string,
  signal?: AbortSignal,
): Promise<ApiResult<RunSpanDetail>> {
  return request<RunSpanDetail>(
    `/api/v1/runs/${encodeURIComponent(runId)}/trace/spans/${encodeURIComponent(spanId)}`,
    signal,
  );
}

/** Whether the caller may use the raw Phoenix proxy (administrators only). */
export async function phoenixProxyAvailable(): Promise<boolean> {
  try {
    const res = await fetch("/api/v1/phoenix/proxy/", { method: "HEAD", credentials: "same-origin" });
    return res.ok;
  } catch {
    return false;
  }
}
