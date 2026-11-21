/* Task editing stays separate from board polling so refreshes never erase drafts. */
(function (PR, W) {
  "use strict";
  const B = W.beads;
  B.renderDetail = (p, data) => {
    const issue = data.issue, id = issue?.id;
    p.detail.replaceChildren(); p.stale = null;
    const head = PR.el("div", { class: "ws-row" });
    head.append(PR.el("h3", { text: id || PR.t("新建任务") }), W.iconButton(PR.t("关闭任务详情"), async () => {
      if (!await B.canLeave(p) || !B.live(p)) return;
      p.dirty = false; p.selected = undefined; p.detailIssue = null; p.detailSeq++;
      p.detail.hidden = true; p.body.classList.remove("has-detail"); B.render(p); p.create.focus();
    }, "x"));
    p.detail.append(head);
    if (issue) {
      p.stale = PR.el("div", { class: "ws-beads-warning", hidden: true });
      p.stale.append(PR.el("p", { text: PR.t("任务已在其他位置更新。你的输入已保留，请重新载入后再编辑。") }), W.button(PR.t("重新载入任务"), () => B.show(p, id), "sync"));
      p.detail.append(p.stale);
    }
    const form = PR.el("form", { class: "ws-beads-form" }), fields = PR.el("fieldset"); form.append(fields);
    const title = W.field(PR.t("任务标题"), W.input(issue?.title || "", { required: true, maxlength: 500 }), fields);
    const description = PR.el("textarea", { class: "input", rows: 5, maxlength: 20000 }); description.value = issue?.description || "";
    W.field(PR.t("任务描述"), description, fields);
    const row = PR.el("div", { class: "ws-beads-field-row" }); fields.append(row);
    const type = W.field(PR.t("任务类型"), B.select(PR.t("任务类型"), B.types(), issue?.issue_type || "task"), row);
    const priority = W.field(PR.t("优先级"), B.select(PR.t("优先级"), { "0": "P0 · " + PR.t("紧急"), "1": "P1 · " + PR.t("高"), "2": "P2 · " + PR.t("中"), "3": "P3 · " + PR.t("低"), "4": "P4 · " + PR.t("最低") }, String(issue?.priority ?? 2)), row);
    const status = issue ? W.field(PR.t("任务状态"), B.select(PR.t("任务状态"), B.statuses(), issue.status), fields) : null;
    const assignee = W.field(PR.t("负责人"), W.input(issue?.assignee || "", { maxlength: 200 }), fields);
    const labels = W.field(PR.t("标签（逗号分隔）"), W.input((issue?.labels || []).join(", "), { maxlength: 2000 }), fields);
    const save = PR.el("button", { class: "btn accent", type: "submit", text: issue ? PR.t("保存任务") : PR.t("创建任务") });
    fields.append(save);
    const values = () => ({ title: title.value.trim(), description: description.value, issue_type: type.value, priority: Number(priority.value), assignee: assignee.value.trim(),
      labels: [...new Set(labels.value.split(/[,，]/).map(value => value.trim()).filter(Boolean))], ...(status ? { status: status.value } : {}) });
    const initial = values(); p.formDirty = false;
    const changed = () => {
      p.formDirty = JSON.stringify(values()) !== JSON.stringify(initial);
      p.dirty = p.formDirty || !!p.detail.querySelector(".ws-beads-comments textarea")?.value;
      p.notice.textContent = "";
    };
    form.addEventListener("input", changed); form.addEventListener("change", changed);
    form.onsubmit = event => {
      event.preventDefault();
      if (!title.value.trim()) { title.focus(); return; }
      if (issue && !p.stale.hidden) { B.error(p, new Error(PR.t("请先重新载入任务，以免覆盖其他位置的修改。"))); return; }
      const next = values();
      const body = issue ? { id, ...Object.fromEntries(Object.entries(next).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(initial[key]))) } : next;
      if (issue && Object.keys(body).length === 1) return;
      if (issue?.revision) body.expected_revision = issue.revision;
      p.commentDraft = p.detail.querySelector(".ws-beads-comments textarea")?.value || "";
      B.mutate(p, issue ? "/update" : "/create", body, async result => {
        p.selected = result.issue?.id || id;
        // Never retry a successful write just because its follow-up read fails.
        if (p.selected) await B.loadDetail(p, p.selected);
        else { p.detail.replaceChildren(W.empty(PR.t("任务已保存，请刷新列表查看。"))); }
      });
    };
    p.detail.append(form);
    if (issue) {
      if (issue.updated_at) p.detail.append(PR.el("p", { class: "hint", text: PR.t("更新于 {time}", { time: new Date(issue.updated_at).toLocaleString() }) }));
      renderDependencies(p, issue);
      renderComments(p, issue, data.comments || []);
    }
    p.detail.scrollTop = 0;
    if (issue) { head.tabIndex = -1; head.focus({ preventScroll: true }); }
    else title.focus({ preventScroll: true });
  };
  function renderDependencies(p, issue) {
    const section = PR.el("section", { class: "ws-beads-related" });
    section.append(PR.el("h4", { text: PR.t("依赖关系") }));
    const dependencies = issue.dependencies || [], dependents = issue.dependents || [];
    const link = (dep, incoming) => {
      const id = dep.id || (incoming ? dep.issue_id : dep.depends_on_id);
      if (!id) return;
      const row = PR.el("div", { class: "ws-beads-dependency" });
      const type = dep.dependency_type || dep.type || "blocks";
      const direction = type === "parent-child" ? (incoming ? PR.t("子任务") : PR.t("父任务")) : type === "blocks" ? (incoming ? PR.t("阻塞") : PR.t("依赖")) : type;
      row.append(PR.el("small", { text: direction }), W.button(id + (dep.title ? " · " + dep.title : ""), () => B.show(p, id)));
      if (!incoming && type === "blocks") row.append(W.iconButton(PR.t("移除依赖"), async () => {
        if (p.dirty) { B.error(p, new Error(PR.t("请先保存任务表单或评论，再修改依赖关系。"))); return; }
        if (!await PR.confirm({ title: PR.t("移除这条依赖关系？"), body: issue.id + " → " + id, ok: PR.t("移除依赖") }) || !B.live(p)) return;
        await B.mutate(p, "/dependency", { id: issue.id, depends_on: id, remove: true }, () => B.loadDetail(p, issue.id));
      }, "x"));
      section.append(row);
    };
    dependencies.forEach(dep => link(dep, false)); dependents.forEach(dep => link(dep, true));
    if (!dependencies.length && !dependents.length) section.append(PR.el("p", { class: "hint", text: PR.t("暂无依赖关系") }));
    const form = PR.el("form", { class: "ws-beads-dependency-form" });
    const choices = { "": PR.t("选择前置任务") };
    const linked = new Set(dependencies.map(dep => dep.id || dep.depends_on_id));
    p.issues.filter(item => item.id !== issue.id && !linked.has(item.id)).forEach(item => { choices[item.id] = item.id + " · " + item.title; });
    const target = B.select(PR.t("前置任务"), choices), add = PR.el("button", { type: "submit", class: "btn sm line", text: PR.t("添加依赖") });
    target.required = true; form.append(target, add);
    form.onsubmit = event => {
      event.preventDefault(); if (!target.value) return;
      if (p.dirty) { B.error(p, new Error(PR.t("请先保存任务表单或评论，再修改依赖关系。"))); return; }
      B.mutate(p, "/dependency", { id: issue.id, depends_on: target.value }, () => B.loadDetail(p, issue.id));
    };
    section.append(form); p.detail.append(section);
  }
  function renderComments(p, issue, comments) {
    const section = PR.el("section", { class: "ws-beads-comments" });
    section.append(PR.el("h4", { text: PR.t("评论") + " · " + comments.length }));
    comments.forEach(comment => {
      const item = PR.el("article");
      item.append(PR.el("small", { text: [comment.author, comment.created_at ? new Date(comment.created_at).toLocaleString() : ""].filter(Boolean).join(" · ") }), PR.el("p", { text: comment.text || comment.body || "" }));
      section.append(item);
    });
    const form = PR.el("form"), text = PR.el("textarea", { class: "input", rows: 3, maxlength: 10000, required: true, "aria-label": PR.t("添加评论"), placeholder: PR.t("记录进展、结论或下一步…") });
    const submit = PR.el("button", { class: "btn sm line", type: "submit", text: PR.t("添加评论") });
    // Save the comment independently, without replacing an unsaved issue form.
    text.oninput = () => { p.dirty = p.formDirty || !!text.value; p.notice.textContent = ""; };
    form.onsubmit = event => {
      event.preventDefault(); if (!text.value.trim()) return;
      if (p.formDirty) { B.error(p, new Error(PR.t("请先保存任务表单，再添加评论。"))); return; }
      B.mutate(p, "/comment", { id: issue.id, text: text.value.trim() }, () => B.loadDetail(p, issue.id));
    };
    form.append(text, submit); section.append(form); p.detail.append(section);
  }
})(window.PR, window.PR.workspaces);
