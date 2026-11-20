const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { readerInputRuntime } = require('./reader_input_runtime.cjs');

function setup(options) {
  const PR = {
    state: { reader: { progress: { block: 'p1', off: 0 } }, paper: { blocks: [] } },
    store: { mode: 'server' }, blockById: {}, throttle: fn => fn,
    readingLine: () => 240, t: s => s, toast() {},
  };
  const r = readerInputRuntime(PR, options);
  let top = 1000, ready;
  const getElement = r.host.document.getElementById;
  r.host.document.getElementById = id => id === 'b-p1' ? { getBoundingClientRect: () => ({ top: top - r.host.scrollY, height: 500 }) } : getElement(id);
  r.host.document.fonts = { ready: new Promise(resolve => { ready = resolve; }) };
  r.paper.querySelector = () => ({}); // Chunked landing schedules a first-frame correction too.
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/progress.js', 'utf8'), r.host);
  return Object.assign(r, {
    shift(value = 1600) { top = value; },
    async settle() {
      r.frame(); ready();
      while (r.timer()) {}
      await new Promise(resolve => setImmediate(resolve));
    }
  });
}

test('progress cancels frame, font and settle corrections on accepted optimized pen/finger before swallowed touchstart', async () => {
  for (const pointerType of ['pen', 'touch']) {
    const r = setup();
    assert.equal(r.PR.restoreProgress(), true);
    assert.ok(r.classes.has('landing'));
    r.shift();
    r.event('pointerdown', { pointerType });
    assert.ok(!r.classes.has('landing'), 'input-start reveals immediately, before a native touchstart can bubble');
    assert.ok(r.event('touchstart').stopped, 'router swallows the native cancellation signal');
    r.event('pointerup', { pointerType, buttons: 0, pressure: 0 });
    await r.settle();
    assert.deepEqual(r.scrolls.map(x => x.top), [760], 'no correction after gesture ends either');
  }
});

test('progress input generation invalidates all older anchors but allows a later deliberate citation restore', async () => {
  const r = setup();
  r.PR.restoreProgress();
  r.PR.restoreCitationProgress({ block: 'p1', off: .2 });
  r.event('pointerdown', { pointerType: 'pen' });
  r.shift();
  const count = r.scrolls.length;
  await r.settle();
  assert.equal(r.scrolls.length, count, 'both older anchor closures are invalidated');
  r.event('pointerup', { pointerType: 'pen' });
  r.PR.restoreCitationProgress({ block: 'p1', off: 0 });
  r.shift(1900);
  await r.settle();
  assert.equal(r.scrolls.at(-1).top, 1660, 'new restore uses the new input generation');
  assert.equal(r.subscriptions.get('ink-input-start').length, 1, 'restores do not accumulate event-bus subscriptions');
  assert.equal((r.listeners.get('touchstart') || []).filter(x => !x.capture).length, 0, 'ordinary listeners still expire after final settle');
});

test('progress rejected palms and ambiguous pen moves are not new input-start signals', async () => {
  const r = setup();
  r.event('pointerdown', { pointerType: 'pen', pointerId: 7 });
  r.PR.restoreCitationProgress({ block: 'p1' });
  r.shift();
  assert.ok(r.event('pointerdown', { pointerType: 'touch', pointerId: 2 }).stopped);
  assert.ok(r.event('pointermove', { pointerType: 'pen', pointerId: 7, buttons: 0, pressure: 0 }).stopped);
  assert.ok(r.classes.has('landing'), 'rejected input does not masquerade as a new contact');
  await r.settle();
  assert.equal(r.scrolls.at(-1).top, 1360);
});

test('progress retains wheel cancellation and exact legacy swallowed-touch behavior for opt-out and non-iPad', async () => {
  for (const options of [{}, { feature: false }, { navigator: {} }, { navigator: { userAgent: 'Android AppleWebKit/537.36 Chrome/124.0', maxTouchPoints: 5 } }]) {
    const r = setup(options); r.PR.restoreProgress(); r.shift();
    assert.ok(!r.event('wheel').stopped);
    await r.settle();
    assert.deepEqual(r.scrolls.map(x => x.top), [760]);
  }
  for (const options of [{ feature: false }, { navigator: {} }, { navigator: { userAgent: 'Android AppleWebKit/537.36 Chrome/124.0', maxTouchPoints: 5 } }]) {
    for (const pointerType of ['pen', 'touch']) {
      const r = setup(options); r.PR.restoreProgress(); r.shift();
      r.event('pointerdown', { pointerType });
      assert.ok(r.event('touchstart').stopped);
      await r.settle();
      assert.equal(r.scrolls.at(-1).top, 1360, 'legacy path still corrects when touchstart is swallowed');
    }
  }
});
