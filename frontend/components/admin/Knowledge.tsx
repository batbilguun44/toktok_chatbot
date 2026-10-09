"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE, Me, staffFetch } from "@/lib/api";
import { Badge, Diff, PERSONA_MN, STATE_MN, when } from "./common";

type DocRow = {
  id: string; status: string; published_version: number | null; persona: string | null; title: string;
  visibility: string | null; updated_at: string;
  draft: { version: number; state: string; author: string; source: string; updated_at: string } | null;
};
type Overview = { docs: DocRow[]; modes: Record<string, string>; est_tokens: Record<string, number>;
  full_context_max_tokens: number };
type Version = {
  doc_id: string; version: number; persona: string; title: string; body: string; tags: string[];
  visibility: string; state: string; source: string; author: string; created_at: string; updated_at: string;
  reviewer: string | null; review_comment: string | null;
};
type Form = { id: string; persona: string; title: string; body: string; tags: string; visibility: string };
const EMPTY: Form = { id: "", persona: "driver", title: "", body: "", tags: "", visibility: "public" };

export default function Knowledge({ me, onErr }: { me: Me; onErr: (e: string | null) => void }) {
  const canEdit = me.permissions.includes("kb.edit");
  const canPublish = me.permissions.includes("kb.publish");
  const [ov, setOv] = useState<Overview | null>(null);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
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

  async function open(id: string) {
    setSel(id);
    setMsg(null);
    setPreview({ question: "" });
    const d = await staffFetch<{ versions: Version[] }>(`/api/admin/kb/${id}`);
    setVersions(d.versions);
    const head = d.versions.find((v) => ["draft", "in_review", "rejected"].includes(v.state)) ??
      d.versions.find((v) => v.state === "published") ?? d.versions[0];
    if (head) setForm({ id, persona: head.persona, title: head.title, body: head.body, tags: head.tags.join(", "),
      visibility: head.visibility });
  }

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

  const rows = (ov?.docs ?? []).filter((d) =>
    (filter === "all" || d.persona === filter || (filter === "pending" && d.draft)) &&
    (!q || `${d.id} ${d.title}`.toLowerCase().includes(q.toLowerCase())));

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
          <input placeholder="Хайх…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Шүүлтүүр">
            <option value="all">Бүгд</option><option value="pending">Ноорогтой</option>
            <option value="driver">Хүргэгч</option><option value="client">Харилцагч</option>
            <option value="customer">Хэрэглэгч</option>
          </select>
          {canEdit && <button onClick={() => { setSel(null); setVersions([]); setForm(EMPTY); setMsg(null); }}>+ Шинэ</button>}
        </div>
        <table>
          <thead><tr><th>Гарчиг</th><th>Төрөл</th><th>Төлөв</th></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.id} className={`clickable ${sel === d.id ? "selected" : ""}`} onClick={() => open(d.id)}>
                <td><div>{d.title}</div><code className="muted">{d.id}</code></td>
                <td>{PERSONA_MN[d.persona ?? ""] ?? "—"}</td>
                <td>
                  {d.status === "archived" ? <Badge kind="muted">Архив</Badge>
                    : d.published_version ? <Badge kind="ok">v{d.published_version}</Badge> : null}{" "}
                  {d.draft && <Badge kind={d.draft.state}>{STATE_MN[d.draft.state]}{d.draft.source === "ai-suggestion" ? " · AI" : ""}</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
        <input placeholder="Tag (таслалаар)" value={form.tags} disabled={!canEdit}
               onChange={(e) => setForm({ ...form, tags: e.target.value })} />
        <textarea rows={14} placeholder={"## Дэд гарчиг\nАгуулга…"} value={form.body} disabled={!canEdit}
                  onChange={(e) => setForm({ ...form, body: e.target.value })} />
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
                    <td className="small">{v.author}{v.reviewer ? ` → ${v.reviewer}` : ""}<div className="muted">{when(v.updated_at)}</div></td>
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
