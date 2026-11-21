/* Local-only regression for reader Settings → CLI & terminal after installing Pi.
   NODE_PATH=<playwright node_modules> node tests/browser/terminal-status.cjs
   Disposable library/HOME and fake version-only CLIs; never touches the NAS.
   The iframe peer is a fixture: this test checks DOM retention, not ttyd/SSH. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const ROOT = path.resolve(__dirname, '../..');
(async () => {
  const python = execFileSync(process.env.PYTHON || 'python', ['-B','-c','import sys; print(sys.executable)'], {encoding:'utf8'}).trim();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'easyread-terminal-status-'));
  const home = path.join(tmp, 'home'), bin = path.join(tmp, 'bin'), log = path.join(tmp, 'cli.log');
  fs.mkdirSync(home); fs.mkdirSync(bin);
  function install(name, version) {
    fs.writeFileSync(path.join(bin, name), '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_CLI_LOG"\ncase "$1" in --version) echo "' + version + '";; *) echo "Model calls forbidden" >&2; exit 99;; esac\n', {mode:0o755});
  }
  install('codex', 'codex-cli 0.169.0');
  const env = {...process.env, HOME:home, PATH:bin + path.delimiter + '/usr/bin:/bin', FAKE_CLI_LOG:log,
    EASYREAD_ACCESS_CODE:'', EASYREAD_TERMINAL:'1', PYTHONDONTWRITEBYTECODE:'1', NO_PROXY:'localhost,127.0.0.1'};
  for (const key of ['CODEX_HOME','CLAUDE_CONFIG_DIR','PI_CODING_AGENT_DIR','EASYREAD_HOME','EASYREAD_LIBRARY','ANTHROPIC_API_KEY','OPENAI_API_KEY']) delete env[key];
  const server = spawn(python, ['-B','scripts/preview_fixture.py','--port','0'], {cwd:ROOT,env,stdio:['ignore','pipe','pipe']});
  let output = '', stderr = '', browser, spawnError;
  server.on('error', error => { spawnError = error; });
  server.stdout.on('data', chunk => { output += chunk; }); server.stderr.on('data', chunk => { stderr += chunk; });
  try {
    const deadline = Date.now() + 15000;
    while (!/http:\/\/127\.0\.0\.1:\d+/.test(output) && Date.now() < deadline && server.exitCode === null && !spawnError) await new Promise(r => setTimeout(r,50));
    const base = output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0]; assert.ok(base, spawnError?.message || stderr);
    browser = await chromium.launch({executablePath:process.env.CHROME || '/usr/bin/google-chrome',headless:true});
    const context = await browser.newContext(), page = await context.newPage(), errors = [];
    let opens = 0, closes = 0, iframeLoads = 0;
    page.on('pageerror', error => { if (error.message !== 'Transition was skipped') errors.push(error.message); });
    await page.route('**/api/terminal/open', async route => { opens++; await route.fulfill({json:{url:'/terminal/status-fixture/'}}); });
    await page.route('**/api/terminal/close', async route => { closes++; await route.fulfill({json:{ok:true}}); });
    await page.route('**/terminal/status-fixture/', async route => {
      iframeLoads++;
      await route.fulfill({contentType:'text/html',body:'<!doctype html><title>Terminal fixture</title><textarea aria-label="terminal draft"></textarea>'});
    });
    const location = await (await context.request.get(base + '/api/library/location')).json();
    assert.equal(location.temp, true, 'only a disposable library may be configured');
    const initial = await (await context.request.get(base + '/api/library')).json();
    const preferences = await context.request.post(base + '/api/prefs', {
      headers:{Origin:base,'X-Token':initial.token}, data:{ui:{features:{multi_tabs:false}}}
    });
    assert.equal(preferences.status(), 200);
    await page.goto(base + '/');
    await page.waitForFunction(() => !!window.PR?.openSettings);
    await page.evaluate(() => PR.openSettings('terminal'));
    await page.waitForFunction(() => PR.settingsState.cliStatus?.items?.find(item => item.id === 'pi')?.installed === false);
    await page.locator('[data-cli-action="pi-install"]').click();
    const input = page.frameLocator('#terminalMount iframe').getByRole('textbox', {name:'terminal draft'});
    await input.fill('preserve terminal input and identity');
    await page.evaluate(() => { window.savedTerminalFrame = document.querySelector('#terminalMount iframe'); });
    // Simulates npm publishing its executable after the panel cached 'missing'.
    install('pi', '1.6.0');
    await page.waitForFunction(() => PR.settingsState.cliStatus?.items?.find(item => item.id === 'pi')?.version === '1.6.0', null, {timeout:22000});
    assert.equal(await page.locator('[data-cli-action="pi-login"]').isEnabled(), true);
    assert.equal(await page.locator('[data-cli-action="pi-install"]').count(), 0);
    assert.equal(await input.inputValue(), 'preserve terminal input and identity');
    assert.equal(await page.evaluate(() => savedTerminalFrame === document.querySelector('#terminalMount iframe')), true);
    install('pi', '1.6.1'); await page.locator('#refreshCliStatus').click();
    await page.waitForFunction(() => PR.settingsState.cliStatus.items.find(item => item.id === 'pi').version === '1.6.1');
    assert.equal(opens, 1); assert.equal(closes, 0); assert.equal(iframeLoads, 1);
    await page.setViewportSize({width:390,height:844});
    const dimensions = await page.locator('.terminal-toolbar').evaluate(el => ({scroll:el.scrollWidth,width:el.clientWidth}));
    assert.ok(dimensions.scroll <= dimensions.width, JSON.stringify(dimensions));
    assert.equal(await input.inputValue(), 'preserve terminal input and identity');
    await page.locator('#setCancel').click();
    await page.waitForFunction(() => !document.querySelector('#settingsDlg').classList.contains('open'));
    assert.equal(closes, 1);
    assert.deepEqual(errors, []);
    const commands = fs.readFileSync(log, 'utf8').trim().split('\n');
    assert.ok(commands.every(command => command === '--version'), JSON.stringify(commands));
    console.log(JSON.stringify({passed:true,checks:['installed-after-open auto refresh','manual rescan','Pi login enabled','iframe retained once','draft retained','mobile toolbar','explicit close'],opens,closes,iframeLoads,versionProbes:commands.length},null,2));
    await context.close();
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => {
      if (server.exitCode !== null || spawnError) return resolve();
      const timer = setTimeout(() => { server.kill('SIGKILL'); resolve(); },4000);
      server.once('exit', () => { clearTimeout(timer); resolve(); });
    });
    fs.rmSync(tmp,{recursive:true,force:true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
