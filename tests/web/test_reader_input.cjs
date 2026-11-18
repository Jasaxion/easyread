const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createReaderInput } = require('../../easyread/web/js/reader/reader-input.js');

function setup() {
  const listeners = {}, layerListeners = {}, classes = new Set();
  let cleared = 0, hidden = 0;
  const ended = [], scrolls = [];
  const layer = { addEventListener(type, fn) { layerListeners[type] = fn; } };
  const doc = {
    documentElement: { classList: { toggle(c, on) { on ? classes.add(c) : classes.delete(c); } } },
    getElementById(id) { return id === 'inkLayer' ? layer : null; },
    addEventListener(type, fn) { listeners[type] = fn; }
  };
  const host = { document: doc, scrollBy: (x, y) => scrolls.push([x, y]), getSelection: () => ({ removeAllRanges() { cleared++; } }), addEventListener(type, fn) { listeners[type] = fn; } };
  const input = createReaderInput(host, { hideBlockbar() { hidden++; }, finishInkGesture(id) { ended.push(id); } });
  function event(type, pointerType = '', canvas = false, pointerId = 1, extra = {}) {
    const e = { type, pointerType, pointerId, detail: 1, cancelable: true,
      target: { closest: () => canvas ? layer : null },
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; }, stopPropagation() { this.bubbled = false; }, ...extra };
    listeners[type]?.(e);
    if (canvas && !e.stopped) layerListeners[type]?.(e);
    return e;
  }
  return { input, event, classes, doc, ended, scrolls, selectionCleared: () => cleared > 0 && hidden > 0 };
}

test('reader-input gates canvas by explicit mode, never pressure/isPrimary or unknown pointer types', () => {
  const { input, event } = setup();
  for (const type of ['mouse', 'touch']) assert.equal(input.acceptsCanvas({ pointerType: type }), true);
  for (const type of ['pen', '', undefined]) assert.equal(input.acceptsCanvas({ pointerType: type }), false);
  input.configure(true, 'pen');
  for (const type of ['mouse', 'touch', '', undefined]) assert.equal(input.acceptsCanvas({ pointerType: type, pressure: 1, isPrimary: true }), false);
  assert.equal(input.acceptsCanvas({ pointerType: 'pen', pressure: 0, isPrimary: false }), true);
  assert.ok(event('pointerdown', 'mouse', true).stopped);
  event('pointerup', 'mouse', true);
  assert.ok(event('pointerdown', 'touch', true, 2).stopped);
  event('pointerup', 'touch', true, 2);
  assert.ok(!event('pointerdown', 'mouse', false, 3).stopped, 'mouse toolbar remains usable');
  input.configure(false, 'pen');
  assert.ok(!event('pointerdown', 'touch', true, 4).stopped, 'remembered mode must not disable standalone stickers');
});

test('pen-only finger drag scrolls without being accepted as ink, including immediately after pen-up', () => {
  const {input,event,scrolls} = setup(); input.configure(true,'pen');
  event('pointerdown','pen',true,1); event('pointerup','pen',true,1);
  event('pointerdown','touch',true,2,{clientY:300});
  event('pointermove','touch',true,2,{clientY:240});
  event('pointerup','touch',true,2,{clientY:240});
  assert.deepEqual(scrolls,[[0,60]]);
  assert.equal(input.acceptsCanvas({pointerType:'touch'}),false);
  assert.ok(event('click','touch',true,2).stopped);
});

test('pen contact cancels an existing finger drag and a palm cannot resume it', () => {
  const {input,event,scrolls} = setup(); input.configure(true,'pen');
  event('pointerdown','touch',true,2,{clientY:300});
  event('pointermove','touch',true,2,{clientY:280});
  event('pointerdown','pen',true,1);
  event('pointermove','touch',true,2,{clientY:200});
  event('pointerup','pen',true,1);
  event('pointermove','touch',true,2,{clientY:160});
  event('pointerup','touch',true,2,{clientY:160});
  assert.deepEqual(scrolls,[[0,20]]);
  event('pointerdown','touch',true,3,{clientY:300});
  event('pointermove','touch',true,3,{clientY:260});
  assert.deepEqual(scrolls,[[0,20],[0,40]]);
  input.configure(false,'pen');
  event('pointermove','touch',true,3,{clientY:220});
  assert.deepEqual(scrolls,[[0,20],[0,40]],'tool change ends the drag');
});

test('pen-only mode keeps wheel and trackpad scrolling except during actual pen contact', () => {
  const {input,event} = setup(); input.configure(true,'pen');
  assert.ok(!event('wheel','',true).stopped);
  assert.ok(!event('wheel','',false).stopped);
  event('pointerdown','pen',true);
  assert.ok(event('wheel','',true).stopped);
  event('pointerup','pen',true);
  assert.ok(!event('wheel','',true).stopped, 'pen-up tail must not swallow navigation');
  assert.ok(event('click','pen',true).stopped, 'pen compatibility clicks remain suppressed');
});

