/* Read-only task summaries. Execution and cancellation stay with their owners. */
(function (PR) {
  "use strict";
  if (document.getElementById?.("pr-data") || !["http:", "https:"].includes(location.protocol)) return;
  const tasks = new Map(), operations = new Map();
  let scope = "", generation = 0;
  const active = task => ["queued", "running", "waiting"].includes(task.state);
  const terminal = task => ["completed", "failed", "cancelled", "interrupted"].includes(task.state);
  const esc = value => PR.esc(String(value ?? ""));
  const refresh = () => PR.researchTaskMonitor?.refresh();
  const names = {
    "reader-chat": PR.t("AI 问答"), "library-chat": PR.t("全库问 AI"), notehelp: PR.t("笔记助手"),
    "translate-selection": PR.t("选文翻译"), answer: PR.t("批注问答"), retranslate: PR.t("段落重译"),
    prepare: PR.t("文献预处理"), translate: PR.t("论文翻译"), read: PR.t("原文整理"),
    "figure-images": PR.t("图片翻译"), babeldoc: PR.t("PDF 翻译"), "workspace-session": PR.t("研究空间 AI"),
    "ai-summary": PR.t("摘要整理"), "candidate-summary": PR.t("摘要整理"),
    "ai-rewrite": PR.t("AI 改写"), "ai-query": PR.t("关键词提炼"), "ai-category-explain": PR.t("AI 分类说明"),
  };
  const label = task => names[task.activity_kind] || PR.t("后台任务");
  const status = task => ({queued: PR.t("排队中"), running: PR.t("进行中"), waiting: PR.t("等待处理"),
    completed: PR.t("已完成"), failed: PR.t("失败"), cancelled: PR.t("已取消"), interrupted: PR.t("已中断")})[task.state] || task.state;
  function target(task) {
    try {
      const url = new URL(task.url, location.href);
      return url.origin === location.origin && /^\/(?:read\/[^/]+|workspaces|research|)$/.test(url.pathname) ? url.pathname + url.search + url.hash : "";
    } catch (_) { return ""; }
  }
  async function ack(task) {
    if (!terminal(task) || task.acknowledged || operations.get(task.id)?.busy) return;
    const current = generation, op = {busy: true, error: ""};
    const uncertain = (message = "") => {
      if (current !== generation || operations.get(task.id) !== op) return;
      op.busy = false;
      op.error = PR.t("请求状态待确认，请查看任务进度或重试。") + (message ? " " + message : "");
      refresh();
    };
    // A missing response is not permission to abort or automatically repeat a write.
    op.timer = setTimeout(uncertain, 15000);
    operations.set(task.id, op); refresh();
    try {
      const result = await PR.api("/api/research/v1/activities/" + encodeURIComponent(task.id) + "/ack", {method: "POST", body: {scope_id: scope}});
      if (current !== generation || operations.get(task.id) !== op) return;
      if (result.task?.id !== task.id || !result.task.acknowledged) throw new Error(PR.t("通知确认未完成，请重试"));
      if (tasks.has(task.id)) tasks.get(task.id).acknowledged = true;
      operations.delete(task.id); refresh();
    } catch (error) { uncertain(error.message); }
    finally { clearTimeout(op.timer); }
  }
  PR.activityTasks = {
    active, terminal, label, ack,
    badge: status,
    entryText: task => label(task) + " · " + status(task),
    list: () => [...tasks.values()],
    sync(items, nextScope) {
      if (nextScope !== scope) {
        scope = nextScope; generation++; tasks.clear();
        operations.forEach(op => clearTimeout(op.timer)); operations.clear();
      }
      const live = new Set();
      for (const raw of items || []) {
        if (raw?.kind !== "activity" || !raw.id) continue;
        live.add(raw.id);
        tasks.set(raw.id, {...raw, acknowledged: !!(raw.acknowledged || tasks.get(raw.id)?.acknowledged)});
        if (raw.acknowledged) { clearTimeout(operations.get(raw.id)?.timer); operations.delete(raw.id); }
      }
      for (const id of tasks.keys()) if (!live.has(id)) { tasks.delete(id); clearTimeout(operations.get(id)?.timer); operations.delete(id); }
    },
    open(task) { const url = target(task); if (url) PR.navigateWorkspace(url); },
    presentation(task) {
      const op = operations.get(task.id);
      const row = (name, value) => value ? '<div class="pr-task-row"><span class="pr-task-label">' + esc(name) + '</span><span class="pr-task-val">' + esc(value) + '</span></div>' : "";
      const button = (action, text, style = "line") => '<button class="btn ' + style + '" type="button" data-task-' + action + (op?.busy ? ' disabled' : '') + '>' + esc(text) + '</button>';
      return {title: label(task) + " · " + status(task),
        controls: (target(task) ? button("view", PR.t("前往查看"), "accent") : "") +
          (terminal(task) && !task.acknowledged ? button("ack", PR.t("知道了")) : "") +
          (PR.researchTaskMonitor?.completedCount?.() > 1 ? button("ack-all", PR.t("完成全部通知")) : "") + button("continue", PR.t("关闭")),
        detailsMarkup: '<div class="pr-task-details">' + row(PR.t("任务"), task.title || label(task)) +
          row(PR.t("状态"), status(task)) + row(PR.t("正在做什么"), [...new Set([task.stage_label, task.message].filter(Boolean))].map(value => PR.t(value)).join(" · ")) +
          row(PR.t("进度"), task.total > 0 ? (task.done || 0) + " / " + task.total : "") +
          row(PR.t("失败原因："), task.error_reason) + row(PR.t("请求状态"), op?.error) +
          row(PR.t("下一步"), PR.t("详细结果和操作请到原页面查看；关闭通知不会停止任务。")) + '</div>'};
    },
  };
})(window.PR);
