"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE, staffFetch } from "@/lib/api";
import RichText from "@/components/RichText";
import { Badge, CATEGORY_MN, PERSONA_MN, when } from "./common";

type Row = { id: string; persona: string; started_at: string; last_at: string; turns: number; escalated: boolean;
  preview?: string; negative_feedback?: number; errors: number; handoff_status: "open" | "resolved" | null;
  categories: string[]; assignees: string[]; tickets: string[] };
type Event =
  | { kind: "turn"; at: string; turn_id: string; user_text: string; bot_text: string; intent: string | null;
      sources: string[]; tools: string[]; stop: string | null; latency_ms: number | null; rating: 1 | -1 | null }
  | { kind: "escalation"; at: string; escalation_id: string; category: string; summary: string; status: string;
      assignee: string | null; resolution: string | null }
  | { kind: "handoff"; at: string; id: number; sender: string; author: string | null; text: string };
type Detail = { conversation: Row; events: Event[] };
type Facets = { intents: Record<string, Record<string, string>>; categories: string[]; operators: string[] };
type Filter = { persona: string; escalated: string; negative: boolean; errors: boolean; q: string; intent: string;
  category: string; assignee: string; handoff_status: string; date_from: string; date_to: string };

const PAGE = 30;
const EMPTY: Filter = { persona: "", escalated: "", negative: false, errors: false, q: "", intent: "", category: "",
  assignee: "", handoff_status: "", date_from: "", date_to: "" };
const STATUS_MN: Record<string, string> = { open: "Оператортой (нээлттэй)", resolved: "Оператор шийдсэн" };

function query(f: Filter, extra: Record<string, string | number> = {}) {
  const p = new URLSearchParams();
  for (const k of ["persona", "escalated", "intent", "category", "assignee", "handoff_status", "date_from", "date_to"] as const) {
    if (f[k]) p.set(k, f[k]);
  }
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.negative) p.set("negative", "true");
  if (f.errors) p.set("errors", "true");
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

/**
 * Stored conversations: filter, read the full timeline; admins can also export (Excel opens the CSV).
 * Used on /admin (canExport) and as the operators' read-only "Түүх" tab on /operator.
 */
