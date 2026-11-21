/* Pending prompts belong to the server runtime, not to the lifetime of a browser tab. */
(function (PR, W) {
  "use strict";
  let sessionId;
  const cards = new Map();
  function questionFields(form, questions) {
    const read = [];
    questions.forEach((question, index) => {
      const group = PR.el("fieldset", { class: "ws-request-question" });
      group.append(PR.el("legend", { text: question.header || question.id }), PR.el("p", { text: question.question }));
      const options = question.options || [];
      if (options.length) {
        const choices = [];
        options.forEach(option => {
          const input = W.input("", { type: "radio", name: "question-" + index, required: true }); input.value = option.label;
          const label = PR.el("label", { class: "ws-request-option" });
          label.append(input, PR.el("span", { text: option.label + (option.description ? " — " + option.description : "") }));
          group.append(label); choices.push(input);
        });
        let other, custom;
        if (question.isOther) {
          other = W.input("", { type: "radio", name: "question-" + index, required: true });
          const label = PR.el("label", { class: "ws-request-option" }); label.append(other, PR.el("span", { text: PR.t("其他回答") }));
          custom = W.input("", { type: question.isSecret ? "password" : "text", autocomplete: "off", "aria-label": PR.t("其他回答") });
          custom.disabled = true; group.append(label, custom);
          group.onchange = () => { custom.disabled = !other.checked; custom.required = other.checked; };
        }
        read.push(() => [question.id, { answers: [other?.checked ? custom.value : choices.find(input => input.checked).value] }]);
      } else {
        const input = W.input("", { type: question.isSecret ? "password" : "text", required: true, autocomplete: "off", "aria-label": question.question });
        group.append(input); read.push(() => [question.id, { answers: [input.value] }]);
      }
      form.append(group);
    });
    return () => ({ answers: Object.fromEntries(read.map(fn => fn())) });
  }
  function elicitationFields(form, schema) {
    // Only render the flat primitive subset whose validation we can represent faithfully.
    const allowed = new Set(["type", "properties", "required", "additionalProperties", "title", "description", "$schema"]);
    const fieldKeys = new Set(["type", "title", "description", "enum"]);
    const fields = Object.entries(schema?.properties || {});
    if (!schema || schema.type !== "object" || Object.keys(schema).some(key => !allowed.has(key)) ||
      !fields.length || (schema.required || []).some(key => !Object.hasOwn(schema.properties, key)) ||
      fields.some(([, field]) => !field || !["string", "boolean"].includes(field.type) ||
        Object.keys(field).some(key => !fieldKeys.has(key)) ||
        (field.enum && (field.type !== "string" || !field.enum.length || field.enum.some(value => typeof value !== "string" || value === ""))))) return null;
    const read = fields.map(([key, field]) => {
      const required = (schema.required || []).includes(key);
      let input;
      if (field.enum || field.type === "boolean") {
        input = PR.el("select", { class: "input", required });
        input.append(PR.el("option", { value: "", text: PR.t("请选择") }));
        (field.enum || ["true", "false"]).forEach(value => input.append(PR.el("option", { value,
          text: field.enum ? value : value === "true" ? PR.t("是") : PR.t("否") })));
      } else input = W.input("", { required, autocomplete: "off" });
      W.field(field.title || key, input, form);
      if (field.description) form.append(PR.el("p", { class: "hint", text: field.description }));
      return () => input.value === "" && !required ? [] : [[key, field.type === "boolean" ? input.value === "true" : input.value]];
    });
    return () => ({ decision: "accept", values: Object.fromEntries(read.flatMap(fn => fn())) });
  }
  W.renderSessionRequests = session => {
    const box = W.$("ws-requests"), requests = session?.pending_requests || [];
    if (sessionId !== session?.id) { sessionId = session?.id; cards.clear(); box.replaceChildren(); }
    const keys = new Set(requests.map(W.requestKey));
    cards.forEach((card, key) => { if (!keys.has(key)) { card.form.remove(); cards.delete(key); } });
    box.hidden = !requests.length;
    requests.forEach(request => {
      const key = W.requestKey(request), signature = JSON.stringify(request), previous = cards.get(key);
      if (previous?.signature === signature) return;
      previous?.form.remove();
      const form = PR.el("form", { class: "ws-request-card" }), fields = PR.el("fieldset");
      const kind = request.kind === "approval" ? PR.t("需要批准") : request.kind === "elicitation" ? PR.t("工具请求") : PR.t("需要回答");
      fields.append(PR.el("legend", { text: request.title || kind }), PR.el("p", { class: "hint", text: kind + " · " + (request.expired ? PR.t("请求已过期") : request.is_blocking ? PR.t("等待你的答复") : PR.t("可稍后回答，任务仍可继续")) }));
      if (request.reason) fields.append(PR.el("p", { text: request.reason }));
      if (request.command) fields.append(PR.el("pre", { text: typeof request.command === "string" ? request.command : JSON.stringify(request.command, null, 2) }));
      const error = PR.el("p", { class: "err", role: "alert" });
      let answer;
      if (request.kind === "question" && request.questions?.length) answer = questionFields(fields, request.questions);
      else if (request.kind === "approval") answer = () => ({ decision: "accept" });
      else if (request.kind === "elicitation" && !request.url) answer = elicitationFields(fields, request.schema);
      if (request.url) {
        // URL flows are never opened or marked complete automatically.
        try {
          const url = new URL(request.url);
          if (["https:", "http:"].includes(url.protocol)) fields.append(PR.el("a", { href: url.href, target: "_blank", rel: "noopener noreferrer", text: PR.t("查看请求链接") }));
        } catch (_) {}
      }
      if (!answer && !request.expired) fields.append(PR.el("p", { class: "hint", text: PR.t("此请求格式暂不支持；不会自动批准或填写。") }));
      const actions = PR.el("div", { class: "ws-row ws-wrap" });
      if (answer) actions.append(PR.el("button", { class: "btn sm accent", type: "submit", text: request.kind === "approval" ? PR.t("仅允许这一次") : PR.t("提交答复") }));
      const submit = async payload => {
        fields.disabled = true; error.textContent = "";
        let submitted = false;
        try { await W.sessions.respond(session.id, request, payload); submitted = true; }
        catch (err) { error.textContent = err.message; }
        finally { fields.disabled = submitted || !!request.expired; }
      };
      if (["approval", "elicitation"].includes(request.kind)) actions.append(W.button(PR.t("拒绝"), () => submit({ decision: "decline" })));
      fields.append(actions); fields.disabled = !!request.expired; form.append(fields, error);
      form.onsubmit = event => { event.preventDefault(); if (answer && !fields.disabled && form.reportValidity()) submit(answer()); };
      cards.set(key, { signature, form }); box.append(form);
    });
  };
})(window.PR, window.PR.workspaces);
