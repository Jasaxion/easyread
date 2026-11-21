const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js/workspaces');
function load(names, W = {}, PR = {}) {
  const context = vm.createContext({ window: { PR: { t: s => s, ...PR, workspaces: W } }, URLSearchParams });
  for (const name of names) vm.runInContext(fs.readFileSync(path.join(scripts, name), 'utf8'), context);
  return W;
}
const plain = value => JSON.parse(JSON.stringify(value));
const snapshot = (id, cursor = 10, extra = {}) => ({ session: { id, engine: 'codex', state: 'idle', messages: [], events: [], pending_requests: [], ...extra }, cursor, history_before: 5, has_more: true });

test('session-history: cursor upserts, bounded older windows, native goal/turn activity and typed pending identity', () => {
  const W = load(['session-history.js', 'activity.js'], {}, { debounce: fn => fn });
  const history = new W.SessionHistory(snapshot('a', 10, { messages: [{ id: 'm', turn_id: 't', role: 'assistant', content: 'part', partial: true }], events: [{ seq: 9, kind: 'tool', turn_id: 't', id: 'cmd' }] }));
  history.update({ session: { id: 'a', goal: { status: 'active' }, state: 'idle' }, messages: [{ id: 'm', turn_id: 't', role: 'assistant', content: 'done' }], events: [{ seq: 9, kind: 'tool', turn_id: 't', id: 'cmd' }], pending_requests: [], next_cursor: 11, has_more: true });
  assert.equal(history.session.messages.length, 1); assert.equal(history.session.messages[0].content, 'done');
  assert.equal(history.session.events.length, 1); assert.equal(history.cursor, 11); assert.equal(history.catchingUp, true);
  assert.equal(W.sessionBusy(history.session), true, 'an active goal between turns must not permit another send');
  assert.equal(history.update({ reset: true }), false); assert.equal(history.cursor, 11);
  history.update({ session: { state: 'error' }, next_cursor: 8 }); assert.equal(history.session.state, 'idle', 'old updates do not roll back state');
  const requests = [{ id: 7, generation: 'g1', thread_id: 'native', turn_id: 't', is_blocking: true }];
  history.session.pending_requests = requests;
  assert.ok(W.pendingRequest(history.session, requests[0]));
  assert.equal(W.pendingRequest(history.session, { ...requests[0], id: '7' }), undefined);
  assert.equal(W.pendingRequest(history.session, { ...requests[0], generation: 'g2' }), undefined);
  for (let page = 0; page < 8; page++) history.prepend({ messages: Array.from({ length: 40 }, (_, i) => ({ id: `old-${page}-${i}`, role: 'user', content: 'old' })), events: Array.from({ length: 200 }, (_, i) => ({ seq: -page * 200 - i })), history_before: -page - 1, has_more: true });
  assert.equal(history.older.messages.length, 240); assert.equal(history.older.events.length, 1200);
  assert.equal(history.before, -8); assert.equal(history.view.messages[0].id, 'old-7-0');
  history.update({ session: { state: 'running', active_turn_id: 'goal-turn' }, messages: Array.from({ length: 100 }, (_, i) => ({ id: `live-${i}`, role: 'assistant', content: 'new' })), events: Array.from({ length: 500 }, (_, i) => ({ seq: 20 + i })), pending_requests: [], next_cursor: 520 });
  assert.equal(history.session.messages.length, 80); assert.equal(history.session.events.length, 400);
  assert.equal(history.view.messages[0].id, 'old-7-0', 'background updates do not evict the older view');
  assert.equal(history.session.pending_requests.length, 0);
  const turns = W.conversationTurns({ messages: [], events: [{ kind: 'tool', turn_id: 'goal-turn', id: 'cmd' }], active_turn_id: 'goal-turn', state: 'waiting_user' });
  assert.equal(turns.length, 1); assert.equal(turns[0].running, true, 'tool-only native turns retain an activity group while waiting');
});

