/* 界面语言。PR.t 的源词条用简体中文；en / zh-Hant 词典只翻译界面，不转换论文或用户内容。
   后端按系统语言（或设置里选的）写好 <html lang>，并把对应词典塞进 #pr-i18n。 */
window.PR = window.PR || {};
(function (PR) {
  "use strict";

  let dict = {};
  try { dict = JSON.parse((document.getElementById("pr-i18n") || {}).textContent || "{}"); } catch (e) { dict = {}; }
  PR.lang = ["en", "zh-Hant"].includes(document.documentElement.lang) ? document.documentElement.lang : "zh";
  PR.langChoice = document.documentElement.dataset.langChoice || "auto";  // 设置里选的：auto 跟随系统
  PR.target = document.documentElement.dataset.target || "zh";  // 译文语言（论文翻成什么），名字表在 util.js

  const fill = (s, vars) => vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s;
  PR.t = (zh, vars) => fill(PR.lang !== "zh" ? dict[zh] || zh : zh, vars);

  // Built-in labels can be renamed. Only known defaults are UI copy; never translate custom names.
  const statusDefaults = {
    unread: { aliases: ["待阅读", "待閱讀", "未读", "未讀", "To read", "Unread"], label: () => PR.t("待阅读") },
    reading: { aliases: ["阅读中", "閱讀中", "在读", "在讀", "Reading"], label: () => PR.t("阅读中") },
    organizing: { aliases: ["待整理", "To organize"], label: () => PR.t("待整理") },
    done: { aliases: ["已完成", "已读", "已讀", "Completed", "Read"], label: () => PR.t("已完成") },
    paused: { aliases: ["暂停", "暫停", "Paused"], label: () => PR.t("暂停") },
  };
  PR.researchStatusLabel = function (status) {
    const entry = statusDefaults[status.id];
    return status.builtin && entry?.aliases.includes(status.label) ? entry.label() : status.label;
  };
  PR.captureStatusLabel = (status, displayed) => displayed == null || displayed === PR.researchStatusLabel(status) ? status.label : displayed;
  const viewDefaults = {
    all: { aliases: ["全部", "All"], label: () => PR.t("全部") },
    starred: { aliases: ["星标", "星標", "Starred"], label: () => PR.t("星标") },
    archived: { aliases: ["已封存", "Archived"], label: () => PR.t("已封存") },
    questions: { aliases: ["有待回答的问题", "有待回答的問題", "Open questions"], label: () => PR.t("有待回答的问题") },
    translating: { aliases: ["翻译中", "翻譯中", "Translating"], label: () => PR.t("翻译中") },
  };
  PR.libraryViewLabel = (id, label) => {
    const entry = viewDefaults[id];
    return entry?.aliases.includes(label) ? entry.label() : PR.researchStatusLabel({ id, label, builtin: true });
  };

  // CSS-generated labels share the same dictionaries as buttons and dialogs.
  for (const [name, text] of Object.entries({
    "appendix-label": PR.t("附录"), "question-label": PR.t("问　"),
    "lost-quote-label": PR.t("（未找到对应文字，请核对原文或译文）"),
  })) document.documentElement.style.setProperty("--" + name, JSON.stringify(text));

  /* Only static interface text is translated here. Dynamic UI must use PR.t at its source. */
  const CJK = /[一-鿿]/;
  const ATTRS = ["placeholder", "title", "aria-label", "alt"];
  const CONTENT = 'script, style, [contenteditable], [translate="no"], [data-i18n-skip], #paper, #list, #side, .bar-titles, .card, .cm, .ch-thread, .ch-title, .pn-rich, .pn-mindmap, .rw-paper, .rw-summary, .rw-quick-peek-title, .rp-current, .rp-deep-preview, .deep-modal-body, .mm-modal-sub, .smart-note, .smart-reason, .smart-quote, .np-ai-body, .kwm-node, me-tpc';
  const isContent = (el) => el?.closest(CONTENT);
  PR.translateDom = function (root) {
    if (PR.lang === "zh") return;
    root = root || document.body;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (isContent(n.parentElement) || n.parentElement?.closest("textarea")) continue;
      const raw = n.nodeValue, key = raw.trim();
      if (key && CJK.test(key) && dict[key]) n.nodeValue = raw.replace(key, dict[key]);
    }
    [root, ...root.querySelectorAll("*")].forEach((el) => {
      if (isContent(el)) return;
      ATTRS.forEach((a) => {
        const v = el.getAttribute(a);
        if (v && CJK.test(v) && dict[v.trim()]) el.setAttribute(a, dict[v.trim()]);
      });
    });
  };
  if (PR.lang !== "zh") {
    if (document.querySelector("title[data-i18n-title]") && dict[document.title]) document.title = dict[document.title];
    if (document.body) PR.translateDom(document.body);
    else document.addEventListener("DOMContentLoaded", () => PR.translateDom(document.body));
  }
})(window.PR);
