/* ttyd 1.7.4 Xterm.open exposes window.term. Change only its public xterm options:
   https://github.com/tsl0922/ttyd/blob/1.7.4/html/src/components/terminal/xterm/index.ts */
(function (PR) {
  "use strict";
  const RETRY_MS = 100, MAX_ATTEMPTS = 100;
  const palettes = {
    light: {
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
  const frames = new Map();
  let observer = null;
  const effective = () => document.documentElement.dataset.theme === "dark" ? "dark" : "light";

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
    for (const entry of frames.values()) apply(entry);
  }
  function watch() {
    if (observer || typeof MutationObserver !== "function") return;
    observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }
  function unwatch() {
    if (frames.size) return;
    observer?.disconnect(); observer = null;
  }

  PR.terminalTheme = {
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
  };
})(window.PR);
