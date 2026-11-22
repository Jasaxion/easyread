/* ttyd 1.7.4 Xterm.open exposes window.term. Change only its public xterm options:
   https://github.com/tsl0922/ttyd/blob/1.7.4/html/src/components/terminal/xterm/index.ts */
(function (PR) {
  "use strict";
  const KEY = "easyread-terminal-theme", RETRY_MS = 100, MAX_ATTEMPTS = 100;
  const palettes = {
    "modern-light": {
      foreground: "#100f0f", background: "#fffcf0", cursor: "#100f0f", cursorAccent: "#fffcf0",
      selectionBackground: "#e6e4d9", selectionForeground: "#100f0f",
      black: "#100f0f", red: "#af3029", green: "#66800b", yellow: "#ad8301",
      blue: "#205ea6", magenta: "#a02f6f", cyan: "#24837b", white: "#6f6e69",
      brightBlack: "#b7b5ac", brightRed: "#d14d41", brightGreen: "#879a39", brightYellow: "#d0a215",
      brightBlue: "#4385be", brightMagenta: "#ce5d97", brightCyan: "#3aa99f", brightWhite: "#cecdc3",
    },
    dark: {
      foreground: "#cecdc3", background: "#100f0f", cursor: "#cecdc3", cursorAccent: "#100f0f",
      selectionBackground: "#403e3c", selectionForeground: "#fffcf0",
      black: "#100f0f", red: "#d14d41", green: "#879a39", yellow: "#d0a215",
      blue: "#4385be", magenta: "#ce5d97", cyan: "#3aa99f", white: "#cecdc3",
      brightBlack: "#6f6e69", brightRed: "#e8705f", brightGreen: "#a0af54", brightYellow: "#dfb431",
      brightBlue: "#66a0c8", brightMagenta: "#df81a2", brightCyan: "#5abdac", brightWhite: "#fffcf0",
    },
  };
  const valid = value => value === "auto" || Object.hasOwn(palettes, value);
  const normalize = value => valid(value) ? value : "auto";
  const frames = new Map(), controls = new Map();
  let selection = "auto", observer = null, unsaved = false;
  function readSelection() {
    if (unsaved) return selection;
    try { return normalize(localStorage.getItem(KEY)); } catch (_) { return selection; }
  }
  selection = readSelection();
  const effective = () => selection === "auto" ? (document.documentElement.dataset.theme === "dark" ? "dark" : "modern-light") : selection;

  function allowed(value) {
    try {
      const url = new URL(value, location.href);
      return url.origin === location.origin && ["http:", "https:"].includes(url.protocol) &&
        !url.username && !url.password && !url.search && !url.hash &&
        /^\/(?:terminal|workspace-terminal)\/[a-zA-Z0-9_-]+\/$/.test(url.pathname);
    } catch (_) { return false; }
  }
  function apply(entry) {
    const frame = entry.frame;
    // Validate both the requested URL and the loaded document (including redirects).
    if (!allowed(frame.getAttribute("src"))) return false;
    try {
      const runtime = frame.contentWindow;
      if (!runtime || !allowed(runtime.location.href)) return false;
      const term = runtime.term, name = effective();
      if (!term?.options) return false;
      if (entry.term !== term || entry.name !== name) {
        term.options.theme = { ...palettes[name] };
        entry.term = term; entry.name = name;
      }
      return true;
    } catch (_) { return false; } // Older/custom ttyd and inaccessible frames stay usable.
  }
  function refresh() {
    for (const select of controls.keys()) select.value = selection;
    for (const entry of frames.values()) apply(entry);
  }
  function storageChanged(event) {
    if (event.key !== KEY && event.key !== null) return;
    try { if (event.storageArea && event.storageArea !== localStorage) return; } catch (_) { return; }
    selection = readSelection(); refresh();
  }
  function watch() {
    if (observer) return;
    selection = readSelection();
    observer = new MutationObserver(() => { if (selection === "auto") refresh(); });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    window.addEventListener("storage", storageChanged);
  }
  function unwatch() {
    if (frames.size || controls.size) return;
    observer?.disconnect(); observer = null;
    window.removeEventListener("storage", storageChanged);
  }

  const T = PR.terminalTheme = {
    getSelection: () => selection,
    setSelection(value) {
      if (!valid(value)) return;
      selection = value;
      try { localStorage.setItem(KEY, selection); unsaved = false; } catch (_) { unsaved = true; }
      refresh();
    },
    // Only terminal owners register frames; this module never discovers arbitrary iframes.
    register(frame) {
      if (!allowed(frame.getAttribute("src"))) return () => {};
      if (frames.has(frame)) return frames.get(frame).dispose;
      const entry = { frame, timer: null, attempts: 0, disposed: false };
      const attempt = () => {
        entry.timer = null;
        if (entry.disposed) return;
        if (!apply(entry) && ++entry.attempts < MAX_ATTEMPTS) entry.timer = setTimeout(attempt, RETRY_MS);
      };
      const loaded = () => {
        clearTimeout(entry.timer); entry.attempts = 0; entry.term = null;
        attempt();
      };
      entry.dispose = () => {
        if (entry.disposed) return;
        entry.disposed = true; clearTimeout(entry.timer);
        frame.removeEventListener("load", loaded);
        frames.delete(frame); entry.term = null; unwatch();
      };
      frames.set(frame, entry); watch();
      frame.addEventListener("load", loaded);
      attempt();
      return entry.dispose;
    },
    controlHtml(id) {
      return '<div class="terminal-theme-controls"><label class="terminal-theme-control" for="' + id + '"><span>' + PR.t("终端配色") + '</span>' +
        '<select class="input" id="' + id + '" aria-describedby="' + id + '-hint">' +
        [["auto", PR.t("跟随界面")], ["dark", PR.t("深色")], ["modern-light", PR.t("现代浅色")]].map(([value, text]) =>
          '<option value="' + value + '"' + (value === selection ? ' selected' : '') + '>' + PR.esc(text) + '</option>').join('') + '</select></label>' +
        '<p class="hint terminal-theme-hint" id="' + id + '-hint">' + PR.t("仅更改终端配色；Pi 等 Agent 的真彩色主题需在应用内设置。") + '</p></div>';
    },
    bindControl(select) {
      if (!select) return () => {};
      if (controls.has(select)) return controls.get(select);
      const changed = () => T.setSelection(select.value);
      const dispose = () => {
        if (controls.get(select) !== dispose) return;
        select.removeEventListener("change", changed);
        controls.delete(select); unwatch();
      };
      controls.set(select, dispose); watch(); select.value = selection;
      select.addEventListener("change", changed);
      return dispose;
    },
  };
})(window.PR);
