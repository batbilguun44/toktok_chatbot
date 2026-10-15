"use client";

import { useCallback, useEffect, useState } from "react";
import { Me, staffFetch } from "@/lib/api";
import { Badge, Card, PERSONA_MN, pct, when } from "./common";

type Metrics = {
  conversations: number; turns: number; bot_resolution_rate: number | null; escalated_conversations: number;
  rated_answers: number; satisfaction: number | null; judged_answers: number; groundedness: number | null;
  judge_score_avg: number | null; latency_ms_p50: number | null; latency_ms_p90: number | null;
  top_intents: { persona: string; intent: string; count: number }[];
  by_persona: Record<string, { turns?: number; conversations?: number; escalated?: number }>;
};

export function MetricsPanel({ onErr }: { onErr: (e: string | null) => void }) {
  const [days, setDays] = useState(7);
  const [m, setM] = useState<Metrics | null>(null);
  useEffect(() => {
    staffFetch<Metrics>(`/api/admin/metrics?days=${days}`).then((x) => { setM(x); onErr(null); })
      .catch((e) => onErr(String(e)));
  }, [days, onErr]);
  if (!m) return null;
  const s = (ms: number | null) => (ms == null ? "—" : `${(ms / 1000).toFixed(1)}с`);
  return (
    <section>
      <label className="inline">Хугацаа:{" "}
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={1}>1 өдөр</option><option value={7}>7 өдөр</option><option value={30}>30 өдөр</option>
        </select>
      </label>
      <div className="cards">
        <Card label="Яриа" value={m.conversations} />
        <Card label="Chatbot шийдсэн" value={pct(m.bot_resolution_rate)} hint="Зорилт ≥70%" />
        <Card label="Сэтгэл ханамж" value={pct(m.satisfaction)} hint={`Зорилт ≥85% · ${m.rated_answers} үнэлгээ`} />
        <Card label="Үндэслэлтэй хариулт" value={pct(m.groundedness)} hint={`AI шүүгч · ${m.judged_answers} шалгасан`} />
        <Card label="Оператор руу" value={m.escalated_conversations} />
        <Card label="Хариу (p50 / p90)" value={`${s(m.latency_ms_p50)} / ${s(m.latency_ms_p90)}`} />
      </div>
      <h3>Төрлөөр</h3>
      <table>
        <thead><tr><th>Төрөл</th><th>Яриа</th><th>Мессеж</th><th>Оператор руу</th></tr></thead>
        <tbody>{Object.entries(m.by_persona).map(([p, v]) => (
          <tr key={p}><td>{PERSONA_MN[p] ?? p}</td><td>{v.conversations ?? 0}</td><td>{v.turns ?? 0}</td><td>{v.escalated ?? 0}</td></tr>
        ))}</tbody>
      </table>
      <h3>Давтагддаг асуултууд (intent)</h3>
      <table>
        <thead><tr><th>Төрөл</th><th>Intent</th><th>Тоо</th></tr></thead>
        <tbody>{m.top_intents.map((r) => (
          <tr key={r.persona + r.intent}><td>{PERSONA_MN[r.persona] ?? r.persona}</td><td>{r.intent}</td><td>{r.count}</td></tr>
        ))}</tbody>
      </table>
    </section>
  );
}

type Judgement = { turn_id: string; grounded: string; answered: boolean; score: number; unsupported_claims: string[];
  rationale: string; persona: string; user_text: string; bot_text: string; created_at: string };
type Cluster = { persona: string; topic: string; count: number; covered_by_doc_id: string | null;
  proposed_doc_id: string | null; draft_created: boolean; notes: string };
type Run = { id: string; actor: string; stats: { questions: number; drafts: number; days: number }; clusters: Cluster[];
  created_at?: string };

