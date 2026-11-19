/* AI quotes are copied from source Markdown; the reader searches visible DOM text.
   Normalize presentation only, never paraphrase or search a different block. */
(function (PR) {
  "use strict";
  function normalized(text) {
    let value = "";
    const positions = [];
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === "‖" || c === "\u200b") continue;
      if (/\s/.test(c)) {
        if (!value.endsWith(" ")) { value += " "; positions.push(i); }
      } else { value += c; positions.push(i); }
    }
    return { value, positions };
  }
  PR.matchSmartQuote = function (root, note) {
    if (!note.ai_generated && !note.ai_annotation_id) return null;
    const doc = (Array.isArray(root) ? root[0] : root).ownerDocument || document;
    const web = doc !== document;
    const probe = doc.createElement("span");
    const rendered = text => {
      text = String(text || "").replace(/[‖\u200b]/g, "");
      probe.innerHTML = web ? PR.webMathHtml(text) : PR.md(text, PR.footnoteOptions?.(note.key));
      return PR.fullText(probe);
    };
    const text = normalized(PR.fullText(root));
    for (const convert of [s => String(s || ""), rendered]) {
      const quote = normalized(convert(note.quote)).value.trim();
      if (!quote) continue;
      const prefix = normalized(convert(note.prefix)).value.slice(-16);
      const suffix = normalized(convert(note.suffix)).value.slice(0, 16);
      const hits = [];
      for (let i = text.value.indexOf(quote); i >= 0; i = text.value.indexOf(quote, i + 1)) {
        const score = (prefix && text.value.slice(0, i).endsWith(prefix) ? 1 : 0) +
          (suffix && text.value.slice(i + quote.length).startsWith(suffix) ? 1 : 0);
        hits.push({ i, score });
      }
      hits.sort((a, b) => b.score - a.score);
      if (!hits.length || (hits.length > 1 && hits[0].score === hits[1].score)) continue;
      const i = text.positions[hits[0].i];
      const end = text.positions[hits[0].i + quote.length - 1] + 1;
      return { i, length: end - i };
    }
    return null;
  };
})(window.PR);
