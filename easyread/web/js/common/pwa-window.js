/* Public window metadata only; safe on login, never loads private offline data. */
(function () {
  "use strict";
  const root = document.documentElement;
  const mode = matchMedia("(display-mode: standalone)"), dark = matchMedia("(prefers-color-scheme: dark)");
  function sync() {
    root.classList.toggle("pwa-standalone", mode.matches || navigator.standalone === true);
    const theme = root.dataset.theme || (dark.matches ? "dark" : "light");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === "dark" ? "#202627" : "#2d6173";
  }
  mode.addEventListener("change", sync); dark.addEventListener("change", sync);
  new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  sync();
})();
