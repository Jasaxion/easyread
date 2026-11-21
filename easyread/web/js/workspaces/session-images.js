/* Image drafts stay in memory, keyed by project + conversation, never localStorage. */
(function (PR, W) {
  "use strict";
  const drafts = new Map(), st = {};
  let currentKey = null, draft;
  const key = id => W.key("message:" + (id || "new"));
  function render() {
    W.$("ws-images").innerHTML = draft.html(st.images, true);
    W.$("ws-image-status").textContent = st.imageError || (st.imageLoading ? PR.t("正在读取图片…") : "");
    if (currentKey) drafts.set(currentKey, st.images.slice());
    W.sessions.updateSend();
  }
  const I = W.sessionImages = {
    init() {
      if (draft) return;
      draft = PR.createChatImages(st, () => W.$("ws-chat-form"), render, () => W.$("ws-message").focus(), {
        inputId: "ws-message", canAdd: () => !W.sessions.isPending(W.sessions.current?.id) && !W.sessions.current?.terminal_open && !W.sessionTerminal.owns(W.sessions.current?.id)
      });
      W.$("ws-images").addEventListener("click", event => {
        const button = event.target.closest('[data-c="unimage"]');
        if (button) { st.images.splice(Number(button.dataset.i), 1); st.imageError = ""; render(); }
      });
    },
    get pending() { return !!st.imageLoading; },
    get any() { return !!st.images?.length; },
    payload() { return (st.images || []).map(image => image.data); },
    restore(id) {
      I.init();
      const next = key(id);
      if (next === currentKey) return;
      if (currentKey) drafts.set(currentKey, st.images.slice());
      draft.reset(); currentKey = next; st.images = (drafts.get(next) || []).slice(); render();
    },
    copyTo(id) { drafts.set(key(id), (st.images || []).slice()); },
    forget(id) {
      const target = key(id); drafts.delete(target);
      if (target === currentKey) { draft.reset(); currentKey = null; render(); }
    },
    sent(id, images) {
      const target = key(id), saved = target === currentKey ? st.images : drafts.get(target) || [];
      if (JSON.stringify(saved.map(image => image.data)) !== JSON.stringify(images)) return;
      drafts.delete(target);
      if (target === currentKey) { draft.reset(); render(); }
    },
    history(images) {
      return (images || []).filter(name => /^chat-media\/[0-9a-f]{32}\.png$/.test(name)).map((name, index) =>
        '<div class="ch-image"><img src="' + PR.esc(W.raw(name)) + '" alt="' + PR.t("提问附图 {n}", { n: index + 1 }) + '"></div>').join("");
    }
  };
})(window.PR, window.PR.workspaces);
