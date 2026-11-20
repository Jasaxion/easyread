(() => {
  "use strict";
  const PR = window.PR = window.PR || {};
  const initial = document.documentElement.lang;
  let lang = ["en", "zh-Hant"].includes(initial) ? initial : "zh";
  const dictionaries = { zh: {} };
  try { dictionaries[lang] = JSON.parse(document.getElementById("pr-i18n")?.textContent || "{}"); } catch (_) { dictionaries[lang] = {}; }
  PR.t = text => dictionaries[lang]?.[text] || text;
  const input = document.querySelector("#accessCode"), error = document.querySelector("#accessError"), submit = document.querySelector("#accessSubmit"), picker = document.querySelector("#accessLang");
  function renderLanguage() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : lang;
    document.querySelectorAll("[data-zh]").forEach(el => { el.textContent = PR.t(el.dataset.zh); });
    picker.value = lang;
    document.querySelector('#accessLang option[value="zh"]').textContent = PR.t("简体中文");
    document.querySelector('#accessLang option[value="zh-Hant"]').textContent = PR.t("繁体中文");
    picker.setAttribute("aria-label", PR.t("界面语言"));
    input.placeholder = PR.t("输入访问授权码");
    document.querySelector("#toggleCode").setAttribute("aria-label", PR.t("显示或隐藏授权码"));
    document.title = PR.t("EasyRead · 访问授权");
  }
  let languageRequest = 0;
  picker.onchange = async () => {
    const next = picker.value, request = ++languageRequest;
    if (!["zh", "zh-Hant", "en"].includes(next)) return;
    error.textContent = "";
    try {
      if (!dictionaries[next]) {
        const response = await fetch("/web/i18n/" + next + ".json", { cache: "no-store" });
        if (!response.ok) throw new Error("locale");
        dictionaries[next] = await response.json();
      }
      if (request !== languageRequest) return;
      lang = next; renderLanguage();
    } catch (_) { picker.value = lang; error.textContent = PR.t("连接失败，请重试。"); }
  };
  document.querySelector("#toggleCode").onclick = () => { input.type = input.type === "password" ? "text" : "password"; };
  document.querySelector("#accessForm").onsubmit = async e => {
    e.preventDefault(); error.textContent = ""; submit.disabled = true;
    try {
      const r = await fetch("/api/access/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code: input.value }) });
      if (!r.ok) {
        error.textContent = r.status === 429 ? PR.t("尝试过于频繁，请一分钟后重试") : PR.t("访问授权码不正确");
        input.focus(); return;
      }
      let next = new URLSearchParams(location.search).get("next") || "/";
      const url = new URL(next, location.origin);
      if (url.origin !== location.origin || url.pathname === "/login") next = "/";
      else if (url.pathname === "/web/tabs.html" && !url.searchParams.has("open") && !url.searchParams.has("detached")) {
        url.searchParams.set("home", "1");
        next = url.pathname + url.search + url.hash;
      } else if (["/workspaces", "/web/workspaces.html"].includes(url.pathname)) {
        // Research is an adjacent workspace, not the application home. In the
        // tabs shell retain the requested project in the background, but land
        // on the library after authorization. Ordinary document deep links stay.
        try {
          if (window.parent !== window && window.parent.easyreadTabs) {
            void window.parent.easyreadTabs.open("/");
            location.replace(next); return;
          }
        } catch (_) {}
        next = "/";
      }
      location.replace(next);
    } catch (_) { error.textContent = PR.t("连接失败，请重试。"); }
    finally { submit.disabled = false; }
  };
  renderLanguage();
})();
