async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    // The human-readable detail wins; the machine `error` code is the fallback.
    return body.message || body.error || res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", signal });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function apiPostJson<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function apiPutJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: "DELETE", credentials: "same-origin" });
  if (!res.ok) throw new Error(await readError(res));
  return res.json() as Promise<T>;
}

export async function apiPostStream(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw new Error(await readError(res));
  return res;
}

export async function uploadAttachment(
  file: File,
  conversationId?: string | number,
): Promise<{ public_id: string; filename: string; path: string; content_type?: string }> {
  const fd = new FormData();
  fd.append("file", file);
  if (conversationId != null) fd.append("conversation_id", String(conversationId));
  const res = await fetch("/api/v1/attachments", {
    method: "POST",
    credentials: "same-origin",
    body: fd,
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}
