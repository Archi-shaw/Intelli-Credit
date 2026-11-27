/**
 * API client. All requests go to the same origin (/api/v1/...) and are proxied to FastAPI by Next.js,
 * so the httpOnly session cookie is first-party. Mutations carry the `x-ic-client` header that the
 * backend requires for cookie-authenticated requests (CSRF defence).
 */
export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const BASE = "/api/v1";

async function request<T>(method: string, path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "x-ic-client": "web", accept: "application/json" };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["content-type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { method, headers, body: payload, credentials: "same-origin", cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, "network_error", "Unable to reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = undefined;
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
      window.dispatchEvent(new CustomEvent("ic:unauthorized"));
    }
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `Request failed (${res.status})`, err?.details ?? {});
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};

/** URL for authorised file downloads (session cookie is sent automatically on same-origin GETs). */
export const fileUrl = (path: string) => `${BASE}${path}`;

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}
