"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, Me, staffFetch } from "@/lib/api";
import { Badge, Diff, PERSONA_MN, STATE_MN, personLabel, when } from "./common";
import Quality from "./Quality";

type Usage = { uses: number; negative: number; last_used: string | null };
type DocRow = {
  id: string; status: string; published_version: number | null; persona: string | null; title: string;
  visibility: string | null; updated_at: string | null; created_by: string | null; last_editor: string | null;
  tags: string[]; published_at: string | null; usage: Usage;
  draft: { version: number; state: string; author: string; source: string; updated_at: string } | null;
};
type Overview = { docs: DocRow[]; modes: Record<string, string>; est_tokens: Record<string, number>;
  full_context_max_tokens: number; usage_days: number; stale_days: number };
type Version = {
  doc_id: string; version: number; persona: string; title: string; body: string; tags: string[];
  visibility: string; state: string; source: string; author: string; created_at: string; updated_at: string;
  reviewer: string | null; review_comment: string | null;
};
type Found = { doc_id: string; title: string; snippet: string; match: ("text" | "meaning")[] };
type Hit = { doc_id: string; title: string; score: number; text: string };
type Form = { id: string; persona: string; title: string; body: string; tags: string; visibility: string };
type ListFilter = { q: string; persona: string; state: string; visibility: string; person: string; tag: string; sort: string };

const EMPTY: Form = { id: "", persona: "driver", title: "", body: "", tags: "", visibility: "public" };
const NO_FILTER: ListFilter = { q: "", persona: "", state: "", visibility: "", person: "", tag: "", sort: "updated" };
const PAGE_SIZES = [10, 30, 50, 100];
const DAY = 86_400_000;

const headOf = (vs: Version[]) => vs.find((v) => ["draft", "in_review", "rejected"].includes(v.state)) ??
  vs.find((v) => v.state === "published") ?? vs[0];
const formOf = (id: string, v: Version): Form => ({ id, persona: v.persona, title: v.title, body: v.body,
  tags: v.tags.join(", "), visibility: v.visibility });
const sameAsForm = (v: Version, f: Form) => {
  const g = formOf(f.id, v);
  return g.persona === f.persona && g.title === f.title && g.body === f.body && g.tags === f.tags && g.visibility === f.visibility;
};

