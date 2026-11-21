/* Clipboard attachments belong to the current conversation draft. */
(function (PR) {
  "use strict";
  PR.createChatImages = function (st, panel, render, focusInput, options = {}) {
  let generation = 0;
  const canAdd = options.canAdd || (() => PR.canChat());
  const inputId = options.inputId || "chatInput";
  Object.assign(st, { images: [], imageError: "", imageLoading: false });
  const imageUrl = (value) => /^chat-media\/[0-9a-f]{32}\.png$/.test(value) ? (options.imageUrl || PR.imageUrl)(value) : /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value) ? value : "";
  function imagesHtml(images, draft) {
    if (!images.length) return "";
    return '<div class="ch-images">' + images.map((image, index) => {
      const url = imageUrl(draft ? image.data : image);
      return url ? '<div class="ch-image"><img src="' + PR.esc(url) + '" alt="' + PR.t("提问附图 {n}", {n:index + 1}) + '">' + (draft ? '<button type="button" data-c="unimage" data-i="' + index + '" aria-label="' + PR.t("移除图片 {n}", {n:index + 1}) + '">×</button>' : '') + '</div>' : '';
    }).join('') + '</div>';
  }

  panel().addEventListener("paste", async event => {
    if (event.target.id !== inputId || !canAdd()) return;
    const clipboard = event.clipboardData;
    const candidates = Array.from(clipboard?.items || []).filter(item => item.kind === "file").map(item => item.getAsFile()).filter(Boolean);
    // Some browsers expose files only, or leave the clipboard item's MIME empty.
    const files = candidates.filter(isImage);
    for (const file of Array.from(clipboard?.files || []).filter(isImage)) {
      if (!files.some(item => item === file || (item.name === file.name && item.size === file.size && item.type === file.type))) files.push(file);
    }
    if (!files.length) return; // Plain text keeps the browser's normal paste behavior.
    if (clipboard?.getData?.("text/plain")) {
      // Let the browser insert mixed text before the reader re-renders its textarea.
      const ticket = generation;
      setTimeout(() => { if (ticket === generation) add(files); }, 0);
      return;
    }
    event.preventDefault();
    await add(files);
  });
  function isImage(file) { return file.type?.startsWith("image/") || (!file.type && /\.(png|jpe?g|webp)$/i.test(file.name || "")); }

  async function add(files) {
    if (!files.length || st.imageLoading || !canAdd()) return;
    if (st.streaming) { st.imageError = PR.t("请先停止当前回答再添加附件"); render(); return; }
    const ticket = generation;
    st.imageError = ""; st.imageLoading = true; render();
    try {
      if (st.images.length + files.length > 4) throw Error(PR.t("每次最多添加 4 张图片"));
      const pending = [];
      for (const file of files) {
        const ext = (file.name || "").split(".").pop().toLowerCase();
        const type = file.type || ({png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp"})[ext];
        if (!["image/png", "image/jpeg", "image/webp"].includes(type)) throw Error(PR.t("仅支持 PNG、JPEG 或 WebP 图片"));
        if (file.size > 8 * 1024 * 1024) throw Error(PR.t("图片过大，单张最多 8 MB"));
        const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.replace(/^data:[^;,]*;/, "data:" + type + ";")); reader.onerror = () => reject(Error(PR.t("图片读取失败"))); reader.readAsDataURL(file); });
        if (ticket !== generation) return;
        pending.push({data});
      }
      st.images.push(...pending);
    } catch (error) { if (ticket === generation) st.imageError = error.message; }
    finally { if (ticket === generation) { st.imageLoading = false; render(); focusInput(); } }
  }
  return { add, html: imagesHtml, reset() { generation++; st.images = []; st.imageError = ""; st.imageLoading = false; } };
  };
})(window.PR);
