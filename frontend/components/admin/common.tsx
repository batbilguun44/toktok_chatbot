"use client";

import { DiffLine, diffLines } from "@/lib/diff";

export const PERSONA_MN: Record<string, string> = {
  driver: "Хүргэгч", client: "Харилцагч", customer: "Хэрэглэгч", all: "Бүгд",
};
export const STATE_MN: Record<string, string> = {
  draft: "Ноорог", in_review: "Хянагдаж буй", rejected: "Буцаагдсан", published: "Нийтлэгдсэн",
  superseded: "Хуучирсан",
};
export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("mn-MN") : "—");

export function Badge({ kind, children }: { kind: string; children: React.ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function Diff({ before, after }: { before: string; after: string }) {
  const lines: DiffLine[] = diffLines(before, after);
  return (
    <pre className="diff">
      {lines.map((l, i) => (
        <div key={i} className={l.op}>
          {l.op === "add" ? "+ " : l.op === "del" ? "- " : "  "}
          {l.text}
        </div>
      ))}
    </pre>
  );
}

export function Card({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className="card-value">{value}</div>
      {hint && <div className="card-hint">{hint}</div>}
    </div>
  );
}
