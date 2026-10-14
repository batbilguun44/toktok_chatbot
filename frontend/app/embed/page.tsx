"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatWindow from "@/components/ChatWindow";

/**
 * Widget for TokTok's web site (public/widget.js popup) and app webviews.
 *
 * The host sends the signed-in user's TokTok token with postMessage, so the
 * token never appears in a URL:
 *   iframe.contentWindow.postMessage({ type: "toktok-auth", token, persona }, CHATBOT_ORIGIN)
 * `?token=` is accepted only in development.
 *
 * Messages to the host (web: window.parent; app: the native WebView bridge):
 *   { type: "toktok-chat-ready" }            ready to receive auth
 *   { type: "toktok-chat-close" }            user pressed ✕
 *   { type: "toktok-chat-unread", count }    operator replies while the chat is hidden
 * The host reports visibility with { type: "toktok-chat-visibility", open: boolean }.
 */
const ALLOWED_PARENTS = (process.env.NEXT_PUBLIC_EMBED_PARENT_ORIGINS ?? "https://www.toktok.mn,https://toktok.mn")
  .split(",")
  .map((s) => s.trim());

type NativeBridge = { postMessage: (msg: string) => void };

function toHost(msg: Record<string, unknown>, parentOrigin: string | null) {
  const w = window as unknown as {
    ReactNativeWebView?: NativeBridge;
    TokTokNative?: NativeBridge; // Flutter JavaScriptChannel / Android addJavascriptInterface
    webkit?: { messageHandlers?: { toktok?: NativeBridge } }; // iOS WKWebView
  };
  if (window.parent !== window) {
    window.parent.postMessage(msg, parentOrigin ?? "*");
    return;
  }
  const json = JSON.stringify(msg);
  w.ReactNativeWebView?.postMessage(json);
  w.TokTokNative?.postMessage(json);
  w.webkit?.messageHandlers?.toktok?.postMessage(json);
}

export default function Embed() {
  const [auth, setAuth] = useState<{ token: string; persona: string } | null>(null);
  const parentOrigin = useRef<string | null>(null);
  const visible = useRef(true);
  const unread = useRef(0);

  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    if (process.env.NODE_ENV !== "production" && qs.get("token")) {
      setAuth({ token: qs.get("token")!, persona: qs.get("persona") ?? "driver" });
    }
    function onMessage(e: MessageEvent) {
      // Web hosts must be allow-listed. Our own origin is trusted: native apps inject
      // window.postMessage from inside the page, and public/widget-demo.html lives here.
      if (!ALLOWED_PARENTS.includes(e.origin) && e.origin !== window.location.origin) return;
      if (e.data?.type === "toktok-auth" && typeof e.data.token === "string") {
        parentOrigin.current = e.origin;
        setAuth({ token: e.data.token, persona: e.data.persona ?? "driver" });
      } else if (e.data?.type === "toktok-chat-visibility") {
        visible.current = !!e.data.open;
        if (visible.current) unread.current = 0;
      }
    }
    window.addEventListener("message", onMessage);
    toHost({ type: "toktok-chat-ready" }, null);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const close = useCallback(() => toHost({ type: "toktok-chat-close" }, parentOrigin.current), []);
  const onOperatorMessages = useCallback((n: number) => {
    if (visible.current && document.visibilityState === "visible") return;
    unread.current += n;
    toHost({ type: "toktok-chat-unread", count: unread.current }, parentOrigin.current);
  }, []);

  if (!auth) return <div className="embed embed-wait">Холбогдож байна…</div>;
  return (
    <main className="embed">
      <ChatWindow token={auth.token} persona={auth.persona} onClose={close} onOperatorMessages={onOperatorMessages} />
    </main>
  );
}
