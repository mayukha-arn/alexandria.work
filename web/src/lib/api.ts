// Thin typed client for the Alexandria API. The access token lives in memory (and sessionStorage so a
// reload keeps you signed in); it is never put in a URL.

/** Where the API lives. Read at run time from /config.js (so a deployed site can be re-pointed without a
 *  rebuild), falling back to the build-time NEXT_PUBLIC_API_URL, then to localhost. */
export const apiBase = (): string => {
  const w = typeof window !== "undefined" ? (window as unknown as { ALEXANDRIA_CONFIG?: { apiUrl?: string } }) : undefined;
  return (w?.ALEXANDRIA_CONFIG?.apiUrl || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8088").replace(/\/+$/, "");
};

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

let token: string | null = null;
let onUnauthorized: () => void = () => {};

export const setToken = (t: string | null) => {
  token = t;
};
export const getToken = () => token;
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

type Opts = { method?: string; body?: unknown; form?: FormData; token?: string | null; quiet401?: boolean };

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const t = opts.token === undefined ? token : opts.token;
  if (t) headers.Authorization = `Bearer ${t}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  } catch {
    throw new ApiError(0, "Cannot reach the Alexandria server. Is it running?");
  }
  if (res.status === 401 && !opts.quiet401 && token) onUnauthorized();
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const d = (data as { detail?: unknown } | null)?.detail;
    const detail = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x?.msg ?? String(x)).join("; ") : `Request failed (${res.status})`;
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

// ---------------------------------------------------------------------------- types
export type Me = {
  id: string; username: string; role: string; label: string; department: string; level: string;
  persona: string; clearance: number; manager_id: string | null; wallet_pubkey: string | null;
  totp_enrolled: boolean; capabilities: string[];
};
export type Meta = {
  departments: string[]; classifications: Record<string, number>;
  roles: { name: string; label: string; department: string; level: string; clearance: number; persona: string }[];
  capabilities: string[];
};
export type Channel = { id: string; name: string; kind: "company" | "department"; department: string | null; can_post: boolean; last_message_at: number | null };
export type ChatMessage = { id: number; channel: string; author: string | null; author_id: string; body: string; created_at: number };
export type PingMessage = { id: number; kind: "question" | "answer" | "comment"; author: string | null; author_id: string; created_at: number; redacted: boolean; min_role: string | null; body: string };
export type Ping = {
  id: number; title: string; to_department: string; status: "open" | "claimed" | "answered" | "resolved" | "closed";
  asker: string | null; asker_id: string; claimed_by: string | null; min_role: string; created_at: number; updated_at: number;
  resolved_at: number | null; draft: { doc_hash: string; staged_id: number | null; state: string } | null;
  can: { claim: boolean; release: boolean; answer: boolean; comment: boolean; resolve: boolean; close: boolean; draft: boolean };
  messages?: PingMessage[];
};
export type Source = { n: number; chunk_id: string; doc_hash: string; source: string; department: string | null; verification: string | null };
export type AskDone = { answer: string; sources: Source[]; grounded: boolean; warnings: string[]; persona: string; metrics: { ttft_ms?: number | null; total_ms?: number; retrieved?: number } };
export type Doc = { doc_hash: string; source: string; source_type: string; min_role: string; department: string | null; status: string; chunk_count: number; indexed: number; timestamp: string };
export type Pending = {
  id: number; doc_hash: string; parent_hash: string | null; source: string; min_role: string; department: string | null;
  uploader_id: string; similarity: number; risk_level: "high" | "normal"; risk_reasons: string[]; diff: string; is_new_document: boolean; timestamp: string;
};
export type LedgerRow = {
  id: number; created_at: number; department: string | null; actor_hash: string | null; required_clearance: number;
  anchored: boolean; tx_signature: string | null; redacted: boolean; action: string; target: string | null; payload: unknown; payload_hash: string;
};
export type ManagedUser = Me;
export type SessionInfo = { jti: string; ip: string | null; user_agent: string | null; created_at: number; expires_at: number; current: boolean };

export const has = (me: Me | null, cap: string) => !!me && me.capabilities.includes(cap);
