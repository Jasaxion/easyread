const { createReaderInput } = require('../../easyread/web/js/reader/reader-input.js');

const ipad = { userAgent: 'Mozilla/5.0 (iPad) AppleWebKit/605.1.15 Version/17.5 Safari/604.1', platform: 'iPad', maxTouchPoints: 5 };

// Deterministic capture/bubble routing and queued work for the real input router
// plus progress/chunks; this is not a browser or WebKit event simulator.
function readerInputRuntime(PR, { navigator = ipad, feature = true } = {}) {
  const listeners = new Map(), subscriptions = new Map(), timers = new Map(), frames = new Map();
  const scrolls = [], classes = new Set(), captures = new Set();
  let nextId = 0;
  const layer = {
    closest: () => layer, addEventListener() {},
    setPointerCapture(id) { captures.add(id); }, hasPointerCapture: id => captures.has(id),
    releasePointerCapture(id) { captures.delete(id); }
  };
  const paper = { classList: { add(c) { classes.add(c); }, remove(c) { classes.delete(c); } }, querySelector: () => null };
  const host = {
    navigator, scrollY: 0, innerHeight: 800, visualViewport: { scale: 1 },
    document: {
      hidden: false, fonts: null,
      documentElement: { scrollHeight: 10000, clientHeight: 800, classList: { toggle() {} } },
      addEventListener() {}, getElementById: id => id === 'inkLayer' ? layer : null
    },
    getSelection: () => ({ removeAllRanges() {} }),
    scrollTo(options) { scrolls.push({ kind: 'to', ...options }); host.scrollY = options.top; },
    scrollBy(options, y) { const top = typeof options === 'number' ? y : options.top; scrolls.push({ kind: 'by', top }); host.scrollY += top; },
    addEventListener(type, fn, options) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push({ fn, capture: options === true || !!options?.capture });
    },
    removeEventListener(type, fn) { listeners.set(type, (listeners.get(type) || []).filter(x => x.fn !== fn)); },
    setTimeout(fn, ms) { const id = ++nextId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame(fn) { const id = ++nextId; frames.set(id, fn); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    location: { hash: '', search: '' }, history: { state: null }, URLSearchParams,
  };
  Object.assign(PR, {
    $: selector => selector === '#paper' ? paper : null,
    on(name, fn) { if (!subscriptions.has(name)) subscriptions.set(name, []); subscriptions.get(name).push(fn); },
    emit(name, detail) { for (const fn of subscriptions.get(name) || []) fn(detail); },
    feature: () => feature,
  });
  host.window = host; host.PR = PR;
  PR.readerInput = createReaderInput(host, PR);
  PR.readerInput.configure(true, 'pen');
  function event(type, extra = {}) {
    const e = { type, pointerType: 'touch', pointerId: 2, target: layer, clientY: 300, screenY: 300,
      buttons: 1, pressure: .5, cancelable: true, changedTouches: [],
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra };
    const handlers = listeners.get(type) || [];
    for (const capture of [true, false]) for (const handler of handlers) {
      if (e.stopped) return e;
      if (handler.capture === capture) handler.fn(e);
    }
    return e;
  }
  function frame() { const work = [...frames.values()]; frames.clear(); for (const fn of work) fn(); }
  function timer(ms) {
    const entry = [...timers].find(([, work]) => ms == null || work.ms === ms);
    if (!entry) return false;
    timers.delete(entry[0]); entry[1].fn(); return true;
  }
  return { PR, host, paper, layer, classes, scrolls, listeners, subscriptions, timers, frames, event, frame, timer };
}
module.exports = { readerInputRuntime };
