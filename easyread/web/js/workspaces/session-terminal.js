/* A hidden terminal is still the native session's writer. Never detach it to change views. */
(function (PR, W) {
  "use strict";
  const T = W.sessionTerminal = {}, frames = new Map(), opening = new Set();
  const owns = id => frames.has(id) || opening.has(id);
  T.owns = owns;
  T.url = value => {
    const url = new URL(value, location.href);
    if (url.origin !== location.origin || !["http:", "https:"].includes(url.protocol) ||
      url.username || url.password || url.search || url.hash || !/^\/workspace-terminal\/[a-zA-Z0-9_-]+\/$/.test(url.pathname)) {
      throw new Error(PR.t("终端地址无效"));
    }
    return url.href;
  };
  T.dispose = id => { frames.get(id)?.frame.remove(); frames.delete(id); };
  T.observe = session => {
    if (session && !session.terminal_open && !opening.has(session.id)) T.dispose(session.id);
  };
  T.reconcile = sessions => {
    const active = new Set(sessions.filter(session => session.terminal_open).map(session => session.id));
    for (const id of frames.keys()) if (!active.has(id) && !opening.has(id)) T.dispose(id);
  };
  T.toggle = async () => {
    const S = W.sessions, session = S.current, id = session?.id;
    if (!id || S.isPending(id)) return;
    const entry = frames.get(id);
    if (entry) { entry.visible = !entry.visible; S.render(); return; }
    // Reopening an existing terminal is allowed, but must never cancel GUI work.
    if (!session.terminal_open && W.sessionOwnsWork(session)) return;
    opening.add(id); S.render();
    try {
      const data = await S.mutate(id, "terminal", {}), url = T.url(data.url);
      const frame = PR.el("iframe", { src: url, title: PR.t("原生 CLI 终端"), class: "ws-terminal-frame" });
      // A late response belongs to its original session, never the newly selected pane.
      const visible = S.current?.id === id;
      frame.hidden = !visible; frames.set(id, { frame, visible }); W.$("ws-terminal-frames").append(frame);
    } finally { opening.delete(id); S.render(); }
    await S.poll();
  };
  T.stop = async () => {
    const S = W.sessions, id = S.current?.id;
    if (!id || S.isPending(id) || !(owns(id) || S.current.terminal_open)) return;
    if (!await PR.confirm({ title: PR.t("停止并关闭原生终端？"),
      body: PR.t("这会终止此终端及其中正在执行的任务，保留会话历史。仅切回图形界面不会停止终端。"),
      danger: true, ok: PR.t("停止并关闭终端") })) return;
    if (S.current?.id !== id || S.isPending(id) || !(owns(id) || S.current.terminal_open)) return;
    const data = await S.mutate(id, "cancel", {});
    if (data.session?.terminal_open) throw new Error(PR.t("终端仍在运行，请核对状态后重试停止。"));
    T.dispose(id); S.render();
  };
  T.init = () => {
    W.$("ws-terminal").onclick = W.run(T.toggle);
    W.$("ws-terminal-stop").onclick = W.run(T.stop);
  };
  T.render = () => {
    const S = W.sessions, session = S.current, id = session?.id;
    const entry = frames.get(id), visible = !!entry?.visible, active = owns(id) || !!session?.terminal_open;
    const pending = S.isPending(id) || opening.has(id);
    for (const [sid, item] of frames) item.frame.hidden = sid !== id || !visible;
    W.$("ws-terminal-frames").hidden = !visible;
    W.$("ws-session-gui").hidden = visible;
    const button = W.$("ws-terminal"), available = W.capabilities.terminal?.available;
    W.setIcon("ws-terminal", visible ? "back" : "log", visible ? PR.t("返回图形界面") : PR.t("显示原生终端"));
    button.setAttribute("aria-pressed", String(visible));
    button.disabled = !id || pending || (!entry && (!available || (!active && W.sessionOwnsWork(session))));
    const note = W.$("ws-terminal-note");
    note.textContent = active && !visible ? PR.t("终端仍占用此会话。可查看同步记录；发送或启动目标前，请先停止并关闭终端。") :
      !available ? W.capabilities.terminal?.reason || PR.t("原生终端需要服务器安装 ttyd；聊天仍可使用完整 Agent 工具。") : "";
    note.hidden = !note.textContent;
    W.$("ws-terminal-stop").hidden = !active;
    W.$("ws-terminal-stop").disabled = pending;
  };
})(window.PR, window.PR.workspaces);
