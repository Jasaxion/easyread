/* Capture before reader/UI listeners; keyboard activation is never an input lock. */
(function (root) {
  "use strict";
  function createReaderInput(host, PR) {
    let active = false, mode = "mouse", compatUntil = 0;
    const pens = new Set(), blocked = new Set(), rejectedClicks = new Map();
    let legacyRejected = null, lastPointer = null, pan = null;
    const doc = host.document, clock = () => Date.now();
    const onCanvas = e => !!e.target?.closest?.("#inkLayer");
    const penOnly = () => active && mode === "pen";
    const suppressing = () => pens.size > 0 || clock() < compatUntil;
    const stop = e => { if (e.cancelable) e.preventDefault(); e.stopImmediatePropagation(); };
    const syncContact = () => doc.documentElement.classList.toggle("ink-pen-contact", pens.size > 0);
    function endPan() {
      const old = pan; pan = null;
      if (old?.target?.hasPointerCapture?.(old.id)) old.target.releasePointerCapture(old.id);
    }
    function reset() {
      endPan();
      pens.clear(); blocked.clear(); rejectedClicks.clear(); legacyRejected = null; compatUntil = 0; syncContact();
    }
    function configure(enabled, nextMode) {
      // Keep only a bounded compatibility tail if a keyboard tool switch ends contact.
      const tail = pens.size ? clock() + 400 : compatUntil;
      reset(); active = !!enabled; mode = nextMode === "pen" ? "pen" : "mouse";
      compatUntil = tail;
    }
    function acceptsCanvas(e) {
      return penOnly() ? e.pointerType === "pen" : e.pointerType === "mouse" || e.pointerType === "touch";
    }
    function rememberRejected(e) {
      const entry = { target: e.target, x: e.clientX, y: e.clientY, until: clock() + 400 };
      rejectedClicks.set(e.pointerId, entry); legacyRejected = entry;
    }
    function rejectedCompatibility(e) {
      if (!["mousedown", "mouseup", "click", "dblclick", "contextmenu"].includes(e.type)) return false;
      const identified = typeof e.pointerId === "number";
      const entry = identified ? rejectedClicks.get(e.pointerId) : legacyRejected;
      // Older MouseEvents have no pointer ID: restrict the fallback to this release's target/position.
      return entry && clock() < entry.until && (identified || (e.target === entry.target && e.clientX === entry.x && e.clientY === entry.y));
    }
    function pointer(e) {
      if (e.type === "pointerdown" && onCanvas(e)) lastPointer = { type: e.pointerType || "unknown", button: e.button, buttons: e.buttons, pressure: e.pressure };
      for (const [id, entry] of rejectedClicks) if (clock() >= entry.until) rejectedClicks.delete(id);
      if (e.type === "pointerdown") {
        rejectedClicks.delete(e.pointerId); legacyRejected = null;
      }
      const trackedPen = pens.has(e.pointerId);
      const hoverEnd = trackedPen && e.type === "pointermove" && e.buttons === 0 && e.pressure === 0;
      const ending = hoverEnd || ["pointerup", "pointercancel", "lostpointercapture"].includes(e.type);
      if (e.pointerType === "pen" && (penOnly() || trackedPen)) {
        if (e.type === "pointerdown") {
          // A finger already on the canvas becomes a palm, not a resumed pan.
          if (pan) { blocked.add(pan.id); endPan(); }
          pens.add(e.pointerId); compatUntil = 0; syncContact();
          host.getSelection()?.removeAllRanges();
          PR.hideBlockbar?.(); doc.getElementById("selbar")?.classList.remove("open");
        }
        if (ending && trackedPen) {
          if (!hoverEnd && e.type !== "pointerup") blocked.clear();
          pens.delete(e.pointerId); compatUntil = clock() + 400; syncContact();
        }
        if (hoverEnd || (ending && trackedPen && !onCanvas(e))) PR.finishInkGesture?.(e.pointerId);
        if (e.cancelable) e.preventDefault();
        if (!onCanvas(e)) stop(e);
        return;
      }
      // A fresh pointer gesture may configure/exit immediately after pen-up.
      // Old palm releases and generated compatibility clicks are still rejected.
      if (e.type === "pointerdown" && !pens.size) {
        blocked.delete(e.pointerId);
        if (e.pointerType === "mouse" || e.pointerType === "touch") compatUntil = 0;
      }
      // Keep touch-action:none for stable pen strokes across browsers. Finger
      // navigation on this canvas is an explicit drag, not native/inertial pan.
      // A fresh finger may navigate immediately after pen-up despite the click tail.
      if (penOnly() && e.pointerType === "touch" && !pens.size && !blocked.has(e.pointerId) &&
          (onCanvas(e) || pan?.id === e.pointerId)) {
        if (e.type === "pointerdown" && !pan) {
          pan = { id: e.pointerId, y: e.clientY, start: e.clientY, moved: false, target: doc.getElementById("inkLayer") };
          try { pan.target.setPointerCapture(e.pointerId); } catch (_) { /* Window routing still handles releases. */ }
        }
        if (pan?.id === e.pointerId) {
          if (e.type === "pointermove") {
            const dy = pan.y - e.clientY;
            pan.moved ||= Math.abs(e.clientY - pan.start) > 5;
            pan.y = e.clientY;
            if (pan.moved) host.scrollBy?.(0, dy);
          }
          if (ending) { rememberRejected(e); endPan(); }
        }
        stop(e); return;
      }
      const reject = blocked.has(e.pointerId) || suppressing() || (penOnly() && onCanvas(e));
      if (reject && e.type === "pointerdown") { blocked.add(e.pointerId); rememberRejected(e); }
      if (ending && blocked.delete(e.pointerId)) rememberRejected(e);
      if (reject) stop(e);
    }
    for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel", "lostpointercapture", "pointerover", "pointerout"])
      host.addEventListener(type, pointer, { capture: true, passive: false });
    for (const type of ["mousedown", "mouseup", "mousemove", "mouseover", "mouseout", "click", "dblclick", "contextmenu", "selectstart", "selectionchange", "touchstart", "touchmove", "touchend", "wheel", "dragstart"]) {
      host.addEventListener(type, e => {
        // Wheel/trackpad navigation is not drawing; block it only during actual pen contact.
        if (type === "wheel") { if (pens.size) stop(e); return; }
        if (type === "click" && e.detail === 0 && !e.pointerType) return;
        if (suppressing() || rejectedCompatibility(e) || (penOnly() && (e.pointerType === "pen" || onCanvas(e)))) stop(e);
      }, { capture: true, passive: false });
    }
    // Canvas tools consume pen pointers without exposing them to paragraph/UI handlers.
    const layer = doc.getElementById("inkLayer");
    for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"])
      layer?.addEventListener(type, e => { if (penOnly() && e.pointerType === "pen") e.stopPropagation(); });
    host.addEventListener("blur", reset);
    doc.addEventListener("visibilitychange", () => { if (doc.hidden) reset(); });
    return { configure, acceptsCanvas, penOnly, suppressing, reset, lastPointer: () => lastPointer && { ...lastPointer } };
  }
  if (typeof module === "object" && module.exports) module.exports = { createReaderInput };
  else root.PR.readerInput = createReaderInput(root, root.PR);
})(typeof window === "object" ? window : globalThis);
