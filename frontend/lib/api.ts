export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const getWs = () => (typeof window === "undefined" ? null : localStorage.getItem("ns_ws"));
export const setWsStored = (id: string) => localStorage.setItem("ns_ws", id);

type Opts = { method?: string; json?: unknown; form?: FormData; headers?: Record<string, string>; raw?: boolean };

export async function api<T = any>(path: string, o: Opts = {}): Promise<T> {
  const headers: Record<string, string> = { ...(o.headers || {}) };
  const ws = getWs();
  if (ws) headers["X-Workspace-Id"] = ws;
  let body: BodyInit | undefined;
  if (o.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(o.json);
  } else if (o.form) body = o.form;
  const res = await fetch(path, { method: o.method || (body ? "POST" : "GET"), headers, body, credentials: "include" });
  if (res.status === 401 && typeof window !== "undefined" && !location.pathname.startsWith("/login") && !location.pathname.startsWith("/register")) {
    location.href = "/login";
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail
        : Array.isArray(j.detail) ? j.detail.map((d: any) => `${(d.loc || []).filter((x: string) => x !== "body").join(".")}: ${String(d.msg).replace(/^Value error, /, "")}`).join("; ")
        : JSON.stringify(j.detail);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  if (o.raw) return res as unknown as T;
  return res.json();
}

export async function download(path: string, filename: string) {
  const res = await api<Response>(path, { raw: true });
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
