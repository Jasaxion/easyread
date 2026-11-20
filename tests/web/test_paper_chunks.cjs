const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { readerInputRuntime } = require('./reader_input_runtime.cjs');

function setup(options) {
  const PR = {
    state: { reader: {}, discussion: { entries: [] } }, myNotes: () => [], noteBlocks: () => [],
    prepareReadingBlocks() {}, layoutMargin() { layouts++; },
  };
  let layouts = 0, anchorTop = 300;
  const r = readerInputRuntime(PR, options), rendered = [], emitted = [], observers = [];
  PR.on('rendered', () => emitted.push('rendered'));
  const anchor = { isConnected: true, getBoundingClientRect: () => ({ top: anchorTop - r.host.scrollY }) };
  r.paper.getBoundingClientRect = () => ({ left: 0, width: 600 });
  r.host.document.elementFromPoint = () => ({ closest: () => anchor });
  r.host.document.createElement = type => {
    assert.equal(type, 'template');
    return { content: { children: [{ outerHTML: '<p>chunk</p>' }] } };
  };
  r.host.IntersectionObserver = class {
    constructor(fn) { this.fn = fn; observers.push(this); }
    observe() {} unobserve() {} disconnect() { this.disconnected = true; }
  };
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/paper-chunks.js', 'utf8'), r.host);
  function open() {
    const result = PR.openPaperChunks(Array.from({ length: 120 }, (_, i) => ({ id: 'p' + i })), (start, end) => {
      rendered.push(start / 12); return `<p>${start}-${end}</p>`;
    });
    const markers = [...result.html.matchAll(/data-paper-chunk="(\d+)"/g)].map(m => ({
      dataset: { paperChunk: m[1] }, replaceWith() { anchorTop += 12; }
    }));
    r.paper.querySelectorAll = () => markers;
    result.start();
    return { markers, result, observer: observers.at(-1) };
  }
  const job = open(); rendered.length = 0;
  return Object.assign(r, {
    rendered, emitted, open, job, layouts: () => layouts,
    visible(ids) { job.observer.fn(ids.map(id => ({ isIntersecting: true, target: job.markers.find(m => +m.dataset.paperChunk === id) }))); },
    startTimer() { r.frame(); r.frame(); },
    drain() { let steps = 0; while (r.timer()) assert.ok(++steps < 100, 'chunk timers must finish'); }
  });
}

const pen = { pointerType: 'pen', pointerId: 7 };

