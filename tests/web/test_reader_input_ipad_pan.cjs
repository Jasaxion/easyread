const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createReaderInput } = require('../../easyread/web/js/reader/reader-input.js');

const ipadUA = 'Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
const macUA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15';
const ipad = { userAgent: ipadUA, platform: 'iPad', maxTouchPoints: 5 };

function setup({ navigator = ipad, scale = 1, scrollY = 100, feature } = {}) {
  const listeners = {}, pending = new Map(), callbacks = new Map(), captures = new Set();
  const scrolls = [], legacyScrolls = [], emitted = [], released = [], classes = new Set();
  let nextFrame = 0;
  const layer = {
    closest: selector => selector === '#inkLayer' ? layer : null,
    addEventListener() {},
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture: id => captures.has(id),
    releasePointerCapture(id) {
      captures.delete(id); released.push(id);
      event('lostpointercapture', id);
    }
  };
  const doc = {
    hidden: false,
    documentElement: { classList: { toggle(c, on) { on ? classes.add(c) : classes.delete(c); } } },
    getElementById: id => id === 'inkLayer' ? layer : null,
    addEventListener(type, fn) { listeners[type] = fn; }
  };
  const host = {
    document: doc, navigator, visualViewport: { scale }, scrollY, scrollX: 25,
    addEventListener(type, fn) { listeners[type] = fn; },
    getSelection: () => ({ removeAllRanges() {} }),
    scrollTo(options) { scrolls.push(options); host.scrollY = options.top; },
    scrollBy(x, y) { legacyScrolls.push([x, y]); host.scrollY += y; },
    requestAnimationFrame(fn) { const id = ++nextFrame; pending.set(id, fn); callbacks.set(id, fn); return id; },
    cancelAnimationFrame(id) { pending.delete(id); }
  };
  const PR = { emit(name) { emitted.push({ name, panning: input.panning(), scrollY: host.scrollY }); } };
  if (feature) PR.feature = feature;
  const input = createReaderInput(host, PR);
  function event(type, pointerId = 2, extra = {}) {
    const e = { type, pointerType: 'touch', pointerId, clientY: 300, clientX: 20, target: layer,
      cancelable: true, detail: 1, preventDefault() { this.prevented = true; },
      stopImmediatePropagation() { this.stopped = true; }, ...extra };
    listeners[type]?.(e);
    return e;
  }
  function frame() {
    const batch = [...pending.values()]; pending.clear();
    for (const fn of batch) fn();
  }
  input.configure(true, 'pen');
  return { input, event, frame, pending, callbacks, scrolls, legacyScrolls, emitted, released, captures, classes, doc, host, PR };
}

const move = (s, y, extra = {}) => s.event('pointermove', 2, { screenY: y, clientY: y, ...extra });
const down = (s, y = 300) => s.event('pointerdown', 2, { screenY: y, clientY: y });

test('reader-input iPad Safari detection includes desktop-site and installed PWA UAs only on iPad WebKit', () => {
  const cases = [
    ['iPad Safari', ipad, true],
    ['iPad standalone without Safari token', { ...ipad, userAgent: ipadUA.replace(/ Version\/.*$/, ' Mobile/15E148') }, true],
    ['desktop-site iPad Safari', { userAgent: macUA, platform: 'MacIntel', maxTouchPoints: 5 }, true],
    ['desktop-site iPad standalone', { userAgent: macUA.replace(/ Version\/.*$/, ''), platform: 'MacIntel', maxTouchPoints: 2 }, true],
    ['desktop Safari', { userAgent: macUA, platform: 'MacIntel', maxTouchPoints: 0 }, false],
    ['single-touch Mac', { userAgent: macUA, platform: 'MacIntel', maxTouchPoints: 1 }, false],
    ['iPhone', { userAgent: ipadUA.replaceAll('iPad', 'iPhone'), platform: 'iPhone', maxTouchPoints: 5 }, false],
    ['unknown touch platform', { userAgent: macUA, platform: 'Linux', maxTouchPoints: 5 }, false],
    ['iPad non-WebKit', { ...ipad, userAgent: 'Mozilla/5.0 (iPad) Gecko/20100101' }, false],
    ['no navigator', null, false],
    ['empty navigator', {}, false]
  ];
  for (const token of ['Chrome/124.0', 'Chromium/124.0', 'CriOS/124.0', 'Edg/124.0', 'EdgiOS/124.0', 'OPR/80.0', 'FxiOS/125.0', 'Android 14']) {
    cases.push([token, { ...ipad, userAgent: ipadUA + ' ' + token }, false]);
    cases.push(['MacIntel ' + token, { userAgent: macUA + ' ' + token, platform: 'MacIntel', maxTouchPoints: 5 }, false]);
  }
  for (const [label, navigator, expected] of cases) assert.equal(setup({ navigator }).input.ipadSafari, expected, label);
});

