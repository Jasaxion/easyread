const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../easyread/web/js/common/terminal-touch.js'), 'utf8');
function target(extra = {}) {
  const listeners = new Map();
  return Object.assign({ listeners,
    addEventListener(name, fn, options) { if (!listeners.has(name)) listeners.set(name, []); listeners.get(name).push({ fn, options }); },
    removeEventListener(name, fn) { listeners.set(name, (listeners.get(name) || []).filter(item => item.fn !== fn)); },
    dispatch(name, event = {}) { for (const { fn } of listeners.get(name) || []) { fn(event); if (event.stopped) break; } },
    count(name) { return (listeners.get(name) || []).length; },
  }, extra);
}
const point = (x, y, identifier = 7) => ({ clientX: x, clientY: y, identifier });
function fixture(src = '/workspace-terminal/test/') {
  const timers = new Map(), animations = new Map(), wheels = [];
  let next = 0, nativeMoves = 0;
  const element = target({ clientHeight: 480, dispatchEvent: event => wheels.push(event) });
  const forbidden = () => assert.fail('touch scroll must not invoke input, scrollLines, focus, blur, resize, or terminal lifecycle');
  const term = { element, rows: 24, hasSelection: () => false, options: {},
    write: forbidden, input: forbidden, scrollLines: forbidden, focus: forbidden, blur: forbidden, dispose: forbidden, resize: forbidden };
  const runtime = target({ location: { href: new URL(src, 'http://localhost').href }, document: { getSelection: () => ({ isCollapsed: true }) }, term,
    WheelEvent: class { constructor(type, options) { this.type = type; Object.assign(this, options); } },
    requestAnimationFrame(fn) { const id = ++next; animations.set(id, fn); return id; }, cancelAnimationFrame: id => animations.delete(id) });
  const frame = target({ contentWindow: runtime, getAttribute: () => src });
  const PR = {};
  vm.runInNewContext(source, { window: { PR }, URL, location: { href: 'http://localhost/workspaces', origin: 'http://localhost' },
    setTimeout(fn, delay) { const id = ++next; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id) });
  return { T: PR.terminalTouch, frame, runtime, term, element, wheels, animations, timers,
    get nativeMoves() { return nativeMoves; },
    tick() { const entry = timers.entries().next().value; if (!entry) return false; const [id, timer] = entry; timers.delete(id); timer.fn(); return true; },
    paint() { for (const [id, fn] of animations) { animations.delete(id); fn(); } },
    send(name, touches = [], options = {}) {
      const event = { touches, target: { closest: () => null }, timeStamp: 100, cancelable: true, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...options };
      element.dispatch(name, event);
      if (name === 'touchmove' && !event.stopped) nativeMoves++;
      return event;
    },
  };
}

test('terminal-touch: vertical intent dispatches a coalesced pixel wheel at actual coordinates, never scrollLines/input', () => {
  const f = fixture(); f.T.register(f.frame);
  assert.deepEqual(Object.keys(f.T), ['register']);
  assert.equal(f.send('touchstart', [point(51, 200)]).defaultPrevented, false);
  assert.equal(f.send('touchmove', [point(52, 196)]).defaultPrevented, false);
  assert.equal(f.animations.size, 0); assert.equal(f.nativeMoves, 1);
  const move = f.send('touchmove', [point(53, 180)]);
  assert.equal(move.defaultPrevented, true); assert.equal(move.stopped, true);
  f.send('touchmove', [point(54, 165)]);
  assert.equal(f.animations.size, 1); assert.equal(f.wheels.length, 0); f.paint();
  assert.equal(f.wheels.length, 1); assert.equal(f.nativeMoves, 1, 'claimed moves cannot reach xterm native touchmove twice');
  assert.deepEqual({ ...f.wheels[0] }, { type: 'wheel', deltaY: 31, deltaX: 0, deltaZ: 0, deltaMode: 0,
    clientX: 54, clientY: 165, bubbles: true, cancelable: true });
  assert.equal(f.element.listeners.get('touchmove')[0].options.capture, true);
  assert.equal(f.element.listeners.get('touchmove')[0].options.passive, false);
  assert.equal(f.element.count('wheel'), 0, 'real mouse/trackpad wheel remains untouched');
  f.send('touchmove', [point(54, 190)]); f.paint(); assert.equal(f.wheels.at(-1).deltaY, -25);
});

