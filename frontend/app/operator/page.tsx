"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Badge, PERSONA_MN, when } from "@/components/admin/common";
import Knowledge from "@/components/admin/Knowledge";
import Prompts from "@/components/admin/Prompts";
import StaffGate from "@/components/StaffGate";
import { Me, staffFetch } from "@/lib/api";

type Esc = { id: string; conversation_id: string; persona: string; category: string; summary: string; status: string;
  assignee: string | null; created_at: string; updated_at: string; resolution: string | null };
type Msg = { id: number; sender: string; author: string | null; text: string; created_at: string };
type Detail = { escalation: Esc; transcript: { role: string; text: string }[]; messages: Msg[] };

const CATEGORY_MN: Record<string, string> = {
  user_requested: "Хүн хүссэн", not_in_knowledge: "Мэдлэгт алга", needs_human_decision: "Шийдвэр хэрэгтэй",
  incident: "Яаралтай", complaint: "Гомдол", account_security: "Аюулгүй байдал", sales_manager: "Борлуулалт",
  technical_issue: "Техникийн",
};

export default function OperatorPage() {
  return (
    <StaffGate permission="handoff" title="ТокТок · Операторын самбар" returnTo="/operator">
      {(me, logout) => <Console me={me} logout={logout} />}
    </StaffGate>
  );
}

// Operators answer chats and maintain the bot's knowledge; an admin approves what they submit.
const AREAS = [
  { id: "chat", label: "Чат", perm: "handoff" },
  { id: "knowledge", label: "Мэдлэгийн сан", perm: "kb.read" },
  { id: "prompts", label: "Ботын заавар", perm: "kb.read" },
] as const;

