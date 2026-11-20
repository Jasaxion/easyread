/* File references are per-session draft metadata, never client-side file contents. */
(function (PR, W) {
  "use strict";
  const M = W.mentions = { files: [] };
  let items = [], selected = 0, range = null, seq = 0, cached = [], fetched = 0;
  M.restore = draft => {
    M.files = [...new Set((Array.isArray(draft?.files) ? draft.files : []).filter(path => {
      try { return W.safePath(path) === path; } catch (_) { return false; }
    }))];
    M.close(); M.renderChips();
  };
  M.close = () => {
    ++seq; range = null; items = []; W.$("ws-mentions").hidden = true;
    W.$("ws-message").setAttribute("aria-expanded", "false");
    W.$("ws-message").removeAttribute("aria-activedescendant");
  };
  M.renderChips = () => {
    const box = W.$("ws-file-chips"); box.replaceChildren(); box.hidden = !M.files.length;
    M.files.forEach(path => {
      const chip = PR.el("span", { class: "ws-file-chip", title: path });
      const name = PR.el("span", { text: path });
      const remove = W.iconButton(PR.t("移除引用：{path}", { path }), () => {
        M.files = M.files.filter(item => item !== path); M.renderChips(); W.sessions.persist();
      }, "x");
      chip.append(name, remove); box.append(chip);
    });
    W.sessions.updateSend?.();
  };
  function choose(path) {
    if (!range) return;
    const input = W.$("ws-message");
    if (input.selectionStart !== range.to || input.selectionEnd !== range.to) { M.close(); return; }
    input.setRangeText("", range.from, range.to, "end");
    if (!M.files.includes(path)) M.files.push(path);
    M.close(); M.renderChips(); W.sessions.persist(); input.focus();
  }
  function render() {
    const box = W.$("ws-mentions"); box.replaceChildren(); box.hidden = false;
    W.$("ws-message").setAttribute("aria-expanded", "true");
    if (!items.length) { box.append(PR.el("div", { class: "ws-mention-empty", text: PR.t("没有匹配的文件") })); return; }
    items.forEach((path, index) => {
      const button = PR.el("button", { type: "button", role: "option", id: "ws-mention-" + index, "aria-selected": index === selected, title: path, text: path });
      button.tabIndex = -1;
      button.onmousedown = event => event.preventDefault();
      button.onclick = () => choose(path); box.append(button);
    });
    W.$("ws-message").setAttribute("aria-activedescendant", "ws-mention-" + selected);
    box.children[selected]?.scrollIntoView({ block: "nearest" });
  }
  M.update = async () => {
    const input = W.$("ws-message"), before = input.value.slice(0, input.selectionStart);
    const match = /(?:^|\s)@([^\s@]*)$/.exec(before);
    if (!match || input.selectionStart !== input.selectionEnd) { M.close(); return; }
    const request = ++seq;
    range = { from: input.selectionStart - match[1].length - 1, to: input.selectionStart };
    if (Date.now() - fetched > 3000) {
      const tree = await W.api("/tree");
      if (request !== seq) return;
      cached = (tree.entries || []).filter(entry => entry.kind === "file" && !/^(\.git|\.beads)(\/|$)/.test(entry.path) && !/\.(aux|log|fls|fdb_latexmk|synctex\.gz)$/i.test(entry.path)).map(entry => entry.path);
      fetched = Date.now();
    }
    if (request !== seq) return;
    items = cached.filter(path => !M.files.includes(path) && path.toLowerCase().includes(match[1].toLowerCase())).sort((a, b) => a.localeCompare(b)).slice(0, 12);
    selected = 0; render();
  };
  M.keydown = event => {
    if (!range || W.$("ws-mentions").hidden) return false;
    if (event.key === "Escape") { event.preventDefault(); M.close(); return true; }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); selected = (selected + (event.key === "ArrowDown" ? 1 : -1) + items.length) % (items.length || 1); render(); return true;
    }
    if (["Enter", "Tab"].includes(event.key) && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
      event.preventDefault(); if (items[selected]) choose(items[selected]); return true;
    }
    return false;
  };
  M.init = () => {
    W.$("ws-message").addEventListener("click", W.run(M.update));
    W.$("ws-message").addEventListener("keyup", event => {
      if (!event.isComposing && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) W.run(M.update)();
    });
    W.$("ws-message").addEventListener("blur", () => setTimeout(() => { if (!W.$("ws-mentions").contains(document.activeElement)) M.close(); }, 150));
    W.$("ws-attach-file").onclick = () => {
      const input = W.$("ws-message"), pos = input.selectionStart;
      input.setRangeText((pos && !/\s/.test(input.value[pos - 1]) ? " " : "") + "@", pos, input.selectionEnd, "end");
      input.focus(); W.sessions.persist(); W.run(M.update)();
    };
  };
})(window.PR, window.PR.workspaces);
