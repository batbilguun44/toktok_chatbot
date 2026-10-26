"use client";

import { useEffect, useState } from "react";
import { staffFetch } from "@/lib/api";
import { PERSONA_MN, STATE_MN, personLabel } from "./common";

export type Similar = { doc_id: string; title: string; persona: string; state: string; author: string; score: number };
type Conflict = { other_doc_id: string; other_title: string; new_statement: string; existing_statement: string;
  explanation: string };
type Input = { persona: string; title: string; body: string; doc_id?: string };

/**
 * Duplicate warning (automatic, no AI) and contradiction check (AI, on demand) for a knowledge draft.
 * `auto` re-checks duplicates whenever the text settles; `onOpen` opens a document from the list.
 */
export default function Quality({ input, auto, onOpen }: { input: Input; auto?: boolean; onOpen?: (id: string) => void }) {
  const [similar, setSimilar] = useState<Similar[] | null>(null);
  const [conflicts, setConflicts] = useState<{ checked: string[]; conflicts: Conflict[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const key = `${input.persona}|${input.title}|${input.body}|${input.doc_id ?? ""}`;

  useEffect(() => {
    setConflicts(null);
    if (!auto || input.body.trim().length < 20) { setSimilar(null); return; }
    const t = setTimeout(() => {
      staffFetch<Similar[]>("/api/admin/kb-similar", { method: "POST", body: JSON.stringify(input) })
        .then(setSimilar).catch(() => setSimilar(null));
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, auto]);

  async function checkConflicts() {
    setBusy(true);
    setErr(null);
    try {
      setConflicts(await staffFetch("/api/admin/kb-conflicts", { method: "POST", body: JSON.stringify(input) }));
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const open = (id: string) => onOpen && (
    <button className="linkish" onClick={() => onOpen(id)}>нээх</button>
  );

  return (
    <div className="quality">
      {similar && similar.length > 0 && (
        <div className="quality-warn">
          <b>⚠️ Ижил төстэй баримт байна.</b> Шинээр нэмэхийн оронд байгааг засах нь зөв байж магадгүй:
          <ul>
            {similar.map((s) => (
              <li key={s.doc_id}>
                <b>{s.title}</b> <span className="muted small">({PERSONA_MN[s.persona] ?? s.persona} ·{" "}
                  {STATE_MN[s.state] ?? s.state} · {personLabel(s.author)} · {Math.round(s.score * 100)}% төстэй)</span> {open(s.doc_id)}
              </li>
            ))}
          </ul>
        </div>
      )}
      {similar && similar.length === 0 && auto && <div className="small ok">✓ Ижил төстэй баримт олдсонгүй.</div>}
      <div className="row2 wrap">
        <button className="ghost" onClick={checkConflicts} disabled={busy || input.body.trim().length < 20}>
          {busy ? "Шалгаж байна…" : "🔎 Зөрчил шалгах (AI)"}
        </button>
        <span className="small muted">Ижил сэдвийн баримтуудтай дүн, хугацаа, журам зөрж байгаа эсэхийг шалгана.</span>
      </div>
      {err && <div className="err small">{err}</div>}
      {conflicts && (conflicts.conflicts.length ? (
        <div className="quality-conflict">
          <b>❗ Зөрчил олдлоо</b> (аль нь зөвийг шийдээрэй):
          <ul>
            {conflicts.conflicts.map((c, i) => (
              <li key={i}>
                <div>{c.explanation}</div>
                <div className="small">Энэ баримт: «{c.new_statement}»</div>
                <div className="small"><b>{c.other_title}</b>: «{c.existing_statement}» {open(c.other_doc_id)}</div>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="small ok">
          ✓ Зөрчил олдсонгүй {conflicts.checked.length ? `(${conflicts.checked.length} төстэй баримттай харьцуулсан)` : "(харьцуулах төстэй баримт алга)"}.
          <span className="muted"> AI бүх зөрчлийг олно гэсэн баталгаагүй.</span>
        </div>
      ))}
    </div>
  );
}
