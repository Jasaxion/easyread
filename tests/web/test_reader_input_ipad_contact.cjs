const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createReaderInput } = require('../../easyread/web/js/reader/reader-input.js');

const ipad = { userAgent: 'Mozilla/5.0 (iPad) AppleWebKit/605.1.15 Version/17.5 Safari/604.1', platform: 'iPad', maxTouchPoints: 5 };
function setup({ navigator = ipad, feature } = {}) {
  const listeners = {}, layerListeners = {}, classes = new Set(), captures = new Set();
  const forwarded = [], finished = [], emitted = [], scrolls = [], delivered = [];
  const layer = {
    closest: () => layer,
    addEventListener(type, fn) { layerListeners[type] = fn; },
    setPointerCapture(id) { captures.add(id); }, hasPointerCapture: id => captures.has(id),
    releasePointerCapture(id) { captures.delete(id); event('lostpointercapture', 'touch', id); }
  };
  const child = { closest: () => layer }, ui = { closest: () => null };
  const doc = {
    hidden: false,
    documentElement: { classList: { toggle(c, on) { on ? classes.add(c) : classes.delete(c); } } },
    getElementById: id => id === 'inkLayer' ? layer : null,
    addEventListener(type, fn) { listeners[type] = fn; }
  };
  const host = { navigator, document: doc, getSelection: () => ({ removeAllRanges() {} }),
    addEventListener(type, fn) { listeners[type] = fn; }, scrollBy(x, y) { scrolls.push([x, y]); } };
  const PR = {
    forwardInkPointer(e) { forwarded.push(e); }, finishInkGesture(id) { finished.push(id); },
    emit(name) { emitted.push({ name, contact: input.penContact() }); }
  };
  if (feature) PR.feature = feature;
  const input = createReaderInput(host, PR);
  function event(type, pointerType = 'pen', pointerId = 7, extra = {}) {
    const e = { type, pointerType, pointerId, target: layer, clientX: 20, clientY: 300,
      buttons: 1, pressure: .5, cancelable: true, detail: 1,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; },
      stopPropagation() { this.bubbled = false; }, ...extra };
    listeners[type]?.(e);
    if (!e.stopped && e.target.closest?.('#inkLayer')) {
      layerListeners[type]?.(e); delivered.push(e);
    }
    return e;
  }
  const touch = (type, contacts, extra = {}) => event(type, '', undefined, {
    target: ui, changedTouches: contacts.map(c => typeof c === 'number' ? { identifier: c, touchType: 'direct' } : c), ...extra
  });
  input.configure(true, 'pen');
  return { input, event, touch, PR, doc, layer, child, ui, classes, captures, forwarded, finished, emitted, scrolls, delivered };
}

const endNames = s => s.emitted.filter(e => e.name === 'ink-contact-end');

test('reader-input iPad pen retains physical contact across descendant/root/outside capture loss, never a timeout', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const s = setup();
  s.event('pointerdown', 'pen', 7, { target: s.child });
  for (const target of [s.child, s.layer, s.ui]) {
    s.event('lostpointercapture', 'pen', 7, { target });
    t.mock.timers.tick(60000);
    assert.equal(s.input.keepsPenContact(7), true);
    assert.equal(s.input.keepsPenContact(9), false);
    assert.equal(s.input.penContact(), true);
    assert.ok(s.input.suppressing() && s.classes.has('ink-pen-contact'));
    assert.ok(s.event('wheel', '', undefined, { target: s.ui }).stopped);
  }
  s.event('pointermove', 'pen', 7, { buttons: 1, pressure: 0 });
  s.event('pointermove', 'pen', 7, { buttons: 0, pressure: .1 });
  assert.equal(s.input.penContact(), true, 'neither low pressure nor buttons alone proves pen-up');
  assert.deepEqual(s.finished, []);
  assert.deepEqual(s.forwarded, []);
  assert.deepEqual(endNames(s), []);
  s.event('pointerup', 'pen', 7);
  assert.equal(s.input.keepsPenContact(7), false);
  assert.equal(s.input.penContact(), false);
  assert.deepEqual(endNames(s), [{ name: 'ink-contact-end', contact: false }]);
  s.event('lostpointercapture', 'pen', 7);
  s.event('pointercancel', 'pen', 7);
  assert.equal(endNames(s).length, 1, 'capture cleanup/untracked endings do not re-emit');
});