test('reader-input iPad pan accumulates initial displacement and scrolls at most once per frame', () => {
  const s = setup({ scrollY: 100 });
  assert.equal(s.input.panning(), false);
  assert.ok(down(s).stopped);
  assert.equal(s.input.panning(), true);
  assert.ok(s.captures.has(2));
  move(s, 297); move(s, 295);
  assert.equal(s.pending.size, 0, 'same five-pixel drag threshold');
  move(s, 292); move(s, 270); move(s, 240);
  assert.equal(s.pending.size, 1);
  assert.deepEqual(s.scrolls, []);
  s.frame();
  assert.deepEqual(s.scrolls, [{ top: 160, behavior: 'instant' }], 'includes movement before crossing threshold');
  move(s, 220); move(s, 200);
  assert.equal(s.pending.size, 1);
  s.frame(); s.frame();
  assert.deepEqual(s.scrolls, [{ top: 160, behavior: 'instant' }, { top: 200, behavior: 'instant' }]);
  assert.deepEqual(s.legacyScrolls, []);
  s.event('pointerup');
  s.frame();
  assert.equal(s.scrolls.length, 2, 'up after a committed frame does not replay or add inertia');
  assert.equal(s.input.panning(), false);
  assert.deepEqual(s.released, [2]);
  assert.deepEqual(s.emitted, [{ name: 'ink-pan-end', panning: false, scrollY: 200 }]);
  assert.ok(s.event('click').stopped, 'compatibility click stays suppressed');
});

test('reader-input iPad pan uses stable screen coordinates and initial visual viewport scale', () => {
  const s = setup({ scale: 2, scrollY: 250 }); down(s, 600);
  move(s, 520, { clientY: 480 }); s.frame();
  move(s, 520, { clientY: 300 }); s.frame();
  assert.ok(s.scrolls.every(x => x.top === 290), 'moving Safari viewport does not accumulate client-coordinate drift');
  s.host.visualViewport.scale = 3;
  move(s, 480, { clientY: 120 }); s.frame();
  assert.equal(s.scrolls.at(-1).top, 310, 'gesture keeps a single coordinate/scale baseline');
  assert.equal(s.host.scrollX, 25, 'vertical navigation leaves horizontal offset alone');
});

test('reader-input iPad pan accepts zero screenY and falls back to clientY only when screen coordinates are unavailable at start', () => {
  const zero = setup(); down(zero, 0); move(zero, -20); zero.frame();
  assert.equal(zero.scrolls[0].top, 120);
  for (const screenY of [undefined, null, NaN, Infinity]) {
    const s = setup({ scale: 2 });
    s.event('pointerdown', 2, { screenY, clientY: 300 });
    s.event('pointermove', 2, { screenY: 900, clientY: 280 }); s.frame();
    assert.equal(s.scrolls[0].top, 120, 'client-coordinate fallback is not divided by viewport scale or switched mid-gesture');
  }
  for (const scale of [undefined, null, NaN, Infinity, 0, -1]) {
    const s = setup(); s.host.visualViewport.scale = scale;
    down(s); move(s, 280); s.frame();
    assert.equal(s.scrolls[0].top, 120, 'invalid scale defaults to 1');
  }
  const noViewport = setup(); delete noViewport.host.visualViewport;
  down(noViewport); move(noViewport, 280); noViewport.frame();
  assert.equal(noViewport.scrolls[0].top, 120);
});

test('reader-input iPad falls back when screen coordinates are zero placeholders', () => {
  const s=setup({scale:2});
  s.event('pointerdown',2,{screenY:0,clientY:300});
  s.event('pointermove',2,{screenY:0,clientY:250});s.frame();
  assert.equal(s.scrolls[0].top,150);
});

test('reader-input iPad boundary overshoot does not create a reversal dead zone', () => {
  const bottom=setup({scrollY:1450});Object.assign(bottom.doc.documentElement,{scrollHeight:2000,clientHeight:500});
  down(bottom,300);move(bottom,150);bottom.frame();assert.equal(bottom.scrolls.at(-1).top,1500);
  move(bottom,160);bottom.frame();assert.equal(bottom.scrolls.at(-1).top,1490);
  const top=setup({scrollY:10});Object.assign(top.doc.documentElement,{scrollHeight:2000,clientHeight:500});
  down(top,300);move(top,400);top.frame();assert.equal(top.scrolls.at(-1).top,0);
  move(top,390);top.frame();assert.equal(top.scrolls.at(-1).top,10);
});

