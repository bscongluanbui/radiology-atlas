"use strict";

// Independent of mouse tools. Capture uses the stable viewport, not replaceable labels.
window.ViewerTouchGestures = class ViewerTouchGestures {
  constructor(viewport, callbacks, host = window) {
    this.viewport = viewport; this.callbacks = callbacks; this.host = host;
    this.points = new Map(); this.timer = null; this.frame = null; this.session = null;
    viewport.addEventListener("pointerdown", (event) => this.down(event), { passive: false });
    viewport.addEventListener("lostpointercapture", (event) => {
      if (this.points.has(event.pointerId)) this.cancel();
    });
    host.addEventListener("pointermove", (event) => this.move(event), { passive: false });
    host.addEventListener("pointerup", (event) => this.up(event), { passive: false });
    host.addEventListener("pointercancel", (event) => {
      if (this.points.has(event.pointerId)) this.cancel();
    });
    host.addEventListener("blur", () => this.cancel());
    host.addEventListener("pagehide", () => this.cancel());
  }

  clearHold() {
    if (this.timer !== null) this.host.clearTimeout(this.timer);
    this.timer = null;
  }

  down(event) {
    if (event.pointerType !== "touch" || !this.callbacks.ready()) return;
    if (event.target.closest?.("button,input,select,textarea,a")) return;
    event.preventDefault(); this.callbacks.begin();
    this.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
    try { this.viewport.setPointerCapture(event.pointerId); } catch { /* Contact ended. */ }
    if (this.points.size === 1) {
      const item = this.callbacks.itemAt(event.target);
      const session = this.session = {
        x: event.clientX, y: event.clientY, scrollY: event.clientY,
        moved: false, held: false, multi: false, item,
      };
      if (item) this.timer = this.host.setTimeout(() => {
        this.timer = null;
        if (this.session !== session || session.moved || session.multi || this.points.size !== 1) return;
        session.held = true; this.callbacks.hold(item);
      }, 450);
    } else {
      this.clearHold(); this.cancelFrame();
      this.session.multi = true; this.session.pinch = this.pinch();
    }
  }

  pinch() {
    const [a, b] = this.points.values();
    return { distance: Math.hypot(b.x - a.x, b.y - a.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  move(event) {
    if (!this.points.has(event.pointerId)) return;
    event.preventDefault(); this.updatePoint(event);
    if (this.session.moved) this.clearHold();
    if (this.frame === null) this.frame = this.host.requestAnimationFrame(() => {
      this.frame = null; this.flush();
    });
  }

  flush() {
    const session = this.session;
    if (!session) return;
    if (this.points.size === 2) {
      const current = this.pinch(), previous = session.pinch;
      if (previous?.distance > 0 && current.distance > 0) {
        this.callbacks.zoom(current.distance / previous.distance,
          { clientX: previous.x, clientY: previous.y }, current.x - previous.x, current.y - previous.y);
      }
      session.pinch = current;
    } else if (this.points.size === 1 && !session.multi && session.moved && !session.held) {
      const point = this.points.values().next().value;
      const steps = Math.trunc((point.y - session.scrollY) / 12);
      if (steps) { session.scrollY += steps * 12; this.callbacks.scroll(steps); }
    }
    // After a pinch, ignore the remaining finger until all fingers are lifted.
  }

  up(event) {
    if (!this.points.has(event.pointerId)) return;
    event.preventDefault(); this.clearHold(); this.cancelFrame();
    this.updatePoint(event); this.flush();
    const session = this.session;
    this.points.delete(event.pointerId); this.release(event.pointerId);
    if (this.points.size >= 2) session.pinch = this.pinch();
    if (!this.points.size) {
      this.session = null;
      if (!session.multi && !session.moved && !session.held && session.item) this.callbacks.tap(session.item);
    }
  }

  updatePoint(event) {
    this.points.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (!this.session.multi
      && Math.hypot(event.clientX - this.session.x, event.clientY - this.session.y) > 8) this.session.moved = true;
  }

  release(id) {
    if (this.viewport.hasPointerCapture?.(id)) this.viewport.releasePointerCapture(id);
  }

  cancelFrame() {
    if (this.frame !== null) this.host.cancelAnimationFrame(this.frame);
    this.frame = null;
  }

  cancel() {
    this.clearHold(); this.cancelFrame();
    const ids = [...this.points.keys()];
    this.points.clear(); this.session = null;
    ids.forEach((id) => this.release(id));
  }
};
