"use client";

import { useCallback, useEffect, useState } from "react";
import { Me, staffFetch } from "@/lib/api";
import { Diff, PERSONA_MN, when } from "./common";

type V = { doc_id: string; version: number; persona: string; title: string; body: string; author: string;
  source: string; updated_at: string; visibility: string };
type Item = { draft: V; published: V | null };

export default function Review({ me, onErr }: { me: Me; onErr: (e: string | null) => void }) {
  const [items, setItems] = useState<Item[]>([]);
  const [comments, setComments] = useState<Record<string, string>>({});
  const canPublish = me.permissions.includes("kb.publish");

  const load = useCallback(async () => {
    try {
      setItems(await staffFetch<Item[]>("/api/admin/kb/review"));
      onErr(null);
    } catch (e) {
      onErr(String(e));
    }
  }, [onErr]);
  useEffect(() => { load(); }, [load]);

  async function decide(it: Item, action: "approve" | "reject") {
    try {
      await staffFetch(`/api/admin/kb/${it.draft.doc_id}/versions/${it.draft.version}/${action}`, {
        method: "POST", body: JSON.stringify({ comment: comments[it.draft.doc_id] || null }),
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
        const own = it.draft.author === me.email && !me.roles.includes("admin");
        return (
          <article key={it.draft.doc_id} className="review-item">
            <header>
              <b>{it.draft.title}</b> <code className="muted">{it.draft.doc_id} v{it.draft.version}</code>
              <div className="small muted">
                {PERSONA_MN[it.draft.persona]} · {it.draft.author} · {when(it.draft.updated_at)}
                {it.draft.source === "ai-suggestion" && " · AI санал (баримтжуулах шаардлагатай)"}
                {it.draft.visibility === "internal" && " · дотоод"}
              </div>
            </header>
            {it.published ? <Diff before={it.published.body} after={it.draft.body} />
              : <pre className="diff">{it.draft.body.split("\n").map((l, i) => <div key={i} className="add">+ {l}</div>)}</pre>}
            {/\[ТОДРУУЛАХ/.test(it.draft.body) && <div className="err small">Бөглөөгүй [ТОДРУУЛАХ] хэсэг байна — нийтлэхээс өмнө засна уу.</div>}
            {canPublish && (
              <div className="row2">
                <input placeholder="Тайлбар (буцаахад заавал)" value={comments[it.draft.doc_id] ?? ""}
                       onChange={(e) => setComments({ ...comments, [it.draft.doc_id]: e.target.value })} />
                <button onClick={() => decide(it, "approve")} disabled={own}
                        title={own ? "Өөрийн өөрчлөлтийг өөр хүн батлах ёстой" : ""}>Батлах</button>
                <button className="ghost" onClick={() => decide(it, "reject")} disabled={!comments[it.draft.doc_id]}>Буцаах</button>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
