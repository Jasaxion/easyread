/* Research projects are separate from the reader's limited-permission chat. */
(function (PR) {
  "use strict";
  const W = PR.workspaces = {};
  W.$ = (id) => document.getElementById(id);
  W.base = "/api/workspaces";
  W.project = null;
  W.navigate = url => PR.openWorkspaceTab ? PR.openWorkspaceTab(url) : (location.href = url);
  W.capabilities = {};
  W.view = "project";
  W.path = (suffix = "") => W.base + "/" + encodeURIComponent(W.project.id) + suffix;
  W.key = (name) => "easyread-workspaces:" + W.libraryId + ":" + W.project.id + ":" + name;
  W.raw = (path, version) => W.path("/raw?") + new URLSearchParams({ path, ...(version ? { v: version } : {}) });
  // PR.api does not preserve HTTP status. Keep the same session/token policy here,
  // but expose 409 so an optimistic file write can never become a blind retry.
  W.request = async (url, body, raw = false) => {
    const headers = body === undefined ? {} : { "X-Token": PR.token || "" };
    if (body !== undefined && !raw) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { method: body === undefined ? "GET" : "POST", headers,
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body), cache: "no-store", credentials: "same-origin" });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && data.login_required) location.assign("/login?next=" + encodeURIComponent(location.pathname + location.search));
    if (!response.ok) {
      const error = new Error(data.error || PR.t("请求失败 {status}", { status: response.status }));
      error.status = response.status; error.code = data.code; throw error;
    }
    return data;
  };
  W.api = (suffix, body, raw) => W.request(W.path(suffix), body, raw);
  W.error = (error) => {
    const box = W.$("ws-alert");
    box.hidden = !error;
    box.textContent = error ? error.message || String(error) : "";
  };
  W.run = (fn) => async (...args) => {
    try { return await fn(...args); } catch (error) { W.error(error); return false; }
  };
  W.button = (text, fn, icon, cls = "") => {
    const button = PR.el("button", { type: "button", class: "btn sm " + cls, title: text });
    if (icon) button.innerHTML = PR.icon(icon);
    button.append(document.createTextNode(text));
    button.onclick = W.run(fn); return button;
  };
  W.iconButton = (text, fn, icon) => {
    const button = W.button(text, fn, icon, "ws-icon-button");
    button.setAttribute("aria-label", text); return button;
  };
  W.setIcon = (id, icon, text) => {
    const button = W.$(id), label = text || button.title || button.textContent.trim();
    button.title = label; button.setAttribute("aria-label", label);
    button.innerHTML = PR.icon(icon); button.classList.add("ws-icon-button");
  };
  W.empty = (text) => PR.el("div", { class: "ws-empty", text });
  W.modal = (title) => {
    const box = PR.el("dialog", { class: "ws-dialog", "aria-label": title });
    const head = PR.el("header", { class: "ws-row" });
    head.append(PR.el("h2", { text: title }), W.button(PR.t("关闭"), () => box.close(), "x"));
    box.append(head); document.body.append(box);
    box.addEventListener("close", () => box.remove(), { once: true });
    return box;
  };
  W.field = (label, input, root) => {
    const el = PR.el("label", { class: "ws-field" }); el.append(PR.el("span", { text: label }), input); root.append(el); return input;
  };
  W.input = (value = "", attrs = {}) => {
    const input = PR.el("input", { class: "input", ...attrs }); input.value = value; return input;
  };
  W.store = (name, value) => {
    if (!PR.ls.set(W.key(name), value)) {
      W.error(new Error(PR.t("浏览器无法保存草稿；请先保存文件再离开此页。"))); return false;
    }
    return true;
  };
  W.safePath = (value, scope = "") => {
    const path = String(value || "").trim();
    if (!path || path.startsWith("/") || /[\\\x00-\x1f]/.test(path) || path.split("/").some(p => !p || p === "." || p === "..")) throw new Error(PR.t("请输入工作区内的相对路径，不能包含 .. 或空目录。"));
    if (scope && !path.startsWith(scope + "/")) throw new Error(PR.t("LaTeX 文件必须位于 latex-workspace 内。"));
    return path;
  };
  W.splitter = (handle, container, property, fallback, min = 15, max = 80) => {
    const key = "split:" + property;
    let value = Number(PR.ls.get(W.key(key), fallback));
    const set = (next) => {
      value = Math.max(min, Math.min(max, next));
      container.style.setProperty(property, value + "%"); handle.setAttribute("aria-valuenow", Math.round(value));
    };
    set(value);
    handle.setAttribute("role", "separator"); handle.tabIndex = 0;
    handle.setAttribute("aria-orientation", "vertical"); handle.setAttribute("aria-valuemin", min); handle.setAttribute("aria-valuemax", max);
    handle.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); set(event.key === "Home" ? min : event.key === "End" ? max : value + (event.key === "ArrowLeft" ? -2 : 2));
      W.store(key, value);
    };
    handle.onpointerdown = (event) => {
      if (event.button !== 0) return;
      event.preventDefault(); handle.setPointerCapture(event.pointerId);
      const start = event.clientX, before = value, width = container.getBoundingClientRect().width;
      document.body.classList.add("ws-resizing");
      handle.onpointermove = (e) => set(before + (e.clientX - start) / width * 100);
      const stop = () => { handle.onpointermove = null; document.body.classList.remove("ws-resizing"); W.store(key, value); };
      handle.onpointerup = stop; handle.onpointercancel = stop; handle.onlostpointercapture = stop;
    };
  };
})(window.PR);
