"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE, staffFetch } from "@/lib/api";
import RichText from "@/components/RichText";
import { Badge, PERSONA_MN, when } from "./common";

type Row = { id: string; persona: string; started_at: string; last_at: string; turns: number; escalated: boolean;
  preview: string; negative_feedback: number };
type Event =
  | { kind: "turn"; at: string; turn_id: string; user_text: string; bot_text: string; intent: string | null;
      sources: string[]; tools: string[]; stop: string | null; latency_ms: number | null; rating: 1 | -1 | null }
  | { kind: "escalation"; at: string; escalation_id: string; category: string; summary: string; status: string;
      assignee: string | null; resolution: string | null }
  | { kind: "handoff"; at: string; id: number; sender: string; author: string | null; text: string };
type Detail = { conversation: Row; events: Event[] };
type Filter = { persona: string; escalated: string; negative: boolean; q: string; date_from: string; date_to: string };

const PAGE = 30;
const EMPTY: Filter = { persona: "", escalated: "", negative: false, q: "", date_from: "", date_to: "" };

function query(f: Filter, extra: Record<string, string | number> = {}) {
  const p = new URLSearchParams();
  if (f.persona) p.set("persona", f.persona);
  if (f.escalated) p.set("escalated", f.escalated);
  if (f.negative) p.set("negative", "true");
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.date_from) p.set("date_from", f.date_from);
  if (f.date_to) p.set("date_to", f.date_to);
  for (const [k, v] of Object.entries(extra)) p.set(k, String(v));
  return p.toString();
}

/** All stored conversations: filter, read, export for analysis (Excel opens the CSV). */
export default function Conversations({ onErr }: { onErr: (e: string | null) => void }) {
  const [draft, setDraft] = useState<Filter>(EMPTY);
  const [filter, setFilter] = useState<Filter>(EMPTY);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: Row[] } | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);

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
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;

  return (
    <section className="convs">
      <div className="conv-filters">
        <input placeholder="Мессежээс хайх…" value={draft.q} onChange={(e) => setDraft({ ...draft, q: e.target.value })}
               onKeyDown={(e) => e.key === "Enter" && apply()} />
        <select value={draft.persona} onChange={(e) => setDraft({ ...draft, persona: e.target.value })} aria-label="Төрөл">
          <option value="">Бүх төрөл</option><option value="driver">Хүргэгч</option>
          <option value="client">Харилцагч</option><option value="customer">Хэрэглэгч</option>
        </select>
        <select value={draft.escalated} onChange={(e) => setDraft({ ...draft, escalated: e.target.value })} aria-label="Оператор">
          <option value="">Бүгд</option><option value="true">Оператор руу шилжсэн</option>
          <option value="false">Бот шийдсэн</option>
        </select>
        <label className="chk"><input type="checkbox" checked={draft.negative}
               onChange={(e) => setDraft({ ...draft, negative: e.target.checked })} /> 👎 авсан</label>
        <input type="date" value={draft.date_from} onChange={(e) => setDraft({ ...draft, date_from: e.target.value })} aria-label="Эхлэх огноо" />
        <input type="date" value={draft.date_to} onChange={(e) => setDraft({ ...draft, date_to: e.target.value })} aria-label="Дуусах огноо" />
        <button onClick={apply}>Шүүх</button>
        <a className="button ghost-link" href={`${API_BASE}/api/admin/conversations.csv?${query(filter)}`}>⬇ Excel (CSV)</a>
      </div>

      <div className="kb">
        <div className="kb-list">
          <div className="small muted">{data ? `${data.total} яриа` : "…"}</div>
          <table>
            <thead><tr><th>Огноо</th><th>Төрөл</th><th>Эхний асуулт</th><th></th></tr></thead>
            <tbody>
              {data?.items.map((r) => (
                <tr key={r.id} className={`clickable ${sel === r.id ? "selected" : ""}`} onClick={() => setSel(r.id)}>
                  <td className="small">{when(r.last_at)}</td>
                  <td>{PERSONA_MN[r.persona] ?? r.persona}</td>
                  <td><div className="conv-preview">{r.preview}</div><div className="small muted">{r.turns} асуулт</div></td>
                  <td>
                    {r.escalated && <Badge kind="in_review">Оператор</Badge>}{" "}
                    {r.negative_feedback > 0 && <Badge kind="rejected">👎 {r.negative_feedback}</Badge>}
                  </td>
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
                {detail.conversation.escalated ? " · оператор руу шилжсэн" : " · бот шийдсэн"}
              </div>
              <div className="conv-thread">
                {detail.events.map((e, i) => <EventView key={i} e={e} />)}
              </div>
              <p className="small muted">Хувийн мэдээллийг (утас, регистр, карт) хадгалахдаа нууцалсан.</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function EventView({ e }: { e: Event }) {
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
              {e.sources.length > 0 && <> · мэдлэг: {e.sources.join(", ")}</>}
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
        ⇄ Оператор руу шилжсэн ({e.category}) · {e.status}{e.assignee ? ` · ${e.assignee}` : ""}
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
