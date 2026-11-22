const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js/workspaces');
function load(names, W = {}, PR = {}) {
  W.sessionCommands ||= {isLocal:()=>false,resolve:async message=>({message,nativeCommand:false}),init(){},close(){},keydown:()=>false,update:async()=>false,open(){}};
  W.sessionQueue ||= {render(){}};
  W.button ||= (text, onclick) => { const button = {text, onclick}; if (text === '详情') W.details = button; return button; };
  W.modal ||= () => { W.dialog = []; return {append: (...items) => W.dialog.push(...items), showModal(){}}; };
  const context = vm.createContext({ window: { PR: { t: s => s, ...PR, workspaces: W } }, document: {createTextNode: text => ({text})}, URLSearchParams, URL,
    location: { href: 'http://localhost/workspaces?project=test', origin: 'http://localhost' } });
  for (const name of ['session-capabilities.js', 'session-operations.js', ...names]) vm.runInContext(fs.readFileSync(path.join(scripts, name), 'utf8'), context);
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
  assert.equal(W.sessionCanStop({ state: 'idle', pending_requests: [{ is_blocking: false }] }), true, 'non-blocking pending work can still be stopped');
  assert.equal(W.sessionCanStop({ state: 'idle', pending_requests: [{ expired: true }] }), false, 'an expired card alone is not live work');
  assert.equal(W.sessionCanStop({ state: 'interrupted' }), true, 'interrupted sessions retain a non-destructive Stop control');
});

