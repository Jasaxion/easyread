/* Versioned text buffers: remote observations never replace a dirty draft. */
(function (PR) {
  "use strict";
  class WorkspaceBuffer {
    constructor(file, draft) {
      this.path = file.path; this.binary = !!file.binary; this.mime = file.mime || "";
      this.content = file.content || ""; this.base = this.content; this.version = file.version;
      this.conflict = false; this.remote = null; this.saving = false; this.deleted = false;
      if (draft && !file.binary) {
        this.content = draft.content; this.base = draft.base; this.version = draft.version;
        this.selection = draft.selection;
        this.observe(file);
      }
    }
    get dirty() { return !this.binary && this.content !== this.base; }
    observe(file) {
      if (this.saving) return false;
      if (file.version === this.version) return false;
      if (this.dirty) { this.conflict = true; this.remote = file; return false; }
      this.content = file.content || ""; this.base = this.content; this.version = file.version;
      this.binary = !!file.binary; this.mime = file.mime || ""; this.deleted = false; this.conflict = false; this.remote = null;
      return true;
    }
    async save(write) {
      if (this.saving) return false;
      if (this.conflict || this.deleted) return false;
      if (!this.dirty) return true;
      this.saving = true;
      const content = this.content, version = this.version;
      try {
        const result = await write({ path: this.path, content, version });
        // A keystroke during the request remains dirty against this saved snapshot.
        this.base = content; this.version = result.version; return true;
      } catch (error) {
        if (error.status === 409) this.conflict = true;
        throw error;
      } finally { this.saving = false; }
    }
    draft() { return { content: this.content, base: this.base, version: this.version, selection: this.selection }; }
  }
  PR.WorkspaceBuffer = WorkspaceBuffer;
})(window.PR);
