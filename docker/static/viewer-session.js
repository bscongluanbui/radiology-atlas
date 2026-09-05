"use strict";

// Website only. No storage/cookie copy for the document ID: even a duplicated tab
// must acquire its own slot. The database, not this UI, enforces exclusivity.
(() => {
  const dialog = document.getElementById("viewerSessionDialog");
  if (!dialog) return;
  const message = document.getElementById("viewerSessionMessage");
  const retry = document.getElementById("viewerSessionRetry");
  const app = document.getElementById("app");
  const csrf = document.querySelector('#viewerSessionLogout input[name="csrf"]').value;
  const client = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("");
  const request = window.fetch.bind(window);
  const conflict = "Bạn đang dùng tài khoản ở nhiều nơi cùng thời điểm, vui lòng đăng xuất";
  let active = false, leaving = false, deadline = 0, timer, watchdog, pending, resolveReady, epoch = 0, recheck = false;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  window.viewerSession = { ready, get blocked() { return !active; } };

  function clearTimers() { clearTimeout(timer); clearTimeout(watchdog); }
  function suspend(text, canRetry = true) {
    active = false; clearTimers();
    document.documentElement.classList.add("viewer-session-locked");
    app.inert = true;
    message.textContent = text; retry.hidden = !canRetry;
    if (!dialog.matches(":modal")) { dialog.removeAttribute("open"); dialog.showModal(); }
    window.dispatchEvent(new Event("viewer-session-suspended"));
    window.viewerResourceCache?.clear();
  }
  function activate(started, ttl, interval) {
    deadline = started + (ttl - 5) * 1000;
    if (leaving || Date.now() >= deadline || document.visibilityState === "hidden") return;
    clearTimers(); active = true; app.inert = false;
    dialog.close(); document.documentElement.classList.remove("viewer-session-locked");
    resolveReady(true);
    watchdog = setTimeout(() => suspend("Cần kiểm tra lại phiên viewer. Vui lòng thử lại."), Math.max(0, deadline - Date.now()));
    timer = setTimeout(() => check("heartbeat"), interval * 1000);
  }
  function release() {
    // Delayed release from an old page is matched to both login and document IDs.
    request("/api/viewer-session", { method: "POST", credentials: "same-origin", cache: "no-store", keepalive: true,
      headers: { "X-CSRF-Token": csrf, "X-Viewer-ID": client },
      body: new URLSearchParams({ action: "release" }) }).catch(() => {});
  }
  async function check(action) {
    if (leaving) return;
    if (pending) { if (action === "acquire") recheck = true; return; }
    const version = epoch, started = Date.now(), controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    pending = controller;
    try {
      const response = await request("/api/viewer-session", { method: "POST", credentials: "same-origin", cache: "no-store",
        signal: controller.signal, headers: { "X-CSRF-Token": csrf, "X-Viewer-ID": client },
        body: new URLSearchParams({ action }) });
      const data = await response.json();
      if (version !== epoch || leaving) { if (response.ok) release(); return; }
      if (response.ok) activate(started, Number(data.ttl) || 90, Number(data.heartbeat) || 20);
      else if (data.code === "viewer_conflict") suspend(conflict);
      else if (response.status === 401) suspend("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.", false);
      else suspend(data.error || "Cần kiểm tra lại phiên viewer. Vui lòng thử lại.");
    } catch {
      if (!leaving && version === epoch) suspend("Mất kết nối kiểm tra phiên viewer. Vui lòng thử lại.");
    } finally {
      clearTimeout(timeout); if (pending === controller) pending = null;
      if (recheck && !leaving) { recheck = false; check("acquire"); }
    }
  }
  function protectedURL(input) {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    return url.origin === location.origin && (url.pathname.startsWith("/data/")
      || (url.pathname.startsWith("/api/") && url.pathname !== "/api/viewer-session"));
  }
  window.fetch = async (input, options) => {
    if (!protectedURL(input)) return request(input, options);
    if (!active || Date.now() >= deadline || leaving) {
      if (active) suspend("Cần kiểm tra lại phiên viewer. Vui lòng thử lại.");
      throw new DOMException("Viewer session is inactive", "AbortError");
    }
    const headers = new Headers(options?.headers || (input instanceof Request ? input.headers : undefined));
    headers.set("X-Viewer-ID", client);
    const response = await request(input, { ...options, headers });
    if ([401, 409, 428].includes(response.status)) {
      const data = await response.clone().json().catch(() => ({}));
      suspend(data.code === "viewer_conflict" ? conflict : (data.error || "Vui lòng kiểm tra lại phiên viewer."));
      throw new DOMException("Viewer session lost", "AbortError");
    }
    return response;
  };
  // Keyboard/wheel handlers live on window too: a modal alone isn't sufficient.
  for (const type of ["keydown", "pointerdown", "pointermove", "wheel", "click"]) {
    window.addEventListener(type, event => {
      if (active && Date.now() >= deadline) suspend("Cần kiểm tra lại phiên viewer. Vui lòng thử lại.");
      if (!active && !dialog.contains(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, { capture: true, passive: false });
  }
  dialog.addEventListener("cancel", event => event.preventDefault());
  retry.addEventListener("click", () => { suspend("Đang kiểm tra phiên viewer…", false); check("acquire"); });
  window.addEventListener("offline", () => suspend("Mất kết nối kiểm tra phiên viewer. Vui lòng thử lại."));
  window.addEventListener("online", () => { if (!leaving) check("acquire"); });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") suspend("Đang kiểm tra phiên viewer…", false);
    else check("acquire");
  });
  window.addEventListener("pagehide", () => {
    leaving = true; epoch++; suspend("Đang kiểm tra phiên viewer…", false); release();
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted) { leaving = false; check("acquire"); }
  });
  suspend("Đang kiểm tra phiên viewer…", false);
  check("acquire");
})();
