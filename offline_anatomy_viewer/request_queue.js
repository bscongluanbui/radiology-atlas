"use strict";

// Reserve request capacity for the visible frame, ahead of background preload.
window.ViewerRequestQueue = class ViewerRequestQueue {
  constructor({ concurrency = 4, background = concurrency - 1 } = {}) {
    this.limit = Math.max(2, Math.min(8, Math.floor(Number(concurrency) || 4)));
    this.backgroundLimit = Math.max(1, Math.min(this.limit - 1, Math.floor(Number(background) || 1)));
    this.jobs = new Map(); this.active = 0; this.backgroundActive = 0; this.sequence = 0;
  }
  schedule(key, factory, priority = 2) {
    const existing = this.jobs.get(key);
    if (existing) { this.promote(key, priority); return existing.promise; }
    const job = { key, factory, priority, order: this.sequence++, running: false, cancelled: false, controller: new AbortController() };
    job.promise = new Promise((resolve, reject) => { job.resolve = resolve; job.reject = reject; });
    this.jobs.set(key, job); this.pump(); return job.promise;
  }
  promote(key, priority = 0) {
    const job = this.jobs.get(key);
    if (job) { job.priority = Math.min(job.priority, priority); this.pump(); }
  }
  pump() {
    while (this.active < this.limit) {
      const job = [...this.jobs.values()].filter((item) => !item.running && !item.cancelled
        && (item.priority === 0 || this.backgroundActive < this.backgroundLimit))
        .sort((a, b) => a.priority - b.priority || a.order - b.order)[0];
      if (!job) return;
      job.running = true; job.background = job.priority !== 0;
      this.active += 1; if (job.background) this.backgroundActive += 1;
      let work;
      try { work = job.factory(job.controller.signal); } catch (error) { work = Promise.reject(error); }
      Promise.resolve(work).then(
        (value) => { if (!job.cancelled) job.resolve(value); },
        (error) => { if (!job.cancelled) job.reject(error); },
      ).finally(() => {
        if (this.jobs.get(job.key) === job) this.jobs.delete(job.key);
        this.active -= 1; if (job.background) this.backgroundActive -= 1;
        this.pump();
      });
    }
  }
  clear() {
    const error = new Error("Viewer request cancelled after context change"); error.name = "AbortError";
    for (const job of this.jobs.values()) { job.cancelled = true; job.controller.abort(); job.reject(error); }
    this.jobs.clear();
  }
  diagnostics() {
    return { limit: this.limit, backgroundLimit: this.backgroundLimit, active: this.active,
      backgroundActive: this.backgroundActive, queued: [...this.jobs.values()].filter((job) => !job.running).length };
  }
};
