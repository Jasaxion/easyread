/* CSS-hidden iframes are still document-visible. Only pause UI reads, never jobs or saves. */
(function () {
  "use strict";
  const states = new WeakMap();
  window.PRTabsVisibility = {
    setActive(frame, active) {
      try {
        const win = frame?.contentWindow, doc = frame?.contentDocument;
        if (!win?.PR || !doc) return;
        let state = states.get(doc);
        if (!state) {
          state = { active: !!active }; states.set(doc, state);
          win.PR.isPageActive = () => state.active;
        } else if (state.active === !!active) return;
        state.active = !!active;
        doc.dispatchEvent(new win.CustomEvent("easyread:visibility", { detail: { active: state.active } }));
      } catch (_) { /* Cross-origin/native frames do not participate in the same-origin shell. */ }
    }
  };
})();