test('older WebKit scroll behavior fallback stays immediate and restores page CSS', () => {
  const s=setup();let value='smooth',priority='important',unsupported=0;
  s.doc.documentElement.style={getPropertyValue:()=>value,getPropertyPriority:()=>priority,
    setProperty(_k,v,p){value=v;priority=p;},removeProperty(){value='';priority='';}};
  s.host.scrollTo=options=>{
    if(options.behavior==='instant'){unsupported++;throw new TypeError('unsupported enum');}
    assert.equal(value,'auto');assert.equal(priority,'important');s.scrolls.push(options);s.host.scrollY=options.top;
  };
  down(s);move(s,260);s.frame();move(s,240);s.frame();
  assert.equal(unsupported,1);assert.equal(s.scrolls.at(-1).top,160);
  assert.equal(value,'smooth');assert.equal(priority,'important');
  const legacy=setup();delete legacy.host.requestAnimationFrame;
  down(legacy);move(legacy,260);assert.deepEqual(legacy.legacyScrolls,[[0,40]]);
});

test('reader-input iPad normal finger up flushes the last accepted move, never release or invalid coordinates', () => {
  const s = setup(); down(s); move(s, 260);
  const stale = [...s.callbacks.values()][0];
  move(s, NaN, { clientY: 0 });
  s.event('pointerup', 2, { screenY: 0, clientY: 0 });
  assert.equal(s.pending.size, 0);
  assert.deepEqual(s.scrolls, [{ top: 140, behavior: 'instant' }]);
  stale(); s.frame();
  assert.equal(s.scrolls.length, 1);
  assert.deepEqual(s.emitted, [{ name: 'ink-pan-end', panning: false, scrollY: 140 }]);
  const tap = setup(); down(tap); move(tap, 298); tap.event('pointerup');
  assert.deepEqual(tap.scrolls, [], 'tap does not scroll');
  assert.equal(tap.emitted.length, 1);
});

test('reader-input iPad cancellation and lifecycle resets discard pending frames and emit one end', () => {
  for (const ending of ['pointercancel', 'lostpointercapture', 'blur', 'visibilitychange', 'reset', 'panel', 'mode']) {
    const s = setup(); down(s); move(s, 280); s.frame();
    move(s, 240);
    const stale = [...s.callbacks.values()];
    if (ending === 'reset') s.input.reset();
    else if (ending === 'panel') s.input.configure(false, 'pen');
    else if (ending === 'mode') s.input.configure(true, 'mouse');
    else if (ending === 'visibilitychange') { s.doc.hidden = true; s.event(ending); }
    else s.event(ending);
    assert.equal(s.input.panning(), false, ending);
    assert.equal(s.pending.size, 0, ending);
    for (const fn of stale) fn();
    s.frame(); s.event('pointermove', 2, { screenY: 0, clientY: 0 }); s.event('pointerup');
    assert.deepEqual(s.scrolls, [{ top: 120, behavior: 'instant' }], ending);
    assert.deepEqual(s.emitted, [{ name: 'ink-pan-end', panning: false, scrollY: 120 }], ending);
    assert.deepEqual(s.released, [2], ending);
    s.input.reset();
    assert.equal(s.emitted.length, 1, 'repeated reset cannot re-emit an old end');
  }
});

test('reader-input iPad stale callbacks cannot scroll or clear a new gesture frame, including pointer ID reuse', () => {
  for (const ending of ['pointerup', 'pointercancel', 'new-down']) {
    const s = setup(); down(s); move(s, 260);
    const stale = [...s.callbacks.values()][0];
    if (ending !== 'new-down') s.event(ending);
    down(s, 500); move(s, 470);
    assert.equal(s.pending.size, 1);
    const count = s.scrolls.length;
    stale();
    assert.equal(s.scrolls.length, count, ending);
    move(s, 460);
    assert.equal(s.pending.size, 1, 'stale frame cannot clear a new frame handle');
    s.frame();
    assert.equal(s.scrolls.at(-1).top, ending === 'pointerup' ? 180 : 140, ending);
    assert.equal(s.emitted.length, 1, ending);
  }
});

test('reader-input iPad finger then Pencil cancels queued pan and quarantines the old finger until release', () => {
  for (const canvas of [true, false]) {
    const s = setup(); down(s); move(s, 280);
    const stale = [...s.callbacks.values()][0];
    const pen = { pointerType: 'pen', target: canvas ? undefined : { closest: () => null } };
    if (canvas) delete pen.target;
    s.event('pointerdown', 7, pen);
    assert.equal(s.input.panning(), false);
    assert.ok(s.classes.has('ink-pen-contact'));
    assert.equal(s.pending.size, 0);
    stale(); s.frame();
    assert.ok(move(s, 250).stopped);
    assert.ok(s.event('wheel').stopped);
    s.event('pointerup', 7, pen);
    assert.ok(move(s, 220).stopped);
    s.event('pointerup', 2);
    assert.deepEqual(s.scrolls, []);
    assert.deepEqual(s.emitted.map(e => e.name), ['ink-pan-end', 'ink-contact-end']);
    assert.ok(s.event('click', 2).stopped);
    down(s); move(s, 260); s.frame();
    assert.deepEqual(s.scrolls, [{ top: 140, behavior: 'instant' }], 'fresh finger can pan immediately after pen-up');
    assert.equal(s.input.acceptsCanvas({ pointerType: 'touch' }), false);
  }
});

