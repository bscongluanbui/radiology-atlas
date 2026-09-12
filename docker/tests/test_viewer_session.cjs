"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(process.argv[2] || path.join(__dirname, "../.."));
const sourcePath = process.env.VIEWER_SESSION_SOURCE || path.join(root, "docker/static/viewer-session.js");
const source = fs.readFileSync(sourcePath, "utf8");

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function response(status = 200, body = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    clone() { return response(status, body); },
    json: async () => body,
  };
}

function harness() {
  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const timeoutDelays = [];
  const listeners = new Map();
  const documentListeners = new Map();
  const sessionQueue = [];
  const protectedQueue = [];
  const sessionCalls = [];
  const protectedCalls = [];
  const initialSession = deferred();
  sessionQueue.push(initialSession);
  let suspended = 0;
  let cacheClears = 0;
  let dialogOpen = true;
  const documentElement = { classList: { add() {}, remove() {} } };
  const dialog = {
    matches: (selector) => selector === ":modal" && dialogOpen,
    removeAttribute: (name) => { if (name === "open") dialogOpen = false; },
    showModal: () => { dialogOpen = true; },
    close: () => { dialogOpen = false; },
    contains: () => false,
    addEventListener: (name, fn) => { documentListeners.set(`dialog:${name}`, fn); },
  };
  const retry = { hidden: true, addEventListener: (name, fn) => { documentListeners.set(`retry:${name}`, fn); } };
  const message = { textContent: "" };
  const app = { inert: true };
  const document = {
    visibilityState: "visible",
    documentElement,
    getElementById: (id) => ({ viewerSessionDialog: dialog, viewerSessionMessage: message, viewerSessionRetry: retry, app }[id] || null),
    querySelector: () => ({ value: "csrf-fixture" }),
    addEventListener: (name, fn) => { documentListeners.set(name, fn); },
  };
  const FakeDate = class extends Date { static now() { return now; } };
  const window = {
    fetch: (input, options = {}) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url === "/api/viewer-session") {
        sessionCalls.push({ input, options });
        const item = sessionQueue.shift();
        return item ? item.promise : Promise.reject(new Error("missing session fixture"));
      }
      protectedCalls.push({ input, options });
      const item = protectedQueue.shift();
      return item ? item.promise : Promise.reject(new Error("missing protected fixture"));
    },
    addEventListener: (name, fn) => { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push(fn); },
    dispatchEvent: (event) => {
      if (event.type === "viewer-session-suspended") suspended += 1;
      (listeners.get(event.type) || []).forEach((fn) => fn(event));
    },
    viewerResourceCache: { clear: () => { cacheClears += 1; } },
  };
  const context = {
    window,
    document,
    location: { origin: "https://atlas.test", href: "https://atlas.test/viewer" },
    URL,
    Request,
    Headers,
    DOMException,
    AbortController,
    URLSearchParams,
    Date: FakeDate,
    setTimeout: (fn, delay) => { timeoutDelays.push(delay); const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout: (id) => { timers.delete(id); },
    crypto: { getRandomValues: (bytes) => crypto.randomFillSync(bytes) },
    Event,
    console,
  };
  vm.runInNewContext(source, context, { filename: "viewer-session.js" });

  function queueSession(item = deferred()) { sessionQueue.push(item); return item; }
  function queueProtected(item = deferred()) { protectedQueue.push(item); return item; }
  function emitDocument(type) { (documentListeners.get(type) || (() => {}))({ type }); }
  function emitWindow(type) { window.dispatchEvent({ type }); }
  function runTimer(predicate) {
    const entry = [...timers.entries()].find(([, fn]) => predicate(fn));
    assert.ok(entry, "expected timer");
    timers.delete(entry[0]);
    entry[1]();
  }
  return {
    window,
    document,
    dialog,
    app,
    message,
    retry,
    sessionCalls,
    protectedCalls,
    queueSession,
    initialSession,
    queueProtected,
    emitDocument,
    emitWindow,
    runTimer,
    timeoutDelays,
    setNow: (value) => { now = value; },
    resetMetrics: () => { suspended = 0; cacheClears = 0; },
    get suspended() { return suspended; },
    get cacheClears() { return cacheClears; },
    set suspended(value) { suspended = value; },
  };
}

async function settle() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

