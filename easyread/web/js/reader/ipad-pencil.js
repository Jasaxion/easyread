/* iPad-only live surface and saved-layer invalidation. Stored curves are unchanged. */
(function (root) {
  "use strict";
  function createIPadInkRenderer(window, {paint, enabled, busy}) {
    let dirty = true, frame = null;
    function cancel() { if (frame !== null) window.cancelAnimationFrame(frame); frame = null; }
    function flush() {
      cancel();
      if (!dirty || busy()) return;
      dirty = false; paint();
    }
    function request() {
      dirty = true;
      if (!enabled()) { cancel(); paint(); return; }
      if (busy() || frame !== null) return;
      const id = window.requestAnimationFrame(() => { if (frame === id) flush(); });
      frame = id;
    }
    function commit(stroke) {
      cancel();
      // Ordinary pen-up adds just this stroke; pending layout/data changes need a full pass.
      const full = dirty; dirty = false; paint(full ? null : stroke);
    }
    return {request, commit, flush, resume:() => { if (dirty) request(); }};
  }
  function createIPadPencilPreview(window, {stage, block, rect, stroke, pathData}) {
    const doc = window.document, sr = stage.getBoundingClientRect();
    let current = rect, path = null, surface = null, frame = null, fallback = null, closed = false;
    const connected = () => !closed && block.isConnected;
    function geometryValid() {
      if (!connected()) return false;
      const next = block.getBoundingClientRect(), host = stage.getBoundingClientRect();
      // A shared viewport translation is not paragraph reflow. Rebase samples to the current rect.
      const valid = Math.abs(next.width - rect.width) < .5 && Math.abs(next.height - rect.height) < .5 &&
        Math.abs((next.left - host.left) - (rect.left - sr.left)) < .5 &&
        Math.abs((next.top - host.top) - (rect.top - sr.top)) < .5;
      if (valid) current = next;
      return valid;
    }
    const round = n => Math.round(n * 10) / 10;
    function samples(event, endpoint = false) {
      if (!geometryValid()) return false;
      let points = [];
      try { points = Array.from(event.getCoalescedEvents?.() || []); } catch (_) { /* Older WebKit: dispatched event only. */ }
      // Coalesced samples are authoritative; appending a rounded parent can add a backward step.
      if (endpoint || !points.length) points = [event];
      for (const [index, sample] of points.entries()) {
        if (!Number.isFinite(sample.clientX) || !Number.isFinite(sample.clientY)) continue;
        const p = [round(sample.clientX - current.left), round(sample.clientY - current.top)];
        const previous = stroke.points[stroke.points.length - 1];
        const distance = Math.hypot(p[0] - previous[0], p[1] - previous[1]);
        if (distance < .5 && !(endpoint && index === points.length - 1 && distance > 0)) continue;
        if (stroke.points.length < 2000) stroke.points.push(p); else stroke.points[1999] = p;
      }
      return true;
    }
    function paint() {
      if (!geometryValid()) return;
      if (!surface) {
        surface = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
        surface.setAttribute("class", "ipad-ink-preview"); surface.setAttribute("aria-hidden", "true");
        path = doc.createElementNS("http://www.w3.org/2000/svg", "path");
        for (const [name, value] of Object.entries({fill:"none", stroke:stroke.color, "stroke-width":stroke.width,
          "stroke-linecap":"round", "stroke-linejoin":"round", "pointer-events":"none", "vector-effect":"non-scaling-stroke"})) path.setAttribute(name, value);
        surface.append(path); doc.body.append(surface);
      }
      const width = window.innerWidth, height = window.innerHeight;
      surface.style.width = width + "px"; surface.style.height = height + "px";
      surface.setAttribute("viewBox", `0 0 ${width} ${height}`);
      const viewport = surface.getBoundingClientRect();
      path.setAttribute("d", pathData(stroke.points));
      path.setAttribute("transform", `translate(${current.left - viewport.left} ${current.top - viewport.top})`);
    }
    function clearFrame() {
      if (frame !== null) window.cancelAnimationFrame?.(frame);
      if (fallback !== null) window.clearTimeout?.(fallback);
      frame = fallback = null;
    }
    function schedule() {
      if (closed || frame !== null) return;
      const id = window.requestAnimationFrame(() => flush(id));
      frame = id;
      // Safari may delay rAF while resuming; do not leave accepted samples invisible indefinitely.
      fallback = window.setTimeout?.(() => flush(id), 150) ?? null;
    }
    function flush(id) {
      if (closed || frame !== id) return;
      clearFrame();
      if (!doc.hidden) paint();
    }
    function dispose() {
      closed = true; clearFrame(); surface?.remove(); surface = path = null;
    }
    return {samples, paint, schedule, dispose, connected, geometryValid, sameScroll:geometryValid};
  }
  if (typeof module === "object" && module.exports) module.exports = {createIPadPencilPreview, createIPadInkRenderer};
  else Object.assign(root.PR, {createIPadPencilPreview, createIPadInkRenderer});
})(typeof window === "object" ? window : globalThis);
