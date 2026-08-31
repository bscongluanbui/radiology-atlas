"use strict";

// The offline viewer keeps using the browser HTTP cache. The Docker gateway
// supplies viewerRuntime and uses this bounded, tab-local cache because all
// authenticated responses are deliberately sent as private/no-store.
window.viewerResourceCache = (() => {
  const runtime = window.viewerRuntime;
  if (!runtime?.remote) return null;
  const maxBytes = Math.max(16 * 1024 ** 2, Number(runtime.maxBytes) || 256 * 1024 ** 2);
  const ttlMs = Math.max(60000, Number(runtime.ttlMs) || 900000);
  const entries = new Map();
  const inflight = new Map();
  let bytes = 0;
  let generation = 0;

  function touch(url, entry) {
    entries.delete(url);
    entry.used = Date.now();
    entries.set(url, entry);
    return entry;
  }
  function remove(url) {
    const entry = entries.get(url);
    if (!entry) return;
    entries.delete(url);
    bytes -= entry.size;
  }
  function trim() {
    const expired = Date.now() - ttlMs;
    for (const [url, entry] of entries) if (entry.used <= expired) remove(url);
    while (bytes > maxBytes && entries.size) remove(entries.keys().next().value);
  }
  async function load(url) {
    trim();
    const existing = entries.get(url);
    if (existing) return touch(url, existing).blob;
    if (inflight.has(url)) return inflight.get(url);
    const startedGeneration = generation;
    const promise = fetch(url, { cache: "no-store", credentials: "same-origin" }).then(async (response) => {
      if (response.status === 401) {
        clear();
        location.assign("/login");
        throw new Error("Phiên đăng nhập đã hết hạn");
      }
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const blob = await response.blob();
      if (startedGeneration === generation && blob.size <= maxBytes) {
        remove(url);
        entries.set(url, { blob, size: blob.size, used: Date.now() });
        bytes += blob.size;
        trim();
      }
      return blob;
    }).finally(() => { if (inflight.get(url) === promise) inflight.delete(url); });
    inflight.set(url, promise);
    return promise;
  }
  function dataUrl(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("Không đọc được ảnh cache"));
      reader.readAsDataURL(blob);
    });
  }
  function clear() { generation += 1; entries.clear(); inflight.clear(); bytes = 0; }
  const timer = setInterval(trim, 60000);
  window.addEventListener("pagehide", () => { clearInterval(timer); clear(); }, { once: true });
  return { load, dataUrl, clear, trim, diagnostics: () => ({ entries: entries.size, bytes, maxBytes, ttlMs, inflight: inflight.size }) };
})();
