"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Me, staffFetch } from "@/lib/api";
import { Badge, Diff, PERSONA_MN, STATE_MN, personLabel, when } from "./common";

type Version = {
  version: number; body: string; state: string; source: string; author: string; updated_at: string;
  reviewer: string | null; review_comment: string | null;
};
type PersonaRow = { persona: string; locked: string; published: Version | null; draft: Version | null };
type Overview = { personas: PersonaRow[]; max_chars: number };
type Answer = { answer?: string; busy?: boolean };

const PERSONAS = ["driver", "client", "customer"];

export default function Prompts({ me, onErr }: { me: Me; onErr: (e: string | null) => void }) {
  const canEdit = me.permissions.includes("kb.edit");
  const canPublish = me.permissions.includes("kb.publish");
  const [ov, setOv] = useState<Overview | null>(null);
  const [persona, setPersona] = useState("driver");
  const [versions, setVersions] = useState<Version[]>([]);
  const [text, setText] = useState("");
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [cmp, setCmp] = useState<{ current: Answer; edited: Answer }>({ current: {}, edited: {} });

  const row = ov?.personas.find((p) => p.persona === persona);
  const openDraft = row?.draft ?? null;
  const published = row?.published ?? null;

  const load = useCallback(async (p: string, resetText: boolean) => {
    try {
      const o = await staffFetch<Overview>("/api/admin/prompts");
      setOv(o);
      const d = await staffFetch<{ versions: Version[] }>(`/api/admin/prompts/${p}`);
      setVersions(d.versions);
      const r = o.personas.find((x) => x.persona === p);
      if (resetText) setText((r?.draft ?? r?.published)?.body ?? "");
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => { load(persona, true); }, [load, persona]);

  // Pick up review decisions made elsewhere; keep the text if it has unsaved edits.
  const live = useRef({ persona, clean: true });
  useEffect(() => {
    const refresh = () => {
      if (!document.hidden) load(live.current.persona, live.current.clean).catch(() => {});
    };
    const t = setInterval(refresh, 10_000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      setComment("");
      await load(persona, true);
      setMsg(ok);
    } catch (e) {
      onErr(String(e));
    }
  }

  async function ask(which: "current" | "edited") {
    setCmp((c) => ({ ...c, [which]: { busy: true } }));
    try {
      const r = await staffFetch<{ answer: string }>("/api/admin/prompts-preview", {
        method: "POST",
        body: JSON.stringify({ persona, question, body: which === "edited" ? text : undefined }),
      });
      setCmp((c) => ({ ...c, [which]: { answer: r.answer } }));
    } catch (e) {
      setCmp((c) => ({ ...c, [which]: {} }));
      onErr(String(e));
    }
  }

  const max = ov?.max_chars ?? 4000;
  const dirty = text !== ((openDraft ?? published)?.body ?? "");
  const base = `/api/admin/prompts/${persona}`;
  live.current = { persona, clean: !dirty };

  return (
    <section className="prompts">
      <div className="row2 wrap">
        {PERSONAS.map((p) => {
          const r = ov?.personas.find((x) => x.persona === p);
          return (
            <button key={p} className={persona === p ? "" : "ghost"}
                    onClick={() => { setPersona(p); setMsg(null); setCmp({ current: {}, edited: {} }); }}>
              {PERSONA_MN[p]}{r?.draft ? ` · ${STATE_MN[r.draft.state]}` : ""}
            </button>
          );
        })}
      </div>
      <p className="muted small">
        Ботын өнгө аяс, анхаарах сэдэв, хэзээ оператор руу шилжүүлэх зэргийг энд заана. Нийтэлсэн заавар{" "}
        <b>шинээр эхэлсэн ярианд</b> хэрэгжинэ. Хамгаалалтын дүрэм (зохиохгүй байх, нууцлал) түгжээтэй бөгөөд
        энэ заавартай зөрчилдвөл түгжээтэй дүрэм давамгайлна.
      </p>

      <div className="kb">
        <div className="kb-edit">
          <h3>{PERSONA_MN[persona]} — ботын заавар</h3>
          <div className="small muted">
            {published && <>Нийтлэгдсэн: v{published.version} · {personLabel(published.author)}{published.reviewer ? ` → ${published.reviewer}` : ""} · {when(published.updated_at)}</>}
            {openDraft && <div>Ноорог v{openDraft.version} · {STATE_MN[openDraft.state]} · {openDraft.author}</div>}
            {openDraft?.review_comment && <div className="err">Тайлбар: {openDraft.review_comment}</div>}
          </div>
          <textarea rows={16} value={text} disabled={!canEdit} onChange={(e) => setText(e.target.value)}
                    aria-label="Ботын заавар" />
          <div className={`small ${text.length > max ? "err" : "muted"}`}>{text.length} / {max} тэмдэгт</div>
          {canEdit && (
            <div className="row2 wrap">
              <button disabled={!dirty || text.trim().length < 10 || text.length > max}
                      onClick={() => act(() => staffFetch(`${base}/draft`, { method: "PUT", body: JSON.stringify({ body: text }) }),
                        "Ноорог хадгаллаа")}>Ноорог хадгалах</button>
              {openDraft && ["draft", "rejected"].includes(openDraft.state) && !dirty && (
                <button onClick={() => act(() => staffFetch(`${base}/submit`, { method: "POST" }), "Хянуулахаар илгээлээ")}>
                  Хянуулахаар илгээх</button>
              )}
              {openDraft && (
                <button className="ghost" onClick={() => act(() => staffFetch(`${base}/draft`, { method: "DELETE" }),
                  "Ноорог устгалаа")}>Ноорог устгах</button>
              )}
            </div>
          )}
          {canPublish && openDraft?.state === "in_review" && (
            <div className="review-box">
              <h3>Хянах</h3>
              <input placeholder="Тайлбар (буцаахад заавал)" value={comment} onChange={(e) => setComment(e.target.value)} />
              <div className="row2 wrap">
                <button onClick={() => act(() => staffFetch(`${base}/versions/${openDraft.version}/approve`,
                  { method: "POST", body: JSON.stringify({ comment: comment || null }) }), "Баталж нийтэллээ")}>Батлах</button>
                <button className="ghost" disabled={!comment}
                        onClick={() => act(() => staffFetch(`${base}/versions/${openDraft.version}/reject`,
                          { method: "POST", body: JSON.stringify({ comment }) }), "Буцаалаа")}>Буцаах</button>
              </div>            </div>
          )}
          {msg && <div className="ok">{msg}</div>}

          {published && (openDraft || dirty) && published.body !== text && (
            <><h3>Өөрчлөлт (нийтлэгдсэн v{published.version} → засаж буй)</h3><Diff before={published.body} after={text} /></>
          )}
        </div>

        <div className="kb-list">
          <h3>Урьдчилж харах</h3>
          <p className="muted small">Нэг асуултад одоогийн болон засаж буй заавраар бот хэрхэн хариулахыг харьцуулна (хадгалаагүй текстээр ч болно).</p>
          <div className="row2">
            <input placeholder="Хэрэглэгчийн асуулт" value={question} onChange={(e) => setQuestion(e.target.value)} />
            <button disabled={question.length < 2 || cmp.current.busy || cmp.edited.busy}
                    onClick={() => { ask("current"); ask("edited"); }}>Турших</button>
          </div>
          <div className="compare">
            <div className="preview"><div className="small muted">Одоогийн (нийтлэгдсэн)</div>{cmp.current.busy ? "…" : cmp.current.answer}</div>
            <div className="preview"><div className="small muted">Засаж буй</div>{cmp.edited.busy ? "…" : cmp.edited.answer}</div>
          </div>

          <details>
            <summary>Түгжээтэй дүрэм (зөвхөн хөгжүүлэгч засна)</summary>
            <pre className="locked">{row?.locked}</pre>
          </details>

          {versions.length > 0 && (
            <>
              <h3>Түүх</h3>
              <table>
                <tbody>
                  {versions.map((v) => (
                    <tr key={v.version}>
                      <td>v{v.version}</td>
                      <td><Badge kind={v.state}>{STATE_MN[v.state]}</Badge>{v.source === "default" ? " · анхны" : ""}</td>
                      <td className="small">{personLabel(v.author)}{v.reviewer ? ` → ${v.reviewer}` : ""}<div className="muted">{when(v.updated_at)}</div></td>
                      <td>{canPublish && v.state === "superseded" && !openDraft && (
                        <button className="ghost" onClick={() => act(() => staffFetch(`${base}/rollback`,
                          { method: "POST", body: JSON.stringify({ to_version: v.version }) }), `v${v.version} руу буцаалаа`)}>Буцаах</button>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
