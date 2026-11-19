/* iPad-only live preview. Stroke storage and the shared SVG curve format stay unchanged. */
(function (root) {
  "use strict";
  function createIPadPencilPreview(window, {layer, stage, block, rect, stroke, pathData}) {
    const sr = stage.getBoundingClientRect();
    const transform = `translate(${rect.left - sr.left} ${rect.top - sr.top + stage.scrollTop})`;
    const scrollX = window.scrollX || 0, scrollY = window.scrollY || 0;
    let path = null, frame = null, closed = false;
    const connected = () => !closed && block.isConnected;
    function geometryValid() {
      if (!connected()) return false;
      const current = block.getBoundingClientRect(), currentStage = stage.getBoundingClientRect();
      return ["left", "top", "width", "height"].every(key => Math.abs(current[key] - rect[key]) < .1) &&
        Math.abs(currentStage.left - sr.left) < .1 && Math.abs(currentStage.top - sr.top) < .1;
    }
    const round = n => Math.round(n * 10) / 10;
    function samples(event, endpoint = false) {
      if (!geometryValid()) return false;
      let points = [];
      try { points = Array.from(event.getCoalescedEvents?.() || []); } catch (_) { /* Older WebKit: use the dispatched event. */ }
      points.push(event);
      for (const [index, sample] of points.entries()) {
        if (!Number.isFinite(sample.clientX) || !Number.isFinite(sample.clientY)) continue;
        const p = [round(sample.clientX - rect.left), round(sample.clientY - rect.top)];
        const previous = stroke.points[stroke.points.length - 1];
        const distance = Math.hypot(p[0] - previous[0], p[1] - previous[1]);
        if (distance < 1.2 && !(endpoint && index === points.length - 1 && distance > 0)) continue;
        if (stroke.points.length < 2000) stroke.points.push(p); else stroke.points[1999] = p;
      }
      return true;
    }
    function paint() {
      if (!geometryValid()) return;
      if (!path) {
        path = window.document.createElementNS("http://www.w3.org/2000/svg", "path");
        for (const [name, value] of Object.entries({fill:"none", stroke:stroke.color, "stroke-width":stroke.width,
          "stroke-linecap":"round", "stroke-linejoin":"round", "pointer-events":"none", "vector-effect":"non-scaling-stroke"})) path.setAttribute(name, value);
      }
      path.setAttribute("d", pathData(stroke.points)); path.setAttribute("transform", transform);
      if (path.parentNode !== layer) layer.append(path);
    }
    function schedule() {
      if (closed || frame !== null) return;
      const id = window.requestAnimationFrame(() => {
        if (closed || frame !== id) return;
        frame = null; paint();
      });
      frame = id;
    }
    function dispose() {
      closed = true;
      if (frame !== null) window.cancelAnimationFrame?.(frame);
      frame = null; path?.remove(); path = null;
    }
    return {samples, paint, schedule, dispose, connected, geometryValid,
      sameScroll: () => (window.scrollX || 0) === scrollX && (window.scrollY || 0) === scrollY};
  }
  if (typeof module === "object" && module.exports) module.exports = {createIPadPencilPreview};
  else root.PR.createIPadPencilPreview = createIPadPencilPreview;
})(typeof window === "object" ? window : globalThis);