export default function Knowledge({ me, onErr, openDoc }: { me: Me; onErr: (e: string | null) => void; openDoc?: string | null }) {
  const canEdit = me.permissions.includes("kb.edit");
  const canPublish = me.permissions.includes("kb.publish");
  const [ov, setOv] = useState<Overview | null>(null);
  const [lf, setLf] = useState<ListFilter>(NO_FILTER);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(30);
  const [found, setFound] = useState<{ q: string; rows: Found[] } | null>(null);
  const [ask, setAsk] = useState<{ q: string; persona: string; hits?: Hit[]; busy?: boolean }>({ q: "", persona: "driver" });
  const [sel, setSel] = useState<string | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [form, setForm] = useState<Form>(EMPTY);
  const [msg, setMsg] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ question: string; answer?: string; mode?: string; busy?: boolean }>({ question: "" });
  const [importResult, setImportResult] = useState<{ created: string[]; errors: { row: number; error: string }[] } | null>(null);

  const load = useCallback(async () => {
    try {
      setOv(await staffFetch<Overview>("/api/admin/kb"));
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => { load(); }, [load]);

  const open = useCallback(async (id: string) => {
    setSel(id);
    setMsg(null);
    setPreview({ question: "" });
    try {
      const d = await staffFetch<{ versions: Version[] }>(`/api/admin/kb/${id}`);
      setVersions(d.versions);
      const head = headOf(d.versions);
      if (head) setForm(formOf(id, head));
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => { if (openDoc) open(openDoc); }, [openDoc, open]);

  // Pick up changes made elsewhere (e.g. an admin approving in another tab) without a page reload:
  // poll while the tab is visible and on refocus. The open document's form is refreshed only when
  // it has no unsaved edits, so typing is never overwritten.
  const live = useRef({ sel, form, versions });
  live.current = { sel, form, versions };
  useEffect(() => {
    const refresh = async () => {
      if (document.hidden) return;
      try {
        setOv(await staffFetch<Overview>("/api/admin/kb"));
        const { sel: id, form: f, versions: old } = live.current;
        if (!id) return;
        const d = await staffFetch<{ versions: Version[] }>(`/api/admin/kb/${id}`);
        if (live.current.sel !== id) return;
        setVersions(d.versions);
        const prev = headOf(old);
        const head = headOf(d.versions);
        if (head && (!prev || sameAsForm(prev, f))) setForm(formOf(id, head));
      } catch { /* transient; next tick retries */ }
    };
    const t = setInterval(refresh, 10_000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, []);

  async function act(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      onErr(null);
      await load();
      if (form.id) await open(form.id);
      setMsg(ok);
    } catch (e) {
      onErr(String(e));
    }
  }

  const openDraft = versions.find((v) => ["draft", "in_review", "rejected"].includes(v.state));
  const published = versions.find((v) => v.state === "published");
  const saveDraft = () => act(() => staffFetch(`/api/admin/kb/${encodeURIComponent(form.id)}/draft`, {
    method: "PUT", body: JSON.stringify({ persona: form.persona, title: form.title, body: form.body,
      visibility: form.visibility, tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean) }),
  }), "Ноорог хадгаллаа");

  async function runPreview() {
    setPreview((p) => ({ ...p, busy: true, answer: undefined }));
    try {
      const r = await staffFetch<{ answer: string; mode: string }>("/api/admin/kb-preview", {
        method: "POST",
        body: JSON.stringify({ persona: form.persona === "all" ? "customer" : form.persona, question: preview.question,
          doc_id: openDraft ? form.id : undefined }),
      });
      setPreview((p) => ({ ...p, busy: false, answer: r.answer, mode: r.mode }));
    } catch (e) {
      setPreview((p) => ({ ...p, busy: false }));
      onErr(String(e));
    }
  }

  async function upload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      setImportResult(await staffFetch("/api/admin/kb-import", { method: "POST", body: fd }));
      await load();
    } catch (e) {
      onErr(String(e));
    }
  }

  async function findContent() {
    if (lf.q.trim().length < 2) return;
    try {
      const rows = await staffFetch<Found[]>(`/api/admin/kb-find?${new URLSearchParams({ q: lf.q.trim(),
        ...(lf.persona && lf.persona !== "all" ? { persona: lf.persona } : {}) })}`);
      setFound({ q: lf.q.trim(), rows });
    } catch (e) {
      onErr(String(e));
    }
  }

  async function whichDocs() {
    setAsk((a) => ({ ...a, busy: true, hits: undefined }));
    try {
      const hits = await staffFetch<Hit[]>("/api/admin/kb-search", { method: "POST",
        body: JSON.stringify({ query: ask.q, persona: ask.persona }) });
      // One row per document (a document has several sections). Computed outside the state updater:
      // React may run updaters twice, which would empty a filter with side effects.
      const unique = hits.filter((h, i) => hits.findIndex((x) => x.doc_id === h.doc_id) === i);
      setAsk((a) => ({ ...a, busy: false, hits: unique }));
    } catch (e) {
      setAsk((a) => ({ ...a, busy: false }));
      onErr(String(e));
    }
  }

  // ------------------------------------------------------------- list filters
  const staleBefore = ov ? Date.now() - ov.stale_days * DAY : 0;
  const isStale = (d: DocRow) => d.status === "active" && !!d.published_at && Date.parse(d.published_at) < staleBefore;
  const people = useMemo(() => [...new Set((ov?.docs ?? []).flatMap((d) => [d.created_by, d.last_editor])
    .filter((p): p is string => !!p))].sort(), [ov]);
  const tags = useMemo(() => [...new Set((ov?.docs ?? []).flatMap((d) => d.tags))].sort(), [ov]);

  const rows = useMemo(() => {
    const q = lf.q.trim().toLowerCase();
    const out = (ov?.docs ?? []).filter((d) =>
      (!q || `${d.id} ${d.title} ${d.tags.join(" ")}`.toLowerCase().includes(q)) &&
      (!lf.persona || d.persona === lf.persona) &&
      (!lf.visibility || d.visibility === lf.visibility) &&
      (!lf.person || d.created_by === lf.person || d.last_editor === lf.person) &&
      (!lf.tag || d.tags.includes(lf.tag)) &&
      (!lf.state ||
        (lf.state === "published" && d.status === "active" && d.published_version) ||
        (lf.state === "draft" && d.draft) ||
        (lf.state === "in_review" && d.draft?.state === "in_review") ||
        (lf.state === "archived" && d.status === "archived") ||
        (lf.state === "stale" && isStale(d)) ||
        (lf.state === "unused" && d.status === "active" && d.published_version && !d.usage.uses) ||
        (lf.state === "disliked" && d.usage.negative > 0)));
    const by: Record<string, (a: DocRow, b: DocRow) => number> = {
      updated: (a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""),
      oldest: (a, b) => (a.published_at ?? "").localeCompare(b.published_at ?? ""),
      title: (a, b) => a.title.localeCompare(b.title, "mn"),
      uses: (a, b) => b.usage.uses - a.usage.uses,
      disliked: (a, b) => b.usage.negative - a.usage.negative,
    };
    return out.sort(by[lf.sort] ?? by.updated);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ov, lf]);
  useEffect(() => { setPage(0); }, [lf, pageSize]);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const first = rows.length ? page * pageSize + 1 : 0;
  const last = Math.min(rows.length, (page + 1) * pageSize);
  const setL = (patch: Partial<ListFilter>) => setLf({ ...lf, ...patch });
  const activeFilters = Object.entries(lf).filter(([k, v]) => v && v !== NO_FILTER[k as keyof ListFilter]).length;

  return (
    <section className="kb">
      <div className="kb-list">
        {ov && (
          <div className="modes small muted">
            Горим:{" "}
            {Object.entries(ov.modes).map(([p, m]) => (
              <span key={p} title={`~${ov.est_tokens[p]} / ${ov.full_context_max_tokens} токен`}>
                {PERSONA_MN[p]}: <b>{m === "full" ? "Бүтэн мэдлэг" : "Хайлт (RAG)"}</b>{" "}
              </span>
            ))}
          </div>
        )}

        <div className="row2">
          <input placeholder="Гарчиг, ID, tag-аар шүүх…" value={lf.q} onChange={(e) => setL({ q: e.target.value })}
                 onKeyDown={(e) => e.key === "Enter" && findContent()} />
          <button className="ghost" onClick={findContent} disabled={lf.q.trim().length < 2}
                  title="Баримтын агуулгаас болон утгаар хайна">🔍 Агуулгаас</button>
          {canEdit && <button onClick={() => { setSel(null); setVersions([]); setForm(EMPTY); setMsg(null); }}>+ Шинэ</button>}
        </div>
        <div className="kb-filters">
          <select value={lf.persona} onChange={(e) => setL({ persona: e.target.value })} aria-label="Төрөл">
            <option value="">Бүх төрөл</option>
            {Object.entries(PERSONA_MN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={lf.state} onChange={(e) => setL({ state: e.target.value })} aria-label="Төлөв">
            <option value="">Бүх төлөв</option>
            <option value="published">Нийтлэгдсэн</option><option value="draft">Ноорогтой</option>
            <option value="in_review">Хянагдаж буй</option><option value="archived">Архив</option>
            <option value="stale">⏳ Шалгах хугацаа өнгөрсөн</option>
            <option value="unused">💤 Бот ашиглаагүй</option><option value="disliked">👎 авсан</option>
          </select>
          <select value={lf.visibility} onChange={(e) => setL({ visibility: e.target.value })} aria-label="Хэнд">
            <option value="">Chatbot + дотоод</option><option value="public">Chatbot ашиглана</option>
            <option value="internal">Зөвхөн дотоод</option>
          </select>
          <select value={lf.person} onChange={(e) => setL({ person: e.target.value })} aria-label="Хэн">
            <option value="">Нэмсэн / засварласан: бүгд</option>
            {people.map((p) => <option key={p} value={p}>{personLabel(p)}</option>)}
          </select>
          {tags.length > 0 && (
            <select value={lf.tag} onChange={(e) => setL({ tag: e.target.value })} aria-label="Tag">
              <option value="">Tag: бүгд</option>
              {tags.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          )}
          <select value={lf.sort} onChange={(e) => setL({ sort: e.target.value })} aria-label="Эрэмбэ">
            <option value="updated">Сүүлд засварласан</option><option value="oldest">Хамгийн удаан шалгаагүй</option>
            <option value="title">Нэрээр</option><option value="uses">Их ашиглагдсан</option>
            <option value="disliked">Их 👎 авсан</option>
          </select>
          {activeFilters > 0 && <button className="ghost" onClick={() => { setLf(NO_FILTER); setFound(null); }}>Цэвэрлэх</button>}
        </div>

        {found && (
          <div className="kb-found">
            <div className="row2"><b>«{found.q}» агуулгаас: {found.rows.length}</b>
              <button className="ghost" onClick={() => setFound(null)}>✕</button></div>
            {found.rows.map((f) => (
              <div key={f.doc_id} className="kb-found-row clickable" onClick={() => open(f.doc_id)}>
                <b>{f.title}</b>{" "}
                {f.match.includes("text") && <Badge kind="ok">үг таарсан</Badge>}{" "}
                {f.match.includes("meaning") && <Badge kind="draft">утгаар</Badge>}
                <div className="small muted">…{f.snippet}…</div>
              </div>
            ))}
            {!found.rows.length && <div className="muted small">Олдсонгүй</div>}
          </div>
        )}

        <div className="row2 kb-count">
          <span className="small muted">{first}–{last} / {rows.length} баримт{ov ? ` · ашиглалт: сүүлийн ${ov.usage_days} хоног` : ""}</span>
          <label className="small muted">Нэг хуудсанд{" "}
            <select className="narrow" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
              {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </div>
        <table>
          <thead><tr><th>Гарчиг</th><th>Төрөл</th><th>Нэмсэн / огноо</th><th>Ашиглалт</th><th>Төлөв</th></tr></thead>
          <tbody>
            {rows.slice(page * pageSize, (page + 1) * pageSize).map((d) => (
              <tr key={d.id} className={`clickable ${sel === d.id ? "selected" : ""}`} onClick={() => open(d.id)}>
                <td>
                  <div>{d.title}</div><code className="muted">{d.id}</code>
                  {d.tags.length > 0 && <div className="kb-tags">{d.tags.map((t) => <span key={t}>#{t}</span>)}</div>}
                </td>
                <td>{PERSONA_MN[d.persona ?? ""] ?? "—"}{d.visibility === "internal" && <div className="small muted">дотоод</div>}</td>
                <td className="small">{personLabel(d.created_by)}<div className="muted">{when(d.updated_at)}</div></td>
                <td className="small">{d.usage.uses}×{d.usage.negative > 0 && <span className="err"> · 👎{d.usage.negative}</span>}</td>
                <td>
                  {d.status === "archived" ? <Badge kind="muted">Архив</Badge>
                    : d.published_version ? <Badge kind="ok">v{d.published_version}</Badge> : null}{" "}
                  {d.draft && <Badge kind={d.draft.state}>{STATE_MN[d.draft.state]}{d.draft.source === "ai-suggestion" ? " · AI" : ""}</Badge>}{" "}
                  {isStale(d) && <Badge kind="in_review">⏳ шалгах</Badge>}
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={5} className="muted">Баримт олдсонгүй</td></tr>}
          </tbody>
        </table>
        {pages > 1 && (
          <div className="row2 pager">
            <button className="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Өмнөх</button>
            {Array.from({ length: pages }, (_, i) => (
              <button key={i} className={i === page ? "" : "ghost"} onClick={() => setPage(i)}>{i + 1}</button>
            ))}
            <button className="ghost" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Дараах ›</button>
          </div>
        )}

        <div className="import">
          <h3>Бот аль баримтыг ашиглах вэ?</h3>
          <p className="muted small">Хэрэглэгчийн асуултыг бичихэд бот хариулахдаа ашиглах баримтуудыг харуулна. Буруу хариулт гарсан бол засах баримтаа эндээс олно.</p>
          <div className="row2">
            <select value={ask.persona} onChange={(e) => setAsk({ ...ask, persona: e.target.value })} aria-label="Төрөл" className="narrow">
              <option value="driver">Хүргэгч</option><option value="client">Харилцагч</option><option value="customer">Хэрэглэгч</option>
            </select>
            <input placeholder="Жишээ: бонус хэзээ ордог вэ" value={ask.q} onChange={(e) => setAsk({ ...ask, q: e.target.value })}
                   onKeyDown={(e) => e.key === "Enter" && ask.q.length > 1 && whichDocs()} />
            <button className="ghost" onClick={whichDocs} disabled={ask.q.length < 2 || ask.busy}>{ask.busy ? "…" : "Шалгах"}</button>
          </div>
          {ask.hits && (ask.hits.length ? (
            <ol className="small">
              {ask.hits.map((h) => <li key={h.doc_id}><button className="linkish" onClick={() => open(h.doc_id)}>{h.title}</button>
                <div className="muted">{h.text.slice(0, 120)}…</div></li>)}
            </ol>
          ) : <div className="small muted">Тохирох баримт алга — бот оператор руу шилжүүлнэ.</div>)}
        </div>

        {canEdit && (
          <div className="import">
            <h3>Бөөнөөр оруулах (Excel / CSV)</h3>
            <p className="muted small">
              Багана: id (заавал биш), persona, question, answer, tags, visibility. Google Sheets → Файл → Татах → .xlsx.{" "}
              <a href={`${API_BASE}/api/admin/kb/import/template`}>Загвар татах</a>
            </p>
            <input type="file" accept=".xlsx,.csv" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            {importResult && (
              <div className="small">
                {importResult.created.length} ноорог хянуулахаар илгээгдлээ.
                {importResult.errors.map((er) => <div key={er.row} className="err">Мөр {er.row}: {er.error}</div>)}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="kb-edit">
        <h3>{sel ? form.title || sel : "Шинэ баримт"}</h3>
        {openDraft && <div className="small muted">Ноорог v{openDraft.version} · {STATE_MN[openDraft.state]} · {openDraft.author}
          {openDraft.review_comment && <div className="err">Тайлбар: {openDraft.review_comment}</div>}</div>}
        {sel && (() => {
          const row = ov?.docs.find((d) => d.id === sel);
          return row && (
            <div className="small muted">
              Нэмсэн: {personLabel(row.created_by)} · Сүүлд: {personLabel(row.last_editor)} · Нийтэлсэн: {when(row.published_at)} ·
              Бот {row.usage.uses} удаа ашигласан{row.usage.negative ? `, 👎 ${row.usage.negative}` : ""}
              {isStale(row) && <span className="err"> · ⏳ {ov?.stale_days} хоногоос дээш шалгаагүй</span>}
            </div>
          );
        })()}
        <input placeholder="ID (жишээ: driver-winter-roads)" value={form.id} disabled={!!sel}
               onChange={(e) => setForm({ ...form, id: e.target.value.toLowerCase() })} />
        <div className="row2">
          <select value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} disabled={!canEdit}>
            {Object.entries(PERSONA_MN).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })} disabled={!canEdit}>
            <option value="public">Chatbot ашиглана</option><option value="internal">Зөвхөн дотоод</option>
          </select>
        </div>
        <input placeholder="Гарчиг (хэрэглэгчийн асуулт)" value={form.title} disabled={!canEdit}
               onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <input placeholder="Tag (таслалаар: бонус, төлбөр)" value={form.tags} disabled={!canEdit}
               onChange={(e) => setForm({ ...form, tags: e.target.value })} />
        <textarea rows={14} placeholder={"## Дэд гарчиг\nАгуулга…"} value={form.body} disabled={!canEdit}
                  onChange={(e) => setForm({ ...form, body: e.target.value })} />
        {canEdit && (
          <Quality auto input={{ persona: form.persona, title: form.title, body: form.body, doc_id: form.id || undefined }}
                   onOpen={open} />
        )}
        {canEdit && (
          <div className="row2 wrap">
            <button onClick={saveDraft} disabled={!form.id || !form.title || form.body.length < 10}>Ноорог хадгалах</button>
            {openDraft && ["draft", "rejected"].includes(openDraft.state) && (
              <button onClick={() => act(() => staffFetch(`/api/admin/kb/${form.id}/submit`, { method: "POST" }),
                "Хянуулахаар илгээлээ")}>Хянуулахаар илгээх</button>
            )}
            {openDraft && <button className="ghost" onClick={() => act(() => staffFetch(`/api/admin/kb/${form.id}/draft`,
              { method: "DELETE" }), "Ноорог устгалаа")}>Ноорог устгах</button>}
          </div>
        )}
        {msg && <div className="ok">{msg}</div>}

        {sel && (
          <>
            <h3>Урьдчилж харах</h3>
            <p className="muted small">{openDraft ? "Ноорог нийтлэгдсэн гэж үзээд" : "Одоогийн мэдлэгээр"} chatbot хэрхэн хариулахыг шалгана.</p>
            <div className="row2">
              <input placeholder="Хэрэглэгчийн асуулт" value={preview.question}
                     onChange={(e) => setPreview({ question: e.target.value })} />
              <button onClick={runPreview} disabled={preview.question.length < 2 || preview.busy}>
                {preview.busy ? "…" : "Турших"}</button>
            </div>
            {preview.answer && <div className="preview"><div className="small muted">Горим: {preview.mode}</div>{preview.answer}</div>}
          </>
        )}

        {openDraft && published && openDraft.body !== published.body && (
          <><h3>Өөрчлөлт (v{published.version} → v{openDraft.version})</h3><Diff before={published.body} after={openDraft.body} /></>
        )}

        {versions.length > 0 && (
          <>
            <h3>Түүх</h3>
            <table>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version}>
                    <td>v{v.version}</td><td><Badge kind={v.state}>{STATE_MN[v.state]}</Badge></td>
                    <td className="small">{personLabel(v.author)}{v.reviewer ? ` → ${v.reviewer}` : ""}<div className="muted">{when(v.updated_at)}</div></td>
                    <td>{canPublish && v.state === "superseded" && !openDraft && (
                      <button className="ghost" onClick={() => act(() => staffFetch(`/api/admin/kb/${form.id}/rollback`,
                        { method: "POST", body: JSON.stringify({ to_version: v.version }) }), `v${v.version} руу буцаалаа`)}>Буцаах</button>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {canPublish && (
              <div className="row2">
                {ov?.docs.find((d) => d.id === sel)?.status === "archived"
                  ? <button className="ghost" onClick={() => act(() => staffFetch(`/api/admin/kb/${sel}/restore`, { method: "POST" }), "Сэргээлээ")}>Сэргээх</button>
                  : published && <button className="ghost" onClick={() => act(() => staffFetch(`/api/admin/kb/${sel}/archive`, { method: "POST" }), "Архивласан (chatbot ашиглахгүй)")}>Архивлах</button>}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
