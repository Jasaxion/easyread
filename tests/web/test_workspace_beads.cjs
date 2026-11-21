const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const W = { project: { id: 'project-a' }, empty: text => ({ text }) };
  const document = { hidden: false, activeElement: null };
  const context = vm.createContext({ window: { PR: { t: s => s, workspaces: W } }, document, URLSearchParams, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../easyread/web/js/workspaces/beads.js'), 'utf8'), context);
  return W;
}
function panel(W) {
  const p = { pid: 'project-a', dialog: { open: true, setAttribute() {}, removeAttribute() {}, querySelectorAll: () => [] },
    listSeq: 0, detailSeq: 0, board: { replaceChildren() {} }, error: {}, refresh: {}, create: {}, sync: {}, truncated: {}, notice: {}, request: async () => ({ issues: [], ready_ids: [], blocked_ids: [] }) };
  W.beads.panel = p; W.beads.render = () => { p.renders = (p.renders || 0) + 1; };
  return p;
}

test('native readiness is not equated with open; closed issues are never in blocked lane', () => {
  const B = load().beads;
  const open = { id: 'research-1', status: 'open', title: 'Paper review', priority: 0, issue_type: 'task', labels: ['literature'], assignee: 'Alice' };
  assert.equal(B.lane(open, new Set([open.id])), 'blocked');
  assert.equal(B.lane({ ...open, status: 'closed' }, new Set([open.id])), 'closed');
  assert.equal(B.lane({ ...open, status: 'custom' }, new Set()), 'other');
  assert.equal(B.matches(open, { status: 'ready' }, new Set(), new Set()), false);
  assert.equal(B.matches(open, { q: 'alice', status: 'ready', priority: '0', type: 'task' }, new Set([open.id]), new Set()), true);
  assert.equal(B.matches(open, { q: 'LITERATURE', status: 'blocked' }, new Set(), new Set([open.id])), true);
  assert.equal(B.matches(open, { priority: '2' }, new Set(), new Set()), false);
  assert.equal(B.matches(open, { status: 'open' }, new Set(), new Set([open.id])), false, 'status filters follow the visible board lanes');
});

test('same-second field changes invalidate detail even without list revisions', () => {
  const B = load().beads, issue = { updated_at: '2026-11-21T10:00:00Z', title: 'Before', labels: ['a', 'b'] };
  assert.notEqual(B.fingerprint(issue), B.fingerprint({ ...issue, title: 'After' }));
  assert.equal(B.fingerprint(issue), B.fingerprint({ ...issue, labels: ['b', 'a'] }));
});

test('an unchanged poll does not replace focused cards', async () => {
  const W = load(), p = panel(W);
  await W.beads.refresh(p); await W.beads.refresh(p);
  assert.equal(p.renders, 1);
});

test('a late list response cannot populate a closed panel or another project', async () => {
  for (const change of [p => { p.dialog.open = false; }, (p, W) => { W.project.id = 'project-b'; }]) {
    const W = load(), p = panel(W); let resolve;
    p.request = () => new Promise(done => { resolve = done; });
    const refreshing = W.beads.refresh(p);
    change(p, W); resolve({ issues: [{ id: 'private' }], ready_ids: [], blocked_ids: [] }); await refreshing;
    assert.equal(p.renders, undefined); assert.equal(p.issues, undefined);
  }
});

test('polling is single-flight and preserves detail drafts while flagging stale data', async () => {
  const W = load(), p = panel(W); let resolve, calls = 0;
  p.request = () => { calls++; return new Promise(done => { resolve = done; }); };
  p.dirty = true; p.detailIssue = { id: 'research-1', updated_at: 'old' }; p.selected = 'research-1'; p.stale = { hidden: true };
  const refreshing = W.beads.refresh(p); await W.beads.refresh(p);
  assert.equal(calls, 1);
  resolve({ issues: [{ id: 'research-1', updated_at: 'new' }], ready_ids: [], blocked_ids: [], truncated: true }); await refreshing;
  assert.equal(p.dirty, true); assert.equal(p.detailIssue.updated_at, 'old'); assert.equal(p.stale.hidden, false); assert.equal(p.truncated.hidden, false);
});

test('mutation errors survive successful polling and never automatically retry writes', async () => {
  const W = load(), p = panel(W); let writes = 0;
  p.dirty = true;
  p.request = async (route, body) => { if (body) { writes++; throw new Error('database locked'); } return { issues: [] }; };
  await W.beads.mutate(p, '/update', { id: 'research-1' }, () => assert.fail('failed write callback'));
  assert.equal(p.busy, false); assert.equal(p.dirty, true); assert.equal(p.error.textContent, 'database locked');
  await W.beads.refresh(p);
  assert.equal(p.error.textContent, 'database locked'); assert.equal(writes, 1);
});

test('revision conflicts keep a visible reload path even when list fields match', async () => {
  const W = load(), p = panel(W); const issue = { id: 'research-1', title: 'Same', updated_at: 'same' };
  p.dirty = true; p.stale = { hidden: true }; p.detailIssue = issue; p.selected = issue.id;
  p.request = async (_, body) => { if (body) { const error = new Error('changed'); error.code = 'beads_conflict'; throw error; } return { issues: [issue] }; };
  await W.beads.mutate(p, '/update', { id: issue.id }, () => {});
  assert.equal(p.stale.hidden, false); assert.equal(p.dirty, true);
  await W.beads.refresh(p);
  assert.equal(p.stale.hidden, false); assert.equal(p.conflict, true);
});

test('in-flight pre-write list responses are discarded', async () => {
  const W = load(), p = panel(W); let resolve;
  p.request = (route, body) => body ? Promise.resolve({}) : new Promise(done => { resolve = done; });
  const refreshing = W.beads.refresh(p);
  await W.beads.mutate(p, '/update', { id: 'research-1' }, async () => {});
  resolve({ issues: [{ id: 'stale' }] }); await refreshing;
  assert.equal(p.renders, undefined); assert.equal(p.issues, undefined);
});

test('accepted writes are not retried when detail reload fails; comment drafts survive retry', async () => {
  const W = load(), p = panel(W); let writes = 0, fail = true, comment = null;
  W.button = text => ({ text });
  p.body = { classList: { add() {} } };
  p.detail = { replaceChildren() {}, querySelector: () => comment };
  p.commentDraft = 'unsent comment';
  W.beads.renderDetail = () => { comment = { value: '' }; };
  p.request = async (route, body) => {
    if (body) { writes++; return { issue: { id: 'research-1' } }; }
    if (route.startsWith('/show')) { if (fail) throw new Error('read locked'); return { issue: { id: 'research-1' } }; }
    return { issues: [] };
  };
  await W.beads.mutate(p, '/update', { id: 'research-1' }, () => W.beads.loadDetail(p, 'research-1'));
  assert.equal(writes, 1); assert.equal(p.dirty, true); assert.equal(p.commentDraft, 'unsent comment');
  assert.match(p.notice.textContent, /已保存/);
  fail = false; await W.beads.loadDetail(p, 'research-1');
  assert.equal(writes, 1); assert.equal(comment.value, 'unsent comment'); assert.equal(p.dirty, true);
});

test('busy or closed panels cannot issue writes', async () => {
  const W = load(), p = panel(W); let writes = 0;
  p.request = async () => { writes++; };
  p.busy = true; await W.beads.mutate(p, '/create', {}, () => {});
  p.busy = false; p.dialog.open = false; await W.beads.mutate(p, '/create', {}, () => {});
  assert.equal(writes, 0);
});
