"use client";

import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "@/lib/api";

type Metrics = {
  conversations: number;
  turns: number;
  bot_resolution_rate: number | null;
  escalated_conversations: number;
  rated_answers: number;
  satisfaction: number | null;
  latency_ms_p50: number | null;
  latency_ms_p90: number | null;
  top_intents: { persona: string; intent: string; count: number }[];
  by_persona: Record<string, { turns?: number; conversations?: number; escalated?: number }>;
};
type Escalation = { id: string; persona: string; category: string; summary: string; created_at: string };
type Doc = {
  id: string; persona: string; title: string; tags: string[]; visibility: string;
  source: string; updated_at: string | null; indexed: boolean; body: string;
};
type Hit = { doc_id: string; title: string; score: number; matched_by: string[]; text: string };

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const PERSONA_MN: Record<string, string> = { driver: "Хүргэгч", client: "Харилцагч", customer: "Хэрэглэгч", all: "Бүгд" };

export default function Admin() {
  const [key, setKey] = useState("");
  const [tab, setTab] = useState<"metrics" | "escalations" | "knowledge">("metrics");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    try { setKey(sessionStorage.getItem("toktok-admin-key") ?? ""); } catch {}
  }, []);
  const saveKey = (k: string) => {
    setKey(k);
    try { sessionStorage.setItem("toktok-admin-key", k); } catch {}
  };

  return (
    <main className="admin">
      <header className="admin-header">
        <h1>ТокТок туслах · Operation</h1>
        <input type="password" placeholder="Admin key" value={key} onChange={(e) => saveKey(e.target.value)} />
      </header>
      <nav className="tabs">
        {(["metrics", "escalations", "knowledge"] as const).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ metrics: "Үзүүлэлт", escalations: "Оператор руу шилжсэн", knowledge: "Мэдлэгийн сан" }[t]}
          </button>
        ))}
      </nav>
      {err && <div className="alert">{err}</div>}
      {key ? (
        <>
          {tab === "metrics" && <MetricsTab k={key} onErr={setErr} />}
          {tab === "escalations" && <EscalationsTab k={key} onErr={setErr} />}
          {tab === "knowledge" && <KnowledgeTab k={key} onErr={setErr} />}
        </>
      ) : (
        <p className="muted">Admin key оруулна уу.</p>
      )}
    </main>
  );
}

