const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const scripts = path.resolve(__dirname, '../../easyread/web/js/workspaces');

function fixture(names) {
  const nodes = new Map(), stored = new Map();
  function el(tag, attrs = {}) {
    return Object.assign({ tag, value: '', children: [], hidden: false, listeners: {}, style: {},
      append(...items) { for (const item of items) { item.parentElement = this; this.children.push(item); } },
      replaceChildren(...items) { this.children = []; this.append(...items); },
      remove() { this.removed = true; if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(item => item !== this); },
      addEventListener(name, fn) { const previous = this.listeners[name]; this.listeners[name] = (...args) => { previous?.(...args); return fn(...args); }; }, setAttribute(name, value) { this[name] = value; },
      get selectedOptions() { return this.children.filter(item => item.value === this.value); },
      getAttribute(name) { return this[name]; }, focus() {}, showModal() {}, close() { this.listeners.close?.(); },
      getBoundingClientRect() { return { left: 10, bottom: 20 }; }, offsetWidth: 200, offsetHeight: 100,
    }, attrs);
  }
  const W = { $: id => { if (!nodes.has(id)) nodes.set(id, el('div')); return nodes.get(id); }, key: key => key,
    run: fn => fn, setIcon() {}, store: (key, value) => { stored.set(key, value); return true; },
    capabilities: { terminal: { available: true } }, view: 'project',
    field: (label, input, root) => { const field = el('label'); field.append(input); root.append(field); return input; },
    input: (value, attrs) => el('input', { ...attrs, value }), iconButton: (label, fn) => el('button', { onclick: fn }),
    modal: () => { const dialog = el('dialog'); W.dialog = dialog; return dialog; },
  };
  const PR = { workspaces: W, el, t: (text, args = {}) => text.replace(/\{(\w+)\}/g, (_, key) => args[key] ?? key),
    ls: { get: (key, fallback) => stored.get(key) ?? fallback }, confirm: async () => false };
  const context = vm.createContext({ window: { PR, addEventListener() {} }, document: { addEventListener() {} },
    URL, URLSearchParams, location: { href: 'http://localhost/workspaces?project=test', origin: 'http://localhost' }, innerWidth: 1000, innerHeight: 800 });
  ['session-capabilities.js', ...names].forEach(name => vm.runInContext(fs.readFileSync(path.join(scripts, name), 'utf8'), context));
  return { W, PR, el, stored };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('session-terminal: same-origin namespace, retained frames, late selection and confirmed writer release', async () => {
  const { W, PR } = fixture(['session-history.js', 'session-terminal.js']);
  const T = W.sessionTerminal, calls = [];
  let resolveOpen;
  const a = { id: 'a', state: 'idle' }, b = { id: 'b', state: 'idle' };
  W.sessions = { current: a, isPending: () => false, render: () => T.render(), poll: async () => {},
    mutate: async (id, operation) => { calls.push([id, operation]);
      if (operation === 'terminal') return new Promise(resolve => { resolveOpen = resolve; });
      return { session: { id, terminal_open: false } };
    } };
  assert.equal(T.url('/workspace-terminal/token_123/'), 'http://localhost/workspace-terminal/token_123/');
  for (const url of ['https://elsewhere/workspace-terminal/token/', '/terminal/token/', '/workspace-terminal/token/?x=1', 'javascript:alert(1)', 'http://user@localhost/workspace-terminal/token/']) {
    assert.throws(() => T.url(url), /终端地址无效/);
  }
  a.state = 'running'; await T.toggle(); assert.equal(calls.length, 0, 'opening never cancels active GUI work');
  Object.assign(a, { state: 'interrupted', runtime_state: 'sleeping', reconciliation_required: true, goal: { status: 'paused' }, pending_requests: [{ expired: true }] });
  T.render(); assert.equal(W.$('ws-terminal').disabled, false, 'historical cancellation/reconciliation does not block a new terminal');
  const opening = T.toggle();
  T.reconcile([a, b]); assert.equal(T.owns('a'), true, 'a stale idle list cannot drop an opening terminal');
  W.sessions.current = b;
  resolveOpen({ url: '/workspace-terminal/token_123/' }); await opening;
  const host = W.$('ws-terminal-frames'), frame = host.children[0];
  assert.equal(frame.hidden, true); assert.equal(host.hidden, true, 'a late terminal is never shown in another session');
  a.terminal_open = true; W.sessions.current = a; await T.toggle();
  assert.equal(frame.hidden, false); assert.equal(W.$('ws-session-gui').hidden, true);
  await T.toggle(); assert.equal(frame.hidden, true); assert.equal(frame.removed, undefined);
  assert.equal(T.owns('a'), true, 'GUI remains read-only while a hidden terminal owns the session');
  W.sessions.current = b; T.render(); W.sessions.current = a; await T.toggle();
  assert.equal(host.children[0], frame); assert.equal(calls.length, 1, 'switching views and sessions neither remounts nor reopens the terminal');
  await T.stop(); assert.equal(calls.length, 1, 'declining confirmation never cancels');
  PR.confirm = async () => true; await T.stop();
  assert.deepEqual(calls.at(-1), ['a', 'cancel']); assert.equal(frame.removed, true); assert.equal(T.owns('a'), false);
  // A terminal reported by the server, but not yet mounted locally, uses repeat POST.
  const repeat = T.toggle(); resolveOpen({ url: '/workspace-terminal/existing/' }); await repeat;
  assert.equal(host.children.length, 1);
  T.reconcile([{ id: 'a', terminal_open: false }, b]); assert.equal(host.children.length, 0, 'ended/deleted entries do not accumulate iframes');
});

test('model-picker/session-settings: metadata effort, inherited defaults, unknown current values and idle-only writes', async () => {
  const { W, el } = fixture(['session-history.js', 'model-picker.js', 'session-settings.js']);
  W.api = async () => ({ models: [{ id: 'known', reasoning_levels: ['low', 'high', 'ultra'], default_reasoning: 'low' },
    { id: 'empty-metadata', reasoning_levels: [] }, { id: 'missing-metadata' }], reasoning_levels: ['none', 'medium', 'xhigh'], configured_reasoning: 'medium' });
  const form = el('form'), engine = el('select', { value: 'codex' }), dialog = el('dialog');
  const picker = W.modelPicker(form, engine, dialog, { model: 'custom-current', reasoning_effort: 'unlisted-effort' });
  await flush();
  const model = form.children[0].children[0].children[0], effort = form.children.at(-1).children[0];
  assert.equal(picker.value, 'custom-current'); assert.equal(picker.reasoningEffort, 'unlisted-effort');
  assert.match(model.children.find(item => item.value === 'model:custom-current').text, /目录未列出/);
  assert.match(effort.children.find(item => item.value === 'unlisted-effort').text, /目录未列出/);
  model.value = 'model:known'; model.onchange();
  assert.deepEqual(effort.children.map(item => item.value), ['', 'low', 'high', 'ultra', 'unlisted-effort'], 'model-advertised extensions remain available');
  effort.value = ''; effort.onchange(); assert.equal(picker.reasoningEffort, '', 'default remains inherit, not a fabricated effort');
  for (const id of ['empty-metadata', 'missing-metadata']) {
    model.value = 'model:' + id; model.onchange();
    assert.deepEqual(effort.children.map(item => item.value), ['', 'none', 'medium', 'xhigh'], 'empty/missing model metadata preserves engine fallback choices');
    assert.equal(picker.reasoningEffort, '', 'showing fallbacks must not replace inheritance');
  }
  model.value = 'custom'; model.onchange();
  assert.deepEqual(effort.children.map(item => item.value), ['', 'none', 'medium', 'xhigh'], 'unknown models use only the engine metadata');
  engine.value = 'claude'; engine.listeners.change(); await flush(); assert.equal(picker.reasoningEffort, '');
  engine.value = 'codex'; engine.listeners.change(); await flush(); assert.equal(picker.reasoningEffort, '');

  const writes = [];
  W.sessionTerminal = { owns: () => false };
  W.sessions = { current: { id: 'a', state: 'interrupted', runtime_state: 'sleeping', reconciliation_required: true,
    goal: { status: 'paused' }, pending_requests: [{ expired: true }], engine: 'codex', model: 'custom-current', reasoning_effort: 'unlisted-effort' },
    isPending: () => false, refresh: async () => {}, mutate: async (...args) => { writes.push(JSON.parse(JSON.stringify(args))); } };
  await W.sessionSettings.open(); await flush();
  const settingsForm = W.dialog.children[0];
  W.sessions.current.background_active = true; await settingsForm.onsubmit({ preventDefault() {} }); assert.equal(writes.length, 0);
  W.sessions.current.background_active = false; await settingsForm.onsubmit({ preventDefault() {} });
  assert.deepEqual(writes[0], ['a', 'settings', { model: 'custom-current', reasoning_effort: 'unlisted-effort' }]);
});

test('editor-settings: live project-local font size does not replace the editor or its undo/draft state', () => {
  const { W, stored } = fixture(['editor-settings.js']);
  const editor = { undo: ['previous edit'], draft: 'unsaved text', selection: { anchor: 3, head: 8 }, setFontSize(size) { this.size = size; } };
  W.files = { active: { binary: false }, editor };
  W.editorSettings.init(); const input = W.$('ws-editor-font-size');
  assert.equal(input.value, 13);
  input.value = 19; input.oninput();
  assert.equal(editor.size, 19); assert.equal(stored.get('editor-font-size'), 19); assert.equal(W.files.editor, editor);
  assert.equal(editor.draft, 'unsaved text'); assert.deepEqual(editor.undo, ['previous edit']); assert.deepEqual(editor.selection, { anchor: 3, head: 8 });
  W.editorSettings.init(); assert.equal(input.value, 19, 'reinitialization restores the persisted project/device preference');
  input.value = 100; input.oninput(); assert.equal(editor.size, 22);
  W.view = 'latex'; input.value = 12; input.oninput(); assert.equal(editor.size, 22, 'ordinary controls never overwrite the existing LaTeX editor font');
});

const descendants = node => [node, ...node.children.flatMap(descendants)];
const piModeSelect = form => descendants(form).find(node => node.tag === 'select' && node.children.some(option => option.value === 'lean'));

test('Pi model-picker reads only its catalog, keeps provider IDs and offers native thinking levels on failure', async () => {
  const { W, el } = fixture(['model-picker.js']); const calls=[];
  W.api = async url => { calls.push(url); return { models: [], configured_reasoning: 'off' }; };
  const form=el('form'),engine=el('select',{value:'pi'}),dialog=el('dialog');
  const picker=W.modelPicker(form,engine,dialog,{model:'provider/saved',reasoning_effort:'off'});await flush();
  assert.equal(calls[0],'/models?engine=pi');
  const choice=form.children[0].children[0].children[0],effort=form.children.at(-1).children[0];
  assert.equal(picker.value,'provider/saved');assert.equal(picker.reasoningEffort,'off');
  assert.deepEqual(effort.children.map(option=>option.value),['','off','minimal','low','medium','high','xhigh','max']);
  assert.equal(effort.children.find(option=>option.value==='off').text,'不思考');
  assert.deepEqual(choice.children.map(option=>option.value),['','model:provider/saved','custom']);
  choice.value='custom';choice.onchange();
  const custom=form.children[1].children[0];assert.equal(custom.placeholder,'provider/id');
  custom.value='provider/custom';custom.oninput();assert.equal(picker.value,'provider/custom');
  W.api=async()=>{throw new Error('offline fixture');};await form.children[0].children[0].children[1].onclick();
  assert.equal(picker.value,'provider/custom');assert.equal(picker.reasoningEffort,'off');assert.equal(choice.disabled,false);
});

test('Pi workspace create uses native CLI with no lean/full selector', async () => {
  const { W, PR } = fixture(['model-picker.js','sessions.js']);
  W.project={engine:'pi'};W.mentions={files:[]};const S=W.sessions,calls=[];
  S.engines=[{id:'pi',installed:true}];S.current={id:'existing'};
  S.refresh=async()=>{};S.select=async()=>{};
  W.api=async(url,body)=>{calls.push({url,body:body&&JSON.parse(JSON.stringify(body))});return url.startsWith('/models?')?{models:[]}:{session:{id:'new-pi'}};};
  PR.confirm=async()=>{throw new Error('No redundant mode confirmation');};
  await S.create();await flush();const form=W.dialog.children[0];
  const engine=descendants(form).find(node=>node.tag==='select'&&node.children.some(option=>option.value==='pi'));
  assert.equal(engine.children.find(option=>option.value==='pi').text,'Pi Agent');
  assert.equal(piModeSelect(form),undefined);
  assert.match(descendants(form).map(node=>node.textContent||'').join(' '),/研究空间始终使用完整原生 CLI/);
  await form.onsubmit({preventDefault(){}});
  assert.deepEqual(calls.find(call=>call.url==='/sessions/create').body,{engine:'pi',reasoning_effort:''});
});

test('Pi session settings edit model and effort only and protect idle identity', async () => {
  const { W } = fixture(['session-history.js','model-picker.js','session-settings.js','session-view.js']);
  W.api=async()=>({models:[]});W.sessionTerminal={owns:()=>false};const writes=[];
  W.sessions={current:{id:'pi-a',engine:'pi',state:'idle',model:'provider/model',reasoning_effort:'off',capability_mode:'lean'},
    isPending:()=>false,refresh:async()=>{},mutate:async(...args)=>writes.push(JSON.parse(JSON.stringify(args)))};
  assert.doesNotMatch(W.sessionLabel(W.sessions.current),/默认精简/);
  await W.sessionSettings.open();await flush();const form=W.dialog.children[0];
  assert.equal(piModeSelect(form),undefined);
  W.sessions.current.id='other';await form.onsubmit({preventDefault(){}});assert.equal(writes.length,0);
  W.sessions.current.id='pi-a';await form.onsubmit({preventDefault(){}});
  assert.deepEqual(writes[0],['pi-a','settings',{model:'provider/model',reasoning_effort:'off'}]);
});

test('Pi project engine option persists with native-resource hint only', async () => {
  const { W, el }=fixture(['projects.js']);const calls=[];
  W.project={id:'existing'};W.navigate=async()=>{};
  W.request=async(url,body)=>{calls.push({url,body:JSON.parse(JSON.stringify(body))});return {project:{id:'new'}};};
  await W.projects.edit();const form=W.dialog.children[0];
  const engine=descendants(form).find(node=>node.tag==='select');
  assert.equal(engine.children.find(option=>option.value==='pi').text,'Pi Agent');engine.value='pi';
  descendants(form).find(node=>node.tag==='input').value='Pi project';await form.onsubmit({preventDefault(){}});
  assert.equal(calls[0].body.engine,'pi');
  const otherForm=el('form'),otherEngine=el('select',{value:'claude'});
  W.sessionCapabilityHint(otherForm,otherEngine);
  assert.equal(otherForm.children[0].hidden,true);
});

test('Pi transcript and terminal fallback describe full native CLI without automatic EasyRead MCP', () => {
  const { W, el }=fixture(['session-view.js','session-terminal.js']);
  W.conversationTurns=()=>[];W.captureActivity=()=>{};W.empty=text=>el('div',{text});
  W.renderConversation({id:'pi',engine:'pi',state:'idle',messages:[],events:[]});
  const empty=W.$('ws-messages').children[0].children[0].text;
  assert.match(empty,/完整原生工具/);assert.match(empty,/不会自动连接 EasyRead MCP/);
  W.sessions={current:{id:'pi',engine:'pi',state:'idle'},isPending:()=>false};
  W.capabilities.terminal.available=false;W.sessionTerminal.render();
  assert.match(W.$('ws-terminal-note').textContent,/聊天仍可使用完整 Agent 工具/);
});
