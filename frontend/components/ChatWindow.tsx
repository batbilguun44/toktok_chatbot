"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { chatHistory, closeHandoff, pollHandoff, sendFeedback, streamChat } from "@/lib/api";
import RichText from "./RichText";

type Msg = {
  id: string;
  role: "user" | "assistant" | "operator" | "system";
  text: string;
  turnId?: string;
  status?: string;
  escalation?: { ticket_id: string; phone: string; mode?: string };
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

type Props = {
  token: string;
  persona: string;
  title?: string;
  /** Shown as a close button in the header (popup / app embed). */
  onClose?: () => void;
  /** Called with the number of operator messages that just arrived. */
  onOperatorMessages?: (count: number) => void;
};

export default function ChatWindow({ token, persona, title, onClose, onOperatorMessages }: Props) {
  const storageKey = `toktok-session-${token}`;
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  // Live operator: poll for replies while the conversation is with a person.
  const [handoff, setHandoff] = useState<{ active: boolean; lastId: number }>({ active: false, lastId: 0 });
  const lastIdRef = useRef(0);
  const onOperatorMessagesRef = useRef(onOperatorMessages);
  onOperatorMessagesRef.current = onOperatorMessages;

  useEffect(() => {
    if (!handoff.active) return;
    let stopped = false;
    let unlinked = 0; // polls that found no handoff on the session at all
    const tick = async () => {
      const sid = sessionRef.current;
      if (!sid || stopped) return;
      const r = await pollHandoff(token, sid, lastIdRef.current);
      if (stopped) return;
      if (r.messages.length) {
        lastIdRef.current = r.last_id ?? lastIdRef.current;
        const fromOperator = r.messages.filter((m) => m.sender === "operator").length;
        if (fromOperator) onOperatorMessagesRef.current?.(fromOperator);
        setMessages((ms) => [
          ...ms,
          ...r.messages.map((m) => ({ id: `h${m.id}`, role: m.sender, text: m.text }) as Msg),
        ]);
      }
      // Stop only when the operator closed the ticket. "No handoff on this session" can also mean the
      // turn that created it has not been saved yet, so tolerate a few of those before giving up.
      if (r.status === "resolved" || (!r.active && !r.status && ++unlinked > 5)) {
        setHandoff({ active: false, lastId: lastIdRef.current });
      } else if (r.status) {
        unlinked = 0;
      }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [handoff.active, token]);

  useEffect(() => {
    try {
      sessionRef.current = localStorage.getItem(storageKey);
    } catch {
      sessionRef.current = null;
    }
    setMessages([]);
    lastIdRef.current = 0;
    setHandoff({ active: false, lastId: 0 });
    // After a reload, show the conversation so far and resume an open operator handoff.
    const sid = sessionRef.current;
    if (!sid) return;
    let cancelled = false;
    chatHistory(token, sid).then((h) => {
      if (cancelled || !h || sessionRef.current !== sid) return;
      setMessages((ms) => (ms.length ? ms : h.messages.map((m) => ({
        id: m.id, role: m.role, text: m.text, turnId: m.turn_id, escalation: m.escalation,
        rating: m.rating ?? undefined,
      }))));
      lastIdRef.current = h.handoff.last_id;
      if (h.handoff.active) setHandoff({ active: true, lastId: h.handoff.last_id });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [storageKey, token]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const patch = (id: string, fn: (m: Msg) => Msg) =>
    setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)));

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput("");
    const botId = crypto.randomUUID();
    let liveHandoff = false;
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
              try { localStorage.setItem(storageKey, ev.data.session_id); } catch {}
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
            // Start polling once the turn is saved ("done"), or the first poll finds no handoff yet.
            if (ev.data.mode === "inchat") liveHandoff = true;
            break;
          case "handoff":
            // Delivered to the operator: no bot reply for this message.
            setMessages((ms) => ms.filter((m) => m.id !== botId));
            setHandoff((h) => (h.active ? h : { active: true, lastId: lastIdRef.current }));
            break;
          case "sources":
            patch(botId, (m) => ({ ...m, sources: ev.data.documents }));
            break;
          case "error":
            if (ev.data.code === "forbidden") {
              sessionRef.current = null;
              try { localStorage.removeItem(storageKey); } catch {}
            }
            patch(botId, (m) => ({ ...m, text: ev.data.message, error: true, status: undefined }));
            break;
          case "done":
            patch(botId, (m) => ({ ...m, status: undefined }));
            if (liveHandoff) setHandoff((h) => (h.active ? h : { active: true, lastId: lastIdRef.current }));
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

  async function newConversation() {
    // An open operator request would stay in the queue and the operator's replies would go nowhere:
    // confirm, then close it on the server before starting over.
    const sid = sessionRef.current;
    if (handoff.active && sid) {
      if (!window.confirm("Ажилтантай яриа нээлттэй байна. Шинэ яриа эхлүүлбэл энэ хүсэлт хаагдана. Үргэлжлүүлэх үү?")) return;
      await closeHandoff(token, sid);
    }
    sessionRef.current = null;
    try { localStorage.removeItem(storageKey); } catch {}
    setMessages([]);
    lastIdRef.current = 0;
    setHandoff({ active: false, lastId: 0 });
  }

  return (
    <div className="chat">
      <header className="chat-header">
        <div>
          <div className="chat-title">{title ?? "ТокТок туслах"}</div>
          <div className="chat-sub">{handoff.active ? "Оператортой холбогдсон" : "24/7 · Монгол хэлээр"}</div>
        </div>
        <div className="chat-actions">
          <button className="ghost" onClick={newConversation} disabled={busy} title="Шинэ яриа">
            Шинэ яриа
          </button>
          {onClose && (
            <button className="ghost chat-close" onClick={onClose} aria-label="Хаах" title="Хаах">
              ✕
            </button>
          )}
        </div>
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
              {m.role === "operator" && <div className="who">Оператор</div>}
              {m.text && (m.role === "assistant" || m.role === "operator"
                ? <RichText text={m.text} />
                : <div className="text">{m.text}</div>)}
              {m.status && <div className="status"><span className="dot" />{m.status}</div>}
              {m.escalation && (
                <div className="escalation">
                  <div>
                    {m.escalation.mode === "inchat" ? "Оператор энэ чатаар хариулна" : "Оператор руу шилжүүллээ"} ·{" "}
                    <b>{m.escalation.ticket_id}</b>
                  </div>
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