test('sessions: runtime gates and Stop follow HTTP state without losing cross-session drafts', async () => {
  // Plain control properties exercise the real toolbar renderer; transcript/card rendering is outside this test.
  const nodes = new Map(), drafts = new Map(), calls = [], goalOption = { disabled: false };
  const node = id => { if (!nodes.has(id)) nodes.set(id, { value: '', disabled: false, scrollTop: 0, scrollHeight: 0, replaceChildren() {}, append() {}, setAttribute() {} }); return nodes.get(id); };
  node('ws-session-mode').querySelector = () => goalOption;
  const PR = { ls: { get: (key, fallback) => drafts.get(key) ?? fallback, del: key => drafts.delete(key) }, el: (_, attrs) => attrs,
    terminalTheme: { register: () => () => {} }, terminalTouch: { register: () => () => {} } };
  const W = load(['session-history.js', 'session-terminal.js', 'session-settings.js', 'sessions.js'], { $: node, setIcon() {}, key: key => key, run: fn => fn, project: { engine: 'codex' }, capabilities: { terminal: { available: true } },
    sessionImages: { restore() {}, payload: () => [], sent() {} },
    store: (key, value) => { drafts.set(key, plain(value)); return true; }, error: error => { W.lastError = error; }, sessionLabel: item => item.state, goalStateLabel: state => state,
    renderSessionRequests() {}, renderConversation() {}, mentions: { files: [], init() {}, restore(draft) { this.files = [...(draft?.files || [])]; }, close() {} } }, PR);
  const S = W.sessions;
  const capabilities = { sessions: true, engines: { codex: { goal: true, steer: true, user_input: true }, claude: { goal: false, steer: false, user_input: false } }, terminal: false };
  const request = { id: 7, generation: 'runtime-1', kind: 'question', thread_id: 'native-b', turn_id: 'tb', is_blocking: true };
  let resolveSend, restart = false, updatesReset = false;
  W.api = async (url, body) => {
    calls.push({ url, body: body && plain(body) });
    if (url === '/sessions') return { sessions: [snapshot('a').session, snapshot('b').session], engines: [], capabilities };
    if (url === '/sessions/a') return snapshot('a');
    if (url === '/sessions/a/goal') return snapshot('a', 11, { goal: body.action === 'clear' ? null : { objective: 'Research this', status: body.action === 'pause' ? 'paused' : 'active' } });
    if (url === '/sessions/a/send') return new Promise(resolve => { resolveSend = resolve; });
    if (url === '/sessions/b') return snapshot('b', restart ? 21 : 10, { pending_requests: restart ? [] : [request], active_turn_id: restart ? 'new-turn' : 'tb', capabilities: capabilities.engines.codex });
    if (url.startsWith('/sessions/b/updates?')) return updatesReset ? { reset: true } : { session: { id: 'b', state: 'waiting_user', active_turn_id: 'tb', capabilities: { goal: false, steer: false, user_input: false } }, messages: [], events: [], pending_requests: [request], next_cursor: 20 };
    if (url === '/sessions/b/cancel') return snapshot('b', 22, { state: 'interrupted', runtime_state: 'sleeping', reconciliation_required: true, background_active: true,
      messages: [{ id: 'kept', role: 'assistant', content: 'Existing result' }] });
    if (url === '/sessions/b/respond' || url === '/sessions/b/steer') { restart = true; const error = new Error('stale native request/turn'); error.status = 409; throw error; }
    assert.fail('Unexpected request: ' + url);
  };
  await S.init();
  assert.equal(goalOption.disabled, false, 'the actual capabilities.engines envelope enables Goal');
  assert.equal(node('ws-session-notice').hidden, true, 'no constant explanatory notice when no warning applies');
  assert.equal(W.sessionSettings.available(), true);
  const ready = S.current;
  for (const released of [
    { reconciliation_required: true },
    { state: 'interrupted', runtime_state: 'sleeping', reconciliation_required: true, pending_requests: [{ expired: true }] },
    { state: 'error' }, { state: 'completed', goal: { status: 'complete' } }, { state: 'paused', goal: { status: 'paused' } }
  ]) {
    S.current = { ...ready, ...released }; S.render();
    assert.equal(W.sessionSettings.available(), true, 'released/historical state permits model correction: ' + JSON.stringify(released));
    assert.equal(node('ws-terminal').disabled, false, 'released state permits explicit terminal opening');
    if (released.reconciliation_required) assert.match(node('ws-session-notice').textContent, /运行状态待核对/, 'recovery access must not erase warnings');
  }
  for (const work of [
    ...['running', 'queued', 'waiting_user', 'waiting_external'].map(state => ({ state })),
    { runtime_state: 'starting' }, { active_turn_id: 'turn' }, { native_active: true }, { background_active: true }, { queued: true },
    { pending_requests: [{ is_blocking: false }] }, { pending_count: 1 },
    ...['active', 'blocked', 'usageLimited', 'budgetLimited'].map(status => ({ goal: { status } }))
  ]) {
    S.current = { ...ready, ...work }; S.render();
    assert.equal(W.sessionSettings.available(), false, 'actual work prevents edits: ' + JSON.stringify(work));
    assert.equal(node('ws-terminal').disabled, true, 'actual work prevents terminal handoff');
  }
  S.current = ready; S.render();
  node('ws-session-mode').value = 'goal'; node('ws-message').value = 'Research this'; W.mentions.files = ['notes.md'];
  const initialCalls = calls.length;
  S.current.goal = { status: 'active' }; await S.send(); assert.equal(calls.length, initialCalls);
  await S.goal('pause'); assert.equal(S.current.goal.status, 'paused');
  await S.goal('resume'); assert.equal(S.current.goal.status, 'active');
  await S.goal('clear'); assert.equal(S.current.goal, null);
  const sending = S.send();
  assert.deepEqual(calls.find(call => call.url.endsWith('/send')).body, { message: 'Research this', files: ['notes.md'], mode: 'goal' });
  drafts.set('message:b', { message: 'B draft', files: ['b.md'], mode: 'chat' });
  await S.select('b'); assert.equal(node('ws-message').value, 'B draft'); assert.deepEqual(W.mentions.files, ['b.md']);
  await S.poll(); assert.equal(S.history.cursor, 20); assert.ok(calls.some(call => call.url === '/sessions/b/updates?after=10&limit=100'));
  assert.equal(goalOption.disabled, true, 'refreshed session capability false overrides engine probe true');
  assert.equal(node('ws-steer').hidden, true); assert.equal(node('ws-steer').disabled, true);
  assert.ok(S.items.every(item => !('messages' in item) && !('events' in item) && !('pending_requests' in item)));
  await assert.rejects(S.respond('b', request, { answers: { q: { answers: ['Yes'] } } }), /stale/);
  const response = calls.find(call => call.url.endsWith('/respond'));
  assert.deepEqual(response.body, { request_id: 7, generation: 'runtime-1', answers: { q: { answers: ['Yes'] } } });
  assert.equal(S.current.pending_requests.length, 0); assert.match(W.lastError.message, /请求已过期/);
  assert.equal(goalOption.disabled, false, 'a refreshed runtime snapshot restores its supported capabilities');
  assert.equal(node('ws-steer').hidden, true); assert.equal(node('ws-steer').disabled, false);
  assert.equal(node('ws-send').title,'引导当前执行','Codex uses the primary button for native steering without duplicate controls');
  const count = calls.length; await assert.rejects(S.respond('b', request, {})); assert.equal(calls.length, count, 'expired card is rejected before sending');
  await assert.rejects(S.send(true), /stale/);
  assert.equal(calls.filter(call => call.url.endsWith('/steer')).length, 1);
  assert.equal(calls.find(call => call.url.endsWith('/steer')).body.expected_turn_id, 'new-turn');
  assert.equal(node('ws-message').value, 'B draft'); assert.deepEqual(W.mentions.files, ['b.md']);
  updatesReset = true; await S.poll(); assert.equal(S.history.cursor, 21, 'reset reloads only the selected snapshot');
  resolveSend(snapshot('a', 12, { state: 'running' })); await sending;
  assert.equal(S.current.id, 'b'); assert.equal(node('ws-message').value, 'B draft');
  assert.deepEqual(drafts.get('message:a'), { message: '', files: [], mode: 'goal' });
  Object.assign(S.current, { active_turn_id: null, state: 'waiting_external', background_active: true,
    messages: [{ id: 'kept', role: 'assistant', content: 'Existing result' }] }); S.render();
  assert.equal(node('ws-send').disabled, true); assert.equal(node('ws-cancel').hidden, false); assert.equal(node('ws-cancel').disabled, false);
  await node('ws-cancel').onclick();
  assert.equal(calls.at(-1).url, '/sessions/b/cancel'); assert.deepEqual(calls.at(-1).body, {});
  assert.equal(S.current.messages[0].content, 'Existing result', 'cancellation preserves session history');
  assert.equal(node('ws-cancel').hidden, false, 'reconciliation remains reachable without an active turn');
  assert.match(node('ws-session-notice').textContent, /运行状态待核对/);
  W.details.onclick(); assert.match(W.dialog[0].text, /点击“停止”释放运行时/, 'long recovery guidance remains available on demand');
  await node('ws-cancel').onclick();
  assert.equal(calls.filter(call => call.url.endsWith('/cancel')).length, 2);
  assert.ok(!calls.some(call => call.url.endsWith('/delete')));
  assert.equal(node('ws-send').disabled, false, 'persisted reconciliation flags do not prevent explicit retry after cancellation');
  assert.equal(W.sessionSettings.available(), false, 'reported background work remains protected after cancellation');
  S.current.background_active = false; S.render();
  assert.equal(W.sessionSettings.available(), true, 'once released, cancelled sessions can correct their model/effort');
  assert.equal(node('ws-terminal').disabled, false); assert.match(node('ws-session-notice').textContent, /运行状态待核对/);
  Object.assign(S.current, { engine: 'claude', active_turn_id: null, state: 'idle', background_active: false, reconciliation_required: false, capabilities: capabilities.engines.claude });
  node('ws-session-mode').value = 'goal'; S.render(); assert.equal(node('ws-send').disabled, true);
  node('ws-session-mode').value = 'chat'; S.render(); assert.equal(node('ws-send').disabled, false, 'Claude retains ordinary execution');
  assert.equal(node('ws-session-notice').hidden, true, 'healthy Claude sessions have no permanent capability prose');
  assert.equal(node('ws-session-mode').hidden, true, 'one-option mode selector is hidden');
  Object.assign(S.current, { engine: 'pi', capability_mode: 'lean', capabilities: { goal: false, steer: false, user_input: false } });
  node('ws-session-mode').value = 'goal'; S.render(); assert.equal(node('ws-send').disabled, true);
  node('ws-session-mode').value = 'chat'; S.render(); assert.equal(node('ws-send').disabled, false);
  assert.equal(node('ws-steer').hidden, true); assert.equal(goalOption.disabled, true);
  assert.equal(node('ws-session-notice').hidden, true, 'healthy Pi sessions have no permanent safety/capability prose');
  assert.equal(node('ws-session-mode').hidden, true);
  node('ws-session-mode').value = 'goal'; S.render(); assert.equal(node('ws-session-mode').hidden, false, 'an unsupported saved mode remains reachable for correction');
  node('ws-session-mode').value = 'chat'; S.render();
  S.current.terminal_open = true; S.render();
  assert.equal(node('ws-send').disabled, true); assert.equal(node('ws-goal-resume').disabled, true);
  assert.equal(node('ws-terminal').disabled, false, 'an existing terminal may be revealed without claiming another writer');
  assert.equal(node('ws-terminal-stop').hidden, false); assert.equal(node('ws-cancel').hidden, true);
  assert.equal(W.sessionSettings.available(), false); assert.equal(node('ws-history-older').disabled, false);
  assert.match(node('ws-terminal-note').textContent, /终端运行中/);
});

