/* Workspace labels; capability support remains enforced by the session adapter. */
(function (PR, W) {
  "use strict";
  W.engineName = engine => ({ codex: "Codex", claude: "Claude Code", pi: "Pi Agent" }[engine] || engine);
})(window.PR, window.PR.workspaces);
