const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../../easyread/web/js/workspaces');
const turn = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { resolve, promise }; };

function fixture(fetcher = async () => ({ ok: true, json: async () => ({}) })) {
  const nodes = new Map(), calls = [], events = [];
  const el = (tag, attrs = {}) => Object.assign({ tag, children: [], value: '', disabled: false, isConnected: true,
    listeners: {}, classList: { add() {}, toggle() {} },
    append(...children) { this.children.push(...children); }, replaceChildren(...children) { this.children = children; },
    setAttribute(key, value) { this[key] = value; }, addEventListener(key, fn) { this.listeners[key] = fn; }, focus() {},
  }, attrs);
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, el('div')); return nodes.get(id); },
    createTextNode: text => ({ text }), dispatchEvent: event => events.push(event.type),
  };
  const PR = { t: (s, args) => s.replace(/\{(\w+)\}/g, (_, key) => args[key]), el, icon: () => '', token: 'csrf' };
  const context = vm.createContext({ window: { PR }, document, Event, URLSearchParams, location: {},
    fetch: async (...args) => { calls.push(args); return fetcher(...args); },
  });
  for (const name of ['core.js', 'session-capabilities.js', 'servers.js']) vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context);
  const W = PR.workspaces;
  W.button = (text, onclick) => el('button', { text, onclick });
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  return { W, PR, el, calls, nodes, events, flatten };
}

const server = { id: 'remote-a', name: 'GPU', host: 'gpu.example', workspace: '/research' };

test('remote creation gate validates exact server/engine and never silently falls back to local', async () => {
  const { W, el, flatten } = fixture();
  let servers = [server], checkResult = { ok: true, checks: [{ name: 'git', ok: true }] };
  const requests = [];
  W.request = async (url, body) => { requests.push({ url, body }); return url.endsWith('/check') ? checkResult : { servers }; };
  const form = el('form'), engine = el('select', { value: 'codex' }), submit = el('button');
  const target = W.servers.projectTarget(form, engine, submit); await turn();
  const select = flatten(form).find(n => n.tag === 'select');
  const check = flatten(form).find(n => n.text === '验证所选服务器');
  const reload = flatten(form).find(n => n.text === '刷新服务器列表');
  assert.equal(target.valid(), true); assert.equal(submit.disabled, false, 'local projects remain unchanged');
  select.value = server.id; select.listeners.change();
  assert.equal(target.valid(), false); assert.equal(submit.disabled, true);
  await check.onclick(); assert.equal(target.valid(), true); assert.equal(submit.disabled, false);
  assert.equal(requests.at(-1).body.engine, 'codex');
  engine.value = 'pi'; engine.listeners.change(); assert.equal(submit.disabled, true);
  checkResult = { ok: false, checks: [{ name: 'pi', ok: false, required: true, message: 'missing' }] };
  await check.onclick(); assert.equal(target.valid(), false);
  checkResult = { ok: true }; await check.onclick(); assert.equal(target.valid(), true);
  target.setBusy(true); assert.equal(select.disabled, true); assert.equal(engine.disabled, true); assert.equal(submit.disabled, true);
  target.setBusy(false); servers = []; await reload.onclick();
  assert.equal(select.value, server.id, 'a disappeared target cannot become local');
  assert.equal(target.valid(), false); assert.equal(submit.disabled, true); assert.equal(check.disabled, true);
  select.value = ''; select.listeners.change(); assert.equal(submit.disabled, false, 'explicit local selection is allowed');
});

test('late validation cannot enable another engine; concurrent list refresh does not retarget', async () => {
  const { W, el, flatten } = fixture(); const pending = deferred(); let checks = 0;
  W.request = async url => url.endsWith('/check') ? (++checks, pending.promise) : { servers: [server] };
  const form = el('form'), engine = el('select', { value: 'codex' }), submit = el('button');
  const target = W.servers.projectTarget(form, engine, submit); await turn();
  const select = flatten(form).find(n => n.tag === 'select');
  const check = flatten(form).find(n => n.text === '验证所选服务器');
  select.value = server.id; select.listeners.change();
  const checking = check.onclick(); await check.onclick(); assert.equal(checks, 1);
  engine.value = 'claude'; engine.listeners.change(); pending.resolve({ ok: true }); await checking;
  assert.equal(target.valid(), false); assert.equal(submit.disabled, true);
});

test('ambiguous transport/response failures never replay mutations or report empty success', async () => {
  for (const mode of ['network', 'broken-json', 'bad-gateway']) {
    const { W, calls } = fixture(async () => {
      if (mode === 'network') throw new TypeError('network interrupted');
      return { ok: mode !== 'bad-gateway', status: mode === 'bad-gateway' ? 502 : 200,
        json: async () => { if (mode === 'broken-json') throw new SyntaxError('truncated'); return { error: 'SSH lost', code: 'remote_unavailable' }; } };
    });
    W.project = { id: 'project-a', remote: { server_id: server.id } };
    const body = { message: 'keep draft', images: [{ data: 'image-bytes' }] };
    await assert.rejects(W.api('/sessions/session-a/send', body), error => /状态|重复|重试|重发/.test(error.message));
    assert.equal(calls.length, 1); assert.equal(body.message, 'keep draft'); assert.equal(body.images[0].data, 'image-bytes');
    assert.equal(calls[0][0], '/api/workspaces/project-a/sessions/session-a/send');
    assert.equal(calls[0][1].headers['X-Token'], 'csrf');
    assert.equal(calls[0][1].credentials, 'same-origin');
  }
});

test('explicit disconnect blocks background project requests until explicit reconnect, not local projects', async () => {
  const { W, calls, events } = fixture(); W.project = { id: 'project-a', remote: { server_id: server.id } };
  W.servers.disconnected(server.id);
  await assert.rejects(W.api('/tree'), error => error.code === 'remote_disconnected'); assert.equal(calls.length, 0);
  await W.servers.connect(server.id);
  assert.equal(calls[0][0], '/api/workspaces/servers/remote-a/connect');
  assert.equal(calls[0][1].method, 'POST'); assert.ok(events.includes('easyread:visibility'));
  await W.api('/tree'); assert.equal(calls.length, 2);
  W.servers.disconnected(server.id); W.project = { id: 'local-project' };
  await W.api('/tree'); assert.equal(calls.length, 3);
});

test('server disconnect from another tab is recognized, and GET errors do not claim mutation outcomes', async () => {
  const { W, calls } = fixture(async () => ({ ok: false, status: 409, json: async () => ({ error: 'Disconnected', code: 'remote_disconnected' }) }));
  W.project = { id: 'project-a', remote: { server_id: server.id } };
  await assert.rejects(W.api('/tree'), error => error.code === 'remote_disconnected');
  assert.equal(W.servers.isDisconnected(), true);
  await assert.rejects(W.api('/sessions')); assert.equal(calls.length, 1);
});
