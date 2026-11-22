/* Input submission must not prevent answering a native dialog or stopping it. */
(function (W) {
  "use strict";
  const inputs = new Set(['send','steer','follow-up','command']);
  W.SessionOperations = class {
    constructor() { this.active = new Map(); }
    pending(id) { return !!this.active.get(id)?.size; }
    can(id, operation) {
      const active = [...(this.active.get(id)?.values() || [])];
      if (!active.length) return true;
      if (active.includes(operation)) return false;
      if (operation === 'cancel') return active.every(item => inputs.has(item) || item === 'respond' || item === 'queue/clear');
      if (operation === 'respond' || operation === 'queue/clear') return active.every(item => inputs.has(item));
      return false;
    }
    begin(id, operation) {
      if (!this.can(id, operation)) return null;
      const token = Symbol(operation), entries = this.active.get(id) || new Map();
      entries.set(token, operation); this.active.set(id, entries);
      return () => {
        if (this.active.get(id) !== entries) return;
        entries.delete(token);
        if (!entries.size) this.active.delete(id);
      };
    }
  };
})(window.PR.workspaces);