test('terminal-touch: dispatch is bounded per animation frame and release flushes once with no inertia', () => {
  const f = fixture(); f.T.register(f.frame); f.send('touchstart', [point(70, 500)]);
  for (let y = 470; y >= 50; y -= 30) f.send('touchmove', [point(70, y)]);
  assert.equal(f.animations.size, 1); f.paint();
  assert.equal(f.wheels.length, 1); assert.equal(f.wheels[0].deltaY, 60, 'at most three public rows per frame');
  f.send('touchmove', [point(71, 32)]);
  assert.equal(f.send('touchend').defaultPrevented, false);
  assert.equal(f.wheels.length, 2); assert.equal(f.wheels[1].clientX, 71); assert.equal(f.wheels[1].clientY, 32);
  assert.equal(f.wheels[1].deltaY, 18); assert.equal(f.animations.size, 0); assert.equal(f.timers.size, 0);
  f.paint(); f.send('touchmove', [point(70, 0)]); assert.equal(f.wheels.length, 2);
});

test('terminal-touch: taps, long presses, horizontal/ambiguous drags, links and selection retain native behavior', () => {
  for (const mode of ['tap', 'hold', 'horizontal', 'diagonal', 'link', 'selection', 'document-selection', 'stylus']) {
    const f = fixture(); f.T.register(f.frame);
    if (mode === 'selection') f.term.hasSelection = () => true;
    if (mode === 'document-selection') f.runtime.document.getSelection = () => ({ isCollapsed: false });
    const options = mode === 'link' ? { target: { closest: () => ({}) } } : {};
    const p = point(50, 200); if (mode === 'stylus') p.touchType = 'stylus';
    f.send('touchstart', [p], options);
    const next = mode === 'horizontal' ? point(100, 198) : mode === 'diagonal' ? point(80, 170) : mode === 'tap' ? point(51, 198) : point(52, 150);
    const move = f.send('touchmove', [next], { timeStamp: mode === 'hold' ? 451 : 120 });
    assert.equal(move.defaultPrevented, false, mode); assert.equal(move.stopped, undefined, mode);
    f.send('touchend'); f.paint(); assert.equal(f.wheels.length, 0, mode);
  }
});

test('terminal-touch: pinch, identifier changes, cancellation, composition and lost cancelability drop queued input', () => {
  for (const mode of ['pinch', 'identifier', 'cancel', 'composition', 'contextmenu', 'pagehide', 'selection', 'uncancelable', 'prevented', 'invalid']) {
    const f = fixture(); f.T.register(f.frame);
    f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 175)]);
    assert.equal(f.animations.size, 1);
    if (mode === 'pinch') f.send('touchstart', [point(50, 175), point(90, 175, 8)]);
    if (mode === 'identifier') f.send('touchmove', [point(50, 150, 8)]);
    if (mode === 'cancel') f.send('touchcancel');
    if (mode === 'composition') f.send('compositionstart');
    if (mode === 'contextmenu') f.send('contextmenu');
    if (mode === 'pagehide') f.runtime.dispatch('pagehide');
    if (mode === 'selection') f.term.hasSelection = () => true;
    if (mode === 'uncancelable') f.send('touchmove', [point(50, 150)], { cancelable: false });
    if (mode === 'prevented') f.send('touchmove', [point(50, 150)], { defaultPrevented: true });
    if (mode === 'invalid') f.send('touchmove', [point(NaN, 150)]);
    f.paint(); f.send('touchend'); assert.equal(f.wheels.length, 0, mode); assert.equal(f.animations.size, 0, mode);
  }
  const f = fixture(); f.T.register(f.frame); f.send('compositionstart');
  f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 170)]); f.paint(); assert.equal(f.wheels.length, 0);
  f.send('compositionend'); f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 170)]); f.paint(); assert.equal(f.wheels.length, 1);
});

test('terminal-touch: claimed gestures never resume stale native touch scrolling after selection or pinch', () => {
  for (const mode of ['selection', 'composition', 'pinch']) {
    const f = fixture(); f.T.register(f.frame);
    f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 250)]); f.paint();
    const wheels = f.wheels.length, native = f.nativeMoves;
    f.send('touchmove', [point(50, 270)]);
    if (mode === 'selection') f.term.hasSelection = () => true;
    if (mode === 'composition') f.send('compositionstart');
    if (mode === 'pinch') f.send('touchstart', [point(50, 270), point(80, 290, 8)]);
    const move = f.send('touchmove', mode === 'pinch' ? [point(50, 300), point(80, 320, 8)] : [point(50, 300)]);
    assert.equal(move.stopped, true, mode);
    assert.equal(move.defaultPrevented, false, 'browser selection/pinch remains available');
    f.paint(); assert.equal(f.wheels.length, wheels); assert.equal(f.nativeMoves, native);
    f.send('touchend'); f.term.hasSelection = () => false; f.send('compositionend');
    f.send('touchstart', [point(50, 100)]);
    const pending = f.send('touchmove', [point(50, 104)]);
    assert.equal(pending.stopped, undefined, 'fresh gestures rebase native touch normally');
  }
});

