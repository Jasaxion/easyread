/* One in-flight immutable save; a failed receipt is retried with the same mutation. */
(function (root) {
  "use strict";
  function create(options) {
    let revision = options.revision, scene = options.scene, pending = null, retry = null;
    let writing = false, paused = false, idle = Promise.resolve(), finishIdle, conflict = false, timer, serial = Promise.resolve(), localError = false, recoveryId;
    const id = options.id, scope = options.scope;
    const notify = state => options.status(state, { localError, pending: !!pending, conflict });
    function persist() {
      const record = pending && { id, scope, at: Date.now(), pending: structuredClone(pending), retry: retry && structuredClone(retry) };
      serial = serial.catch(() => {}).then(() => record ? options.drafts.put(record) : options.drafts.remove(id))
        .then(() => { localError = false; }, () => { localError = true; notify("storage-error"); });
      return serial;
    }
    async function flush() {
      clearTimeout(timer);
      if (writing || paused || conflict || !pending) return;
      writing = true; idle = new Promise(resolve => { finishIdle = resolve; });
      const snapshot = retry || structuredClone(pending);
      retry = snapshot; persist(); notify("saving");
      let again = false;
      try {
        const result = await options.save(snapshot);
        revision = result.revision;
        if (pending.mutation === snapshot.mutation) pending = null;
        else { pending.revision = revision; again = true; }
        retry = null;
        await persist();
        if (recoveryId && !pending) { await options.drafts.remove(recoveryId); recoveryId = null; }
        notify(pending ? "pending" : "saved");
        if (!pending) Promise.resolve().then(() => options.onSaved?.(snapshot.scene, result.revision)).catch(() => {});
      } catch (error) {
        conflict = error.status === 409;
        notify(conflict ? "conflict" : "error");
      } finally { writing = false; finishIdle(); }
      if (again) return flush();
    }
    return {
      async pause() { paused = true; clearTimeout(timer); await idle; },
      resume() { paused = false; if (pending) { clearTimeout(timer); timer = setTimeout(flush, 700); } },
      change(value) {
        if (paused) return;
        scene = value;
        pending = { revision, mutation: options.uuid(), scene: value };
        persist(); notify("pending");
        clearTimeout(timer); timer = setTimeout(flush, 700);
      },
      restore(record) {
        recoveryId = record.id;
        pending = structuredClone(record.pending); retry = record.retry && structuredClone(record.retry);
        revision = pending.revision;
        scene = pending.scene; conflict = false; persist(); notify("pending");
      },
      flush,
      get scene() { return scene; },
      get dirty() { return !!pending; },
      get busy() { return writing; },
      get revision() { return revision; },
      async discard() {
        if (writing) throw new Error("A save is still in progress");
        clearTimeout(timer); pending = retry = null; conflict = false;
        await persist();
        if (recoveryId) await options.drafts.remove(recoveryId);
      },
    };
  }
  if (typeof module !== "undefined" && module.exports) module.exports = { create };
  else root.EasyReadWhiteboardSync = { create };
})(globalThis);
