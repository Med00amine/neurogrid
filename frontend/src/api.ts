// Tiny typed fetch wrapper. The session cookie is HttpOnly (JavaScript cannot read it); the CSRF token is
// kept in memory only and sent on every state-changing request.

let csrfToken: string | null = null;
export const setCsrf = (t: string | null) => { csrfToken = t; };

export class ApiError extends Error {
  status: number;
  fields: { field: string; message: string }[];
  constructor(status: number, message: string, fields: { field: string; message: string }[] = []) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

function detailToMessage(detail: unknown): { message: string; fields: { field: string; message: string }[] } {
  if (typeof detail === "string") return { message: detail, fields: [] };
  if (Array.isArray(detail)) {
    const fields = detail.map((d: any) => ({ field: String(d.field ?? ""), message: String(d.message ?? d.msg ?? "invalid") }));
    return { message: fields.map((f) => (f.field ? `${f.field}: ${f.message}` : f.message)).join("; "), fields };
  }
  return { message: "Something went wrong", fields: [] };
}

export async function request<T = any>(method: string, url: string, body?: unknown, form?: FormData): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let payload: BodyInit | undefined;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  let res: Response;
  try {
    res = await fetch(url, { method, headers, body: payload, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Is it running?");
  }
  if (res.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new Event("auth:expired"));
  const ctype = res.headers.get("content-type") || "";
  if (!res.ok) {
    let detail: unknown = res.statusText;
    if (ctype.includes("json")) { try { detail = (await res.json()).detail; } catch { /* ignore */ } }
    const { message, fields } = detailToMessage(detail);
    throw new ApiError(res.status, message, fields);
  }
  if (res.status === 204) return undefined as T;
  return (ctype.includes("json") ? res.json() : (res.text() as unknown)) as Promise<T>;
}

export const get = <T = any>(url: string) => request<T>("GET", url);
export const post = <T = any>(url: string, body?: unknown) => request<T>("POST", url, body ?? {});
export const put = <T = any>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {});
export const patch = <T = any>(url: string, body?: unknown) => request<T>("PATCH", url, body ?? {});
export const del = <T = any>(url: string) => request<T>("DELETE", url);
export const upload = <T = any>(url: string, form: FormData) => request<T>("POST", url, undefined, form);

export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function download(url: string, filename: string) {
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new ApiError(res.status, "Download failed");
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