test('explicit pen hover ends a missing release without mistaking low pressure for pen-up', () => {
  const {input,event,ended,classes} = setup(); input.configure(true,'pen');
  event('pointerdown','pen',true,7,{button:0,buttons:1,pressure:.5});
  event('pointermove','pen',true,7,{buttons:1,pressure:0});
  assert.ok(classes.has('ink-pen-contact'));
  event('pointermove','pen',true,7,{buttons:0,pressure:0});
  assert.deepEqual(ended,[7]); assert.ok(!classes.has('ink-pen-contact'));
  assert.ok(!event('wheel','',true).stopped);
  assert.equal(input.lastPointer().type,'pen');
});

test('pen release outside an uncaptured canvas still closes the owned gesture', () => {
  const {input,event,ended} = setup(); input.configure(true,'pen');
  event('pointerdown','pen',true,7);
  assert.ok(event('pointerup','pen',false,7).stopped);
  assert.deepEqual(ended,[7]);
});

test('reader-input pen contact suppresses palm, compatibility, selection and paragraph events, not keyboard', () => {
  const { input, event, classes, selectionCleared } = setup();
  input.configure(true, 'pen');
  const down = event('pointerdown', 'pen', true);
  assert.ok(down.prevented && !down.stopped && down.bubbled === false);
  assert.ok(classes.has('ink-pen-contact') && selectionCleared());
  for (const type of ['mouse', 'touch']) {
    for (const where of [true, false]) {
      assert.ok(event('pointerdown', type, where, 3).stopped);
      assert.ok(event('pointermove', type, where, 3).stopped);
      assert.ok(event('pointerup', type, where, 3).stopped);
    }
  }
  for (const type of ['mousedown', 'mouseup', 'mousemove', 'click', 'dblclick', 'contextmenu', 'selectstart', 'selectionchange', 'touchstart', 'touchmove', 'touchend', 'wheel', 'dragstart'])
    assert.ok(event(type).stopped, type);
  assert.ok(!event('click', '', false, -1, { detail: 0 }).stopped, 'keyboard click is not trapped');
  assert.ok(!event('keydown').stopped);
  assert.ok(event('pointerdown', 'pen', false, 9).stopped, 'pen cannot activate a toolbar');
  event('pointerup', 'pen', false, 9);
  assert.ok(input.suppressing(), 'another pen is still contacting');
  event('pointerup', 'pen', true);
  assert.ok(!classes.has('ink-pen-contact'));
  assert.ok(event('click', 'pen').stopped);
  assert.ok(event('mouseup').stopped);
  assert.ok(!event('pointerdown', 'mouse', false, 5).stopped);
  assert.ok(!event('click', 'mouse', false, 5).stopped);
});

test('reader-input cancellation, lost capture, blur and tool switches release bounded locks', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  for (const end of ['pointerup', 'pointercancel', 'lostpointercapture', 'blur', 'visibilitychange', 'panel', 'mode']) {
    const { input, event, doc, classes } = setup();
    input.configure(true, 'pen'); event('pointerdown', 'pen', true);
    event('pointerdown', 'touch', false, 2);
    if (end === 'panel') input.configure(false, 'pen');
    else if (end === 'mode') input.configure(true, 'mouse');
    else if (end === 'visibilitychange') { doc.hidden = true; event(end); }
    else event(end, 'pen', true);
    if (end === 'pointerup') event('pointerup', 'touch', false, 2);
    assert.ok(!classes.has('ink-pen-contact'), end);
    t.mock.timers.tick(401);
    assert.equal(input.suppressing(), false, end);
    assert.ok(!event('pointerdown', 'mouse', false, 4).stopped, end);
    assert.ok(!event('click', 'mouse', false, 4).stopped, end);
  }
});

test('reader-input blocks a palm that started on UI until its own release, but not the next touch', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const { input, event } = setup(); input.configure(true, 'pen');
  event('pointerdown', 'pen', true);
  event('pointerdown', 'touch', false, 2);
  event('pointerup', 'pen', true);
  t.mock.timers.tick(401);
  assert.ok(event('pointermove', 'touch', false, 2).stopped);
  assert.ok(event('pointerup', 'touch', false, 2).stopped);
  assert.equal(input.suppressing(), false, 'late palm release must not restart the global pen tail');
  assert.ok(!event('pointerdown', 'touch', false, 3).stopped);
  assert.ok(!event('touchstart', '', false, undefined).stopped);
  assert.ok(event('click', 'touch', false, 2).stopped, 'delayed palm click is suppressed by its pointer ID');
  t.mock.timers.tick(401);
  assert.ok(!event('pointerdown', 'touch', false, 2).stopped);
});