test('reader-input iPad pan isolates the owning pointer across other finger/mouse moves and endings', () => {
  const s = setup(); down(s); move(s, 280);
  for (const pointerType of ['touch', 'mouse']) {
    for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'lostpointercapture']) {
      s.event(type, 3, { pointerType, screenY: 0, clientY: 0 });
      assert.equal(s.input.panning(), true, `${pointerType} ${type}`);
      assert.equal(s.pending.size, 1);
    }
  }
  s.frame();
  assert.deepEqual(s.scrolls, [{ top: 120, behavior: 'instant' }]);
  assert.equal(s.emitted.length, 0);
  s.event('pointermove', 2, { screenY: 250, clientY: 100, target: { closest: () => null } });
  s.event('pointerup', 2, { target: { closest: () => null } });
  assert.equal(s.scrolls.at(-1).top, 150, 'owner continues and releases outside the canvas');
  assert.equal(s.emitted.length, 1);
});

test('reader-input ipad_ink feature is checked at gesture start, including late loading and explicit opt-out', () => {
  const s = setup(), checked = [];
  down(s); move(s, 280);
  s.PR.feature = name => { checked.push(name); return false; };
  s.frame(); s.event('pointerup');
  assert.equal(s.scrolls.length, 1, 'late feature change does not switch an active gesture');
  assert.deepEqual(checked, []);
  down(s); move(s, 298); move(s, 280);
  assert.deepEqual(checked, ['ipad_ink']);
  assert.deepEqual(s.legacyScrolls, [[0, 18]], 'opt-out preserves exact incremental legacy behavior');
  assert.equal(s.pending.size, 0);
  s.PR.feature = () => true;
  move(s, 270); s.event('pointerup');
  assert.deepEqual(s.legacyScrolls, [[0, 18], [0, 10]]);
  assert.equal(s.emitted.length, 1, 'legacy pan does not emit optimized-pan end');
  down(s); move(s, 260); s.frame(); s.event('pointerup');
  assert.equal(s.scrolls.length, 2, 'next gesture can opt back in');
  for (const value of [undefined, null, 0, true]) {
    const enabled = setup({ feature: () => value }); down(enabled); move(enabled, 260);
    assert.equal(enabled.pending.size, 1, 'only explicit false opts out');
  }
});

test('reader-input non-iPad/other browsers keep per-event legacy scrolling and never query ipad_ink', () => {
  for (const navigator of [
    {}, { userAgent: macUA, platform: 'MacIntel', maxTouchPoints: 0 },
    { userAgent: 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Safari/537.36', maxTouchPoints: 5 },
    { ...ipad, userAgent: ipadUA + ' CriOS/124.0' },
    { ...ipad, userAgent: ipadUA + ' FxiOS/125.0' }
  ]) {
    const s = setup({ navigator, feature() { assert.fail('legacy platforms must not query feature'); } });
    down(s); move(s, 298); move(s, 280); move(s, 250); s.event('pointerup');
    assert.deepEqual(s.legacyScrolls, [[0, 18], [0, 30]]);
    assert.deepEqual(s.scrolls, []);
    assert.equal(s.pending.size, 0);
    assert.deepEqual(s.emitted, []);
  }
});

test('reader-input iPad optimization stays inside active pen-only canvas finger navigation', () => {
  for (const [active, mode] of [[false, 'pen'], [true, 'mouse']]) {
    const s = setup(); s.input.configure(active, mode);
    assert.ok(!down(s).stopped); move(s, 260); s.frame();
    assert.equal(s.input.panning(), false);
    assert.deepEqual(s.scrolls, []);
  }
  const s = setup();
  assert.ok(!s.event('pointerdown', 2, { target: { closest: () => null } }).stopped);
  assert.equal(s.input.panning(), false);
  assert.ok(!s.event('wheel').stopped);
  down(s);
  for (const type of ['touchstart', 'touchmove', 'touchend', 'selectstart'])
    assert.ok(s.event(type).stopped, type);
  assert.ok(!s.event('click', -1, { pointerType: '', detail: 0 }).stopped, 'keyboard activation remains available');
});
