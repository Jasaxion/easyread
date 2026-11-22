const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('easyread/web/js/common/connection.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (data = {}, status = 200) => ({ok: status === 200, status, redirected: false, json: async () => data});
function fixture() {
  const timers = new Map(), requests = [], events = {}, navigator = {onLine: true};
  let timer = 0;
  const PR = {t: s => s, emit() {}};
  vm.runInNewContext(source, {window: {PR, addEventListener: (name, fn) => {events[name] = fn;}}, navigator,
    AbortController, setTimeout: (fn, ms) => {timers.set(++timer, {fn, ms}); return timer;}, clearTimeout: id => timers.delete(id),
    fetch: (url, options) => new Promise((resolve, reject) => {
      requests.push({url, options, resolve, reject});
      if (options.signal.aborted) reject(options.signal.reason);
      else options.signal.addEventListener('abort', () => reject(options.signal.reason), {once: true});
    })});
  const fire = ms => {const entry = [...timers].find(([,t]) => t.ms === ms); assert.ok(entry, 'expected deadline ' + ms); timers.delete(entry[0]); entry[1].fn();};
  return {PR, requests, timers, fire, events, navigator};
}

test('normal browser reads tolerate more than six seconds and keep a body deadline', async () => {
  const f = fixture(), request = f.PR.connection.json('/versions');
  assert.deepEqual([...f.timers.values()].map(t => t.ms), [30000]);
  f.requests[0].resolve(response({reader: '2'}));
  assert.deepEqual(await request, {reader: '2'});
  assert.equal(f.timers.size, 0); assert.equal(f.PR.connection.offline, false);
  const bodyRequest = f.PR.connection.json('/reader').catch(e => e);
  const signal = f.requests[1].options.signal;
  f.requests[1].resolve({...response(), json: () => new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason)))});
  await tick(); f.fire(30000);
  assert.equal((await bodyRequest).kind, 'timeout');
  assert.equal(f.PR.connection.offline, false, 'received headers prove reachability, a slow body is not offline');
});

test('a timed-out API with a reachable server is retryable, not offline', async () => {
  const f = fixture(), request = f.PR.connection.json('/versions').catch(e => e);
  f.fire(30000); await tick();
  assert.equal(f.requests[1].url, '/healthz');
  f.requests[1].resolve(response());
  const error = await request;
  assert.equal(error.kind, 'timeout'); assert.doesNotMatch(error.message, /aborted/);
  assert.equal(f.PR.connection.offline, false); assert.equal(f.timers.size, 0);
});

test('an older failure or failed health probe cannot override a newer successful read', async () => {
  for (const failBeforeSuccess of [false, true]) {
    const f = fixture(), first = f.PR.connection.json('/old').catch(e => e);
    if (failBeforeSuccess) {f.requests[0].reject(new TypeError('network')); await tick();}
    const second = f.PR.connection.json('/new');
    f.requests.at(-1).resolve(response()); await second;
    if (failBeforeSuccess) f.requests[1].reject(new TypeError('old probe'));
    else f.requests[0].reject(new TypeError('old request'));
    await first;
    assert.equal(f.PR.connection.offline, false);
    assert.equal(f.requests.length, failBeforeSuccess ? 3 : 2);
  }
});

test('failed reads share a bounded probe, detect outage and recover after success', async () => {
  const f = fixture();
  const reads = [f.PR.connection.json('/one').catch(e => e), f.PR.connection.json('/two').catch(e => e)];
  f.requests[0].reject(new TypeError('network')); f.requests[1].reject(new TypeError('network')); await tick();
  assert.equal(f.requests.filter(r => r.url === '/healthz').length, 1);
  f.fire(5000); await Promise.all(reads); assert.equal(f.PR.connection.offline, true);
  const recovered = f.PR.connection.json('/versions'); f.requests.at(-1).resolve(response()); await recovered;
  assert.equal(f.PR.connection.offline, false);
});

test('caller cancellation never triggers a probe or marks offline, including already cancelled signals', async () => {
  for (const early of [false, true]) {
    const f = fixture(), controller = new AbortController();
    if (early) controller.abort();
    const request = f.PR.connection.json('/superseded', {signal: controller.signal}).catch(e => e);
    controller.abort(); const error = await request;
    assert.equal(error.cancelled, true); assert.equal(f.PR.connection.offline, false);
    assert.equal(f.requests.length, 1); assert.equal(f.timers.size, 0);
  }
});

test('HTTP denials, server errors and invalid JSON are never transport outages', async () => {
  for (const status of [401,403,500]) {
    const f = fixture(), request = f.PR.connection.json('/versions').catch(e => e);
    f.requests[0].resolve(response({}, status)); const error = await request;
    assert.equal(error.status, status); assert.equal(error.http, true);
    assert.equal(f.PR.connection.offline, false); assert.equal(f.requests.length, 1);
  }
  const f = fixture(), request = f.PR.connection.json('/versions').catch(e => e);
  f.requests[0].resolve({...response(), json: async () => {throw new SyntaxError('bad data');}});
  assert.equal((await request).kind, 'response'); assert.equal(f.PR.connection.offline, false);
});

test('writes get a deadline but are never replayed by the transport helper', async () => {
  const f = fixture(), saving = f.PR.connection.json('/ops', {method: 'POST', body: '{}'}).catch(e => e);
  f.fire(30000); await tick(); f.requests[1].resolve(response()); await saving;
  assert.equal(f.requests.filter(r => r.options.method === 'POST').length, 1);
});