test('reader-input missing palm/mouse release cannot lock all UI after the pen lifts', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const { input, event } = setup(); input.configure(true, 'pen');
  event('pointerdown', 'pen', true);
  event('pointerdown', 'mouse', false, 9);
  event('pointerdown', 'touch', false, 2);
  event('pointerup', 'pen', true);
  t.mock.timers.tick(401);
  assert.equal(input.suppressing(), false);
  assert.ok(!event('pointerdown', 'mouse', false, 9).stopped, 'fresh mouse down replaces a missed release');
  assert.ok(!event('click', 'mouse', false, 9).stopped);
  assert.ok(event('pointermove', 'touch', false, 2).stopped, 'old palm is still quarantined');
  assert.ok(!event('pointerdown', 'touch', false, 3).stopped, 'different touch pointer can start on UI');
  for (const type of ['touchstart', 'touchmove', 'touchend'])
    assert.ok(!event(type, '', false, undefined, { changedTouches: [{ identifier: 30 }] }).stopped, type);
});

test('reader-input loads before UI handlers and is discoverable by offline script inlining', () => {
  const fs = require('node:fs');
  const html = fs.readFileSync('easyread/web/reader.html', 'utf8');
  const scripts = [...html.matchAll(/<script src="\/web\/([^"]+)"><\/script>/g)].map(m => m[1]);
  const input = scripts.indexOf('js/reader/reader-input.js');
  assert.ok(input > scripts.indexOf('js/common/util.js'));
  for (const file of ['js/common/dropdown.js', 'js/reader/ink.js', 'js/reader/stickers.js', 'js/reader/anchor.js', 'js/reader/actions.js'])
    assert.ok(input < scripts.indexOf(file), file);
});

test('reader-input rejected nonpen canvas releases never create a global pen tail', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  for (const pointerType of ['mouse', 'touch']) {
    const { input, event } = setup(); input.configure(true, 'pen');
    assert.ok(event('pointerdown', pointerType, true, 2).stopped);
    assert.ok(event('pointerup', pointerType, true, 2).stopped);
    assert.equal(input.suppressing(), false, pointerType);
    assert.ok(event('click', pointerType, false, 2).stopped, 'retargeted rejected click is still quarantined');
    assert.ok(!event('pointerdown', 'touch', false, 3).stopped);
    for (const type of ['touchstart', 'touchmove', 'touchend'])
      assert.ok(!event(type, '', false, undefined, { changedTouches: [{ identifier: 30 }] }).stopped, type);
    assert.ok(!event('pointerup', 'touch', false, 3).stopped);
    assert.ok(!event('click', 'touch', false, 3).stopped);
    assert.ok(!event('wheel').stopped);
  }
});

test('reader-input legacy rejected-click fallback is local and a fresh gesture clears it', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const { input, event } = setup(); input.configure(true, 'pen');
  const canvas = { closest: () => true }, target = { closest: () => null };
  const position = { clientX: 20, clientY: 30 };
  event('pointerdown', 'mouse', true, 2, { target: canvas, ...position });
  event('pointerup', 'mouse', false, 2, { target, ...position });
  const legacy = { pointerId: undefined, pointerType: undefined, target, ...position };
  assert.ok(event('mouseup', '', false, undefined, legacy).stopped);
  assert.ok(event('click', '', false, undefined, legacy).stopped);
  assert.ok(!event('click', '', false, undefined, { ...legacy, clientX: 200 }).stopped);
  assert.ok(!event('click', '', false, undefined, { ...legacy, target: { closest: () => null } }).stopped);
  assert.equal(input.suppressing(), false);
  event('pointerdown', 'touch', false, 3, { target, ...position });
  assert.ok(!event('click', '', false, undefined, legacy).stopped, 'new gesture on same target must not inherit rejection');
  assert.ok(event('click', 'mouse', false, 2).stopped, 'identified old click remains rejected');
  t.mock.timers.tick(401);
  assert.ok(!event('click', 'mouse', false, 2).stopped, 'local rejection expires');
});

test('reader-input only tracked pen termination starts a global compatibility deadline', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const { input, event } = setup(); input.configure(true, 'pen');
  event('pointerup', 'pen', true);
  assert.equal(input.suppressing(), false, 'untracked pen release');
  event('pointerdown', 'pen', true); event('pointerup', 'pen', true);
  t.mock.timers.tick(300);
  event('lostpointercapture', 'pen', true);
  input.configure(false, 'pen');
  t.mock.timers.tick(101);
  assert.equal(input.suppressing(), false, 'lost capture and mode changes do not extend the finished contact tail');
});
