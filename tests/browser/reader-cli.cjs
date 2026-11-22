/* Paper sidebar + real local fake-CLI subprocesses. No providers or real library.
   PYTHON=<venv> NODE_PATH=<playwright modules> node tests/browser/reader-cli.cjs */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'../..');
(async()=>{
  const env={...process.env,READER_RESTRICTED:'1',PYTHONDONTWRITEBYTECODE:'1',NO_PROXY:'localhost,127.0.0.1'};
  for(const key of Object.keys(env))if(/API_KEY|AUTH_TOKEN|BASE_URL/.test(key))delete env[key];
  const proc=spawn(process.env.PYTHON||'python',['-u','-m','tests.reader_cli_fixture'],{cwd:ROOT,env,stdio:['ignore','pipe','pipe']});
  let output='',stderr='',browser,info;
  proc.stdout.on('data',c=>output+=c);proc.stderr.on('data',c=>stderr+=c);
  try {
    const deadline=Date.now()+15000;
    while(!/http:\/\/127\.0\.0\.1:\d+/.test(output)&&Date.now()<deadline&&proc.exitCode===null)await new Promise(r=>setTimeout(r,50));
    const base=output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0];assert.ok(base,stderr);
    info=JSON.parse(output.split('\n').find(l=>l.startsWith('READER_FIXTURE ')).slice(15));
    browser=await chromium.launch({executablePath:process.env.CHROME||'/usr/bin/google-chrome',headless:true});
    const context=await browser.newContext(),page=await context.newPage(),errors=[];
    page.on('pageerror',e=>{if(e.message!=='Transition was skipped')errors.push(e.message);});
    const get=async url=>{const r=await context.request.get(base+url);assert.equal(r.status(),200);return r.json();};
    const library=await get('/api/library');
    const prefs=await context.request.post(base+'/api/prefs',{headers:{Origin:base,'X-Token':library.token},data:{ui:{features:{multi_tabs:false}}}});
    assert.equal(prefs.status(),200);
    await page.goto(base+'/read/reader-fixture');
    await page.waitForFunction(()=>!!window.PR?.chatAsk&&!!PR.blockById?.p1,{},{timeout:10000}).catch(async error=>{console.error(await page.evaluate(()=>({url:location.href,text:document.body.innerText.slice(0,800)})),errors);throw error;});
    async function question(engine) {
      await page.evaluate(()=>PR.toggleChat(true));
      await page.locator('#chatpanel [data-c="new"]').first().click();
      await page.locator('#chatpanel [data-c="menu"]').click();
      await page.locator('#chatpanel [data-c="model"][data-m="'+engine+'-test"]').click();
      await page.evaluate(()=>PR.chatAsk({anchor:'p1',quote:'Local paragraph 1.'}));
      await page.locator('#chatInput').fill('Read the distant final paragraph, which was not included in this selection.');
      const response=page.waitForResponse(r=>r.url().endsWith('/api/p/reader-fixture/chat')&&r.request().method()==='POST');
      await page.locator('#chatInput').press('Enter');
      const r=await response;assert.equal(r.status(),200);
      assert.equal(r.request().postDataJSON().context_scope,'selection');
      await page.waitForFunction(()=>!!document.querySelector('#chatList .cm.ai:last-child .body')?.innerText.trim()&&!document.querySelector('#chatpanel [data-c="stop"]'));
      const answer=await page.locator('#chatList .cm.ai:last-child .body').innerText();
      const saved=await get('/api/p/reader-fixture/chat');
      assert.ok(saved.threads.some(t=>t.messages.some(m=>m.role==='assistant'&&answer.includes(m.content.split('\n')[0]))),'answer persisted through the real reader endpoint');
      return answer;
    }
    for(const engine of ['codex','claude','pi'])assert.match(await question(engine),/DISTANT-PAPER-READ-7c92/);
    await page.evaluate(()=>PR.openSettings('chat'));
    await page.evaluate(()=>PR.openQaCli('codex','codex-test'));
    await page.waitForFunction(()=>!!PR.settingsState.qaCli?.settings&&!PR.settingsState.qaCli.loading);
    await page.locator('[data-qa-mode="full"]').click();
    assert.equal(await page.locator('dialog.confirm[open]').count(),0,'full mode needs no redundant confirmation');
    assert.equal(await page.locator('[data-qa="disable_sandbox"]').count(),0);
    await page.locator('[data-qa-save]').click();
    await page.waitForFunction(()=>!PR.settingsState.qaCli.saving&&PR.settingsState.qaCli.message.includes('已保存'));
    const policy=await get('/api/chat/cli/codex/models/codex-test');assert.equal(policy.settings.mode,'full');
    assert.equal((await get('/api/config')).config.engine,'none','translation configuration is unchanged');
    await page.evaluate(()=>PR.settingsClose());
    for(const mode of ['full','custom'])for(const engine of ['codex','claude','pi']) {
      const r=await context.request.post(base+'/api/chat/cli/'+engine+'/models/'+engine+'-test',
        {headers:{Origin:base,'X-Token':library.token},data:{mode,allowed_tools:[],web_search:false,web_fetch:false}});
      assert.equal(r.status(),200);
      assert.match(await question(engine),/DISTANT-PAPER-READ-7c92/);
    }
    const calls=fs.readFileSync(info.log,'utf8').trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls.map(c=>c.engine),['codex','claude','pi','codex','claude','pi','codex','claude','pi']);
    assert.ok(calls.every(c=>!c.inline&&c.cwd.endsWith('/reader-fixture')));
    assert.ok(calls.filter(c=>c.engine==='codex').every(c=>c.argv.includes('--dangerously-bypass-approvals-and-sandbox')&&!c.blocked));
    const notices=(await get('/api/research/v1/monitor')).activities.filter(item=>item.activity_kind==='reader-chat');
    assert.equal(notices.length,calls.length);assert.ok(notices.every(item=>item.state==='completed'));
    assert.ok(!JSON.stringify(notices).includes('DISTANT-PAPER-READ-7c92'),'notification summaries do not expose answer text');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['actual reader selection request','default file access without setup','full mode without extra confirmation','all three engines read paper in lean/full/empty-custom modes','reader completion reaches task capsule without answer leakage','translation unchanged'],cliCalls:calls.length},null,2));
    await context.close();
  } finally {
    if(browser)await browser.close();
    proc.kill('SIGINT');
    await new Promise(resolve=>{if(proc.exitCode!==null)return resolve();const timer=setTimeout(()=>{proc.kill('SIGKILL');resolve();},5000);proc.once('exit',()=>{clearTimeout(timer);resolve();});});
    if(info&&fs.existsSync(info.root))fs.rmSync(info.root,{recursive:true,force:true});
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