function MetricsTab({ k, onErr }: { k: string; onErr: (e: string | null) => void }) {
  const [days, setDays] = useState(7);
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => {
    adminFetch<Metrics>(k, `/metrics?days=${days}`).then((x) => { setM(x); onErr(null); }).catch((e) => onErr(String(e)));
  }, [k, days, onErr]);
  if (!m) return null;
  return (
    <section>
      <label className="inline">
        Хугацаа:{" "}
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={1}>1 өдөр</option><option value={7}>7 өдөр</option><option value={30}>30 өдөр</option>
        </select>
      </label>
      <div className="cards">
        <Card label="Яриа" value={m.conversations} />
        <Card label="Chatbot шийдсэн" value={pct(m.bot_resolution_rate)} hint="Зорилт ≥70%" />
        <Card label="Сэтгэл ханамж" value={pct(m.satisfaction)} hint={`Зорилт ≥85% · ${m.rated_answers} үнэлгээ`} />
        <Card label="Оператор руу" value={m.escalated_conversations} />
        <Card label="Хариу (p50 / p90)" value={m.latency_ms_p50 == null ? "—" : `${(m.latency_ms_p50 / 1000).toFixed(1)}с / ${((m.latency_ms_p90 ?? 0) / 1000).toFixed(1)}с`} />
      </div>
      <h3>Төрлөөр</h3>
      <table>
        <thead><tr><th>Төрөл</th><th>Яриа</th><th>Мессеж</th><th>Оператор руу</th></tr></thead>
        <tbody>
          {Object.entries(m.by_persona).map(([p, v]) => (
            <tr key={p}><td>{PERSONA_MN[p] ?? p}</td><td>{v.conversations ?? 0}</td><td>{v.turns ?? 0}</td><td>{v.escalated ?? 0}</td></tr>
          ))}
        </tbody>
      </table>
      <h3>Давтагддаг асуултууд (intent)</h3>
      <table>
        <thead><tr><th>Төрөл</th><th>Intent</th><th>Тоо</th></tr></thead>
        <tbody>
          {m.top_intents.map((r) => (
            <tr key={r.persona + r.intent}><td>{PERSONA_MN[r.persona] ?? r.persona}</td><td>{r.intent}</td><td>{r.count}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Card({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      {hint && <div className="card-hint">{hint}</div>}
    </div>
  );
}

function EscalationsTab({ k, onErr }: { k: string; onErr: (e: string | null) => void }) {
  const [rows, setRows] = useState<Escalation[]>([]);
  useEffect(() => {
    adminFetch<Escalation[]>(k, "/escalations?status=open").then((x) => { setRows(x); onErr(null); }).catch((e) => onErr(String(e)));
  }, [k, onErr]);
  return (
    <table>
      <thead><tr><th>Тикет</th><th>Огноо</th><th>Төрөл</th><th>Ангилал</th><th>Хураангуй</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td><code>{r.id}</code></td><td>{new Date(r.created_at).toLocaleString("mn-MN")}</td>
            <td>{PERSONA_MN[r.persona] ?? r.persona}</td><td>{r.category}</td><td>{r.summary}</td>
          </tr>
        ))}
        {rows.length === 0 && <tr><td colSpan={5} className="muted">Нээлттэй тикет алга.</td></tr>}
      </tbody>
    </table>
  );
}

const EMPTY = { id: "", persona: "driver", title: "", body: "", tags: "", visibility: "public" };

function KnowledgeTab({ k, onErr }: { k: string; onErr: (e: string | null) => void }) {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [q, setQ] = useState({ query: "", persona: "driver" });
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    adminFetch<Doc[]>(k, "/knowledge").then((x) => { setDocs(x); onErr(null); }).catch((e) => onErr(String(e)));
  }, [k, onErr]);
  useEffect(load, [load]);

  async function save() {
    try {
      const res = await adminFetch<{ index: { docs: number; chunks: number } }>(k, `/knowledge/${encodeURIComponent(form.id)}`, {
        method: "PUT",
        body: JSON.stringify({
          persona: form.persona, title: form.title, body: form.body, visibility: form.visibility,
          tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
        }),
      });
      setMsg(`Хадгаллаа · ${res.index.docs} баримт, ${res.index.chunks} хэсэг индекслэгдсэн`);
      load();
    } catch (e) {
      onErr(String(e));
    }
  }

  async function search() {
    try {
      setHits(await adminFetch<Hit[]>(k, "/knowledge/search", { method: "POST", body: JSON.stringify(q) }));
    } catch (e) {
      onErr(String(e));
    }
  }

  return (
    <section className="kb">
      <div className="kb-list">
        <table>
          <thead><tr><th>ID</th><th>Төрөл</th><th>Гарчиг</th><th>Эх үүсвэр</th><th>Индекс</th></tr></thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.id} className="clickable" onClick={() => setForm({
                id: d.id, persona: d.persona, title: d.title, body: d.body, tags: d.tags.join(", "), visibility: d.visibility,
              })}>
                <td><code>{d.id}</code></td><td>{PERSONA_MN[d.persona] ?? d.persona}</td><td>{d.title}</td>
                <td>{d.source === "admin" ? "Засварласан" : "Файл"}</td><td>{d.indexed ? "✓" : d.visibility === "internal" ? "дотоод" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="kb-edit">
        <h3>Баримт засах / нэмэх</h3>
        <p className="muted small">Хадгалахад шууд индекслэгдэж chatbot ашиглаж эхэлнэ. Файлтай ижил ID-тай бол файлыг дарж бичнэ.</p>
        <input placeholder="ID (жишээ: driver-winter)" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} />
        <div className="row2">
          <select value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })}>
            {Object.entries(PERSONA_MN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })}>
            <option value="public">Chatbot ашиглана</option><option value="internal">Зөвхөн дотоод</option>
          </select>
        </div>
        <input placeholder="Гарчиг" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <input placeholder="Tag (таслалаар)" value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} />
        <textarea rows={14} placeholder={"## Дэд гарчиг\nАгуулга…"} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} />
        <div className="row2">
          <button onClick={save} disabled={!form.id || !form.title || form.body.length < 10}>Хадгалах</button>
          <button className="ghost" onClick={() => { setForm(EMPTY); setMsg(null); }}>Шинэ</button>
        </div>
        {msg && <div className="ok">{msg}</div>}

        <h3>Хайлт турших</h3>
        <div className="row2">
          <input placeholder="Асуулт (латинаар ч болно)" value={q.query} onChange={(e) => setQ({ ...q, query: e.target.value })} />
          <select value={q.persona} onChange={(e) => setQ({ ...q, persona: e.target.value })}>
            <option value="driver">Хүргэгч</option><option value="client">Харилцагч</option><option value="customer">Хэрэглэгч</option>
          </select>
          <button onClick={search} disabled={!q.query}>Хайх</button>
        </div>
        {hits && (
          <ol className="hits">
            {hits.map((h, i) => (
              <li key={i}><b>{h.title}</b> <span className="muted small">{h.doc_id} · {h.matched_by.join("+")} · {h.score}</span>
                <div className="small">{h.text.slice(0, 200)}…</div></li>
            ))}
            {hits.length === 0 && <li className="muted">Илэрц алга — chatbot энэ асуултад оператор руу шилжүүлнэ.</li>}
          </ol>
        )}
      </div>
    </section>
  );
}
