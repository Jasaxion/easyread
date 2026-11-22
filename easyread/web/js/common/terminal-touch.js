/* Touch swipes enter xterm's public wheel path: scrollback, alternate-screen arrows,
   and mouse reports stay xterm's responsibility. No terminal input is synthesized here. */
(function (PR) {
  "use strict";
  const frames = new Map(), RETRY_MS = 100, MAX_ATTEMPTS = 100;
  const INTENT_PX = 10, HOLD_MS = 350;
  function allowed(value) {
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin && ["http:", "https:"].includes(url.protocol) &&
        !url.username && !url.password && !url.search && !url.hash &&
        /^\/(?:terminal|workspace-terminal)\/[a-zA-Z0-9_-]+\/$/.test(url.pathname);
    } catch (_) { return false; }
  }
  function attach(runtime, term) {
    const element = term.element, doc = runtime.document;
    if (!element || typeof runtime.WheelEvent !== "function" ||
      !runtime.requestAnimationFrame || !runtime.cancelAnimationFrame) return null;
    // Some old WebViews expose WheelEvent but cannot construct it.
    try { new runtime.WheelEvent("wheel"); } catch (_) { return null; }
    let gesture = null, animation = null, delta = 0, composing = false, claimed = false;
    function cancelAnimation() {
      const pending = animation; animation = null;
      // The WindowProxy can become inaccessible after an iframe navigation.
      try { if (pending !== null) runtime.cancelAnimationFrame(pending); } catch (_) {}
    }
    function cancelMovement() {
      delta = 0; gesture = null; cancelAnimation();
    }
    function reset() { claimed = false; cancelMovement(); }
    function suppressNative(event) {
      // Once wheel translation owns a gesture, xterm's legacy touch baseline is
      // stale. Do not hand later moves back to it (selection/pinch/IME jumps).
      // Not preventing default here leaves browser pinch/selection available.
      if (claimed) event.stopImmediatePropagation();
    }
    const selected = () => term.hasSelection?.() || doc.getSelection?.()?.isCollapsed === false;
    const point = event => {
      if (event.touches.length !== 1) return null;
      const touch = event.touches[0];
      return Number.isFinite(touch.clientX) && Number.isFinite(touch.clientY) &&
        touch.touchType !== "stylus" ? touch : null;
    };
    function flush() {
      animation = null;
      if (!gesture || composing || selected()) { cancelMovement(); return; }
      const amount = delta; delta = 0;
      if (!amount) return;
      element.dispatchEvent(new runtime.WheelEvent("wheel", {
        deltaY: amount, deltaX: 0, deltaZ: 0, deltaMode: 0,
        clientX: gesture.x, clientY: gesture.y, bubbles: true, cancelable: true,
      }));
    }
    function start(event) {
      if (claimed && event.touches.length > 1) { cancelMovement(); suppressNative(event); return; }
      reset();
      const touch = point(event);
      if (!touch || composing || selected() || event.defaultPrevented ||
        event.target.closest?.("a, button, input, textarea, select, [contenteditable=true]")) return;
      gesture = { id: touch.identifier, startX: touch.clientX, startY: touch.clientY,
        x: touch.clientX, y: touch.clientY, time: event.timeStamp, scrolling: false };
    }
    function move(event) {
      if (!gesture) { suppressNative(event); return; }
      const touch = point(event);
      if (!touch || touch.identifier !== gesture.id || composing || selected() ||
        event.defaultPrevented || !event.cancelable) { cancelMovement(); suppressNative(event); return; }
      const dx = Math.abs(touch.clientX - gesture.startX), dy = Math.abs(touch.clientY - gesture.startY);
      const amount = gesture.y - touch.clientY;
      gesture.x = touch.clientX; gesture.y = touch.clientY;
      if (!gesture.scrolling) {
        // A held finger belongs to selection/context menus, not delayed TUI input.
        if (event.timeStamp - gesture.time >= HOLD_MS) { reset(); return; }
        if (dx < INTENT_PX && dy < INTENT_PX) return;
        if (dy < INTENT_PX || dy <= dx * 1.25) { reset(); return; }
        gesture.scrolling = true; claimed = true;
      }
      // Capture precedes xterm's native touchmove handler. Only claimed moves are
      // intercepted; pending moves retain native behavior and are not replayed.
      event.preventDefault(); event.stopImmediatePropagation();
      const maxDelta = Math.max(24, Math.min(120, element.clientHeight / (term.rows || 24) * 3));
      delta = Math.max(-maxDelta, Math.min(maxDelta, delta + amount));
      if (animation === null) animation = runtime.requestAnimationFrame(flush);
    }
    function end(event) {
      if (event.touches.length) { cancelMovement(); suppressNative(event); return; }
      // Flush actual movement once on release; never continue with inertia.
      cancelAnimation(); flush(); reset();
    }
    const compositionStart = () => { composing = true; cancelMovement(); };
    const compositionEnd = () => { composing = false; };
    const listeners = [
      ["touchstart", start, true], ["touchmove", move, false], ["touchend", end, true],
      ["touchcancel", reset, true], ["contextmenu", cancelMovement, true],
      ["compositionstart", compositionStart, true], ["compositionend", compositionEnd, true],
    ];
    for (const [name, fn, passive] of listeners) element.addEventListener(name, fn, { capture: true, passive });
    runtime.addEventListener("pagehide", reset);
    return () => {
      reset();
      for (const [name, fn] of listeners) element.removeEventListener(name, fn, true);
      try { runtime.removeEventListener("pagehide", reset); } catch (_) {}
    };
  }
  PR.terminalTouch = {
    // Only the settings/session owner may register an iframe; never scan the page.
    register(frame) {
      if (!allowed(frame.getAttribute("src"))) return () => {};
      if (frames.has(frame)) return frames.get(frame).dispose;
      const entry = { timer: null, attempts: 0, release: null, disposed: false };
      function attempt() {
        entry.timer = null;
        if (entry.disposed) return;
        try {
          if (allowed(frame.getAttribute("src"))) {
            const runtime = frame.contentWindow;
            if (runtime && allowed(runtime.location.href) && runtime.term) entry.release = attach(runtime, runtime.term);
          }
        } catch (_) { /* Inaccessible/custom ttyd frames keep their native behavior. */ }
        if (!entry.release && ++entry.attempts < MAX_ATTEMPTS) entry.timer = setTimeout(attempt, RETRY_MS);
      }
      function releaseRuntime() {
        const release = entry.release; entry.release = null;
        // Teardown of an old document must never abort its owner's frame/process close.
        try { release?.(); } catch (_) {}
      }
      function loaded() {
        clearTimeout(entry.timer); releaseRuntime(); entry.attempts = 0;
        attempt();
      }
      entry.dispose = () => {
        if (entry.disposed) return;
        entry.disposed = true; clearTimeout(entry.timer);
        frame.removeEventListener("load", loaded); frames.delete(frame); releaseRuntime();
      };
      frames.set(frame, entry); frame.addEventListener("load", loaded); attempt();
      return entry.dispose;
    },
  };
})(window.PR);