test('reader-input iPad outside move/up/cancel route to ink once and do not reach UI/layer handlers', () => {
  for (const ending of ['pointerup', 'pointercancel']) {
    const s = setup(); s.event('pointerdown');
    s.event('lostpointercapture');
    const move = s.event('pointermove', 'pen', 7, { target: s.ui, clientX: 60 });
    const end = s.event(ending, 'pen', 7, { target: s.ui, clientX: 90 });
    assert.ok(move.stopped && move.prevented && end.stopped && end.prevented);
    assert.deepEqual(s.forwarded, [move, end]);
    assert.ok(!s.delivered.includes(move) && !s.delivered.includes(end));
    assert.deepEqual(s.finished, [], 'forwarded endings must not also call the fallback');
    assert.equal(s.input.penContact(), false);
    assert.equal(endNames(s).length, 1);
  }
  const s = setup(); s.event('pointerdown'); s.event('pointermove'); s.event('pointerup');
  assert.deepEqual(s.forwarded, [], 'canvas/descendant events use the existing layer route');
  for (const ending of ['pointerup', 'pointercancel']) {
    const fallback = setup(); delete fallback.PR.forwardInkPointer;
    fallback.event('pointerdown'); fallback.event('lostpointercapture');
    fallback.event('pointermove', 'pen', 7, { target: fallback.ui });
    fallback.event(ending, 'pen', 7, { target: fallback.ui });
    assert.deepEqual(fallback.finished, [7]);
  }
});

test('reader-input iPad keeps every primary, secondary and UI finger quarantined until its own physical end', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  for (const penEnd of ['pointerup', 'pointercancel', 'hover']) {
    const s = setup();
    s.event('pointerdown', 'touch', 2);
    s.event('pointerdown', 'touch', 3); // Secondary canvas finger never owns the pan.
    s.event('pointerdown', 'touch', 4, { target: s.ui }); // Already down outside the canvas.
    s.event('lostpointercapture', 'touch', 4, { target: s.ui });
    s.event('pointerdown');
    s.event('pointerdown', 'touch', 5, { target: s.ui }); // New palm during the pen contact.
    assert.equal(s.input.panning(), false);
    s.event('lostpointercapture', 'pen', 7, { target: s.child });
    for (const id of [2, 3, 4, 5]) s.event('lostpointercapture', 'touch', id, { target: s.ui });
    if (penEnd === 'hover') s.event('pointermove', 'pen', 7, { buttons: 0, pressure: 0 });
    else s.event(penEnd);
    s.event('pointerdown', 'touch', 8);
    s.event('pointermove', 'touch', 8, { clientY: 260 });
    s.event('pointerup', 'touch', 8);
    assert.deepEqual(s.scrolls, [[0, 40]], 'fresh finger pans immediately even with old palms held');
    t.mock.timers.tick(401);
    s.input.configure(true, 'pen'); // UI sync must not discard held-palm quarantine either.
    assert.equal(s.input.suppressing(), false);
    for (const id of [2, 3, 4, 5]) {
      assert.ok(s.event('lostpointercapture', 'touch', id, { target: s.ui }).stopped);
      assert.ok(s.event('pointermove', 'touch', id, { target: s.ui, clientY: 100 }).stopped, `${penEnd}: ${id}`);
      assert.ok(s.event(id % 2 ? 'pointercancel' : 'pointerup', 'touch', id, { target: s.ui }).stopped);
      assert.ok(s.event('click', 'touch', id, { target: s.ui }).stopped);
      assert.ok(!s.event('pointermove', 'touch', id, { target: s.ui }).stopped, 'real end releases this finger only');
    }
    assert.deepEqual(s.scrolls, [[0, 40]], 'capture loss/pen-up never resume an old finger pan');
    assert.equal(s.input.penContact(), false);
  }
});

test('reader-input iPad TouchEvent palms use independent identifiers and survive pointer ends/capture changes', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const s = setup();
  s.event('pointerdown', 'touch', 2, { target: s.ui }); s.touch('touchstart', [202]);
  s.event('pointerdown', 'touch', 3); s.touch('touchstart', [203]);
  s.event('pointerdown');
  s.event('pointerdown', 'touch', 4, { target: s.ui }); s.touch('touchstart', [204]);
  s.event('lostpointercapture'); s.event('pointercancel');
  for (const id of [2, 3, 4]) s.event('lostpointercapture', 'touch', id, { target: s.ui });
  s.event('pointerup', 'touch', 2, { target: s.ui });
  t.mock.timers.tick(401);
  for (const id of [202, 203, 204]) assert.ok(s.touch('touchmove', [id]).stopped);
  assert.ok(s.touch('touchend', [202]).stopped, 'pointerup does not clear the later TouchEvent release');
  assert.ok(s.touch('touchcancel', [203]).stopped);
  assert.ok(!s.touch('touchmove', [202, 203]).stopped, 'each native touch lifetime clears independently');
  s.event('pointerdown', 'touch', 9, { target: s.ui });
  for (const type of ['touchstart', 'touchmove', 'touchend']) {
    assert.ok(!s.touch(type, [209], { touches: [{ identifier: 204 }] }).stopped, 'unchanged held palm does not trap a fresh finger');
  }
  assert.ok(s.touch('touchmove', [204]).stopped, 'fresh contact does not release other held palms');
  assert.ok(s.touch('touchend', [204]).stopped);
  assert.ok(!s.touch('touchstart', [204]).stopped, 'identifier can be reused after real touchend');
});