async function testHideKeepsLeaseAndHeartbeat() {
  const h = harness();
  h.initialSession.resolve(response(200, { ttl: 90, heartbeat: 20 }));
  await settle();
  h.resetMetrics();
  assert.equal(h.window.viewerSession.blocked, false);
  assert.equal(h.dialog.matches(":modal"), false);

  h.document.visibilityState = "hidden";
  h.emitDocument("visibilitychange");
  h.emitWindow("focus");
  assert.equal(h.sessionCalls.length, 1, "hiding/focusing a hidden document must not reacquire");
  assert.equal(h.window.viewerSession.blocked, false);
  assert.equal(h.cacheClears, 0);
  assert.equal(h.suspended, 0);

  const heartbeat = h.queueSession();
  h.runTimer((fn) => String(fn).includes("check(\"heartbeat\")"));
  heartbeat.resolve(response(200, { ttl: 90, heartbeat: 20 }));
  await settle();
  assert.equal(h.sessionCalls.at(-1).options.body.get("action"), "heartbeat");
  assert.equal(h.cacheClears, 0);
  assert.equal(h.suspended, 0);
}

async function testResumeGatesProtectedFetch() {
  const h = harness();
  h.initialSession.resolve(response());
  await settle();
  h.resetMetrics();
  h.document.visibilityState = "hidden";
  h.emitDocument("visibilitychange");
  h.document.visibilityState = "visible";
  const resumed = h.queueSession();
  const data = h.queueProtected();
  h.emitDocument("visibilitychange");
  const pendingFetch = h.window.fetch("/data/series/slice.png");
  await settle();
  assert.equal(h.protectedCalls.length, 0, "protected work waits for resume acquire");
  resumed.resolve(response());
  data.resolve(response(200, { ok: true }));
  const result = await pendingFetch;
  assert.equal(result.status, 200);
  assert.equal(h.protectedCalls.length, 1);
  assert.equal(h.sessionCalls.at(-1).options.body.get("action"), "acquire");
}

async function testExpiredResponseRetriesReadWithoutSuspending() {
  const h = harness();
  h.initialSession.resolve(response());
  await settle();
  h.resetMetrics();
  const expired = h.queueProtected();
  const renewed = h.queueSession();
  const retried = h.queueProtected();
  expired.resolve(response(409, { code: "viewer_expired", error: "expired" }));
  renewed.resolve(response());
  retried.resolve(response(200, { ok: true }));
  const result = await h.window.fetch("/api/module?key=BRAIN/mri-brain");
  assert.equal(result.status, 200);
  assert.equal(h.protectedCalls.length, 2);
  assert.equal(h.cacheClears, 0);
  assert.equal(h.suspended, 0);
}

async function testConflictAnd401StillBlock() {
  for (const [status, body] of [[409, { code: "viewer_conflict", error: "conflict" }], [401, { code: "login_required", error: "login" }]]) {
    const h = harness();
    h.initialSession.resolve(response());
    await settle();
    h.resetMetrics();
    const denied = h.queueProtected();
    denied.resolve(response(status, body));
    await assert.rejects(h.window.fetch("/api/module?key=BRAIN/mri-brain"), { name: "AbortError" });
    assert.equal(h.cacheClears, 1);
    assert.equal(h.dialog.matches(":modal"), true);
  }
}

async function testThrottledHeartbeatAndConcurrentResume() {
  for (const resumeDuringHeartbeat of [false, true]) {
    const h = harness(); h.initialSession.resolve(response()); await settle(); h.resetMetrics();
    h.document.visibilityState = "hidden"; h.emitDocument("visibilitychange");
    h.setNow(100000);
    const heartbeat = h.queueSession();
    h.runTimer(fn => String(fn).includes('check("heartbeat")'));
    const renewed = h.queueSession();
    if (resumeDuringHeartbeat) {
      h.document.visibilityState = "visible"; h.emitDocument("visibilitychange"); h.emitWindow("focus");
    }
    heartbeat.resolve(response(409, { code: "viewer_expired" })); await settle();
    assert.equal(h.sessionCalls.length, 3, "one acquire follows expired heartbeat");
    assert.equal(h.dialog.matches(":modal"), false);
    const data = h.queueProtected();
    const result = h.window.fetch('/api/module?key=BRAIN/mri-brain');
    await settle(); assert.equal(h.protectedCalls.length, 0);
    renewed.resolve(response()); data.resolve(response()); await result;
    assert.equal(h.cacheClears, 0); assert.equal(h.suspended, 0);
    assert.equal(h.sessionCalls.length, 3);
  }
}

