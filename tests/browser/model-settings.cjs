/* Local-only model settings regression. Requires Python project dependencies and Playwright.
   PYTHON=/path/to/python NODE_PATH=/path/to/node_modules node tests/browser/model-settings.cjs
   Uses a fake Codex executable; never contacts a model provider or the NAS. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const ROOT = path.resolve(__dirname,'../..');
(async()=>{
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'easyread-model-browser-'));
  const home=path.join(tmp,'home'),bin=path.join(tmp,'bin');
  fs.mkdirSync(path.join(home,'.codex'),{recursive:true});fs.mkdirSync(bin);
  fs.writeFileSync(path.join(home,'.codex','config.toml'),'model = "fixture-native-default"\nmodel_reasoning_effort = "medium"\n');
  const cliLog=path.join(tmp,'cli.log');
  fs.writeFileSync(path.join(bin,'codex'),'#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_CLI_LOG"\ncase "$1" in --version|-v|-V) echo "codex-cli 0.169.0";; *) echo "Model execution forbidden in this fixture" >&2; exit 99;; esac\n',{mode:0o755});
  const env={...process.env,HOME:home,PATH:bin+path.delimiter+'/usr/bin'+path.delimiter+'/bin',FAKE_CLI_LOG:cliLog,
    EASYREAD_ACCESS_CODE:'',PYTHONDONTWRITEBYTECODE:'1',NO_PROXY:'localhost,127.0.0.1'};
  for(const key of ['CODEX_HOME','CLAUDE_CONFIG_DIR','EASYREAD_HOME','EASYREAD_LIBRARY','ANTHROPIC_API_KEY','OPENAI_API_KEY']) delete env[key];
  const server=spawn(process.env.PYTHON || 'python',['scripts/preview_fixture.py','--port','0','--with-web'],{cwd:ROOT,env,stdio:['ignore','pipe','pipe']});
  let output='',stderr='';server.stdout.on('data',chunk=>{output+=chunk;});server.stderr.on('data',chunk=>{stderr+=chunk;});
  let browser;
  try {
    const deadline=Date.now()+15000;
    while(!/http:\/\/127\.0\.0\.1:\d+/.test(output)&&Date.now()<deadline&&server.exitCode===null) await new Promise(r=>setTimeout(r,50));
    const base=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(base,stderr);
    browser=await chromium.launch({executablePath:process.env.CHROME || '/usr/bin/google-chrome',headless:true});
    const context=await browser.newContext(), page=await context.newPage(), errors=[];
    page.on('pageerror',e=>{if(e.message!=='Transition was skipped') errors.push(e.message);});
    async function get(url) {const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();}
    const initial=await get('/api/library');assert.ok(initial.items.some(i=>i.id==='sample-paper'));
    assert.equal((await get('/api/library/location')).temp,true);
    const headers={Origin:base,'X-Token':initial.token};
    const setup=await context.request.post(base+'/api/config',{headers,data:{engine:'codex',codex:{model:'legacy-translator'},auto_translate:false}});
    assert.equal(setup.status(),200);
    await page.goto(base+'/?settings=chat');
    await page.waitForFunction(()=>PR.settingsState?.sources?.some(cli=>cli.id==='codex'&&cli.installed));
    const legacy=await page.evaluate(()=>PR.settingsState.chat.models.find(m=>m._unsaved && m.model==='legacy-translator'));
    assert.ok(legacy);
    await page.evaluate(id=>PR.openQaCli('codex',id),legacy.id);
    await page.waitForFunction(()=>PR.settingsState.qaCli?.settings && !PR.settingsState.qaCli.loading);
    assert.equal(await page.evaluate(()=>PR.settingsState.qaCli.model_id),legacy.id);
    assert.equal((await get('/api/config')).config.codex.model,'legacy-translator');
    assert.equal((await get('/api/config')).config.auto_translate,false);
    assert.ok((await get('/api/chat/models')).models.some(m=>m.id===legacy.id));
    await page.locator('[data-qa-back]').click();
    async function add(model) {
      await page.locator('[data-cm="add"]').click();
      await page.locator('[data-model-source]').selectOption('codex');
      assert.equal(await page.locator('#cmModel').evaluate(el=>el.tagName),'INPUT');
      assert.match(await page.locator('.model-editor').innerText(),/models_cache.json.*缺失/);
      await page.locator('#cmModel').fill(model);await page.locator('#cmModel').press('Tab');
      await page.locator('[data-cm="ok"]').click();
      await page.waitForFunction(()=>!PR.settingsState.editing && PR.settingsState.modelSavePhase==='saved');
      const entry=(await get('/api/chat/models')).models.find(m=>m.model===model);
      assert.ok(entry);return entry.id;
    }
    const a=await add('fixture-translate-a'),b=await add('fixture-answer-b');
    const index=async id=>page.evaluate(id=>PR.settingsState.chat.models.findIndex(m=>m.id===id),id);
    await page.locator('[data-cm="translate"][data-i="'+await index(a)+'"]').click();
    await page.evaluate(()=>PR.flushModelSettings());
    await page.locator('[data-cm="chat-default"][data-i="'+await index(b)+'"]').click();
    await page.evaluate(()=>PR.flushModelSettings());
    const roles=(await get('/api/config')).config;
    assert.equal(roles.codex.model,'fixture-translate-a');assert.equal(roles.chat.default,b);
    async function open(id) {
      if(await page.locator('[data-qa-back]').count()) await page.locator('[data-qa-back]').click();
      await page.locator('.model-entry[data-i="'+await index(id)+'"] [data-cm="more"]').last().click();
      await page.locator('#ctxmenu').getByText('AI 问答 · CLI 功能管理',{exact:true}).click();
      await page.waitForFunction(id=>PR.settingsState.qaCli?.model_id===id && !PR.settingsState.qaCli.loading && !!PR.settingsState.qaCli.settings,id);
    }
    async function choose(mode) {
      await page.locator('[data-qa-mode="'+mode+'"]').click();
      const confirm=page.locator('dialog.confirm[open]');
      if(await confirm.count()) await confirm.getByRole('button',{name:'开启',exact:true}).click();
      await page.locator('[data-qa-save]').click();
      await page.waitForFunction(()=>!PR.settingsState.qaCli.saving && PR.settingsState.qaCli.message.includes('已保存'));
    }
    await open(b);assert.equal(await page.locator('[data-qa-mode="inherit"]').getAttribute('aria-pressed'),'true');
    await choose('full');
    await open(a);await choose('default');
    assert.equal((await get('/api/chat/cli/codex/models/'+a)).settings.mode,'default');
    assert.equal((await get('/api/chat/cli/codex/models/'+b)).settings.mode,'full');
    assert.equal((await get('/api/config')).config.codex.model,'fixture-translate-a');
    await page.locator('[data-qa-global]').click();
    await page.waitForFunction(()=>!PR.settingsState.qaCli.model_id && !!PR.settingsState.qaCli.settings);
    await choose('full');
    assert.equal((await get('/api/chat/cli/codex/models/'+a)).settings.mode,'default');
    await open(a);await choose('inherit');
    assert.equal((await get('/api/chat/cli/codex/models/'+a)).settings.mode,'full');
    await choose('default');
    await page.setViewportSize({width:390,height:844});
    const rect=await page.locator('.qa-modes').boundingBox();assert.ok(rect.width<=390);
    if(process.env.MODEL_SCREENSHOT) await page.screenshot({path:process.env.MODEL_SCREENSHOT});
    await page.reload();await page.locator('#settingsBtn').click();
    await page.waitForFunction(()=>!!PR.settingsState.chat);
    await open(b);
    assert.equal(await page.locator('[data-qa-mode="full"]').getAttribute('aria-pressed'),'true');
    await open(a);assert.equal(await page.locator('[data-qa-mode="default"]').getAttribute('aria-pressed'),'true');
    const saved=(await get('/api/config')).config;
    assert.equal(saved.codex.model,'fixture-translate-a');assert.equal(saved.chat.default,b);
    const log=fs.existsSync(cliLog)?fs.readFileSync(cliLog,'utf8').trim().split('\n'):[];
    assert.ok(log.every(line=>['--version','-v','-V'].includes(line)),JSON.stringify(log));
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['missing-cache editable IDs','synthesized entry persistence','distinct translation and Q&A models','entry modes isolated','global inheritance explicit','reload persistence','mobile mode layout'],cliMetadataCalls:log.length,errors},null,2));
    await context.close();
  } finally {
    if(browser) await browser.close();
    server.kill('SIGTERM');
    await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(()=>{server.kill('SIGKILL');resolve();},4000))]);
    fs.rmSync(tmp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