test('reader-input iPad no-op configure retains already-down UI contacts for the next pen down', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const s = setup();
  s.event('pointerdown', 'touch', 2, { target: s.ui }); s.touch('touchstart', [202]);
  s.input.configure(true, 'pen');
  s.event('pointerdown'); s.event('pointerup');
  t.mock.timers.tick(401);
  assert.ok(s.event('pointermove', 'touch', 2, { target: s.ui }).stopped);
  assert.ok(s.touch('touchmove', [202]).stopped);
});

test('reader-input iPad no-op configure preserves pen contact but real resets finish and unlock once', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  for (const ending of ['reset', 'blur', 'hidden', 'panel', 'mode']) {
    const s = setup(); s.event('pointerdown');
    s.event('pointerdown', 'touch', 2, { target: s.ui }); s.touch('touchstart', [202]);
    s.event('lostpointercapture'); s.input.configure(true, 'pen');
    assert.equal(s.input.keepsPenContact(7), true);
    assert.deepEqual(s.finished, []);
    assert.deepEqual(endNames(s), []);
    if (ending === 'reset') s.input.reset();
    else if (ending === 'blur') s.event('blur');
    else if (ending === 'hidden') { s.doc.hidden = true; s.event('visibilitychange'); }
    else s.input.configure(ending !== 'panel', ending === 'mode' ? 'mouse' : 'pen');
    assert.equal(s.input.penContact(), false, ending);
    assert.equal(s.input.keepsPenContact(7), false, ending);
    assert.deepEqual(s.finished, [7], ending);
    assert.deepEqual(endNames(s), [{ name: 'ink-contact-end', contact: false }], ending);
    t.mock.timers.tick(401);
    const retainedPalm = ending === "panel" || ending === "mode";
    assert.equal(!!s.event('pointermove', 'touch', 2, { target: s.ui }).stopped, retainedPalm);
    assert.equal(!!s.touch('touchmove', [202]).stopped, retainedPalm);
    s.event("pointerup", "touch", 2, {target:s.ui}); s.touch("touchend", [202]);
    s.input.reset(); assert.equal(endNames(s).length, 1);
  }
});

test('reader-input iPad explicit hover and positively identified stylus touchend recover missing pointerup', () => {
  const s = setup(); s.event('pointerdown'); s.event('lostpointercapture');
  s.event('pointermove', 'pen', 7, { target: s.ui, buttons: 0, pressure: 0 });
  assert.deepEqual(s.forwarded, [], "hover coordinates must not be drawn as a contact sample");
  assert.deepEqual(s.finished, [7]);
  assert.equal(s.input.penContact(), false);
  assert.equal(endNames(s).length, 1);
  for (const ending of ['touchend', 'touchcancel']) {
    const s = setup(); s.event('pointerdown');
    s.touch('touchstart', [{ identifier: 900, touchType: 'stylus' }]); s.event('lostpointercapture');
    s.touch('touchend', [7]); // Matching numeric ID from an ordinary finger must never end the pen.
    s.touch('touchend', [{ identifier: 901, touchType: 'stylus' }]);
    assert.equal(s.input.penContact(), true);
    assert.ok(s.touch(ending, [{ identifier: 900, touchType: 'stylus' }]).stopped);
    assert.equal(s.input.penContact(), false);
    assert.deepEqual(s.finished, [7]);
    assert.equal(endNames(s).length, 1);
    s.event('pointerup'); assert.equal(endNames(s).length, 1);
  }
  const unknown = setup(); unknown.event('pointerdown');
  unknown.touch('touchstart', [{ identifier: 900 }]); unknown.touch('touchend', [{ identifier: 900 }]);
  unknown.touch('touchend', [{ identifier: 7, touchType: 'stylus' }]);
  assert.equal(unknown.input.penContact(), true, 'no guessed association for untyped or unobserved stylus touches');
  const multiple = setup(); multiple.event('pointerdown'); multiple.event('pointerdown', 'pen', 8);
  multiple.touch('touchstart', [{ identifier: 900, touchType: 'stylus' }]);
  multiple.touch('touchend', [{ identifier: 900, touchType: 'stylus' }]);
  assert.ok(multiple.input.keepsPenContact(7) && multiple.input.keepsPenContact(8), 'ambiguous stylus cannot end either pen');
});

