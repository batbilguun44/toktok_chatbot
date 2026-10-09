"use client";

import { useCallback, useState } from "react";
import { AuditPanel, ImprovePanel, MetricsPanel, StaffPanel } from "@/components/admin/Panels";
import Knowledge from "@/components/admin/Knowledge";
import Review from "@/components/admin/Review";
import StaffGate from "@/components/StaffGate";
import { Me } from "@/lib/api";

const TABS = [
  { id: "metrics", label: "Үзүүлэлт", perm: "metrics.read" },
  { id: "knowledge", label: "Мэдлэгийн сан", perm: "kb.read" },
  { id: "review", label: "Хянах", perm: "kb.read" },
  { id: "improve", label: "Сайжруулалт", perm: "insights.read" },
  { id: "staff", label: "Ажилтнууд", perm: "staff.manage" },
  { id: "audit", label: "Аудит", perm: "audit.read" },
] as const;

export default function Admin() {
  return (
    <StaffGate permission="kb.read" title="ТокТок туслах · Operation" returnTo="/admin">
      {(me, logout) => <AdminShell me={me} logout={logout} />}
    </StaffGate>
  );
}

function AdminShell({ me, logout }: { me: Me; logout: () => void }) {
  const tabs = TABS.filter((t) => me.permissions.includes(t.perm));
  const [tab, setTab] = useState<string>(tabs[0]?.id ?? "knowledge");
  const [err, setErr] = useState<string | null>(null);
  const onErr = useCallback((e: string | null) => setErr(e), []);

  return (
    <main className="admin">
      <header className="admin-header">
        <h1>ТокТок туслах · Operation</h1>
        <div className="small">
          {me.email} · {me.roles.join(", ")}{" "}
          {me.permissions.includes("handoff") && <a href="/operator">Оператор</a>}{" "}
          <button className="ghost" onClick={logout}>Гарах</button>
        </div>
      </header>
      <nav className="tabs">
        {tabs.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => { setTab(t.id); setErr(null); }}>
            {t.label}
          </button>
        ))}
      </nav>
      {err && <div className="alert">{err}</div>}
      {tab === "metrics" && <MetricsPanel onErr={onErr} />}
      {tab === "knowledge" && <Knowledge me={me} onErr={onErr} />}
      {tab === "review" && <Review me={me} onErr={onErr} />}
      {tab === "improve" && <ImprovePanel me={me} onErr={onErr} />}
      {tab === "staff" && <StaffPanel onErr={onErr} />}
      {tab === "audit" && <AuditPanel onErr={onErr} />}
    </main>
  );
}