async function testReacquireFailureKeepsReasonAndNoRetryWrites() {
  const h = harness(); h.initialSession.resolve(response()); await settle(); h.resetMetrics();
  h.queueProtected().resolve(response(409, { code: 'viewer_expired' }));
  h.queueSession().resolve(response(401, { code: 'login_required' }));
  await assert.rejects(h.window.fetch('/api/module'), { name: 'AbortError' });
  assert.equal(h.retry.hidden, true);
  assert.match(h.message.textContent, /đăng nhập/);
  assert.equal(h.protectedCalls.length, 1);
  const w = harness(); w.initialSession.resolve(response()); await settle(); w.resetMetrics();
  w.queueProtected().resolve(response(409, { code: 'viewer_expired' }));
  await assert.rejects(w.window.fetch('/api/write', {method: 'POST'}), { name: 'AbortError' });
  assert.equal(w.sessionCalls.length, 1); assert.equal(w.protectedCalls.length, 1);
}

async function testSlowNetworkRecoveryPreservesCache() {
  for (const failure of ['timeout', '503', 'offline']) {
    const h = harness(); h.initialSession.resolve(response()); await settle(); h.resetMetrics();
    let resumed = 0;
    h.window.addEventListener('viewer-session-resumed', () => { resumed++; });
    if (failure === 'offline') h.emitWindow('offline');
    else {
      const heartbeat = h.queueSession();
      h.runTimer(fn => String(fn).includes('check("heartbeat")'));
      if (failure === '503') heartbeat.resolve(response(503));
      else heartbeat.reject(new DOMException('slow network', 'AbortError'));
      await settle();
    }
    assert.equal(h.dialog.matches(':modal'), false);
    assert.equal(h.cacheClears, 0); assert.equal(h.suspended, 0);
    assert.equal(h.window.viewerSession.blocked, false, 'valid lease retained');
    h.setNow(100000);
    const renewed = h.queueSession();
    h.runTimer(fn => String(fn).includes('acquire()'));
    renewed.resolve(response()); await settle();
    assert.equal(h.window.viewerSession.blocked, false);
    assert.equal(h.cacheClears, 0); assert.equal(resumed, 1);
  }
}

async function testExpiredConnectionFailureNoProtectedReads() {
  const h = harness(); h.initialSession.resolve(response()); await settle(); h.resetMetrics();
  h.setNow(100000);
  h.queueSession().reject(new TypeError('network down'));
  await assert.rejects(h.window.fetch('/api/module'), {name:'AbortError'});
  assert.equal(h.window.viewerSession.blocked, true);
  assert.equal(h.protectedCalls.length, 0);
  assert.equal(h.dialog.matches(':modal'), false);
  assert.equal(h.cacheClears, 0);
  h.queueSession().resolve(response(401)); h.emitWindow('online'); await settle();
  assert.equal(h.dialog.matches(':modal'), true);
  assert.equal(h.cacheClears, 1, 'revoked login still clears protected cache');
}

async function testSlowHandshakeAndBoundedBackoff() {
  const h = harness();
  assert.equal(h.timeoutDelays[0], 30000);
  h.setNow(12000); h.initialSession.resolve(response()); await settle(); h.resetMetrics();
  assert.equal(h.window.viewerSession.blocked, false);
  assert.equal(h.dialog.matches(':modal'), false);
  for (let i=0; i<8; i++) {
    h.queueSession().reject(new TypeError('slow'));
    h.emitWindow('online'); await settle();
    assert.equal(h.timeoutDelays.at(-1), Math.min(15000, 2000 * 2**i));
    assert.equal(h.cacheClears, 0);
  }
}

(async () => {
  await testHideKeepsLeaseAndHeartbeat();
  await testResumeGatesProtectedFetch();
  await testExpiredResponseRetriesReadWithoutSuspending();
  await testConflictAnd401StillBlock();
  await testThrottledHeartbeatAndConcurrentResume();
  await testReacquireFailureKeepsReasonAndNoRetryWrites();
  await testSlowNetworkRecoveryPreservesCache();
  await testExpiredConnectionFailureNoProtectedReads();
  await testSlowHandshakeAndBoundedBackoff();
  console.log("VIEWER_SESSION=PASS; focus,lease_recovery,slow_handshake,transient_cache_preserved,bounded_backoff,expired_reads_blocked,401_conflict_enforced");
})().catch((error) => { console.error(error); process.exitCode = 1; });
