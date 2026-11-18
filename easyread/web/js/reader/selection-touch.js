/* Native selection handles do not reliably emit mouseup on mobile browsers. */
(function (PR) {
  "use strict";
  const bar = document.getElementById("selbar");
  let holding = false, quietUntil = 0, releaseTimer;
  const refresh = PR.debounce(() => PR.showSelectionBar(), 120);
  const coarse = matchMedia("(pointer: coarse)");
  bar.classList.toggle("touch-selection", coarse.matches);
  PR.selectionToolbarHeld = () => holding || Date.now() < quietUntil || bar.contains(document.activeElement);
  PR.selectionToolbarDidAct = () => {
    quietUntil = Date.now() + 180;
    refresh.cancel();
    bar.classList.remove("open");
    if (bar.contains(document.activeElement)) document.activeElement.blur();
  };
  PR.selectionPointerDown = e => {
    if (e.pointerType) bar.classList.toggle("touch-selection", e.pointerType !== "mouse");
    if (!e.target.closest?.("#selbar")) { quietUntil = 0; holding = false; clearTimeout(releaseTimer); }
  };
  document.addEventListener("pointerdown", PR.selectionPointerDown, true);
  document.addEventListener("selectionchange", refresh);
  bar.addEventListener("pointerdown", e => {
    if (!e.target.closest("button")) return;
    holding = true; clearTimeout(releaseTimer); refresh.cancel();
    // Keep the semantic quote through touch focus/collapse until click consumes it.
    if (e.cancelable) e.preventDefault();
  });
  const release = () => {
    clearTimeout(releaseTimer);
    releaseTimer = setTimeout(() => { holding = false; refresh(); }, 160);
  };
  window.addEventListener("pointerup", release);
  window.addEventListener("pointercancel", release);
  bar.addEventListener("click", () => { holding = false; clearTimeout(releaseTimer); });
  bar.addEventListener("focusout", refresh);
  function reset() {
    holding = false; clearTimeout(releaseTimer); refresh.cancel(); bar.classList.remove("open");
  }
  window.addEventListener("blur", reset);
  document.addEventListener("visibilitychange", () => { if (document.hidden) reset(); });
  function viewport() {
    const v = window.visualViewport;
    const bottom = v ? Math.max(0, innerHeight - v.height - v.offsetTop) : 0;
    bar.style.setProperty("--selection-keyboard", bottom + "px");
  }
  window.visualViewport?.addEventListener("resize", viewport);
  window.visualViewport?.addEventListener("scroll", viewport);
  window.addEventListener("resize", viewport);
  viewport();
})(window.PR);