test('paper-chunks parks observer, timer and explicit fills during optimized finger or pen; corrects only when idle', () => {
  for (const contact of [{ pointerType: 'touch', pointerId: 2 }, pen]) {
    const r = setup(); r.startTimer();
    assert.equal(r.timers.size, 1);
    r.event('pointerdown', contact);
    assert.equal(r.PR.readerInput.ipadBusy(), true);
    r.timer(36); // The already queued background fill must park, not re-poll.
    for (let i = 0; i < 100; i++) { r.visible([3, 4]); r.PR.finishPaperChunks(); }
    assert.equal(r.timers.size, 0, 'held contact creates no timer spin');
    assert.deepEqual(r.rendered, []);
    assert.deepEqual(r.scrolls, [], 'neither spacer replacement nor correction may run');
    r.event('pointerup', contact);
    assert.equal(r.timers.size, 1, 'physical end re-arms exactly one existing 36ms timer');
    assert.deepEqual(r.rendered, [], 'do not render synchronously inside pointerup');
    r.drain();
    assert.deepEqual([...r.rendered].sort((a, b) => a - b), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
    assert.deepEqual(r.scrolls, [{ kind: 'by', top: 108 }]);
    assert.deepEqual(r.emitted, ['rendered']);
    assert.equal(r.layouts(), 1);
    assert.equal(r.timers.size, 0);
    assert.equal(r.job.observer.disconnected, true);
  }
});

test('paper-chunks resumes after all optimized end/reset paths, including stale pen recovery into opt-out', () => {
  for (const contact of [{ pointerType: 'touch', pointerId: 2 }, pen]) {
    const ends = contact.pointerType === 'pen' ? ['pointercancel', 'reset', 'blur', 'mode', 'stylus-end', 'opt-out-recovery'] : ['pointercancel', 'reset', 'blur', 'mode', 'lostpointercapture'];
    for (const ending of ends) {
      const r = setup(); r.event('pointerdown', contact); r.startTimer();
      r.visible([3]);
      assert.equal(r.timers.size, 0, 'can also start chunk processing while already busy');
      if (ending === 'reset') r.PR.readerInput.reset();
      else if (ending === 'mode') r.PR.readerInput.configure(true, 'mouse');
      else if (ending === 'stylus-end') {
        const changedTouches = [{ identifier: 900, touchType: 'stylus' }];
        r.event('touchstart', { changedTouches }); r.event('touchend', { changedTouches });
      } else if (ending === 'opt-out-recovery') {
        r.PR.feature = () => false;
        r.event('pointerdown', { pointerType: 'pen', pointerId: 8, isPrimary: true });
        assert.equal(r.PR.readerInput.penContact(), true, 'only optimized work is unblocked');
      } else r.event(ending, contact);
      assert.equal(r.PR.readerInput.ipadBusy(), false, ending);
      assert.equal(r.timers.size, 1, ending);
      r.drain();
      assert.equal(r.rendered[0], 3, 'pending visible chunks precede background continuation');
      assert.equal(r.rendered.length, 9, ending);
      assert.equal(r.timers.size, 0);
    }
  }
});

test('paper-chunks waits across finger-to-Pencil takeover, false hover and capture loss, then rechecks rapid new contact', () => {
  const r = setup(); r.startTimer(); r.event('pointerdown'); r.timer();
  r.event('pointerdown', pen); // Cancels finger pan and emits pan-end while pen is already busy.
  assert.equal(r.timers.size, 0);
  r.event('lostpointercapture', pen);
  r.event('pointermove', { ...pen, buttons: 0, pressure: 0 });
  assert.equal(r.timers.size, 0);
  r.event('pointerup', pen);
  assert.equal(r.timers.size, 1);
  r.event('pointerdown', { ...pen, pointerId: 8 });
  r.timer();
  assert.equal(r.timers.size, 0, 'resumed callback rechecks busy rather than rendering into the next stroke');
  assert.deepEqual(r.rendered, []);
  r.event('pointerup', { ...pen, pointerId: 8 });
  r.drain();
  assert.equal(r.rendered.length, 9);
});

test('paper-chunks cancels parked/stale jobs without revival or duplicate timers', () => {
  const r = setup(); r.startTimer();
  const stale = [...r.timers.values()][0].fn;
  r.event('pointerdown', pen); r.visible([2]);
  r.PR.cancelPaperChunks();
  assert.equal(r.job.observer.disconnected, true);
  r.open(); r.rendered.length = 0;
  r.startTimer();
  r.event('pointerup', pen);
  r.PR.emit('ink-contact-end'); r.PR.emit('ink-pan-end');
  assert.equal(r.timers.size, 1);
  stale();
  assert.deepEqual(r.rendered, []);
  r.drain();
  assert.equal(r.rendered.length, 9);
  assert.equal(r.timers.size, 0);
});

test('paper-chunks preserves immediate legacy/opt-out fills and corrections even with pen or finger down', () => {
  for (const options of [{ feature: false }, { navigator: {} }, { navigator: { userAgent: 'Android AppleWebKit/537.36 Chrome/124.0', maxTouchPoints: 5 } }]) {
    for (const contact of [{ pointerType: 'touch', pointerId: 2 }, pen]) {
      const r = setup(options); r.startTimer(); r.event('pointerdown', contact);
      r.visible([3]);
      assert.deepEqual(r.rendered, [3]);
      assert.deepEqual(r.scrolls, [{ kind: 'by', top: 12 }]);
      r.timer(36);
      assert.deepEqual(r.rendered, [3, 1]);
      r.PR.finishPaperChunks();
      assert.equal(r.rendered.length, 9);
      assert.equal(r.timers.size, 0);
    }
  }
});
