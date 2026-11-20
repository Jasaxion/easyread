/* Keep workspace Markdown on the shared renderer; only local file navigation differs. */
(function (PR, W) {
  "use strict";
  W.fileLinks = {
    options(source = "") {
      return { xref: false, cite: false, link: ({ href, label }) => {
        try {
          const url = new URL(href, location.origin);
          const documentLink = href.startsWith("/") && url.origin === location.origin && /^\/(read|p)\/[a-zA-Z0-9_-]+(?:\/[^\s<>]*)?$/.test(url.pathname);
          if (!documentLink && !W.fileLinks.resolve(href, source)) return null;
          return '<a href="' + PR.esc(href) + '">' + PR.esc(label) + '</a>';
        } catch (_) { return null; }
      } };
    },
    resolve(href, source = "") {
      if (!href || href.startsWith("#") || /^\/(read|p)\//.test(href)) return null;
      if (/^(https?:|mailto:)/i.test(href)) return null;
      const invalid = () => { throw new Error(PR.t("此链接不属于当前项目，或文件路径无效。")); };
      if (/^[a-z][a-z\d+.-]*:/i.test(href) || href.startsWith("//")) return invalid();
      let path;
      try { path = decodeURIComponent(href.split(/[?#]/)[0]); } catch (_) { return invalid(); }
      if (/[\\\x00-\x1f]/.test(path)) return invalid();
      if (path.startsWith("/")) {
        const root = (W.workspaceRoot || "").replace(/\\/g, "/").replace(/\/$/, "");
        if (!root || !path.startsWith(root + "/")) return invalid();
        path = path.slice(root.length + 1);
        if (path.split("/").includes("..")) return invalid();
      } else {
        const parts = source ? source.split("/").slice(0, -1) : [];
        for (const part of path.split("/")) {
          if (part === ".") continue;
          if (part === "..") { if (!parts.length) return invalid(); parts.pop(); }
          else parts.push(part);
        }
        path = parts.join("/");
      }
      path = W.safePath(path);
      const entry = W.files.entries.find(item => item.path === path);
      if (entry && entry.kind !== "file") return invalid();
      return { path, line: /#L?(\d+)(?:[-:]|$)/.exec(href)?.[1] };
    },
    init() {
      for (const id of ["ws-messages", "ws-file-preview"]) {
        W.$(id).addEventListener("click", event => {
          const link = event.target.closest(".ws-markdown a[href]");
          if (!link || link.hasAttribute("download")) return;
          let target;
          try { target = W.fileLinks.resolve(link.getAttribute("href"), id === "ws-file-preview" ? W.files.active?.path : ""); }
          catch (error) { event.preventDefault(); event.stopPropagation(); W.error(error); return; }
          if (!target) return;
          event.preventDefault(); event.stopPropagation();
          W.run(async () => {
            if (W.view === "latex" && !target.path.startsWith("latex-workspace/") && !await W.switchView("project")) return;
            // /file validates missing entries, symlinks and paths outside the workspace.
            await W.files.open(target.path, target.line);
          })();
        });
      }
    }
  };
})(window.PR, window.PR.workspaces);
