/* Drafts are written only for explicitly created boards, never for empty panels. */
(function (root) {
  "use strict";
  let opening;
  function db() {
    return opening ||= new Promise((resolve, reject) => {
      const request = indexedDB.open("easyread-whiteboard-drafts", 1);
      request.onupgradeneeded = () => request.result.createObjectStore("drafts", { keyPath: "id" });
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { opening = null; reject(request.error); };
    });
  }
  async function transaction(mode, action) {
    const database = await db();
    return new Promise((resolve, reject) => {
      const tx = database.transaction("drafts", mode), request = action(tx.objectStore("drafts"));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error("Draft storage failed"));
    });
  }
  root.EasyReadWhiteboardDrafts = {
    pendingForLibrary: async library => (await transaction("readonly", store => store.getAll())).some(row => row.scope.startsWith(library + ":")),
    put: draft => transaction("readwrite", store => store.put(draft)),
    remove: id => transaction("readwrite", store => store.delete(id)),
    list: async scope => (await transaction("readonly", store => store.getAll())).filter(row => row.scope === scope).sort((a, b) => b.at - a.at),
  };
})(globalThis);
