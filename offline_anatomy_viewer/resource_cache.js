"use strict";

// One bounded cache for main frame, filmstrip, MPR and overlays.
// Keep HTTP private/no-store and the existing data: CSP; memoize data URLs.
window.viewerResourceCache = (() => {
  const runtime = window.viewerRuntime;
  if (!runtime?.remote) return null;
  const deviceMemory = Number(window.navigator?.deviceMemory) || 0;
  const configuredBytes = Math.max(16 * 1024 ** 2, Math.min(1024 * 1024 ** 2, Number(runtime.maxBytes) || 512 * 1024 ** 2));
  const deviceCap = deviceMemory && deviceMemory <= 2 ? 128 : deviceMemory && deviceMemory <= 4 ? 256 : 1024;
  const maxBytes = Math.min(configuredBytes, deviceCap * 1024 ** 2);
  const ttlMs = Math.max(60000, Number(runtime.ttlMs) || 1800000);
  const queue = new window.ViewerRequestQueue({ concurrency: Number(runtime.imageConcurrency) || 4, background: 3 });
  const entries = new Map();
  let bytes = 0, generation = 0, timer = null;
  const stats = { hits: 0, misses: 0, conversions: 0, evictions: 0 };
  function touch(url, entry) {
    entries.delete(url); entry.used = Date.now(); entries.set(url, entry); return entry;
  }
  function remove(url) {
    const entry = entries.get(url); if (!entry) return;
    entries.delete(url); bytes -= entry.size; stats.evictions += 1;
  }
  function trim() {
    const expired = Date.now() - ttlMs;
    for (const [url, entry] of entries) if (entry.used <= expired) remove(url);
    while (bytes > maxBytes && entries.size) remove(entries.keys().next().value);
  }
  function stale() { const error = new Error("Image belongs to an old viewer context"); error.name = "AbortError"; return error; }
  async function load(url, { priority = 0 } = {}) {
    trim();
    const existing = entries.get(url);
    if (existing) { stats.hits += 1; return touch(url, existing).blob; }
    stats.misses += 1;
    const startedGeneration = generation;
    return queue.schedule(url, async (signal) => {
      const response = await fetch(url, { cache: "no-store", credentials: "same-origin", signal,
        priority: priority === 0 ? "high" : "low" });
      if (response.status === 401) { clear(); location.assign("/login"); throw new Error("Phiên đăng nhập đã hết hạn"); }
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const blob = await response.blob();
      if (startedGeneration !== generation || signal.aborted) throw stale();
      if (blob.size <= maxBytes) {
        remove(url); entries.set(url, { blob, size: blob.size, used: Date.now(), sourcePromise: null });
        bytes += blob.size; trim();
      }
      return blob;
    }, priority);
  }
  function dataUrl(blob) {
    stats.conversions += 1;
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("Không đọc được ảnh cache"));
      reader.readAsDataURL(blob);
    });
  }
  async function source(url, options = {}) {
    const startedGeneration = generation;
    const blob = await load(url, options);
    if (startedGeneration !== generation) throw stale();
    const entry = entries.get(url);
    if (!entry || entry.blob !== blob) return dataUrl(blob).then((value) => {
      if (startedGeneration !== generation) throw stale();
      return value;
    });
    if (!entry.sourcePromise) {
      entry.sourcePromise = dataUrl(blob).then((value) => {
        if (startedGeneration !== generation) throw stale();
        if (entries.get(url) === entry) {
          // Conservative UTF-16 estimate; decoded image/DOM memory is separate.
          const extra = value.length * 2; entry.size += extra; bytes += extra; trim();
        }
        return value;
      }).catch((error) => { if (entries.get(url) === entry) entry.sourcePromise = null; throw error; });
    }
    return entry.sourcePromise;
  }
  function clear() { generation += 1; queue.clear(); entries.clear(); bytes = 0; }
  function startTimer() { if (timer === null) timer = setInterval(trim, 60000); }
  window.addEventListener("pagehide", () => { clearInterval(timer); timer = null; clear(); });
  window.addEventListener("pageshow", startTimer); startTimer();
  return { load, source, dataUrl, clear, trim, promote: (url, priority = 0) => queue.promote(url, priority),
    has: (url) => { trim(); return entries.has(url); },
    diagnostics: () => ({ entries: entries.size, bytes, maxBytes, configuredBytes, ttlMs,
      ...stats, requests: queue.diagnostics(), inflight: queue.jobs.size }) };
})();
