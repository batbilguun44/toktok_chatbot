"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { sendFeedback, streamChat } from "@/lib/api";

type Msg = {
  id: string;
  role: "user" | "assistant";
  text: string;
  turnId?: string;
  status?: string;
  escalation?: { ticket_id: string; phone: string };
  sources?: { id: string; title: string }[];
  rating?: 1 | -1;
  error?: boolean;
};

const PLACEHOLDER: Record<string, string> = {
  driver: "Жишээ: Хүргэлтийн төлбөр яаж бодогддог вэ?",
  client: "Жишээ: Захиалга notification-оор ирэхгүй байна",
  customer: "Жишээ: Миний захиалга хаана явж байна?",
};

const SUGGESTIONS: Record<string, string[]> = {
  driver: ["App ажиллахгүй байна", "Хүргэлтийн төлбөр яаж бодогддог вэ?", "Шинэ хүргэгч юу хийх ёстой вэ?"],
  client: ["Захиалга notification-оор ирэхгүй байна", "Салбар апп дээр харагдахгүй байна", "Тооцоогоо яаж татах вэ?"],
  customer: ["Миний захиалга хаана явж байна?", "Хаягаа буруу оруулчихлаа", "zahialga hezee ireh ve"],
};

export default function ChatWindow({ token, persona, title }: { token: string; persona: string; title?: string }) {
  const storageKey = `toktok-session-${token}`;
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      sessionRef.current = sessionStorage.getItem(storageKey);
    } catch {
      sessionRef.current = null;
    }
    setMessages([]);
  }, [storageKey]);

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [messages]);

  const patch = (id: string, fn: (m: Msg) => Msg) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput("");
    const botId = crypto.randomUUID();
    setMessages((ms) => [
      ...ms,
      { id: crypto.randomUUID(), role: "user", text },
      { id: botId, role: "assistant", text: "", status: "Бодож байна…" },
    ]);
    try {
      for await (const ev of streamChat(token, text, sessionRef.current)) {
        switch (ev.type) {
          case "meta":
            if (ev.data.session_id) {
              sessionRef.current = ev.data.session_id;
              try { sessionStorage.setItem(storageKey, ev.data.session_id); } catch {}
            }
            if (ev.data.turn_id) patch(botId, (m) => ({ ...m, turnId: ev.data.turn_id }));
            break;
          case "status":
            patch(botId, (m) => ({ ...m, status: ev.data.text }));
            break;
          case "text":
            patch(botId, (m) => ({ ...m, text: m.text + ev.data.delta, status: undefined }));
            break;
          case "escalation":
            patch(botId, (m) => ({ ...m, escalation: ev.data }));
            break;
          case "sources":
            patch(botId, (m) => ({ ...m, sources: ev.data.documents }));
            break;
          case "error":
            if (ev.data.code === "forbidden") {
              sessionRef.current = null;
              try { sessionStorage.removeItem(storageKey); } catch {}
            }
            patch(botId, (m) => ({ ...m, text: ev.data.message, error: true, status: undefined }));
            break;
          case "done":
            patch(botId, (m) => ({ ...m, status: undefined }));
            break;
        }
      }
    } catch {
      patch(botId, (m) => ({ ...m, text: "Сүлжээний алдаа гарлаа. Дахин оролдоно уу.", error: true, status: undefined }));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  async function rate(m: Msg, rating: 1 | -1) {
    if (!m.turnId || m.rating) return;
    patch(m.id, (x) => ({ ...x, rating }));
    await sendFeedback(token, m.turnId, rating);
  }

  function newConversation() {
    sessionRef.current = null;
    try { sessionStorage.removeItem(storageKey); } catch {}
    setMessages([]);
  }

  return (
    <div className="chat">
      <header className="chat-header">
        <div>
          <div className="chat-title">{title ?? "ТокТок туслах"}</div>
          <div className="chat-sub">24/7 · Монгол хэлээр</div>
        </div>
        <button className="ghost" onClick={newConversation} disabled={busy} title="Шинэ яриа">
          Шинэ яриа
        </button>
      </header>

      <div className="chat-body">
        {messages.length === 0 && (
          <div className="empty">
            <p>Сайн байна уу! Юугаар туслах вэ?</p>
            <div className="chips">
              {(SUGGESTIONS[persona] ?? []).map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`row ${m.role}`}>
            <div className={`bubble ${m.role} ${m.error ? "error" : ""}`}>
              {m.text && <div className="text">{m.text}</div>}
              {m.status && <div className="status"><span className="dot" />{m.status}</div>}
              {m.escalation && (
                <div className="escalation">
                  <div>Оператор руу шилжүүллээ · <b>{m.escalation.ticket_id}</b></div>
                  <a className="call" href={`tel:${m.escalation.phone.replace(/[^0-9+]/g, "")}`}>
                    📞 {m.escalation.phone} руу залгах
                  </a>
                </div>
              )}
              {m.sources && m.sources.length > 0 && (
                <div className="sources">Эх сурвалж: {m.sources.map((s) => s.title).join(" · ")}</div>
              )}
              {m.role === "assistant" && m.turnId && !m.status && !m.error && (
                <div className="feedback">
                  <button aria-label="Тустай" className={m.rating === 1 ? "on" : ""} onClick={() => rate(m, 1)} disabled={!!m.rating}>👍</button>
                  <button aria-label="Тусгүй" className={m.rating === -1 ? "on" : ""} onClick={() => rate(m, -1)} disabled={!!m.rating}>👎</button>
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form className="composer" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={PLACEHOLDER[persona] ?? "Асуултаа бичнэ үү"}
          maxLength={2000}
          disabled={busy}
          aria-label="Мессеж"
        />
        <button type="submit" disabled={busy || !input.trim()}>Илгээх</button>
      </form>
    </div>
  );
}
