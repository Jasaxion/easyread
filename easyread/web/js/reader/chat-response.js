/* Only done/error completes a reader Q&A, never EOF or an HTTP 200 alone. */
(function (PR) {
  "use strict";
  PR.readChatResponse = async function (response, onEvent) {
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = done ? "" : lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          let event;
          try { event = JSON.parse(line); }
          catch (err) { if (done) return false; throw err; }
          onEvent(event);
          if (event.done || event.error) return true;
        }
        if (done) return false;
      }
    } finally {
      reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  };
})(window.PR);
