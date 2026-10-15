export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ChatEvent =
  | { type: "meta"; data: { session_id?: string; turn_id?: string; intent?: string; confidence?: number; route_source?: string } }
  | { type: "status"; data: { text: string; tool?: string } }
  | { type: "text"; data: { delta: string } }
  | { type: "tool"; data: { name: string; state: "start" | "done" | "failed" } }
  | { type: "escalation"; data: { ticket_id: string; phone: string; category: string; mode?: "inchat" | "phone" } }
  | { type: "handoff"; data: { ticket_id: string; status: string; message_id: number } }
  | { type: "sources"; data: { documents: { id: string; title: string }[] } }
  | { type: "error"; data: { code: string; message: string } }
  | { type: "done"; data: { turn_id?: string; stop: string; latency_ms?: number } };

/** POSTs a message and yields server-sent events as they arrive. */
export async function* streamChat(
  token: string,
  message: string,
  sessionId: string | null,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok || !res.body) {
    const code = res.status === 403 ? "forbidden" : res.status === 401 ? "unauthorized" : "http_error";
    yield { type: "error", data: { code, message: `Холболтын алдаа (${res.status})` } };
    return;
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { type: event, data: JSON.parse(data) } as ChatEvent;
    }
  }
}

export type HandoffPoll = {
  active: boolean;
  status?: string;
  ticket_id?: string;
  last_id?: number;
  messages: { id: number; sender: "operator" | "system"; text: string; created_at: string }[];
};

export type ChatHistory = {
  messages: { id: string; role: "user" | "assistant" | "operator" | "system"; text: string; turn_id?: string;
    rating?: 1 | -1 | null; escalation?: { ticket_id: string; phone: string; mode?: string } }[];
  handoff: { active: boolean; last_id: number };
};

/** The user's own conversation so far (restores the chat window after a reload). */
export async function chatHistory(token: string, sessionId: string): Promise<ChatHistory | null> {
  const res = await fetch(`${API_BASE}/api/chat/history?session_id=${encodeURIComponent(sessionId)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.ok ? res.json() : null;
}

export async function pollHandoff(token: string, sessionId: string, after: number): Promise<HandoffPoll> {
  const res = await fetch(`${API_BASE}/api/chat/handoff?session_id=${encodeURIComponent(sessionId)}&after=${after}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return { active: false, messages: [] };
  return res.json();
}

export async function sendFeedback(token: string, turnId: string, rating: 1 | -1, comment?: string) {
  await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ turn_id: turnId, rating, comment }),
  });
}

// ---------------------------------------------------------------- staff API
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Staff requests use the HttpOnly session cookie set by SSO / dev login. */
export async function staffFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      "X-Requested-With": "toktok-admin",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {}
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

export type Me = { email: string; roles: string[]; permissions: string[]; via: string };
export type AuthConfig = { sso_enabled: boolean; dev_login_enabled: boolean; roles: string[] };

export const authApi = {
  config: () => staffFetch<AuthConfig>("/api/auth/config"),
  me: () => staffFetch<Me>("/api/auth/me"),
  devLogin: (email: string, roles: string[]) =>
    staffFetch("/api/auth/dev-login", { method: "POST", body: JSON.stringify({ email, roles }) }),
  logout: () => staffFetch("/api/auth/logout", { method: "POST" }),
  ssoUrl: (returnTo: string) => `${API_BASE}/api/auth/login?return_to=${encodeURIComponent(returnTo)}`,
};
