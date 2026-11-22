/* Local ttyd/xterm regression. No EasyRead server, SSH, NAS, model or user project.
   NODE_PATH=<playwright node_modules> TTYD=<ttyd> PYTHON=<python> node tests/browser/terminal-touch.cjs
   Chromium's trusted CDP touch input is not an iOS/Android physical-device test. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const { spawn } = require('node:child_process');
const http = require('node:http');
const { chromium } = require('playwright');
const ROOT = path.resolve(__dirname, '../..');
const PROGRAM = String.raw`
import os,sys,tty
from pathlib import Path
tty.setraw(0)
Path('pid').write_text(str(os.getpid()))
Path('input.bin').write_bytes(b'')
os.write(1, ''.join('scrollback line %03d\r\n' % i for i in range(200)).encode())
os.write(1, b'LOCAL TOUCH FIXTURE READY\r\n')
mode = 'normal'
while True:
    data = os.read(0, 4096)
    if not data: break
    with open('input.bin', 'ab') as f: f.write(data)
    if data == b'\x01':
        mode = 'alternate'
        os.write(1, b'\x1b[?1049h\x1b[?1000l\x1b[?1006lALT ARROWS')
    elif data == b'\x02':
        mode = 'mouse'
        os.write(1, b'\x1b[?1049h\x1b[?1000h\x1b[?1006hALT MOUSE')
    elif data == b'\x03':
        mode = 'normal'
        os.write(1, b'\x1b[?1000l\x1b[?1006l\x1b[?1049l')
    elif mode == 'normal': os.write(1, data)
`;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'easyread-terminal-touch-'));
  const program = path.join(tmp, 'terminal.py'); fs.writeFileSync(program, PROGRAM);
  const proc = spawn(process.env.TTYD || 'ttyd', ['-i', '127.0.0.1', '-p', '0', '-W', '-b', '/terminal/local',
    process.env.PYTHON || 'python3', '-u', program], { cwd: tmp, env: { ...process.env, HOME: tmp }, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '', error, browser, crossOriginServer;
  proc.on('error', e => { error = e; }); proc.stdout.on('data', c => { log += c; }); proc.stderr.on('data', c => { log += c; });
  try {
    const deadline = Date.now() + 10000;
    while (!/Listening on port: (\d+)/.test(log) && !error && proc.exitCode === null && Date.now() < deadline) await sleep(30);
    const port = log.match(/Listening on port: (\d+)/)?.[1]; assert.ok(port, error?.message || log);
    const base = 'http://127.0.0.1:' + port;
    browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route(base + '/fixture', route => route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{display:block;width:100%;height:700px;border:0}</style>
      <script>window.PR={};localStorage.setItem('easyread-terminal-theme','dark');document.documentElement.dataset.theme='light';</script>
      <script src="/web/js/common/terminal-theme.js"></script><script src="/web/js/common/terminal-touch.js"></script>
      <iframe src="/terminal/local/"></iframe><script>
      window.frame=document.querySelector('iframe');window.releaseTheme=PR.terminalTheme.register(frame);window.releaseTouch=PR.terminalTouch.register(frame);
      </script>` }));
    await page.route(base + '/web/js/common/*.js', route => route.fulfill({ contentType: 'text/javascript',
      body: fs.readFileSync(path.join(ROOT, 'easyread/web/js/common', path.basename(new URL(route.request().url()).pathname)), 'utf8') }));
    await page.goto(base + '/fixture');
    await page.waitForFunction(() => frame.contentWindow.term?.buffer.active.baseY > 100 && frame.contentWindow.term.options.theme.background === '#fffcf0');
    await sleep(150);
    const pid = fs.readFileSync(path.join(tmp, 'pid'), 'utf8');
    const input = () => fs.readFileSync(path.join(tmp, 'input.bin'));
    const xframe = page.frames().find(f => f.url() === base + '/terminal/local/'); assert.ok(xframe);
    await xframe.evaluate(() => {
      window.wheels = []; window.focusEvents = 0; window.savedTerm = term; window.savedBuffer = term.buffer.normal;
      term.element.addEventListener('wheel', e => wheels.push({ y: e.deltaY, x: e.clientX, cy: e.clientY }), { passive: true });
      document.addEventListener('focusin', () => focusEvents++); document.addEventListener('focusout', () => focusEvents++);
    });
    const cdp = await context.newCDPSession(page);
    const before = await xframe.evaluate(() => term.buffer.active.viewportY);
    async function swipe(dy, steps = 6) {
      const start = { x: 120, y: dy < 0 ? 430 : 240, id: 1, radiusX: 1, radiusY: 1 };
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
      for (let i = 1; i <= steps; i++) {
        await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ ...start, y: start.y + dy * i / steps }] });
        await sleep(20);
      }
      await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
      await sleep(100);
    }
    await swipe(150);
    const normal = await xframe.evaluate(() => ({ viewport: term.buffer.active.viewportY, wheels, focusEvents }));
    assert.ok(normal.viewport < before, JSON.stringify({ before, normal }));
    assert.ok(normal.wheels.length > 0, 'trusted mobile swipe must enter wheel path, not just native touch scroll');
    assert.equal(normal.focusEvents, 0); assert.equal(input().length, 0, 'scrollback must not send terminal input');
    assert.ok(normal.wheels.every(e => e.y < 0 && e.x === 120 && e.cy >= 240 && e.cy <= 390));
    await swipe(-100); assert.ok(await xframe.evaluate(() => term.buffer.active.viewportY) > normal.viewport);
    // The same wheel sequence must scroll the same amount: native touch handlers
    // cannot also consume the claimed movement underneath the adapter.
    await xframe.evaluate(y => term.scrollToLine(y), before); await sleep(100);
    for (const wheel of normal.wheels) {
      await xframe.evaluate(e => term.element.dispatchEvent(new WheelEvent('wheel', {
        deltaY: e.y, clientX: e.x, clientY: e.cy, bubbles: true, cancelable: true,
      })), wheel);
      await sleep(20);
    }
    await sleep(100);
    assert.equal(await xframe.evaluate(() => term.buffer.active.viewportY), normal.viewport, 'no double scrolling through native touch and wheel');
    // Synthetic transition events run against the real xterm native touch state.
    // Queuing and interruption share one JS task, before the adapter's next RAF.
    const transitions = await xframe.evaluate(async () => {
      const results = [];
      for (const surface of ['.xterm-screen', '.xterm-viewport']) {
        const target = term.element.querySelector(surface);
        const touch = (y, id = 1) => new Touch({ identifier: id, target, clientX: 120, clientY: y, pageX: 120, pageY: y });
        const send = (type, touches) => target.dispatchEvent(new TouchEvent(type, { touches, targetTouches: touches,
          changedTouches: touches, bubbles: true, cancelable: true }));
        const settled = () => new Promise(resolve => setTimeout(resolve, 100));
        for (const mode of ['selection', 'composition', 'pinch', 'cancel']) {
          term.clearSelection(); term.scrollToLine(100); await settled();
          send('touchstart', [touch(200)]); send('touchmove', [touch(250)]); await settled();
          const before = term.buffer.active.viewportY, count = wheels.length;
          send('touchmove', [touch(270)]); // Queued movement must be discarded by the transition.
          if (mode === 'selection') term.select(0, 0, 3);
          if (mode === 'composition') term.textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
          if (mode === 'pinch') send('touchstart', [touch(270), touch(290, 2)]);
          if (mode === 'cancel') send('touchcancel', []);
          else send('touchmove', mode === 'pinch' ? [touch(300), touch(320, 2)] : [touch(300)]);
          await settled();
          results.push({ surface, mode, before, after: term.buffer.active.viewportY, wheels: wheels.length - count });
          send('touchend', []);
          if (mode === 'composition') term.textarea.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }));
          term.clearSelection();
        }
      }
      return results;
    });
    for (const result of transitions) {
      assert.ok(result.before < 100, 'transition must follow an already-claimed swipe: ' + JSON.stringify(result));
      assert.equal(result.wheels, 0, 'queued transition must not send a wheel: ' + JSON.stringify(result));
      assert.equal(result.after, result.before, 'claimed movement must not be replayed by native touch state: ' + JSON.stringify(result));
    }
    // Theme changes update options only, retaining process/runtime/buffer and input.
    await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await page.waitForFunction(() => frame.contentWindow.term.options.theme.background === '#100f0f');
    await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
    await page.waitForFunction(() => frame.contentWindow.term.options.theme.background === '#fffcf0');
    assert.equal(await xframe.evaluate(() => term === savedTerm && term.buffer.normal === savedBuffer), true);
    assert.equal(fs.readFileSync(path.join(tmp, 'pid'), 'utf8'), pid);
    const textarea = xframe.locator('.xterm-helper-textarea'); await textarea.focus(); await page.keyboard.type('keyboard-still-works');
    await page.waitForFunction(() => Array.from({ length: frame.contentWindow.term.buffer.normal.length }, (_, i) => frame.contentWindow.term.buffer.normal.getLine(i)?.translateToString(true)).join('\n').includes('keyboard-still-works'));
    await page.keyboard.press('Control+a');
    await page.waitForFunction(() => frame.contentWindow.term.buffer.active.type === 'alternate');
    let mark = input().length;
    const focused = await xframe.evaluate(() => ({ active: document.activeElement === term.textarea, events: focusEvents }));
    await swipe(150);
    assert.deepEqual(await xframe.evaluate(() => ({ active: document.activeElement === term.textarea, events: focusEvents })), focused, 'swipes never focus/blur the terminal or change its keyboard state');
    const arrows = input().subarray(mark).toString(); assert.match(arrows, /\x1b\[A/);
    assert.match(arrows, /^(\x1b\[A)+$/, 'xterm owns standard alternate-screen arrow encoding');
    mark = input().length; await sleep(250); assert.equal(input().length, mark, 'no continued TUI input after release');
    await page.keyboard.press('Control+b'); await sleep(100); mark = input().length;
    await swipe(-150);
    const mouse = input().subarray(mark).toString(); assert.match(mouse, /\x1b\[<65;\d+;\d+M/);
    assert.match(mouse, /^(\x1b\[<65;\d+;\d+M)+$/, 'xterm owns SGR mouse encoding, with no command text');
    const reports = [...mouse.matchAll(/<65;(\d+);(\d+)M/g)].map(m => [Number(m[1]), Number(m[2])]);
    const grid = await xframe.evaluate(() => ({ cols: term.cols, rows: term.rows }));
    assert.ok(reports.every(([col, row]) => col > 1 && col <= grid.cols && row > 1 && row <= grid.rows));
    assert.ok(new Set(reports.map(([, row]) => row)).size > 1, 'mouse reports use changing real touch coordinates');
    mark = input().length; await sleep(250); assert.equal(input().length, mark);
    // Same-task browser events deterministically cancel movement before its RAF.
    // In mouse mode an accidental flush would write an SGR report to the real PTY.
    const canceled = await xframe.evaluate(async () => {
      const target = term.element.querySelector('.xterm-screen'), count = wheels.length;
      const send = (type, y) => {
        const touches = y === null ? [] : [new Touch({ identifier: 2, target, clientX: 100, clientY: y, pageX: 100, pageY: y })];
        const event = new TouchEvent(type, { touches, targetTouches: touches, changedTouches: touches, bubbles: true, cancelable: true });
        target.dispatchEvent(event); return event.defaultPrevented;
      };
      send('touchstart', 300);
      const claimed = send('touchmove', 350);
      send('touchcancel', null);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      return { claimed, wheels: wheels.length - count };
    });
    assert.deepEqual(canceled, { claimed: true, wheels: 0 });
    await sleep(80); assert.equal(input().length, mark);
    await page.keyboard.press('Control+c');
    await page.waitForFunction(() => frame.contentWindow.term.buffer.active.type === 'normal');
    // The registered WindowProxy changes security origin, while cleanup retains
    // its old element. Both the load callback and owner disposal must remain safe.
    crossOriginServer = http.createServer((req, res) => { res.end('<!doctype html><title>Unrelated local origin</title>'); });
    await new Promise(resolve => crossOriginServer.listen(0, '127.0.0.1', resolve));
    const otherOrigin = 'http://127.0.0.1:' + crossOriginServer.address().port;
    await xframe.goto(otherOrigin + '/');
    assert.equal(await page.evaluate(() => {
      try { void frame.contentWindow.document; return false; } catch (error) { return error.name === 'SecurityError'; }
    }), true, 'navigation must actually make the WindowProxy inaccessible');
    const removed = await page.evaluate(() => {
      releaseTouch(); releaseTouch(); releaseTheme(); frame.remove();
      return !frame.isConnected;
    });
    assert.equal(removed, true, 'runtime release must not abort owner frame teardown');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, checks: ['trusted 390px touch scrollback in both directions', 'same iframe/term/buffer/PID on theme changes',
      'legacy theme ignored', 'no native/wheel double scrolling', 'keyboard input and focus retained', 'alternate-screen arrows', 'SGR mouse tracking at actual coordinates', 'no post-release inertia', 'queued touchcancel', 'claimed swipe to selection/composition/pinch/cancel', 'cross-origin load and idempotent owner teardown'],
      normalWheelEvents: normal.wheels.length, alternateArrowBytes: arrows.length, mouseReports: reports.length, transitionCases: transitions.length }, null, 2));
    await context.close();
  } finally {
    if (browser) await browser.close();
    if (crossOriginServer) await new Promise(resolve => crossOriginServer.close(resolve));
    proc.kill('SIGTERM');
    await new Promise(resolve => {
      if (proc.exitCode !== null || error) return resolve();
      const timer = setTimeout(() => { proc.kill('SIGKILL'); resolve(); }, 3000);
      proc.once('exit', () => { clearTimeout(timer); resolve(); });
    });
    fs.rmSync(tmp, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
