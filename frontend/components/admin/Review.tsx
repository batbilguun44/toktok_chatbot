"use client";

import { useCallback, useEffect, useState } from "react";
import { Me, staffFetch } from "@/lib/api";
import { Badge, Diff, PERSONA_MN, personLabel, when } from "./common";
import Quality from "./Quality";

type V = { doc_id: string; version: number; persona: string; title: string; body: string; author: string;
  source: string; updated_at: string; visibility: string };
// kind: knowledge document or bot instructions (one per persona; doc_id = persona).
type Item = { kind: "kb" | "prompt"; draft: V; published: V | null };

const base = (it: Item) =>
  it.kind === "kb" ? `/api/admin/kb/${it.draft.doc_id}` : `/api/admin/prompts/${it.draft.doc_id}`;
const keyOf = (it: Item) => `${it.kind}:${it.draft.doc_id}`;

/** Everything waiting for an admin: knowledge changes and bot-instruction changes. */
export default function Review({ me, onErr, onOpenDoc }: {
  me: Me; onErr: (e: string | null) => void; onOpenDoc?: (docId: string) => void;
}) {
  const [items, setItems] = useState<Item[]>([]);
  const [view, setView] = useState<"pending" | "history">("pending");
  const [comments, setComments] = useState<Record<string, string>>({});
  const canPublish = me.permissions.includes("kb.publish");

  const load = useCallback(async () => {
    try {
      const [kb, prompts] = await Promise.all([
        staffFetch<Omit<Item, "kind">[]>("/api/admin/kb/review"),
        staffFetch<Omit<Item, "kind">[]>("/api/admin/prompts-review"),
      ]);
      setItems([...prompts.map((x) => ({ ...x, kind: "prompt" as const })), ...kb.map((x) => ({ ...x, kind: "kb" as const }))]);
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => {
    load();
    // New submissions from operators appear without a reload.
    const refresh = () => { if (!document.hidden) load(); };
    const t = setInterval(refresh, 10_000);
    window.addEventListener("focus", refresh);
    return () => { clearInterval(t); window.removeEventListener("focus", refresh); };
  }, [load]);

  async function decide(it: Item, action: "approve" | "reject") {
    try {
      await staffFetch(`${base(it)}/versions/${it.draft.version}/${action}`, {
        method: "POST", body: JSON.stringify({ comment: comments[keyOf(it)] || null }),
      });
      await load();
    } catch (e) {
      onErr(String(e));
    }
  }

  return (
    <section className="review">
      <nav className="tabs subtabs">
        <button className={view === "pending" ? "active" : ""} onClick={() => setView("pending")}>
          Хүлээгдэж буй{items.length > 0 && <span className="count">{items.length}</span>}
        </button>
        <button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>Хянасан түүх</button>
      </nav>
      {view === "history" && <ReviewHistory onErr={onErr} onOpenDoc={onOpenDoc} />}
      {view === "pending" && !items.length && <p className="muted">Хянах зүйл алга.</p>}
      {view === "pending" && items.map((it) => {
        const k = keyOf(it);
        const own = it.draft.author === me.email && !me.roles.includes("admin");
        return (
          <article key={k} className="review-item">
            <header>
              {it.kind === "prompt"
                ? <><span className="badge in_review">Ботын заавар</span>{" "}<b>{PERSONA_MN[it.draft.persona]}</b>{" "}
                    <code className="muted">v{it.draft.version}</code></>
                : <><span className="badge">Мэдлэгийн сан</span>{" "}<b>{it.draft.title}</b>{" "}
                    <code className="muted">{it.draft.doc_id} v{it.draft.version}</code></>}
              <div className="small muted">
                {it.kind === "kb" && <>{PERSONA_MN[it.draft.persona]} · </>}{it.draft.author} · {when(it.draft.updated_at)}
                {it.draft.source === "ai-suggestion" && " · AI санал (баримтжуулах шаардлагатай)"}
                {it.draft.visibility === "internal" && it.kind === "kb" && " · дотоод"}
                {it.kind === "prompt" && " · батлагдвал шинээр эхлэх ярианд хэрэгжинэ"}
              </div>
            </header>
            {it.published ? <Diff before={it.published.body} after={it.draft.body} />
              : <pre className="diff">{it.draft.body.split("\n").map((l, i) => <div key={i} className="add">+ {l}</div>)}</pre>}
            {/\[ТОДРУУЛАХ/.test(it.draft.body) && <div className="err small">Бөглөөгүй [ТОДРУУЛАХ] хэсэг байна — нийтлэхээс өмнө засна уу.</div>}
            {it.kind === "kb" && (
              <Quality auto input={{ persona: it.draft.persona, title: it.draft.title, body: it.draft.body, doc_id: it.draft.doc_id }} />
            )}
            {canPublish && (
              <div className="row2">
                <input placeholder="Тайлбар (буцаахад заавал)" value={comments[k] ?? ""}
                       onChange={(e) => setComments({ ...comments, [k]: e.target.value })} />
                <button onClick={() => decide(it, "approve")} disabled={own}
                        title={own ? "Өөрийн өөрчлөлтийг өөр хүн батлах ёстой" : ""}>Батлах</button>
                <button className="ghost" onClick={() => decide(it, "reject")} disabled={!comments[k]}>Буцаах</button>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}

type HistoryRow = { kind: "kb" | "prompt"; doc_id: string; decision: "approve" | "reject" | "rollback"; version: number | null;
  to_version: number | null; reviewer: string; at: string; comment: string | null; title: string; persona: string | null;
  author: string | null };
const DECISION_MN = { approve: "Баталсан", reject: "Буцаасан", rollback: "Хуучин хувилбар руу буцаасан" };
const DECISION_KIND = { approve: "ok", reject: "rejected", rollback: "in_review" };

/** Past decisions (from the audit log): what was approved / rejected / rolled back, by whom, why. */
function ReviewHistory({ onErr, onOpenDoc }: { onErr: (e: string | null) => void; onOpenDoc?: (id: string) => void }) {
  const [rows, setRows] = useState<HistoryRow[] | null>(null);
  const [f, setF] = useState({ q: "", decision: "", kind: "", reviewer: "" });

  useEffect(() => {
    staffFetch<HistoryRow[]>("/api/admin/review-history?limit=300").then(setRows).catch((e) => onErr(String(e)));
  }, [onErr]);

  const reviewers = [...new Set((rows ?? []).map((r) => r.reviewer))].sort();
  const shown = (rows ?? []).filter((r) =>
    (!f.decision || r.decision === f.decision) && (!f.kind || r.kind === f.kind) && (!f.reviewer || r.reviewer === f.reviewer) &&
    (!f.q || `${r.title} ${r.doc_id} ${r.comment ?? ""} ${r.author ?? ""}`.toLowerCase().includes(f.q.toLowerCase())));

  return (
    <div className="review-history">
      <div className="kb-filters">
        <input placeholder="Гарчиг, ID, тайлбар, зохиогчоор хайх…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <select value={f.decision} onChange={(e) => setF({ ...f, decision: e.target.value })} aria-label="Шийдвэр">
          <option value="">Бүх шийдвэр</option><option value="approve">Баталсан</option>
          <option value="reject">Буцаасан</option><option value="rollback">Хуучин хувилбар руу</option>
        </select>
        <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })} aria-label="Төрөл">
          <option value="">Мэдлэг + ботын заавар</option><option value="kb">Мэдлэгийн сан</option>
          <option value="prompt">Ботын заавар</option>
        </select>
        <select value={f.reviewer} onChange={(e) => setF({ ...f, reviewer: e.target.value })} aria-label="Хянагч">
          <option value="">Хянасан хүн: бүгд</option>
          {reviewers.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <table>
        <thead><tr><th>Огноо</th><th>Шийдвэр</th><th>Баримт</th><th>Бичсэн → Хянасан</th><th>Тайлбар</th></tr></thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              <td className="small">{when(r.at)}</td>
              <td><Badge kind={DECISION_KIND[r.decision]}>{DECISION_MN[r.decision]}</Badge></td>
              <td>
                {r.kind === "prompt" ? <>Ботын заавар · {PERSONA_MN[r.doc_id] ?? r.doc_id}</>
                  : onOpenDoc ? <button className="linkish" onClick={() => onOpenDoc(r.doc_id)}>{r.title}</button> : r.title}
                <div className="small muted">
                  <code>{r.doc_id}</code>{r.version != null && ` v${r.version}`}
                  {r.decision === "rollback" && r.to_version != null && ` (v${r.to_version}-ийн агуулга)`}
                </div>
              </td>
              <td className="small">{personLabel(r.author)} → {r.reviewer}</td>
              <td className="small">{r.comment ?? ""}</td>
            </tr>
          ))}
          {rows && !shown.length && <tr><td colSpan={5} className="muted">Олдсонгүй</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
