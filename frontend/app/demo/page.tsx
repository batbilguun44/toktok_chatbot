"use client";

import { useState } from "react";
import ChatWindow from "@/components/ChatWindow";

// Demo accounts served by the backend's mock TokTok gateway.
const DEMO = [
  // Customer chat is paused for now (backend still supports it); re-enable by uncommenting.
  // { token: "demo-customer", persona: "customer", label: "Хэрэглэгч", who: "Болд · 2 идэвхтэй захиалга" },
  { token: "demo-driver", persona: "driver", label: "Хүргэгч", who: "Ганбаа · онлайн" },
  { token: "demo-driver-new", persona: "driver", label: "Шинэ хүргэгч", who: "Тэмүүлэн · onboarding дутуу" },
  { token: "demo-client", persona: "client", label: "Харилцагч", who: "Хаан Бууз ХХК · 2 салбар" },
];

export default function Home() {
  const [active, setActive] = useState(DEMO[0]);
  return (
    <main className="demo">
      <section className="demo-side">
        <h1>ТокТок AI туслах</h1>
        <p className="muted">Демо: TokTok-ын аппаас нэвтэрсэн хэрэглэгчийн төрлөөр туршина уу.</p>
        <div className="personas">
          {DEMO.map((d) => (
            <button
              key={d.token}
              className={`persona ${active.token === d.token ? "active" : ""}`}
              onClick={() => setActive(d)}
            >
              <b>{d.label}</b>
              <span>{d.who}</span>
            </button>
          ))}
        </div>
        <p className="muted small">
          Operation team: <a href="/admin">мэдлэгийн сан, тайлан</a> · Embed: <code>/embed</code>
        </p>
      </section>
      <section className="demo-chat">
        <ChatWindow key={active.token} token={active.token} persona={active.persona} />
      </section>
    </main>
  );
}