function Console({ me, logout }: { me: Me; logout: () => void }) {
  const areas = AREAS.filter((a) => me.permissions.includes(a.perm));
  // This page only drafts and submits; approving, rolling back and archiving happen on /admin
  // (even for admins), so the knowledge / instruction editors here never show those controls.
  const drafter = { ...me, permissions: me.permissions.filter((p) => p !== "kb.publish") };
  const [area, setArea] = useState<(typeof AREAS)[number]["id"]>("chat");
  const [view, setView] = useState<"queued" | "mine" | "resolved">("queued");
  const [rows, setRows] = useState<Esc[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const loadQueue = useCallback(async () => {
    const q = view === "mine" ? "status=active&mine=true" : `status=${view}`;
    try {
      setRows(await staffFetch<Esc[]>(`/api/operator/queue?${q}`));
    } catch (e) {
      setErr(String(e));
    }
  }, [view]);

  const loadDetail = useCallback(async () => {
    if (!sel) return;
    try {
      setDetail(await staffFetch<Detail>(`/api/operator/handoffs/${sel}`));
    } catch (e) {
      setErr(String(e));
    }
  }, [sel]);

  useEffect(() => {
    loadQueue();
    const t = setInterval(loadQueue, 4000);
    return () => clearInterval(t);
  }, [loadQueue]);

  useEffect(() => {
    loadDetail();
    const t = setInterval(loadDetail, 3000);
    return () => clearInterval(t);
  }, [loadDetail]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [detail?.messages.length]);

  const alerts = useQueueAlerts(me.permissions.includes("handoff"));
  // A user writing in the open conversation while this tab is in the background.
  const lastUserMsg = useRef<number | null>(null);
  useEffect(() => {
    const users = detail?.messages.filter((m) => m.sender === "user") ?? [];
    const latest = users.length ? users[users.length - 1].id : 0;
    if (lastUserMsg.current !== null && latest > lastUserMsg.current && document.hidden) {
      alerts.notify("Хэрэглэгч бичлээ", users[users.length - 1].text);
    }
    lastUserMsg.current = latest;
  }, [detail?.messages, alerts]);
  useEffect(() => { lastUserMsg.current = null; }, [sel]);

  async function act(path: string, body?: object) {
    try {
      await staffFetch(`/api/operator/handoffs/${sel}${path}`, { method: "POST", body: JSON.stringify(body ?? {}) });
      setErr(null);
      await Promise.all([loadDetail(), loadQueue()]);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    await act("/messages", { text });
    setText("");
  }

  const esc = detail?.escalation;
  const mine = esc?.assignee === me.email;
  const open = esc && esc.status !== "resolved";

  return (
    <main className="operator">
      <header className="admin-header">
        <h1>Операторын самбар</h1>
        <div className="small">{me.email} {me.permissions.includes("staff.manage") && <a href="/admin">Админ</a>}{" "}
          {alerts.permission === "default" && (
            <button className="ghost" onClick={alerts.enable}>🔔 Мэдэгдэл асаах</button>
          )}{" "}
          <button className="ghost" onClick={logout}>Гарах</button></div>
      </header>
      <nav className="tabs">
        {areas.map((a) => (
          <button key={a.id} className={area === a.id ? "active" : ""} onClick={() => { setArea(a.id); setErr(null); }}>
            {a.label}{a.id === "chat" && alerts.queued > 0 && <span className="count">{alerts.queued}</span>}
          </button>
        ))}
      </nav>
      {err && <div className="alert">{err}</div>}
      {area === "knowledge" && <Knowledge me={drafter} onErr={setErr} />}
      {area === "prompts" && <Prompts me={drafter} onErr={setErr} />}
      {area === "chat" && <div className="op-grid">
        <aside>
          <nav className="tabs">
            {(["queued", "mine", "resolved"] as const).map((v) => (
              <button key={v} className={view === v ? "active" : ""} onClick={() => setView(v)}>
                {{ queued: "Хүлээгдэж буй", mine: "Миний", resolved: "Шийдсэн" }[v]}
                {v === "queued" && alerts.queued > 0 && <span className="count">{alerts.queued}</span>}
              </button>
            ))}
          </nav>
          <ul className="queue">
            {rows.map((r) => (
              <li key={r.id} className={sel === r.id ? "selected" : ""} onClick={() => setSel(r.id)}>
                <div><b>{PERSONA_MN[r.persona]}</b> · <Badge kind={r.category === "incident" ? "rejected" : "in_review"}>
                  {CATEGORY_MN[r.category] ?? r.category}</Badge></div>
                <div className="small">{r.summary.slice(0, 90)}</div>
                <div className="small muted">{when(r.created_at)}{r.assignee ? ` · ${r.assignee}` : ""}</div>
              </li>
            ))}
            {!rows.length && <li className="muted">Хоосон</li>}
          </ul>
        </aside>
        <section className="op-chat">
          {!esc ? <p className="muted op-empty">Зүүн талаас яриа сонгоно уу.</p> : (
            <>
              <div className="op-head">
                <div><b>{esc.id}</b> · {PERSONA_MN[esc.persona]} · {CATEGORY_MN[esc.category] ?? esc.category}</div>
                <div className="small">AI хураангуй: {esc.summary}</div>
                {open && !esc.assignee && <button onClick={() => act("/claim")}>Хариуцаж авах</button>}
                {esc.assignee && !mine && open && <div className="small muted">Хариуцсан: {esc.assignee}</div>}
              </div>
              <div className="op-thread">
                {detail!.transcript.length > 0 && <div className="small muted">— Chatbot-той яриа —</div>}
                {detail!.transcript.map((t, i) => (
                  <div key={`t${i}`} className={`row ${t.role === "user" ? "user" : "assistant"}`}>
                    <div className={`bubble ${t.role === "user" ? "user" : "assistant"}`}><div className="text">{t.text}</div></div>
                  </div>
                ))}
                <div className="small muted">— Оператортой —</div>
                {detail!.messages.map((m) => (
                  <div key={m.id} className={`row ${m.sender === "user" ? "user" : m.sender === "system" ? "system" : "assistant"}`}>
                    <div className={`bubble ${m.sender === "user" ? "user" : m.sender === "system" ? "system" : "operator"}`}>
                      {m.sender === "operator" && <div className="who">{m.author}</div>}
                      <div className="text">{m.text}</div>
                    </div>
                  </div>
                ))}
                <div ref={endRef} />
              </div>
              {open && (mine || !esc.assignee) ? (
                <>
                  <form className="composer" onSubmit={send}>
                    <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Хариу бичих…" aria-label="Хариу" />
                    <button type="submit" disabled={!text.trim()}>Илгээх</button>
                  </form>
                  <div className="row2 pad">
                    <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Шийдвэрийн тэмдэглэл (chatbot-д дамжина)" />
                    <button className="ghost" onClick={() => act("/resolve", { note: note || null }).then(() => setNote(""))}>
                      Шийдсэн · chatbot руу буцаах</button>
                  </div>
                </>
              ) : esc.status === "resolved" ? <p className="small muted pad">Шийдсэн: {esc.resolution ?? "—"}</p> : null}
            </>
          )}
        </section>
      </div>}
    </main>
  );
}

/**
 * New handoffs must not wait unnoticed: polls the waiting queue on every operator tab and, when a new
 * conversation arrives, beeps, shows a browser notification (after the operator allows it) and puts
 * the count in the tab title.
 */
function useQueueAlerts(enabled: boolean) {
  const [queued, setQueued] = useState(0);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("unsupported");
  const seen = useRef<Set<string> | null>(null);

  useEffect(() => {
    setPermission(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  }, []);

  const notify = useCallback((title: string, body: string) => {
    beep();
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      const n = new Notification(title, { body: body.slice(0, 140), tag: "toktok-operator" });
      n.onclick = () => { window.focus(); n.close(); };
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const base = document.title.replace(/^\(\d+\)\s*/, "");
    let stopped = false;
    const check = async () => {
      try {
        const rows = await staffFetch<Esc[]>("/api/operator/queue?status=queued");
        if (stopped) return;
        const fresh = seen.current ? rows.filter((r) => !seen.current!.has(r.id)) : [];
        seen.current = new Set(rows.map((r) => r.id));
        setQueued(rows.length);
        document.title = rows.length ? `(${rows.length}) ${base}` : base;
        if (fresh.length) {
          notify(fresh.length > 1 ? `${fresh.length} шинэ яриа` : "Шинэ яриа ирлээ",
            `${PERSONA_MN[fresh[0].persona] ?? fresh[0].persona}: ${fresh[0].summary}`);
        }
      } catch { /* shown by the queue panel */ }
    };
    check();
    const t = setInterval(check, 5000);
    return () => { stopped = true; clearInterval(t); document.title = base; };
  }, [enabled, notify]);

  const enable = useCallback(async () => {
    if (typeof Notification === "undefined") return;
    setPermission(await Notification.requestPermission());
    beep();
  }, []);

  return { queued, permission, enable, notify };
}

function beep() {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.frequency.value = 880;
    g.gain.setValueAtTime(0.15, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.4);
    o.onended = () => ctx.close();
  } catch { /* audio blocked until the page has been interacted with */ }
}