export default function Conversations({ onErr, canExport, onOpenDoc }: {
  onErr: (e: string | null) => void; canExport: boolean; onOpenDoc?: (docId: string) => void;
}) {
  const [draft, setDraft] = useState<Filter>(EMPTY);
  const [filter, setFilter] = useState<Filter>(EMPTY);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: Row[] } | null>(null);
  const [facets, setFacets] = useState<Facets | null>(null);
  const [more, setMore] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);

  useEffect(() => { staffFetch<Facets>("/api/admin/conversation-facets").then(setFacets).catch(() => {}); }, []);

  const load = useCallback(async () => {
    try {
      setData(await staffFetch(`/api/admin/conversations?${query(filter, { limit: PAGE, offset: page * PAGE })}`));
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [filter, page, onErr]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!sel) return;
    staffFetch<Detail>(`/api/admin/conversations/${sel}`).then(setDetail).catch((e) => onErr(String(e)));
  }, [sel, onErr]);

  const apply = () => { setPage(0); setFilter(draft); };
  const reset = () => { setDraft(EMPTY); setPage(0); setFilter(EMPTY); };
  const set = (patch: Partial<Filter>) => setDraft({ ...draft, ...patch });
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const active = Object.entries(filter).filter(([k, v]) => v && v !== EMPTY[k as keyof Filter]).length;

  // Topics for the chosen user type (or all types), labelled with the router's Mongolian description.
  const intents = facets
    ? Object.entries(facets.intents).filter(([p]) => !draft.persona || p === draft.persona)
        .flatMap(([p, m]) => Object.entries(m).map(([id, label]) => ({ id, label, p })))
        .filter((x, i, a) => a.findIndex((y) => y.id === x.id) === i)
    : [];

  return (
    <section className="convs">
      <div className="conv-filters">
        <input className="conv-search" placeholder="Мессеж, ярианы эсвэл тасалбарын дугаараар хайх…" value={draft.q}
               onChange={(e) => set({ q: e.target.value })} onKeyDown={(e) => e.key === "Enter" && apply()} />
        <select value={draft.persona} onChange={(e) => set({ persona: e.target.value, intent: "" })} aria-label="Хэрэглэгчийн төрөл">
          <option value="">Бүх төрөл</option><option value="driver">Хүргэгч</option>
          <option value="client">Харилцагч</option><option value="customer">Хэрэглэгч</option>
        </select>
        <select value={draft.intent} onChange={(e) => set({ intent: e.target.value })} aria-label="Сэдэв">
          <option value="">Бүх сэдэв</option>
          {intents.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
        </select>
        <select value={draft.escalated} onChange={(e) => set({ escalated: e.target.value })} aria-label="Хэн шийдсэн">
          <option value="">Бот + оператор</option><option value="false">Бот өөрөө шийдсэн</option>
          <option value="true">Оператор руу шилжсэн</option>
        </select>
        <input type="date" value={draft.date_from} onChange={(e) => set({ date_from: e.target.value })} aria-label="Эхлэх огноо" />
        <input type="date" value={draft.date_to} onChange={(e) => set({ date_to: e.target.value })} aria-label="Дуусах огноо" />
        <button className="ghost" onClick={() => setMore(!more)}>{more ? "▲ Бусад шүүлтүүр" : "▼ Бусад шүүлтүүр"}</button>
        <button onClick={apply}>Шүүх</button>
        {active > 0 && <button className="ghost" onClick={reset}>Цэвэрлэх ({active})</button>}
        {canExport && (
          <a className="button ghost-link" href={`${API_BASE}/api/admin/conversations.csv?${query(filter)}`}>⬇ Excel (CSV)</a>
        )}
      </div>
      {more && (
        <div className="conv-filters">
          <select value={draft.category} onChange={(e) => set({ category: e.target.value })} aria-label="Шилжүүлсэн шалтгаан">
            <option value="">Шилжүүлсэн шалтгаан: бүгд</option>
            {facets?.categories.map((c) => <option key={c} value={c}>{CATEGORY_MN[c] ?? c}</option>)}
          </select>
          <select value={draft.handoff_status} onChange={(e) => set({ handoff_status: e.target.value })} aria-label="Операторын төлөв">
            <option value="">Операторын төлөв: бүгд</option>
            <option value="open">Хүлээгдэж буй / ярьж буй</option><option value="resolved">Шийдсэн</option>
          </select>
          <select value={draft.assignee} onChange={(e) => set({ assignee: e.target.value })} aria-label="Хариуцсан оператор">
            <option value="">Хариуцсан оператор: бүгд</option>
            {facets?.operators.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <label className="chk"><input type="checkbox" checked={draft.negative} onChange={(e) => set({ negative: e.target.checked })} /> 👎 авсан</label>
          <label className="chk"><input type="checkbox" checked={draft.errors} onChange={(e) => set({ errors: e.target.checked })} /> Бот хариулж чадаагүй</label>
        </div>
      )}

      <div className="kb">
        <div className="kb-list">
          <div className="small muted">{data ? `${data.total} яриа` : "…"}</div>
          <table>
            <thead><tr><th>Огноо</th><th>Төрөл</th><th>Эхний асуулт</th><th>Төлөв</th></tr></thead>
            <tbody>
              {data?.items.map((r) => (
                <tr key={r.id} className={`clickable ${sel === r.id ? "selected" : ""}`} onClick={() => setSel(r.id)}>
                  <td className="small">{when(r.last_at)}</td>
                  <td>{PERSONA_MN[r.persona] ?? r.persona}</td>
                  <td><div className="conv-preview">{r.preview}</div><div className="small muted">{r.turns} асуулт</div></td>
                  <td><RowBadges r={r} /></td>
                </tr>
              ))}
              {data && !data.items.length && <tr><td colSpan={4} className="muted">Яриа олдсонгүй</td></tr>}
            </tbody>
          </table>
          {pages > 1 && (
            <div className="row2">
              <button className="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>‹</button>
              <span className="small muted">{page + 1} / {pages}</span>
              <button className="ghost" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>›</button>
            </div>
          )}
        </div>

        <div className="kb-edit">
          {!detail ? <p className="muted">Яриа сонгоно уу.</p> : (
            <>
              <h3>{PERSONA_MN[detail.conversation.persona]} · {when(detail.conversation.started_at)}</h3>
              <div className="small muted">
                <code>{detail.conversation.id}</code> · {detail.conversation.turns} асуулт
                {detail.conversation.tickets?.length > 0 && <> · тасалбар: {detail.conversation.tickets.join(", ")}</>}
              </div>
              <div><RowBadges r={detail.conversation} /></div>
              <div className="conv-thread">
                {detail.events.map((e, i) => <EventView key={i} e={e} onOpenDoc={onOpenDoc} />)}
              </div>
              <p className="small muted">Хувийн мэдээллийг (утас, регистр, карт) хадгалахдаа нууцалсан.</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function RowBadges({ r }: { r: Row }) {
  return (
    <span className="conv-badges">
      {r.handoff_status
        ? <Badge kind={r.handoff_status === "open" ? "in_review" : "ok"}>{STATUS_MN[r.handoff_status]}</Badge>
        : <Badge kind="muted">Бот</Badge>}
      {r.categories.map((c) => <Badge key={c} kind="muted">{CATEGORY_MN[c] ?? c}</Badge>)}
      {r.assignees.map((a) => <Badge key={a} kind="muted">{a.split("@")[0]}</Badge>)}
      {(r.negative_feedback ?? 0) > 0 && <Badge kind="rejected">👎 {r.negative_feedback}</Badge>}
      {r.errors > 0 && <Badge kind="rejected">Алдаа {r.errors}</Badge>}
    </span>
  );
}

function EventView({ e, onOpenDoc }: { e: Event; onOpenDoc?: (docId: string) => void }) {
  if (e.kind === "turn") {
    return (
      <>
        <div className="row user"><div className="bubble user"><div className="text">{e.user_text}</div></div></div>
        <div className="row assistant">
          <div className="bubble assistant">
            <RichText text={e.bot_text} />
            <div className="conv-meta">
              {when(e.at)}
              {e.intent && <> · {e.intent}</>}
              {e.latency_ms != null && <> · {(e.latency_ms / 1000).toFixed(1)}с</>}
              {e.sources.length > 0 && <> · мэдлэг: {e.sources.map((s, i) => (
                <span key={s}>{i > 0 && ", "}{onOpenDoc
                  ? <button className="linkish" title="Мэдлэгийн санд нээх" onClick={() => onOpenDoc(s)}>{s}</button> : s}</span>
              ))}</>}
              {e.tools.length > 0 && <> · хэрэгсэл: {e.tools.join(", ")}</>}
              {e.rating === 1 && " · 👍"}{e.rating === -1 && " · 👎"}
              {e.stop && e.stop !== "end" && <> · <span className="err">{e.stop}</span></>}
            </div>
          </div>
        </div>
      </>
    );
  }
  if (e.kind === "escalation") {
    return (
      <div className="conv-esc small">
        ⇄ Оператор руу шилжсэн ({CATEGORY_MN[e.category] ?? e.category}) · {e.escalation_id}
        {e.assignee ? ` · ${e.assignee}` : ""}
        <div className="muted">{e.summary}</div>
        {e.resolution && <div>Шийдвэр: {e.resolution}</div>}
      </div>
    );
  }
  const role = e.sender === "user" ? "user" : e.sender === "system" ? "system" : "operator";
  return (
    <div className={`row ${role === "operator" ? "assistant" : role}`}>
      <div className={`bubble ${role}`}>
        {role === "operator" && <div className="who">{e.author ?? "Оператор"}</div>}
        {role === "operator" ? <RichText text={e.text} /> : <div className="text">{e.text}</div>}
      </div>
    </div>
  );
}
