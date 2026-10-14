/*
 * TokTok chat popup loader (web).
 *
 * Host page adds:
 *   <script>
 *     window.TokTokChat = {
 *       origin: "https://chat.toktok.mn",          // where /embed is served
 *       persona: "driver",                          // driver | client
 *       getToken: async () => currentUserToken(),   // signed-in user's TokTok token
 *     };
 *   </script>
 *   <script src="https://chat.toktok.mn/widget.js" async></script>
 *
 * Desktop: floating button bottom-right, chat opens as a card above it.
 * Phone width (< 640px): chat opens full screen; the chat header has the ✕.
 * The token goes to the iframe by postMessage only (never in a URL).
 * Operator replies while the chat is closed show as a red count on the button.
 * API: TokTokChat.open(), .close(), .toggle()
 */
(function () {
  var cfg = window.TokTokChat || {};
  if (cfg.__loaded) return;
  cfg.__loaded = true;
  var origin = cfg.origin || new URL(document.currentScript ? document.currentScript.src : location.href).origin;
  var persona = cfg.persona || "driver";
  var color = cfg.color || "#ffb600"; // toktok.mn yellow
  var title = cfg.title || "ТокТок туслах";

  var css = [
    ".tkc-btn{position:fixed;right:20px;bottom:20px;z-index:2147483000;width:60px;height:60px;border-radius:50%;",
    "border:0;cursor:pointer;background:" + color + ";color:#fff;box-shadow:0 6px 20px rgba(0,0,0,.2);",
    "display:flex;align-items:center;justify-content:center;transition:transform .15s}",
    ".tkc-btn:hover{transform:scale(1.06)}.tkc-btn:focus-visible{outline:3px solid #1f1f1f;outline-offset:3px}",
    ".tkc-btn svg{width:28px;height:28px}",
    ".tkc-badge{position:absolute;top:-2px;right:-2px;min-width:20px;height:20px;border-radius:10px;background:#d54c4f;",
    "color:#fff;font:600 12px/20px system-ui,sans-serif;text-align:center;padding:0 6px;display:none;",
    "border:2px solid #fff}",
    ".tkc-panel{position:fixed;right:20px;bottom:92px;z-index:2147483000;width:380px;height:min(640px,calc(100dvh - 120px));",
    "background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.18);display:flex;",
    "opacity:0;visibility:hidden;transform:translateY(12px) scale(.98);transition:opacity .18s,transform .18s,visibility .18s}",
    ".tkc-open .tkc-panel{opacity:1;visibility:visible;transform:none}",
    ".tkc-frame{flex:1;width:100%;border:0;background:#fff}",
    "@media (max-width:639px){.tkc-panel{inset:0;width:auto;height:100dvh;border-radius:0}",
    ".tkc-open .tkc-btn{display:none}}",
  ].join("");

  var chatIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  var closeIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  var root = document.createElement("div");
  root.className = "tkc";
  root.innerHTML =
    "<style>" + css + "</style>" +
    '<div class="tkc-panel" role="dialog" aria-label="' + title + '"></div>' +
    '<button class="tkc-btn" aria-label="' + title + '"><span class="tkc-icon">' + chatIcon + '</span>' +
    '<span class="tkc-badge" aria-live="polite"></span></button>';
  var panel = root.querySelector(".tkc-panel");
  var btn = root.querySelector(".tkc-btn");
  var icon = root.querySelector(".tkc-icon");
  var badge = root.querySelector(".tkc-badge");
  var frame = null;
  var isOpen = false;

  function post(msg) {
    if (frame && frame.contentWindow) frame.contentWindow.postMessage(msg, origin);
  }

  function sendAuth() {
    Promise.resolve(cfg.getToken ? cfg.getToken() : cfg.token).then(function (token) {
      if (token) post({ type: "toktok-auth", token: token, persona: persona });
      post({ type: "toktok-chat-visibility", open: isOpen });
    });
  }

  function ensureFrame() {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.className = "tkc-frame";
    frame.title = title;
    frame.src = origin + "/embed";
    frame.allow = "clipboard-write";
    panel.appendChild(frame);
  }

  function setBadge(n) {
    badge.textContent = n > 9 ? "9+" : String(n);
    badge.style.display = n > 0 ? "block" : "none";
  }

  window.addEventListener("message", function (e) {
    if (e.origin !== origin || !frame || e.source !== frame.contentWindow || !e.data) return;
    if (e.data.type === "toktok-chat-ready") sendAuth();
    else if (e.data.type === "toktok-chat-close") close();
    else if (e.data.type === "toktok-chat-unread" && !isOpen) setBadge(e.data.count || 1);
  });

  function open() {
    ensureFrame();
    isOpen = true;
    root.classList.add("tkc-open");
    icon.innerHTML = closeIcon;
    btn.setAttribute("aria-label", "Хаах");
    setBadge(0);
    post({ type: "toktok-chat-visibility", open: true });
  }
  function close() {
    isOpen = false;
    root.classList.remove("tkc-open");
    icon.innerHTML = chatIcon;
    btn.setAttribute("aria-label", title);
    post({ type: "toktok-chat-visibility", open: false });
    btn.focus();
  }
  btn.addEventListener("click", function () { isOpen ? close() : open(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && isOpen) close(); });

  cfg.open = open;
  cfg.close = close;
  cfg.toggle = function () { isOpen ? close() : open(); };
  window.TokTokChat = cfg;

  if (document.body) document.body.appendChild(root);
  else document.addEventListener("DOMContentLoaded", function () { document.body.appendChild(root); });
})();