test('terminal-touch: initialization, duplicate registration, navigation and disposal clean all timers/listeners', () => {
  const f = fixture(); f.runtime.term = null;
  const release = f.T.register(f.frame); assert.equal(f.T.register(f.frame), release);
  assert.equal(f.frame.count('load'), 1); assert.equal(f.timers.size, 1);
  f.runtime.term = f.term; f.tick(); assert.equal(f.timers.size, 0); assert.equal(f.element.count('touchstart'), 1);
  f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 170)]);
  f.frame.dispatch('load'); assert.equal(f.animations.size, 0); assert.equal(f.element.count('touchstart'), 1);
  f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 170)]);
  release(); release(); f.paint(); assert.equal(f.wheels.length, 0);
  assert.equal(f.timers.size, 0); assert.equal(f.frame.count('load'), 0); assert.equal(f.runtime.count('pagehide'), 0);
  for (const listeners of f.element.listeners.values()) assert.equal(listeners.length, 0);
  const next = f.T.register(f.frame); release(); assert.equal(f.element.count('touchstart'), 1); next();
});

test('terminal-touch: unavailable/old runtimes fail gracefully with bounded startup retries', () => {
  for (const mode of ['term', 'element', 'constructor', 'old-constructor', 'raf', 'inaccessible', 'redirect']) {
    const f = fixture();
    if (mode === 'term') f.runtime.term = null;
    if (mode === 'element') f.term.element = null;
    if (mode === 'constructor') f.runtime.WheelEvent = undefined;
    if (mode === 'old-constructor') f.runtime.WheelEvent = class { constructor() { throw new Error('unsupported'); } };
    if (mode === 'raf') f.runtime.requestAnimationFrame = undefined;
    if (mode === 'inaccessible') Object.defineProperty(f.frame, 'contentWindow', { get() { throw new Error('SecurityError'); } });
    if (mode === 'redirect') f.runtime.location.href = 'http://localhost/read/paper';
    const release = f.T.register(f.frame); let attempts = 0;
    while (f.tick()) assert.ok(++attempts < 100);
    assert.equal(attempts, 99); assert.equal(f.element.count('touchstart'), 0); release();
  }
});

test('terminal-touch: only explicitly registered owned same-origin terminal frames are accessed', () => {
  for (const src of ['http://elsewhere/terminal/a/', '/read/paper', '/terminal/a/?query', '/terminal/a/#hash',
    'http://user@localhost/terminal/a/', '/workspace-terminal/a/extra', '/terminal/', 'about:blank']) {
    const f = fixture(src);
    Object.defineProperty(f.frame, 'contentWindow', { get() { assert.fail('must not access disallowed frames'); } });
    const release = f.T.register(f.frame); assert.equal(f.frame.count('load'), 0); assert.equal(f.timers.size, 0); release();
  }
  const f = fixture('/terminal/local/'); assert.equal(f.element.count('touchstart'), 0);
  f.T.register(f.frame); assert.equal(f.element.count('touchstart'), 1);
});

test('terminal-touch: cross-origin navigation and failed old-document cleanup cannot abort owner teardown', () => {
  for (const mode of ['load', 'dispose', 'element-cleanup-error']) {
    const f = fixture(), release = f.T.register(f.frame);
    f.send('touchstart', [point(50, 200)]); f.send('touchmove', [point(50, 170)]);
    assert.equal(f.animations.size, 1);
    const cancelAnimationFrame = f.runtime.cancelAnimationFrame, removeEventListener = f.runtime.removeEventListener;
    const inaccessible = { configurable: true, get() { throw new Error('SecurityError: cross-origin WindowProxy'); } };
    Object.defineProperty(f.runtime, 'cancelAnimationFrame', inaccessible);
    Object.defineProperty(f.runtime, 'removeEventListener', inaccessible);
    f.runtime.location.href = 'http://other-origin/';
    if (mode === 'load') assert.doesNotThrow(() => f.frame.dispatch('load'));
    if (mode === 'element-cleanup-error') f.element.removeEventListener = () => { throw new Error('old document unavailable'); };
    let removed = false;
    assert.doesNotThrow(() => { release(); removed = true; });
    assert.equal(removed, true, 'owner teardown after release must run');
    assert.doesNotThrow(release); assert.equal(f.frame.count('load'), 0); assert.equal(f.timers.size, 0);
    f.paint(); assert.equal(f.wheels.length, 0, 'uncancelable stale animation cannot send terminal input');
    if (mode !== 'element-cleanup-error') for (const listeners of f.element.listeners.values()) assert.equal(listeners.length, 0);
    Object.defineProperty(f.runtime, 'cancelAnimationFrame', { value: cancelAnimationFrame, configurable: true });
    Object.defineProperty(f.runtime, 'removeEventListener', { value: removeEventListener, configurable: true });
    f.runtime.location.href = 'http://localhost/workspace-terminal/test/';
    const next = f.T.register(f.frame);
    assert.notEqual(next, release, 'failed old-runtime cleanup cannot retain the registration');
    next();
  }
});