test('native input admission keeps Respond and Stop reachable without duplicate input', () => {
  const {SessionOperations} = load([]), gate = new SessionOperations();
  const sent=gate.begin('s','send');assert.ok(sent);assert.equal(gate.begin('s','follow-up'),null);
  assert.equal(gate.can('s','respond'),true);assert.equal(gate.can('s','queue/clear'),true);
  const responding=gate.begin('s','respond');assert.ok(responding);
  const stopping=gate.begin('s','cancel');assert.ok(stopping);assert.equal(gate.can('s','respond'),false);
  sent();responding();assert.equal(gate.pending('s'),true);stopping();assert.equal(gate.pending('s'),false);
  assert.equal(gate.can('other','send'),true);
});

test('Pi commands use verified inventory, retain typed Skills, and never turn TUI commands into prompts', async () => {
  const calls=[];
  const W=load(['session-commands.js'], {libraryId:'l',project:{id:'p',engine:'pi'},
    sessions:{current:{id:'s',engine:'pi',generation:'g1'},supports:name=>['commands','compact'].includes(name)},
    sessionTerminal:{owns:()=>false},api:async route=>{calls.push(route);return {commands:[
      {name:'goal',source:'extension'},{name:'skill:review',source:'skill'},{name:'outline',source:'prompt'}]};}});
  const C=W.sessionCommands;
  assert.deepEqual(plain(await C.resolve('$review this draft')),{message:'$review this draft',nativeCommand:false});
  assert.deepEqual(plain(await C.resolve('/goal check it')),{message:'/goal check it',nativeCommand:true});
  await assert.rejects(C.resolve('/login'),/原生终端/);await assert.rejects(C.resolve('$missing'),/未找到/);
  await assert.rejects(C.resolve('/not-a-command'),/未提供/);assert.equal(calls.length,1,'reuse one explicit inventory query');
  W.sessions.current.generation='g2';await C.resolve('/outline text');assert.equal(calls.length,2,'new runtime invalidates cached commands');
  W.sessions.current.engine='claude';W.sessions.supports=()=>false;
  await assert.rejects(C.resolve('$review'),/未找到/);assert.equal(calls.length,2,'unsupported engines do not invent a discovery adapter');
});