test('sessions: another session stays usable during send; stale replies and steers never retry or lose drafts', async () => {
  // Only input values and the session select are needed; rendering is not a mocked DOM framework.
  const nodes = new Map(), drafts = new Map(), calls = [];
  const node = id => { if (!nodes.has(id)) nodes.set(id, { value: '', disabled: false, scrollTop: 0, scrollHeight: 0, replaceChildren() {}, append() {} }); return nodes.get(id); };
  const PR = { ls: { get: (key, fallback) => drafts.get(key) ?? fallback, del: key => drafts.delete(key) }, el: (_, attrs) => attrs };
  const W = load(['session-history.js', 'sessions.js'], { $: node, key: key => key, project: { engine: 'codex' }, store: (key, value) => { drafts.set(key, plain(value)); return true; }, error: error => { W.lastError = error; }, sessionLabel: item => item.state,
    mentions: { files: [], restore(draft) { this.files = [...(draft?.files || [])]; }, close() {} } }, PR);
  const S = W.sessions; S.render = () => S.updateSend();
  const capabilities = { codex: { goal: true, steer: true, user_input: true }, claude: { goal: false, steer: false, user_input: false } };
  S.capabilities = capabilities;
  const request = { id: 7, generation: 'runtime-1', kind: 'question', thread_id: 'native-b', turn_id: 'tb', is_blocking: true };
  let resolveSend, restart = false, updatesReset = false;
  W.api = async (url, body) => {
    calls.push({ url, body: body && plain(body) });
    if (url === '/sessions') return { sessions: [snapshot('a').session, snapshot('b').session], capabilities };
    if (url === '/sessions/a/goal') return snapshot('a', 11, { goal: body.action === 'clear' ? null : { objective: 'Research this', status: body.action === 'pause' ? 'paused' : 'active' } });
    if (url === '/sessions/a/send') return new Promise(resolve => { resolveSend = resolve; });
    if (url === '/sessions/b') return snapshot('b', restart ? 21 : 10, { pending_requests: restart ? [] : [request], active_turn_id: restart ? 'new-turn' : 'tb' });
    if (url.startsWith('/sessions/b/updates?')) return updatesReset ? { reset: true } : { session: { id: 'b', state: 'waiting_user', active_turn_id: 'tb' }, messages: [], events: [], pending_requests: [request], next_cursor: 20 };
    if (url === '/sessions/b/respond' || url === '/sessions/b/steer') { restart = true; const error = new Error('stale native request/turn'); error.status = 409; throw error; }
    assert.fail('Unexpected request: ' + url);
  };
  S.history = new W.SessionHistory(snapshot('a')); S.current = S.history.session;
  node('ws-session-mode').value = 'goal'; node('ws-message').value = 'Research this'; W.mentions.files = ['notes.md'];
  S.current.goal = { status: 'active' }; await S.send(); assert.equal(calls.length, 0);
  await S.goal('pause'); assert.equal(S.current.goal.status, 'paused');
  await S.goal('resume'); assert.equal(S.current.goal.status, 'active');
  await S.goal('clear'); assert.equal(S.current.goal, null);
  const sending = S.send();
  assert.deepEqual(calls.find(call => call.url.endsWith('/send')).body, { message: 'Research this', files: ['notes.md'], mode: 'goal' });
  drafts.set('message:b', { message: 'B draft', files: ['b.md'], mode: 'chat' });
  await S.select('b'); assert.equal(node('ws-message').value, 'B draft'); assert.deepEqual(W.mentions.files, ['b.md']);
  await S.poll(); assert.equal(S.history.cursor, 20); assert.ok(calls.some(call => call.url === '/sessions/b/updates?after=10&limit=100'));
  assert.ok(S.items.every(item => !('messages' in item) && !('events' in item) && !('pending_requests' in item)));
  await assert.rejects(S.respond('b', request, { answers: { q: { answers: ['Yes'] } } }), /stale/);
  const response = calls.find(call => call.url.endsWith('/respond'));
  assert.deepEqual(response.body, { request_id: 7, generation: 'runtime-1', answers: { q: { answers: ['Yes'] } } });
  assert.equal(S.current.pending_requests.length, 0); assert.match(W.lastError.message, /请求已过期/);
  const count = calls.length; await assert.rejects(S.respond('b', request, {})); assert.equal(calls.length, count, 'expired card is rejected before sending');
  await assert.rejects(S.send(true), /stale/);
  assert.equal(calls.filter(call => call.url.endsWith('/steer')).length, 1);
  assert.equal(calls.find(call => call.url.endsWith('/steer')).body.expected_turn_id, 'new-turn');
  assert.equal(node('ws-message').value, 'B draft'); assert.deepEqual(W.mentions.files, ['b.md']);
  updatesReset = true; await S.poll(); assert.equal(S.history.cursor, 21, 'reset reloads only the selected snapshot');
  resolveSend(snapshot('a', 12, { state: 'running' })); await sending;
  assert.equal(S.current.id, 'b'); assert.equal(node('ws-message').value, 'B draft');
  assert.deepEqual(drafts.get('message:a'), { message: '', files: [], mode: 'goal' });
  S.current.engine = 'claude'; S.current.active_turn_id = null; S.current.state = 'idle';
  node('ws-session-mode').value = 'goal'; S.updateSend(); assert.equal(node('ws-send').disabled, true);
  node('ws-session-mode').value = 'chat'; S.updateSend(); assert.equal(node('ws-send').disabled, false, 'Claude retains ordinary chat');
});
