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
  let active = false, leaving = false, deadline = 0, timer, watchdog, pending, resolveReady, epoch = 0;
  let queuedAcquire = null, currentAcquire = null;
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
    if (leaving || Date.now() >= deadline) return false;
    clearTimers(); active = true; app.inert = false;
    dialog.close(); document.documentElement.classList.remove("viewer-session-locked");
    resolveReady(true);
    watchdog = setTimeout(() => acquire(), Math.max(0, deadline - Date.now()));
    timer = setTimeout(() => check("heartbeat"), interval * 1000);
    return true;
  }
  function release() {
    // Delayed release from an old page is matched to both login and document IDs.
    request("/api/viewer-session", { method: "POST", credentials: "same-origin", cache: "no-store", keepalive: true,
      headers: { "X-CSRF-Token": csrf, "X-Viewer-ID": client },
      body: new URLSearchParams({ action: "release" }) }).catch(() => {});
  }
  async function check(action) {
    if (leaving) return;
    if (pending) {
      if (action !== "acquire") return pending.promise;
      if (pending.action === "acquire") return pending.promise;
      if (!queuedAcquire) {
        queuedAcquire = pending.promise.then(() => {
          queuedAcquire = null;
          return check("acquire");
        });
      }
      return queuedAcquire;
    }
    const version = epoch, started = Date.now(), controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    let reacquire = false;
    const promise = (async () => {
      try {
        const response = await request("/api/viewer-session", { method: "POST", credentials: "same-origin", cache: "no-store",
          signal: controller.signal, headers: { "X-CSRF-Token": csrf, "X-Viewer-ID": client },
          body: new URLSearchParams({ action }) });
        const data = await response.json();
        if (version !== epoch || leaving) { if (response.ok) release(); return false; }
        if (response.ok) return activate(started, Number(data.ttl) || 90, Number(data.heartbeat) || 20);
        if (data.code === "viewer_conflict") suspend(conflict);
        else if (action === "heartbeat" && data.code === "viewer_expired") reacquire = true;
        else if (response.status === 401) suspend("Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.", false);
        else suspend(data.error || "Cần kiểm tra lại phiên viewer. Vui lòng thử lại.");
      } catch {
        if (!leaving && version === epoch) suspend("Mất kết nối kiểm tra phiên viewer. Vui lòng thử lại.");
      }
      return false;
    })();
    pending = { action, controller, promise };
    promise.then(() => {
      clearTimeout(timeout);
      if (pending?.promise === promise) pending = null;
      if (reacquire && !leaving) acquire();
    }, () => {
      clearTimeout(timeout);
      if (pending?.promise === promise) pending = null;
    });
    return promise;
  }
  function acquire() {
    if (currentAcquire) return currentAcquire;
    const promise = check("acquire");
    currentAcquire = promise;
    promise.then(() => { if (currentAcquire === promise) currentAcquire = null; },
      () => { if (currentAcquire === promise) currentAcquire = null; });
    return promise;
  }
  function protectedURL(input) {
    const url = new URL(input instanceof Request ? input.url : input, location.href);
    return url.origin === location.origin && (url.pathname.startsWith("/data/")
      || (url.pathname.startsWith("/api/") && url.pathname !== "/api/viewer-session"));
  }
  window.fetch = async (input, options) => {
    if (!protectedURL(input)) return request(input, options);
    if (leaving) throw new DOMException("Viewer session is inactive", "AbortError");
    if (currentAcquire) await currentAcquire;
    if (!active || Date.now() >= deadline) {
      const renewed = active ? await acquire() : false;
      if (!renewed || !active || Date.now() >= deadline) {
        if (active) suspend("Cần kiểm tra lại phiên viewer. Vui lòng thử lại.");
        throw new DOMException("Viewer session is inactive", "AbortError");
      }
    }
    if (leaving) {
      throw new DOMException("Viewer session is inactive", "AbortError");
    }
    const headers = new Headers(options?.headers || (input instanceof Request ? input.headers : undefined));
    headers.set("X-Viewer-ID", client);
    const method = String(options?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    let retried = false;
    while (true) {
      const response = await request(input, { ...options, headers });
      if ([401, 409, 428].includes(response.status)) {
        const data = await response.clone().json().catch(() => ({}));
        const expired = response.status === 409 && data.code === "viewer_expired";
        const required = response.status === 428;
        if (!retried && ["GET", "HEAD"].includes(method) && (expired || required)) {
          retried = true;
          if (await acquire()) continue;
          throw new DOMException("Viewer session lost", "AbortError");
        }
        suspend(data.code === "viewer_conflict" ? conflict : (data.error || "Vui lòng kiểm tra lại phiên viewer."));
        throw new DOMException("Viewer session lost", "AbortError");
      }
      return response;
    }
  };
  // Keyboard/wheel handlers live on window too: a modal alone isn't sufficient.
  for (const type of ["keydown", "pointerdown", "pointermove", "wheel", "click"]) {
    window.addEventListener(type, event => {
      if (active && Date.now() >= deadline) {
        acquire(); event.preventDefault(); event.stopImmediatePropagation(); return;
      }
      if (!active && !dialog.contains(event.target)) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, { capture: true, passive: false });
  }
  dialog.addEventListener("cancel", event => event.preventDefault());
  retry.addEventListener("click", () => { suspend("Đang kiểm tra phiên viewer…", false); acquire(); });
  window.addEventListener("offline", () => suspend("Mất kết nối kiểm tra phiên viewer. Vui lòng thử lại."));
  window.addEventListener("online", () => { if (!leaving) acquire(); });
  function revalidateOnReturn() {
    if (!leaving && document.visibilityState !== "hidden") acquire();
  }
  document.addEventListener("visibilitychange", revalidateOnReturn);
  window.addEventListener("focus", revalidateOnReturn);
  window.addEventListener("pagehide", () => {
    leaving = true; epoch++; suspend("Đang kiểm tra phiên viewer…", false); release();
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted) { leaving = false; acquire(); }
  });
  suspend("Đang kiểm tra phiên viewer…", false);
  acquire();
})();