export function ImprovePanel({ me, onErr }: { me: Me; onErr: (e: string | null) => void }) {
  const [js, setJs] = useState<Judgement[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const [days, setDays] = useState(14);
  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([staffFetch<Judgement[]>("/api/admin/judgements?flagged=true"),
        staffFetch<Run[]>("/api/admin/insights")]);
      setJs(a);
      setRuns(b);
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => { load(); }, [load]);

  async function run() {
    setBusy(true);
    try {
      await staffFetch("/api/admin/insights/run", { method: "POST", body: JSON.stringify({ days }) });
      await load();
    } catch (e) {
      onErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const latest = runs[0];
  return (
    <section className="improve">
      <h3>Сайжруулах санал</h3>
      <p className="muted small">
        Оператор руу шилжсэн, 👎 авсан, AI шүүгч үндэслэлгүй гэж үзсэн асуултуудыг сэдвээр бүлэглэж, дутуу FAQ-ийн
        ноорог үүсгэнэ. Ноорог автоматаар нийтлэгдэхгүй — «Мэдлэгийн сан»-д засаж, хянуулна.
      </p>
      {me.permissions.includes("insights.run") && (
        <div className="row2">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Хугацаа">
            <option value={7}>Сүүлийн 7 хоног</option><option value={14}>14 хоног</option><option value={30}>30 хоног</option>
          </select>
          <button onClick={run} disabled={busy}>{busy ? "Шинжилж байна…" : "Шинжилгээ ажиллуулах"}</button>
        </div>
      )}
      {latest && (
        <>
          <p className="small muted">{when(latest.created_at)} · {latest.stats.questions} асуулт · {latest.stats.drafts} шинэ ноорог</p>
          <table>
            <thead><tr><th>Сэдэв</th><th>Төрөл</th><th>Тоо</th><th>Мэдлэг</th><th>Тайлбар</th></tr></thead>
            <tbody>{latest.clusters.map((c, i) => (
              <tr key={i}><td>{c.topic}</td><td>{PERSONA_MN[c.persona]}</td><td>{c.count}</td>
                <td>{c.covered_by_doc_id ? <code>{c.covered_by_doc_id}</code>
                  : c.draft_created ? <Badge kind="draft">Ноорог: {c.proposed_doc_id}</Badge> : <Badge kind="rejected">Дутуу</Badge>}</td>
                <td className="small">{c.notes}</td></tr>
            ))}</tbody>
          </table>
        </>
      )}
      <h3>AI шүүгчийн анхааруулга</h3>
      <table>
        <thead><tr><th>Асуулт</th><th>Хариулт</th><th>Дүгнэлт</th></tr></thead>
        <tbody>{js.map((j) => (
          <tr key={j.turn_id}>
            <td className="small">{j.user_text}<div className="muted">{PERSONA_MN[j.persona]} · {when(j.created_at)}</div></td>
            <td className="small">{j.bot_text}</td>
            <td className="small"><Badge kind={j.grounded === "no" ? "rejected" : "in_review"}>{j.grounded}</Badge> {j.score}/5
              <div>{j.rationale}</div>
              {j.unsupported_claims.map((c, i) => <div key={i} className="err">“{c}”</div>)}</td>
          </tr>
        ))}
        {!js.length && <tr><td colSpan={3} className="muted">Анхааруулга алга.</td></tr>}</tbody>
      </table>
    </section>
  );
}

type Staff = { email: string; name: string | null; roles: string[]; active: boolean; last_login_at: string | null };
const ROLE_MN: Record<string, string> = { operator: "Оператор", admin: "Админ" };

export function StaffPanel({ onErr }: { onErr: (e: string | null) => void }) {
  const [rows, setRows] = useState<Staff[]>([]);
  const [form, setForm] = useState<{ email: string; roles: string[] }>({ email: "", roles: ["operator"] });
  const load = useCallback(() => {
    staffFetch<Staff[]>("/api/admin/staff").then((x) => { setRows(x); onErr(null); }).catch((e) => onErr(String(e)));
  }, [onErr]);
  useEffect(load, [load]);
  async function save(email: string, roles: string[], active = true) {
    try {
      await staffFetch(`/api/admin/staff/${encodeURIComponent(email)}`, { method: "PUT",
        body: JSON.stringify({ roles, active }) });
      load();
    } catch (e) {
      onErr(String(e));
    }
  }
  const toggle = (roles: string[], r: string) => (roles.includes(r) ? roles.filter((x) => x !== r) : [...roles, r]);
  return (
    <section>
      <p className="muted small">SSO-гоор нэвтрэх ажилтныг энд бүртгэнэ. Эрх: Оператор (чат), Засварлагч (ноорог),
        Батлагч (нийтлэх), Админ (бүгд).</p>
      <table>
        <thead><tr><th>И-мэйл</th><th>Эрх</th><th>Сүүлд нэвтэрсэн</th><th /></tr></thead>
        <tbody>{rows.map((u) => (
          <tr key={u.email} className={u.active ? "" : "muted"}>
            <td>{u.email}<div className="small muted">{u.name}</div></td>
            <td>{Object.keys(ROLE_MN).map((r) => (
              <label key={r} className="chk"><input type="checkbox" checked={u.roles.includes(r)}
                onChange={() => save(u.email, toggle(u.roles, r), u.active)} /> {ROLE_MN[r]}</label>
            ))}</td>
            <td className="small">{when(u.last_login_at)}</td>
            <td><button className="ghost" onClick={() => save(u.email, u.roles, !u.active)}>{u.active ? "Хаах" : "Нээх"}</button></td>
          </tr>
        ))}</tbody>
      </table>
      <h3>Ажилтан нэмэх</h3>
      <div className="row2 wrap">
        <input placeholder="name@toktok.mn" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        {Object.keys(ROLE_MN).map((r) => (
          <label key={r} className="chk"><input type="checkbox" checked={form.roles.includes(r)}
            onChange={() => setForm({ ...form, roles: toggle(form.roles, r) })} /> {ROLE_MN[r]}</label>
        ))}
        <button onClick={() => { save(form.email.trim().toLowerCase(), form.roles); setForm({ email: "", roles: ["operator"] }); }}
                disabled={!/^[^@\s]+@[^@\s]+$/.test(form.email) || !form.roles.length}>Нэмэх</button>
      </div>
    </section>
  );
}

type Audit = { id: number; actor: string; action: string; target: string | null; detail: Record<string, unknown>; created_at: string };

export function AuditPanel({ onErr }: { onErr: (e: string | null) => void }) {
  const [rows, setRows] = useState<Audit[]>([]);
  useEffect(() => {
    staffFetch<Audit[]>("/api/admin/audit").then((x) => { setRows(x); onErr(null); }).catch((e) => onErr(String(e)));
  }, [onErr]);
  return (
    <table>
      <thead><tr><th>Огноо</th><th>Хэн</th><th>Үйлдэл</th><th>Объект</th><th>Дэлгэрэнгүй</th></tr></thead>
      <tbody>{rows.map((a) => (
        <tr key={a.id}><td className="small">{when(a.created_at)}</td><td>{a.actor}</td><td><code>{a.action}</code></td>
          <td><code>{a.target ?? ""}</code></td><td className="small muted">{JSON.stringify(a.detail).slice(0, 160)}</td></tr>
      ))}</tbody>
    </table>
  );
}
