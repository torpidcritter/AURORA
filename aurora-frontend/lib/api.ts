/** Typed client for the Aurora API, always called through the same-origin BFF proxy (`/api/proxy/*`). */

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown = null,
    public requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  form?: FormData;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  const e = body?.error;
  return new ApiError(
    res.status,
    e?.code ?? `http_${res.status}`,
    e?.message ?? (res.status >= 500 ? "The server had a problem. Please try again." : `Request failed (${res.status})`),
    e?.details ?? null,
    e?.request_id ?? res.headers.get("x-request-id"),
  );
}

export async function api<T>(path: string, opts: RequestOptions = {}, retried = false): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { accept: "application/json", ...(opts.headers ?? {}) };
  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form;
  } else if (opts.body !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (method !== "GET") headers["x-aurora-csrf"] = "1";
  const res = await fetch(`/api/proxy${path.startsWith("/") ? path : `/${path}`}`, {
    method,
    headers,
    body,
    signal: opts.signal,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  const err = await parseError(res);
  if (err.status === 401) {
    if (err.code === "refresh_superseded" && !retried) return api<T>(path, opts, true);
    onUnauthorized?.();
  }
  throw err;
}

export const fetcher = <T,>(path: string): Promise<T> => api<T>(path);

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "validation_error" && Array.isArray(e.details) && e.details.length) {
      const first = e.details[0] as { loc?: unknown[]; msg?: string } | string;
      if (typeof first === "string") return `${e.message}: ${first}`;
      const field = Array.isArray(first.loc) ? first.loc.filter((x) => x !== "body").join(".") : "";
      return field ? `${field}: ${first.msg}` : (first.msg ?? e.message);
    }
    return e.message;
  }
  return e instanceof Error ? e.message : "Something went wrong";
}

/** Fetch a file from the API (for example an export) and return it with the server's file name. */
export async function apiDownload(path: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`/api/proxy${path.startsWith("/") ? path : `/${path}`}`, { credentials: "same-origin", cache: "no-store" });
  if (!res.ok) {
    const err = await parseError(res);
    if (err.status === 401) onUnauthorized?.();
    throw err;
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "download";
  return { blob: await res.blob(), filename };
}

/** Hand a downloaded file to the browser's save dialog. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