test('reader-input new iPad pen down recovers a missing release without rehabilitating a held palm', () => {
  const s=setup();s.event('pointerdown');s.event('pointerdown','touch',2,{target:s.ui});
  s.event('lostpointercapture');s.event('pointerdown');
  assert.deepEqual(s.finished,[7]);assert.equal(s.input.keepsPenContact(7),true);
  s.event('pointerup');assert.equal(s.input.penContact(),false);
  assert.ok(s.event('pointermove','touch',2,{target:s.ui}).stopped);
  s.event('pointerdown');s.PR.feature=()=>false;s.event('lostpointercapture');s.event('pointerdown');
  assert.deepEqual(s.finished,[7,7]);assert.equal(s.input.keepsPenContact(7),false);
  assert.equal(s.input.penContact(),true,'fresh opt-out contact uses the legacy route');
});

test('reader-input fresh primary iPad pen recovers a different stale ID even when opting out', () => {
  for(const opt of [true,false]) {
    const s=setup();s.event('pointerdown');s.event('pointerdown','touch',2,{target:s.ui});s.event('lostpointercapture');
    s.PR.feature=()=>opt;s.event('pointerdown','pen',8,{isPrimary:true});
    assert.deepEqual(s.finished,[7]);assert.equal(s.input.keepsPenContact(7),false);
    assert.equal(s.input.keepsPenContact(8),opt);assert.equal(s.input.penContact(),true);
    s.event('pointerup','pen',8);assert.equal(s.input.penContact(),false);
    assert.ok(s.event('pointermove','touch',2,{target:s.ui}).stopped);
  }
  const s=setup();s.event('pointerdown');s.event('pointerdown','pen',8,{isPrimary:false});
  assert.deepEqual(s.finished,[]);assert.equal(s.input.keepsPenContact(7),true);
});

test('reader-input iPad contact feature choice is dynamic at pen down and frozen until the physical end', () => {
  const s = setup(); s.event('pointerdown');
  s.PR.feature = () => false;
  s.input.configure(true, 'pen'); s.event('lostpointercapture');
  assert.equal(s.input.keepsPenContact(7), true, 'late opt-out does not change an existing contact');
  s.event('pointerup');
  s.event('pointerdown');
  assert.equal(s.input.penContact(), true);
  assert.equal(s.input.keepsPenContact(7), false, 'next pen contact takes the opt-out');
  s.PR.feature = () => true;
  s.event('lostpointercapture');
  assert.equal(s.input.penContact(), false, 'late opt-in does not change a legacy contact');
  s.event('pointerdown'); assert.equal(s.input.keepsPenContact(7), true);
  for (const value of [undefined, null, 0, true]) {
    const s = setup({ feature: () => value }); s.event('pointerdown'); assert.equal(s.input.keepsPenContact(7), true);
  }
});

test('reader-input feature-off and non-iPad pen contacts keep capture-loss/cancel/no-op legacy behavior', t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1000 });
  const nonIpadFeature = () => assert.fail('non-iPad contacts must not query ipad_ink');
  for (const options of [
    { feature: () => false }, { navigator: {}, feature: nonIpadFeature },
    { navigator: { userAgent: 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0 Safari/537.36', maxTouchPoints: 5 }, feature: nonIpadFeature },
    { navigator: { userAgent: 'Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15', platform: 'MacIntel', maxTouchPoints: 0 }, feature: nonIpadFeature }
  ]) {
    for (const ending of ['lostpointercapture', 'pointercancel', 'configure']) {
      const s = setup(options); s.event('pointerdown');
      s.event('pointerdown', 'touch', 2, { target: s.ui }); s.touch('touchstart', [202]);
      assert.equal(s.input.penContact(), true); assert.equal(s.input.keepsPenContact(7), false);
      if (ending === 'configure') s.input.configure(true, 'pen');
      else s.event(ending);
      assert.equal(s.input.penContact(), false);
      t.mock.timers.tick(401);
      assert.ok(!s.event('pointermove', 'touch', 2, { target: s.ui }).stopped);
      assert.ok(!s.touch('touchmove', [202]).stopped);
      assert.deepEqual(endNames(s), []); assert.deepEqual(s.forwarded, []);
    }
    const s = setup(options); s.event('pointerdown');
    s.event('pointermove', 'pen', 7, { target: s.ui }); s.event('pointerup', 'pen', 7, { target: s.ui });
    assert.deepEqual(s.finished, [7]); assert.deepEqual(s.forwarded, []);
  }
});
