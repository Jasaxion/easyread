/* Full local fixture: actual OpenSSH + ttyd, synthetic Pi records, no NAS/user data.
   PYTHON=<workspace deps> NODE_PATH=<playwright node_modules> TTYD=<binary> node tests/browser/workspace-recovery.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {spawn, execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const ROOT = path.resolve(__dirname, '../..');
const SERVER = String.raw`
import json,os,sys,signal,threading
from pathlib import Path
from tests.test_workspace_ssh_integration import SSHWorkspaceIntegration as T
from tests.test_workspace_remote_terminal_ttyd import PROGRAM
from easyread.app import config
from easyread.server import server,launch,workspaces_api
from easyread.workspaces import session_cli as cli
from easyread.workspaces.sessions import Sessions
from easyread.workspaces.remote_papers import ReferenceStore
T.setUpClass()
httpd=app=None
try:
    T.wrapper.write_text(T.wrapper.read_text().replace('#!/bin/sh\n', '#!/bin/sh\nexport EASYREAD_TERMINAL=1\nexport EASYREAD_HOME="$HOME"\n',1))
    ttyd=T.tools/'ttyd';ttyd.write_text('#!/bin/sh\nexec '+__import__('shlex').quote(os.environ['FIXTURE_TTYD'])+' "$@"\n');ttyd.chmod(0o700)
    program=PROGRAM.replace('state = sys.argv[1]', 'state = "tty-state.json"').replace('os.write(1, data)', 'open("tty-input.bin", "ab").write(data)\n    os.write(1, data)')
    fake=T.tools/'pi';marker='import json, os, pathlib, sys\n'
    fake.write_text(fake.read_text().replace(marker,marker+'if "--mode" not in sys.argv:\n    open("tty-argv.json", "w").write(json.dumps(sys.argv[1:]))\n    exec('+repr(program)+')\n    sys.exit(0)\n',1))
    os.environ['PATH']=str(T.client)+':/usr/bin:/bin'
    app=server.App(config.load());server.Handler.app=app
    work=workspaces_api.manager(app)
    remote=work.remotes.save_server({'name':'Disposable SSH','host':'127.0.0.1','port':T.ssh_port,'user':T.user,'workspace':str(T.remote_root),'identity_file':str(T.base/'userkey'),'python':str(T.wrapper)})['server']
    project=work.remotes.create({'name':'Recovery fixture','engine':'pi','server_id':remote['id']})['project'];pid=project['id']
    with work.remotes.connection(remote['id']) as conn:
        good=conn.json('POST','/api/workspaces/'+pid+'/sessions/create',{'engine':'pi','title':'Healthy Pi','model':'provider/model'})['session']
        bad=conn.json('POST','/api/workspaces/'+pid+'/sessions/create',{'engine':'pi','title':'Needs reconciliation','model':'provider/model'})['session']
    store=ReferenceStore(T.remote_root);project_root=store.root(pid)
    native=cli.project_home(store.meta(pid),'pi')/'sessions';native.mkdir(parents=True,exist_ok=True)
    def transcript(record, broken=False):
        rows=[{'type':'session','version':3,'id':record['native_id'],'cwd':str(project_root)},
          {'type':'message','id':'u','parentId':None,'message':{'role':'user','content':'Saved question'}},
          {'type':'message','id':'a','parentId':'u','message':{'role':'assistant','content':[{'type':'text','text':'Saved answer'}]}},
          {'type':'message','id':'bash','parentId':'a','message':{'role':'bashExecution','command':'echo '+str(project_root)+'/keep-native-path','output':'native-update-result','exitCode':0,'cancelled':False,'truncated':False,'excludeFromContext':True,'timestamp':1}}]
        if broken:rows.append({'type':'message','id':'broken','parentId':'bash','message':{'role':'assistant'}})
        out=native/('2026-01-01T00-00-00-000Z_'+record['native_id']+'.jsonl')
        out.write_text(''.join(json.dumps(row)+'\n' for row in rows));return out
    good_path=transcript(good);bad_path=transcript(bad,True)
    local=Sessions(store)
    try:
        local._commit(pid,lambda data:[data['sessions'][r['id']].update(native_started=True,terminal_tracking={'baseline':[],'message_count':0}) for r in (good,bad)])
    finally:local.close()
    httpd=launch._Server(('127.0.0.1',0),server.Handler)
    print('WORKSPACE_FIXTURE '+json.dumps({'base':'http://127.0.0.1:'+str(httpd.server_port),'root':str(T.base),'project':pid,'good':good['id'],'bad':bad['id'],'native_id':good['native_id'],'good_path':str(good_path),'bad_path':str(bad_path),'project_root':str(project_root)}),flush=True)
    def stop(*args):threading.Thread(target=httpd.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    httpd.serve_forever()
finally:
    if app:
        workspaces_api.close(app);app.public_api.close();app.terminals.close()
    if httpd:httpd.server_close()
    T.doClassCleanups()
`;
(async () => {
  const python=execFileSync(process.env.PYTHON||'python',['-B','-c','import sys;print(sys.executable)'],{encoding:'utf8'}).trim();
  const ttyd=process.env.TTYD||execFileSync('which',['ttyd'],{encoding:'utf8'}).trim();
  const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'easyread-workspace-browser-'));
  for(const dir of ['app','home','library'])fs.mkdirSync(path.join(tmp,dir));
  const env={...process.env,EASYREAD_HOME:path.join(tmp,'app'),EASYREAD_LIBRARY:path.join(tmp,'library'),HOME:path.join(tmp,'home'),
    EASYREAD_ACCESS_CODE:'',EASYREAD_TERMINAL:'1',EASYREAD_TEST_SSH:'1',EASYREAD_SYSTEM_LANG:'zh-CN',FIXTURE_TTYD:ttyd,PYTHONDONTWRITEBYTECODE:'1'};
  for(const key of ['CODEX_HOME','CLAUDE_CONFIG_DIR','PI_CODING_AGENT_DIR','ANTHROPIC_API_KEY','OPENAI_API_KEY'])delete env[key];
  const proc=spawn(python,['-B','-u','-c',SERVER],{cwd:ROOT,env,stdio:['ignore','pipe','pipe']});
  let stdout='',stderr='',spawnError,browser,info;
  proc.on('error',e=>{spawnError=e;});proc.stdout.on('data',c=>{stdout+=c;});proc.stderr.on('data',c=>{stderr+=c;});
  try {
    const deadline=Date.now()+20000;
    while(!stdout.includes('WORKSPACE_FIXTURE ')&&Date.now()<deadline&&proc.exitCode===null&&!spawnError)await new Promise(r=>setTimeout(r,50));
    const line=stdout.split('\n').find(line=>line.startsWith('WORKSPACE_FIXTURE '));assert.ok(line,spawnError?.message||stderr);
    info=JSON.parse(line.slice('WORKSPACE_FIXTURE '.length));assert.ok(info.root.startsWith(path.join(os.tmpdir(),'easyread-real-ssh-')));
    browser=await chromium.launch({executablePath:process.env.CHROME||'/usr/bin/google-chrome',headless:true});
    const context=await browser.newContext({locale:'zh-CN'}),page=await context.newPage(),errors=[];
    page.on('pageerror',e=>{if(e.message!=='Transition was skipped')errors.push(e.message);});
    const base=info.base,prefix='/api/workspaces/'+info.project;
    async function get(url) { const r=await context.request.get(base+url);assert.equal(r.status(),200,await r.text());return r.json(); }
    const listing=await get('/api/workspaces');
    const headers={Origin:base,'X-Token':listing.token};
    assert.equal((await context.request.post(base+'/api/prefs',{headers,data:{ui:{features:{multi_tabs:false}}}})).status(),200);
    await page.addInitScript(({base,key,value})=>{if(location.origin===base)localStorage.setItem(key,JSON.stringify(value));},
      {base,key:'easyread-workspaces:'+listing.library_id+':'+info.project+':session',value:info.good});
    let failSessions=true;
    await page.route(base+prefix+'/sessions',route=>failSessions?route.fulfill({status:409,json:{error:'Invalid Pi message',code:'invalid_transcript'}}):route.continue());
    await page.goto(base+'/workspaces?project='+info.project);
    await page.waitForFunction(()=>!!PR.workspaces.sessions.loadError);
    await page.locator('#ws-tree').getByRole('button',{name:'README.md',exact:true}).click();
    await page.waitForFunction(()=>PR.workspaces.files.active?.path==='README.md');
    assert.equal(await page.locator('#ws-remote-status').evaluate(el=>el.classList.contains('is-offline')),false);
    assert.match(await page.locator('#ws-session-notice').innerText(),/项目文件仍可使用/);
    // Edit and save a real remote file while the session endpoint is unavailable.
    await page.locator('#ws-editor .cm-content').click();await page.keyboard.press('Control+End');await page.keyboard.type('\nBrowser file edit survives session errors.');
    await page.waitForFunction(()=>PR.workspaces.files.active?.content.includes('Browser file edit survives'));
    await page.locator('#ws-save').click();
    await page.waitForFunction(()=>!PR.workspaces.files.active.saving&&!PR.workspaces.files.active.dirty);
    assert.match((await get(prefix+'/file?path=README.md')).content,/Browser file edit survives/);
    failSessions=false;await page.getByRole('button',{name:'重新读取会话',exact:true}).click();
    await page.waitForFunction(id=>PR.workspaces.sessions.current?.id===id&&!PR.workspaces.sessions.loadError,info.good);
    assert.match(await page.locator('#ws-messages').innerText(),/Saved answer/);
    assert.equal(await page.evaluate(()=>PR.workspaces.sessions.current.native_sync_warning),undefined);
    await page.locator('#ws-session-select').selectOption(info.bad,{force:true});
    await page.waitForFunction(()=>!!PR.workspaces.sessions.current?.native_sync_warning);
    assert.equal(await page.locator('#ws-send').isDisabled(),true);assert.equal(await page.locator('#ws-terminal').isDisabled(),true);
    assert.equal(await page.evaluate(()=>PR.workspaces.files.active.path),'README.md');
    await page.locator('#ws-session-select').selectOption(info.good,{force:true});
    await page.waitForFunction(id=>PR.workspaces.sessions.current?.id===id,info.good);
    await page.locator('#ws-message').fill('Continue the same native conversation');await page.locator('#ws-message').press('Enter');
    await page.waitForFunction(()=>document.getElementById('ws-messages').innerText.includes('answer 2'));
    assert.equal(await page.evaluate(()=>PR.workspaces.sessions.current.native_id),info.native_id);
    const native=fs.readFileSync(info.good_path,'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(native.find(row=>row.message?.role==='bashExecution').message.output,'native-update-result');
    // Repair only a synthetic fixture. Normal reads clear only its scoped warning.
    const bad=fs.readFileSync(info.bad_path,'utf8').trim().split('\n').map(JSON.parse);
    bad.at(-1).message.content=[{type:'text',text:'Repaired test record'}];
    fs.writeFileSync(info.bad_path+'.repair',bad.map(row=>JSON.stringify(row)).join('\n')+'\n');fs.renameSync(info.bad_path+'.repair',info.bad_path);
    const repaired=await get(prefix+'/sessions/'+info.bad);assert.equal(repaired.session.native_sync_warning,undefined);
    const exported=await context.request.get(base+prefix+'/export');assert.equal(exported.status(),200,await exported.text());
    const archive=await exported.body();assert.ok(archive.length>0);
    await page.locator('#ws-message').fill('Unsent draft must survive theme changes');
    await page.locator('#ws-terminal').click();
    await page.waitForFunction(()=>!!document.querySelector('#ws-terminal-frames iframe')?.contentWindow?.term);
    await page.evaluate(()=>{window.fixtureFrame=document.querySelector('#ws-terminal-frames iframe');});
    const input=page.frameLocator('#ws-terminal-frames iframe').locator('.xterm-helper-textarea');await input.focus();await page.keyboard.type('theme-echo');
    await page.waitForFunction(()=>Array.from({length:fixtureFrame.contentWindow.term.buffer.active.length},(_,i)=>fixtureFrame.contentWindow.term.buffer.active.getLine(i)?.translateToString(true)||'').join('\n').includes('theme-echo'));
    const before=JSON.parse(fs.readFileSync(path.join(info.project_root,'tty-state.json'),'utf8'));
    await page.locator('#ws-terminal-theme-select').selectOption('modern-light',{force:true});
    await page.waitForFunction(()=>fixtureFrame.contentWindow.term.options.theme.background==='#fffcf0');
    const palette=await page.evaluate(()=>fixtureFrame.contentWindow.term.options.theme);
    const expected={foreground:'#100f0f',background:'#fffcf0',cursor:'#100f0f',black:'#100f0f',red:'#af3029',green:'#66800b',yellow:'#ad8301',blue:'#205ea6',magenta:'#a02f6f',cyan:'#24837b',white:'#6f6e69',brightBlack:'#b7b5ac',brightRed:'#d14d41',brightGreen:'#879a39',brightYellow:'#d0a215',brightBlue:'#4385be',brightMagenta:'#ce5d97',brightCyan:'#3aa99f',brightWhite:'#cecdc3'};
    for(const [key,value] of Object.entries(expected))assert.equal(palette[key],value,key);
    await page.locator('#ws-terminal-theme-select').selectOption('dark',{force:true});
    await page.waitForFunction(()=>fixtureFrame.contentWindow.term.options.theme.background==='#100f0f');
    await page.locator('#ws-terminal-theme-select').selectOption('auto',{force:true});
    await page.evaluate(()=>{document.documentElement.dataset.theme='light';});
    await page.waitForFunction(()=>fixtureFrame.contentWindow.term.options.theme.background==='#fffcf0');
    await page.evaluate(()=>{document.documentElement.dataset.theme='dark';});
    await page.waitForFunction(()=>fixtureFrame.contentWindow.term.options.theme.background==='#100f0f');
    await page.locator('#ws-terminal-theme-select').selectOption('modern-light',{force:true});
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(await page.evaluate(()=>fixtureFrame===document.querySelector('#ws-terminal-frames iframe')),true);
    assert.equal(JSON.parse(fs.readFileSync(path.join(info.project_root,'tty-state.json'),'utf8')).pid,before.pid);
    await page.locator('#ws-terminal').click();assert.equal(await page.locator('#ws-message').inputValue(),'Unsent draft must survive theme changes');
    await page.locator('#ws-terminal').click();assert.equal(await page.evaluate(()=>fixtureFrame===document.querySelector('#ws-terminal-frames iframe')),true);
    if(process.env.WORKSPACE_SCREENSHOT) {
      await page.locator('#ws-terminal-frames').scrollIntoViewIfNeeded();
      await page.screenshot({path:process.env.WORKSPACE_SCREENSHOT});
    }
    await page.locator('#ws-terminal-stop').click();await page.locator('dialog.confirm[open]').getByRole('button',{name:'停止并关闭终端',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#ws-terminal-frames iframe').length===0);
    await page.reload();
    await page.waitForFunction(id=>PR.workspaces.sessions.current?.id===id,info.good);
    assert.equal(await page.evaluate(()=>PR.terminalTheme.getSelection()),'modern-light');
    assert.match(await page.locator('#ws-messages').innerText(),/answer 2/);
    assert.equal(await page.evaluate(()=>PR.workspaces.sessions.current.native_id),info.native_id);
    assert.match((await get(prefix+'/file?path=README.md')).content,/Browser file edit survives/);
    // Browser refresh while a PTY is active must detach/stop, not silently spawn
    // a replacement. Reopening must use the existing native transcript path.
    const historyCount=(await get(prefix+'/sessions/'+info.good)).session.messages.length;
    await page.locator('#ws-terminal').click();
    await page.waitForFunction(()=>!!document.querySelector('#ws-terminal-frames iframe')?.contentWindow?.term);
    await page.frameLocator('#ws-terminal-frames iframe').locator('.xterm-helper-textarea').focus();await page.keyboard.type('refresh-check');
    await page.waitForFunction(()=>{const t=document.querySelector('#ws-terminal-frames iframe').contentWindow.term;return Array.from({length:t.buffer.active.length},(_,i)=>t.buffer.active.getLine(i)?.translateToString(true)||'').join('\n').includes('refresh-check');});
    const activePid=JSON.parse(fs.readFileSync(path.join(info.project_root,'tty-state.json'),'utf8')).pid;
    const args=JSON.parse(fs.readFileSync(path.join(info.project_root,'tty-argv.json'),'utf8'));
    assert.equal(args[args.indexOf('--session')+1],info.good_path);assert.ok(!args.includes('--session-id'));
    page.on('dialog',dialog=>dialog.accept());
    await page.reload();
    await page.waitForFunction(id=>PR.workspaces.sessions.current?.id===id&&!PR.workspaces.sessions.current.terminal_open,info.good);
    assert.equal(await page.locator('#ws-terminal-frames iframe').count(),0);
    const processState='/proc/'+activePid+'/stat';
    assert.ok(!fs.existsSync(processState)||fs.readFileSync(processState,'utf8').split(' ')[2]==='Z','active PTY survived browser refresh');
    assert.equal((await get(prefix+'/sessions/'+info.good)).session.messages.length,historyCount);
    assert.equal(await page.evaluate(()=>PR.workspaces.sessions.current.native_id),info.native_id);
    await page.locator('#ws-terminal').click();
    await page.waitForFunction(()=>document.querySelector('#ws-terminal-frames iframe')?.contentWindow?.term?.options.theme?.background==='#fffcf0');
    assert.equal(await page.evaluate(()=>PR.workspaces.sessions.current.native_id),info.native_id);
    await page.locator('#ws-terminal-stop').click();await page.locator('dialog.confirm[open]').getByRole('button',{name:'停止并关闭终端',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#ws-terminal-frames iframe').length===0);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['files remain usable on session failure','same-ID Pi bashExecution resume','corrupt session isolated/reconciled','remote file edit/save','strict export after repair','real SSH/ttyd input','exact modern-light palette','live dark/auto/light switching','same iframe/PID and draft retained','390px layout','stop/reload history and preference retained','active refresh stops PTY without replay','reopen resumes original native path']},null,2));
    await context.close();
  } finally {
    if(browser)await browser.close();
    proc.kill('SIGTERM');
    await new Promise(resolve=>{if(proc.exitCode!==null||spawnError)return resolve();const timer=setTimeout(()=>{proc.kill('SIGKILL');resolve();},6000);proc.once('exit',()=>{clearTimeout(timer);resolve();});});
    if(info)assert.equal(fs.existsSync(info.root),false,'SSH fixture cleanup did not finish');
    fs.rmSync(tmp,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
