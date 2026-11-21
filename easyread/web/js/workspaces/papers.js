(function (PR, W) {
  "use strict";
  const P = W.papers = {};
  let signature = "", anchor = null, openedId = null, openTimer;
  P.refresh = async () => {
    const data = await W.api("/papers?linked_only=1"), next = JSON.stringify(data.linked || []);
    if (next === signature) return; signature = next;
    if (openedId && !(data.linked || []).some(paper => paper.id === openedId && !paper.missing)) P.close();
    const box = W.$("ws-papers"); box.replaceChildren();
    W.$("ws-paper-count").textContent = (data.linked || []).length;
    (data.linked || []).forEach(paper => {
      const row = PR.el("div", { class: "ws-paper-row" });
      const button = W.button(PR.titles(paper).main, event => {
        clearTimeout(openTimer);
        const point = { currentTarget: event.currentTarget, detail: event.detail, clientX: event.clientX, clientY: event.clientY };
        // Let a double-click reach the row even if a narrow-screen popover overlaps it.
        if (event.detail) openTimer = setTimeout(() => P.open(paper, point), 240); else P.open(paper, point);
      }, "book");
      if (paper.id === openedId) anchor = button;
      const label = PR.el("span", { class: "ws-paper-label", text: PR.titles(paper).main });
      button.lastChild.replaceWith(label);
      button.classList.add("ws-paper-title"); button.title = PR.titles(paper).main + "\n" + PR.t("单击查看论文详情，双击阅读"); button.setAttribute("aria-haspopup", "dialog");
      button.ondblclick = event => { event.preventDefault(); P.read(paper); };
      const remove = W.button(PR.t("解除关联"), async () => {
        if (!await PR.confirm({ title: PR.t("解除论文关联？"), body: PR.t("只移除此项目的引用，不会删除文献库中的论文。") })) return;
        await W.api("/papers/unlink", { document_id: paper.id }); await P.refresh();
      }, "x"); remove.classList.add("ws-paper-unlink"); row.append(button, remove); box.append(row);
    });
    if (!box.children.length) box.append(W.empty(PR.t("关联文献库中的论文，不复制原文。")));
  };
  P.read = paper => {
    if (paper.missing) return;
    P.close(); const url = "/read/" + encodeURIComponent(paper.id);
    if (PR.openPaperTab) PR.openPaperTab(url); else window.open(url, "_blank", "noopener");
  };
  P.open = (paper, event) => {
    const panel = W.$("ws-paper-popover"); anchor = event.currentTarget; openedId = paper.id;
    const title = PR.titles(paper), head = PR.el("header", { class: "ws-row" });
    head.append(PR.el("strong", { text: PR.t("论文详情") }), W.iconButton(PR.t("关闭"), () => P.close(true), "x"));
    panel.replaceChildren(head, PR.el("h3", { text: title.main }));
    if (title.sub) panel.append(PR.el("p", { class: "hint", text: title.sub }));
    const meta = PR.el("dl");
    [[PR.t("作者"), paper.authors], [PR.t("年份"), paper.year], [PR.t("出处"), paper.venue || paper.arxiv], ["DOI", paper.doi]].forEach(([label, value]) => {
      if (value) meta.append(PR.el("dt", { text: label }), PR.el("dd", { text: String(value) }));
    });
    panel.append(meta);
    if (paper.missing) panel.append(PR.el("p", { class: "hint", text: PR.t("论文已不在文献库中。") }));
    const actions = PR.el("div", { class: "ws-row ws-wrap" });
    const read = W.button(PR.t("开始阅读"), () => P.read(paper), "book", "accent"); read.disabled = !!paper.missing;
    const copy = W.button(PR.t("复制引用"), event => PR.menu(event.currentTarget, [
      [PR.t("GB/T 7714 · 中文论文、学位论文"), "gb"], [PR.t("APA · 英文论文常用"), "apa"], ["BibTeX · LaTeX / Overleaf", "bibtex"]
    ].map(([label, style]) => ({ label, icon: "copy", fn: W.run(async () => { await PR.copyText(PR.cite(paper, style)); PR.toast(PR.t("已复制引用")); }) }))), "copy", "line");
    copy.disabled = !!paper.missing; actions.append(read, copy); panel.append(actions); panel.hidden = false;
    const rect = anchor.getBoundingClientRect(), width = panel.offsetWidth, height = panel.offsetHeight;
    const x = event.detail ? event.clientX : rect.left, y = event.detail ? event.clientY : rect.bottom;
    panel.style.left = Math.max(8, Math.min(x + 8, innerWidth - width - 8)) + "px";
    panel.style.top = Math.max(8, Math.min(y + 8, innerHeight - height - 8)) + "px";
    if (!event.detail) read.focus();
  };
  P.close = (restore = false) => {
    clearTimeout(openTimer); W.$("ws-paper-popover").hidden = true;
    if (restore && anchor?.isConnected) anchor.focus(); anchor = null; openedId = null;
  };
  P.init = () => {
    document.addEventListener("pointerdown", event => {
      if (!W.$("ws-paper-popover").contains(event.target) && !anchor?.contains(event.target) && !event.target.closest(".menu")) P.close();
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !event.defaultPrevented && !W.$("ws-paper-popover").hidden) { event.preventDefault(); P.close(true); }
    });
    window.addEventListener("resize", () => P.close());
    window.addEventListener("blur", () => P.close());
    document.addEventListener("scroll", event => { if (anchor && !W.$("ws-paper-popover").contains(event.target)) P.close(); }, true);
    W.$("ws-paper-add").onclick = W.run(async () => {
      const dialog = W.modal(PR.t("关联论文"));
      const search = W.input("", { type: "search", placeholder: PR.t("搜索论文"), "aria-label": PR.t("搜索论文") });
      const list = PR.el("div", { class: "ws-paper-picker" }), error = PR.el("p", { class: "err", role: "alert" });
      dialog.append(search, error, list);
      let seq = 0;
      const load = async () => {
        const request = ++seq;
        try {
          const data = await W.api("/papers?" + new URLSearchParams({ q: search.value }));
          if (request !== seq || !dialog.isConnected) return;
          const linked = new Set((data.linked || []).map(paper => paper.id)); list.replaceChildren();
          (data.available || []).forEach(paper => {
            const row = PR.el("div", { class: "ws-row" }); row.append(PR.el("span", { text: PR.titles(paper).main }));
            const add = W.button(linked.has(paper.id) ? PR.t("已关联") : PR.t("关联"), async () => {
              add.disabled = true;
              try { await W.api("/papers/link", { document_id: paper.id }); await P.refresh(); await load(); }
              catch (e) { error.textContent = e.message; add.disabled = false; }
            }, "link", "line"); add.disabled = linked.has(paper.id); row.append(add); list.append(row);
          });
          if (!list.children.length) list.append(W.empty(PR.t("没有匹配的论文")));
        } catch (e) { error.textContent = e.message; }
      };
      search.oninput = PR.debounce(load, 250); dialog.showModal(); search.focus(); await load();
    });
  };
})(window.PR, window.PR.workspaces);
