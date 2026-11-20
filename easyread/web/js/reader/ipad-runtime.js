/* Keep background state work out of active iPad input; durable operation writes never wait. */
(function (PR) {
  "use strict";
  let lastInput = -Infinity;
  const enabled = () => PR.readerInput?.ipadSafari === true &&
    (PR.readerInput.ipadBusy?.() === true || PR.feature?.("ipad_ink") !== false);
  const mark = () => { if (enabled()) lastInput = Date.now(); };
  const busy = () => enabled() && !document.hidden &&
    (!!PR.readerInput.ipadBusy?.() || Date.now() - lastInput < 120);
  async function wait(options = {}) {
    while (!options.immediate && busy()) await new Promise(resolve => setTimeout(resolve, 40));
  }
  for (const event of ["ink-input-start", "ink-contact-end", "ink-pan-end"]) PR.on(event, mark);
  PR.ipadRuntime = {enabled, busy, wait, mark};
})(window.PR);
