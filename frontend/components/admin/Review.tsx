"use client";

import { useCallback, useEffect, useState } from "react";
import { Me, staffFetch } from "@/lib/api";
import { Diff, PERSONA_MN, when } from "./common";

type V = { doc_id: string; version: number; persona: string; title: string; body: string; author: string;
  source: string; updated_at: string; visibility: string };
// kind: knowledge document or bot instructions (one per persona; doc_id = persona).
type Item = { kind: "kb" | "prompt"; draft: V; published: V | null };

const base = (it: Item) =>
  it.kind === "kb" ? `/api/admin/kb/${it.draft.doc_id}` : `/api/admin/prompts/${it.draft.doc_id}`;
const keyOf = (it: Item) => `${it.kind}:${it.draft.doc_id}`;

/** Everything waiting for an admin: knowledge changes and bot-instruction changes. */
export default function Review({ me, onErr }: { me: Me; onErr: (e: string | null) => void }) {
  const [items, setItems] = useState<Item[]>([]);
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
  useEffect(() => { load(); }, [load]);

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

  if (!items.length) return <p className="muted">Хянах зүйл алга.</p>;
  return (
    <section className="review">
      {items.map((it) => {
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
