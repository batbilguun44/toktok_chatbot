export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type ChatEvent =
  | { type: "meta"; data: { session_id?: string; turn_id?: string; intent?: string; confidence?: number; route_source?: string } }
  | { type: "status"; data: { text: string; tool?: string } }
  | { type: "text"; data: { delta: string } }
  | { type: "tool"; data: { name: string; state: "start" | "done" | "failed" } }
  | { type: "escalation"; data: { ticket_id: string; phone: string; category: string } }
  | { type: "sources"; data: { documents: { id: string; title: string }[] } }
  | { type: "error"; data: { code: string; message: string } }
  | { type: "done"; data: { turn_id: string; stop: string; latency_ms?: number } };

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

export async function sendFeedback(token: string, turnId: string, rating: 1 | -1, comment?: string) {
  await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ turn_id: turnId, rating, comment }),
  });
}

export async function adminFetch<T>(key: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api/admin${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Admin-Key": key, ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}
