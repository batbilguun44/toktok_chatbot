"use client";

import { DiffLine, diffLines } from "@/lib/diff";

export const PERSONA_MN: Record<string, string> = {
  driver: "Хүргэгч", client: "Харилцагч", customer: "Хэрэглэгч", all: "Бүгд",
};
export const STATE_MN: Record<string, string> = {
  draft: "Ноорог", in_review: "Хянагдаж буй", rejected: "Буцаагдсан", published: "Нийтлэгдсэн",
  superseded: "Хуучирсан",
};
// Why a conversation went to an operator (escalate_to_human categories).
export const CATEGORY_MN: Record<string, string> = {
  user_requested: "Хүн хүссэн", not_in_knowledge: "Мэдлэгт алга", needs_human_decision: "Шийдвэр хэрэгтэй",
  incident: "Яаралтай", complaint: "Гомдол", account_security: "Аюулгүй байдал", sales_manager: "Борлуулалт",
  technical_issue: "Техникийн",
};
// System authors: "git" = sample documents loaded from backend/knowledge/ on first start,
// "default" = the built-in bot instructions. Show them as such instead of a person.
export const personLabel = (p?: string | null) =>
  p === "git" ? "Анхны жишээ" : p === "default" ? "Анхны заавар" : p ?? "—";
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