test('Pi display turns preserve native delivery order independently of the steering token', () => {
  const W=load(['session-history.js','activity.js'],{}, {debounce:fn=>fn});
  const session={engine:'pi',state:'running',runtime_phase:'settling',active_turn_id:'stable-run',active_display_turn_id:'r2',messages:[
    {id:'r1',turn_id:'r1',role:'user',content:'first'}, {id:'a1',turn_id:'r1',role:'assistant',content:'answer one'},
    {id:'r2',turn_id:'r2',role:'user',content:'next'}, {id:'a2',turn_id:'r2',role:'assistant',content:'answer two'}
  ],events:[{kind:'tool',turn_id:'r1',id:'tool-one'}]};
  const turns=W.conversationTurns(session);
  assert.deepEqual(plain(turns.map(t=>t.messages.map(m=>m.id))),[['r1','a1'],['r2','a2']]);
  assert.equal(turns[0].running,false);assert.equal(turns[1].running,true);assert.equal(turns[1].state,'settling');
  assert.equal(turns[0].events[0].id,'tool-one');
  session.active_display_turn_id=null;assert.ok(W.conversationTurns(session).every(t=>!t.running));
  assert.equal(W.sessionOwnsWork({state:'idle',pending_input_count:1}),true);
  assert.equal(W.sessionCanStop({state:'idle',submission_pending:true}),true);
});
