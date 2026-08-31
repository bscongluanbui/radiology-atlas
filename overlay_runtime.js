/* Observe the production Pinia anatomy store before viewer startup.
   Never reads credentials, changes filters, replaces resources or patches the
   renderer bundle. Map.set keeps its original arguments/return/error behavior. */
(() => {
  'use strict';
  if (window.__offlineOverlayRuntime?.version === 1) return;
  const descriptor = Object.getOwnPropertyDescriptor(Map.prototype, 'set');
  const original = descriptor.value;
  const references = [];
  let observing = false;
  let observed = 0;
  const liveStores = () => references.map(r => r.deref()).filter(Boolean);
  function set(key, value) {
    const result = Reflect.apply(original, this, arguments);
    if (!observing && key === 'anatomy' && value && typeof value === 'object') {
      observing = true;
      try {
        // Verified against the production Pinia registration order: the store
        // is inserted before setup's application ref is assigned. Retain the
        // store reference, not its initially absent/null application value.
        if (value.$id === 'anatomy' && typeof value.$patch === 'function'
            && typeof value.$subscribe === 'function' && !liveStores().includes(value)) {
          observed++;
          for (let i = references.length - 1; i >= 0; i--) {
            if (!references[i].deref()) references.splice(i, 1);
          }
          if (references.length >= 16) references.shift();
          references.push(new WeakRef(value));
        }
      } catch (_) {
        // Observability must not change application behavior.
      } finally {
        observing = false;
      }
    }
    return result;
  }
  const bridge = Object.freeze({
    version: 1,
    applicationsForCanvas(canvas) {
      const applications = new Set();
      for (const store of liveStores()) {
        try {
          const value = store.application;
          const app = value?.value || value;
          if (app?.components?.imageCanvas?.getCanvas?.() === canvas) applications.add(app);
        } catch (_) {}
      }
      return [...applications];
    },
    diagnostics() { return {version: 1, stores_observed: observed, stores_alive: liveStores().length}; },
  });
  Object.defineProperty(Map.prototype, 'set', {...descriptor, value: set});
  Object.defineProperty(window, '__offlineOverlayRuntime', {value: bridge});
})();
