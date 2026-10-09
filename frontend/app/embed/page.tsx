"use client";

import { useEffect, useState } from "react";
import ChatWindow from "@/components/ChatWindow";

/**
 * Widget for TokTok's web site / app webviews.
 *
 * The host page sends the signed-in user's TokTok token with postMessage, so the
 * token never appears in a URL:
 *   iframe.contentWindow.postMessage({ type: "toktok-auth", token, persona }, CHATBOT_ORIGIN)
 * `?token=` is accepted only in development.
 */
const ALLOWED_PARENTS = (process.env.NEXT_PUBLIC_EMBED_PARENT_ORIGINS ?? "https://www.toktok.mn,https://toktok.mn")
  .split(",")
  .map((s) => s.trim());

export default function Embed() {
  const [auth, setAuth] = useState<{ token: string; persona: string } | null>(null);

  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    if (process.env.NODE_ENV !== "production" && qs.get("token")) {
      setAuth({ token: qs.get("token")!, persona: qs.get("persona") ?? "customer" });
    }
    function onMessage(e: MessageEvent) {
      if (!ALLOWED_PARENTS.includes(e.origin)) return;
      if (e.data?.type === "toktok-auth" && typeof e.data.token === "string") {
        setAuth({ token: e.data.token, persona: e.data.persona ?? "customer" });
      }
    }
    window.addEventListener("message", onMessage);
    window.parent?.postMessage({ type: "toktok-chat-ready" }, "*");
    return () => window.removeEventListener("message", onMessage);
  }, []);

  if (!auth) return <div className="embed-wait">Холбогдож байна…</div>;
  return (
    <main className="embed">
      <ChatWindow token={auth.token} persona={auth.persona} />
    </main>
  );
}
