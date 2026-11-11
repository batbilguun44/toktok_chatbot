"use client";

import { useCallback, useEffect, useState } from "react";
import { staffFetch } from "@/lib/api";
import { PERSONA_MN, when } from "./common";

type PhoneRow = { value: string; saved: string; default: string };
type Phones = {
  phones: Record<string, PhoneRow>;
  updated_by: string | null;
  updated_at: string | null;
};

const PERSONAS = ["driver", "client", "customer"] as const;
const PHONE_RE = /^\+?[0-9][0-9 ()-]{1,28}[0-9]$/;

// Operator phones shown to users when a chat goes to a person (bot answer + green handoff box).
export default function Settings({ onErr }: { onErr: (e: string | null) => void }) {
  const [data, setData] = useState<Phones | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

  const apply = useCallback((d: Phones) => {
    setData(d);
    setForm(Object.fromEntries(PERSONAS.map((p) => [p, d.phones[p]?.saved ?? ""])));
  }, []);
  useEffect(() => {
    staffFetch<Phones>("/api/admin/settings/phones").then((d) => { apply(d); onErr(null); })
      .catch((e) => onErr(String(e)));
  }, [apply, onErr]);

  const invalid = PERSONAS.filter((p) => (form[p] ?? "").trim() && !PHONE_RE.test(form[p].trim()));
  const changed = data != null && PERSONAS.some((p) => (form[p] ?? "").trim() !== (data.phones[p]?.saved ?? ""));

  async function save() {
    setSaving(true);
    setDone(false);
    try {
      apply(await staffFetch<Phones>("/api/admin/settings/phones", { method: "PUT", body: JSON.stringify(form) }));
      setDone(true);
      onErr(null);
    } catch (e) {
      onErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (!data) return <section><p className="muted">Ачаалж байна…</p></section>;
  return (
    <section>
      <h3>Операторын утас</h3>
      <p className="muted small">
        Чат оператор руу шилжихэд хэрэглэгчид харагдах утасны дугаар (ботын хариулт болон ногоон хайрцагт).
        Хадгалсан даруйд шинэ хариултуудад хэрэгжинэ. Хоосон орхивол анхны (.env) дугаарыг ашиглана.
      </p>
      <table>
        <thead><tr><th>Хэнд</th><th>Одоо харагдаж буй</th><th>Шинэ дугаар</th><th>Анхны (.env)</th></tr></thead>
        <tbody>{PERSONAS.map((p) => (
          <tr key={p}>
            <td>{PERSONA_MN[p]}</td>
            <td><b>{data.phones[p]?.value}</b></td>
            <td>
              <input value={form[p] ?? ""} placeholder={data.phones[p]?.default} aria-label={`${PERSONA_MN[p]} утас`}
                     inputMode="tel" maxLength={30}
                     onChange={(e) => { setForm({ ...form, [p]: e.target.value }); setDone(false); }} />
              {invalid.includes(p) && <div className="small alert">Зөвхөн тоо, зай, «-», «+» ашиглана уу.</div>}
            </td>
            <td className="small muted">{data.phones[p]?.default}</td>
          </tr>
        ))}</tbody>
      </table>
      <div className="row2">
        <button onClick={save} disabled={saving || !changed || invalid.length > 0}>
          {saving ? "Хадгалж байна…" : "Хадгалах"}
        </button>
        {done && <span className="small muted">Хадгалагдлаа. Шинэ чатанд шинэ дугаар гарна.</span>}
      </div>
      {data.updated_at && (
        <p className="small muted">Сүүлд өөрчилсөн: {data.updated_by} · {when(data.updated_at)}</p>
      )}
    </section>
  );
}
