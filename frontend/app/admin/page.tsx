"use client";

import { useCallback, useEffect, useState } from "react";
import { AuditPanel, ImprovePanel, MetricsPanel, StaffPanel } from "@/components/admin/Panels";
import Conversations from "@/components/admin/Conversations";
import Knowledge from "@/components/admin/Knowledge";
import Prompts from "@/components/admin/Prompts";
import Review from "@/components/admin/Review";
import StaffGate from "@/components/StaffGate";
import { Me, staffFetch } from "@/lib/api";

const TABS = [
  { id: "metrics", label: "Үзүүлэлт", perm: "metrics.read" },
  { id: "conversations", label: "Яриа", perm: "conversations.read" },
  { id: "knowledge", label: "Мэдлэгийн сан", perm: "kb.read" },
  { id: "review", label: "Хянах", perm: "kb.publish" },
  { id: "prompts", label: "Ботын заавар", perm: "kb.read" },
  { id: "improve", label: "Сайжруулалт", perm: "insights.read" },
  { id: "staff", label: "Ажилтнууд", perm: "staff.manage" },
  { id: "audit", label: "Аудит", perm: "audit.read" },
] as const;

export default function Admin() {
  return (
    <StaffGate permission="staff.manage" title="ТокТок туслах · Админ" returnTo="/admin">
      {(me, logout) => <AdminShell me={me} logout={logout} />}
    </StaffGate>
  );
}

function AdminShell({ me, logout }: { me: Me; logout: () => void }) {
  const tabs = TABS.filter((t) => me.permissions.includes(t.perm));
  const [tab, setTab] = useState<string>(tabs[0]?.id ?? "knowledge");
  const [err, setErr] = useState<string | null>(null);
  const onErr = useCallback((e: string | null) => setErr(e), []);
  const [openDoc, setOpenDoc] = useState<string | null>(null); // knowledge doc opened from a conversation
  // Pending approvals (knowledge + bot instructions), shown on the review tab.
  const [pending, setPending] = useState(0);
  useEffect(() => {
    const count = () => Promise.all([staffFetch<unknown[]>("/api/admin/kb/review"),
      staffFetch<unknown[]>("/api/admin/prompts-review")])
      .then(([a, b]) => setPending(a.length + b.length)).catch(() => {});
    count();
    const t = setInterval(count, 15000);
    return () => clearInterval(t);
  }, [tab]);

  return (
    <main className="admin">
      <header className="admin-header">
        <h1>ТокТок туслах · Админ</h1>
        <div className="small">
          {me.email} · {me.roles.join(", ")}{" "}
          {me.permissions.includes("handoff") && <a href="/operator">Оператор</a>}{" "}
          <button className="ghost" onClick={logout}>Гарах</button>
        </div>
      </header>
      <nav className="tabs">
        {tabs.map((t) => (
          <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => { setTab(t.id); setErr(null); }}>
            {t.label}{t.id === "review" && pending > 0 && <span className="count">{pending}</span>}
          </button>
        ))}
      </nav>
      {err && <div className="alert">{err}</div>}
      {tab === "metrics" && <MetricsPanel onErr={onErr} />}
      {tab === "conversations" && <Conversations onErr={onErr} canExport={me.permissions.includes("conversations.export")}
        onOpenDoc={(id) => { setOpenDoc(id); setTab("knowledge"); }} />}
      {tab === "knowledge" && <Knowledge me={me} onErr={onErr} openDoc={openDoc} />}
      {tab === "review" && <Review me={me} onErr={onErr} onOpenDoc={(id) => { setOpenDoc(id); setTab("knowledge"); }} />}
      {tab === "prompts" && <Prompts me={me} onErr={onErr} />}
      {tab === "improve" && <ImprovePanel me={me} onErr={onErr} />}
      {tab === "staff" && <StaffPanel onErr={onErr} />}
      {tab === "audit" && <AuditPanel onErr={onErr} />}
    </main>
  );
}
