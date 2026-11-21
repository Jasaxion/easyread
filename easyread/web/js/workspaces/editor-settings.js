/* Ordinary-file preferences are project/device-local; LaTeX keeps its existing settings. */
(function (PR, W) {
  "use strict";
  const E = W.editorSettings = {};
  E.fontSize = () => Math.max(10, Math.min(22, Number(PR.ls.get(W.key("editor-font-size"), 13)) || 13));
  E.close = (restore = false) => {
    W.$("ws-editor-settings-popover").hidden = true;
    W.$("ws-editor-settings").setAttribute("aria-expanded", "false");
    if (restore) W.$("ws-editor-settings").focus();
  };
  E.init = () => {
    const panel = W.$("ws-editor-settings-popover"), button = W.$("ws-editor-settings");
    const font = W.$("ws-editor-font-size"), value = W.$("ws-editor-font-value");
    const sync = () => { font.value = E.fontSize(); value.textContent = font.value + "px"; };
    sync();
    font.oninput = () => {
      const size = Math.max(10, Math.min(22, Math.round(Number(font.value) || 13)));
      font.value = size; value.textContent = size + "px";
      W.store("editor-font-size", size);
      if (W.view !== "latex") W.files.editor?.setFontSize(size);
    };
    button.onclick = () => {
      if (!panel.hidden) { E.close(); return; }
      if (W.view === "latex" || !W.files.active || W.files.active.binary) return;
      sync(); panel.hidden = false; button.setAttribute("aria-expanded", "true");
      const rect = button.getBoundingClientRect();
      panel.style.left = Math.max(8, Math.min(rect.left, innerWidth - panel.offsetWidth - 8)) + "px";
      panel.style.top = Math.max(8, Math.min(rect.bottom + 6, innerHeight - panel.offsetHeight - 8)) + "px";
    };
    document.addEventListener("pointerdown", event => { if (!panel.contains(event.target) && !button.contains(event.target)) E.close(); });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !event.defaultPrevented && !panel.hidden) { event.preventDefault(); E.close(true); }
    });
    window.addEventListener("resize", () => E.close());
    window.addEventListener("blur", () => E.close());
  };
})(window.PR, window.PR.workspaces);
