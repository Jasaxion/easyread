const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function fixture(engine='claude') {
  const calls=[];
  const PR={settingsTabs:{},t:x=>x,esc:x=>String(x).replaceAll('<','&lt;'),opt:()=>'',confirm:async()=>true,settingsRender(){},$:()=>({}),
    api:async(url,args)=>{calls.push({url,args});return {settings:args.body,skills:[],native:{path:'/native/settings'}};}};
  const FileReader = class { readAsDataURL() { this.result = 'data:text/markdown;base64,IyBTa2lsbA=='; this.onload(); } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../easyread/web/js/common/settings-qa-cli.js'),'utf8'),{window:{PR},Object,Promise,FileReader});
  const s={tab:'qa_cli',cfg:{embedded_terminal:true},qaCli:{engine,loading:false,tools:engine==='pi'?['read','grep','find','ls']:['shell','web_search'],skills:[],native:{path:'/native/settings'},settings:{mode:'default',skills:false,mcp:false,web_search:true,web_fetch:true,allowed_tools:[],sandbox:'read-only',skill_overrides:{}}}};
  return {PR,s,calls,tab:PR.settingsTabs.qa_cli,root:{querySelectorAll:()=>[],querySelector:()=>null}};
}

test('provider subpages render scope, modes, skills and native setting link',()=>{
  for(const engine of ['claude','codex','pi']) {
    const f=fixture(engine), html=f.tab.render(f.s);
    assert.ok(html.includes('data-qa-mode="default"'));
    assert.ok(html.includes('data-qa-mode="full"'));
    assert.ok(html.includes('data-qa-mode="custom"'));
    assert.ok(html.includes('不影响翻译'));
    assert.ok(html.includes('data-qa-import'));
    assert.ok(html.includes('/native/settings'));
    assert.ok(html.includes('data-qa-terminal'));
  }
});

test('saving uses only the QA endpoint and preserves engine separation',async()=>{
  const f=fixture('codex');f.s.qaCli.settings.mode='full';
  await f.tab.save(f.s,f.root);
  assert.equal(f.calls.length,1);
  assert.equal(f.calls[0].url,'/api/chat/cli/codex');
  assert.equal(f.calls[0].args.body.mode,'full');
  assert.equal(f.calls[0].args.body.engine,undefined);
});

test('toggling a skill back restores inheritance instead of leaving a stale override',()=>{
  const f=fixture();f.s.qaCli.skills=[{id:'native:x',enabled:true}];f.s.qaCli.initialOverrides={};
  const box={dataset:{qaSkill:'native:x'},checked:false};
  f.root.querySelectorAll=selector=>selector==='[data-qa-skill]'?[box]:[];
  f.tab.sync(f.s,f.root);assert.equal(f.s.qaCli.settings.skill_overrides['native:x'],false);
  box.checked=true;f.tab.sync(f.s,f.root);assert.equal(Object.hasOwn(f.s.qaCli.settings.skill_overrides,'native:x'),false);
});

test('entry overrides use a distinct endpoint and preserve sibling/default settings',async()=>{
  const f=fixture('codex');const q=f.s.qaCli;
  Object.assign(q,{model_id:'answer-b',model_name:'Answerer',inherit:false,defaults:{...q.settings}});
  f.s.cfg.qa_cli={codex:{mode:'default'}};
  f.s.cfg.qa_model_policies={'translate-a':{engine:'codex',settings:{mode:'default'}}};
  q.settings.mode='full';
  f.PR.api=async(url,args)=>{f.calls.push({url,args});return {engine:'codex',model_id:'answer-b',inherit:false,defaults:{...q.defaults},settings:args.body,skills:[],native:q.native};};
  const html=f.tab.render(f.s);
  assert.match(html,/data-qa-mode="inherit"/);assert.match(html,/Answerer/);
  await f.tab.save(f.s,f.root);
  assert.equal(f.calls[0].url,'/api/chat/cli/codex/models/answer-b');
  assert.equal(f.s.cfg.qa_model_policies['translate-a'].settings.mode,'default');
  assert.equal(f.s.cfg.qa_cli.codex.mode,'default');
  assert.equal(f.s.cfg.qa_model_policies['answer-b'].settings.mode,'full');
});

test('inherit sends only an explicit reset and does not mutate shared policy',async()=>{
  const f=fixture('codex');const q=f.s.qaCli;
  Object.assign(q,{model_id:'answer-b',model_name:'B',inherit:false,defaults:{...q.settings,mode:'full'}});
  f.s.cfg.qa_model_policies={'answer-b':{engine:'codex',settings:{mode:'custom'}},other:{engine:'codex',settings:{mode:'default'}}};
  const mode={dataset:{qaMode:'inherit'}};
  await f.tab.click({target:{closest:s=>s==='[data-qa-mode]'?mode:null}},f.s,f.root);
  assert.equal(q.inherit,true);
  f.PR.api=async(url,args)=>{f.calls.push({url,args});return {engine:'codex',model_id:'answer-b',inherit:true,defaults:q.defaults,settings:q.defaults,skills:[],native:q.native};};
  await f.tab.save(f.s,f.root);
  assert.deepEqual({...f.calls[0].args.body},{inherit:true});
  assert.equal(Object.hasOwn(f.s.cfg.qa_model_policies,'answer-b'),false);
  assert.equal(f.s.cfg.qa_model_policies.other.settings.mode,'default');
});

test('a global or mismatched response cannot populate a per-entry editor',async()=>{
  const f=fixture('codex');Object.assign(f.s.qaCli,{model_id:'answer-b',loading:true,settings:null});
  f.PR.api=async()=>({engine:'codex',settings:{mode:'full'},skills:[]});
  await f.tab.mount(f.s);
  assert.equal(f.s.qaCli.settings,null);
  assert.match(f.s.qaCli.message,/身份不匹配/);
});

test('entry navigation waits for pending model persistence',async()=>{
  const f=fixture('codex');f.PR.settingsState=f.s;f.s.tab='chat';f.s.chat={models:[{id:'answer-b',name:'B'}]};
  let release;f.PR.flushModelSettings=()=>new Promise(resolve=>{release=resolve;});
  const opening=f.PR.openQaCli('codex','answer-b');
  assert.equal(f.s.qaCli.model_id,undefined);
  release();await opening;
  assert.equal(f.s.qaCli.model_id,'answer-b');assert.equal(f.s.qaCli.model_name,'B');
});

test('synthesized translation cards are appended without saving translation configuration',async()=>{
  const f=fixture('codex');f.PR.settingsState=f.s;f.s.tab='chat';
  const entry={id:'legacy',engine:'codex',model:'native-model',_unsaved:true};f.s.chat={models:[entry]};
  f.PR.api=async(url,args)=>{f.calls.push({url,args});return {models:[entry]};};
  await f.PR.openQaCli('codex','legacy');
  assert.equal(f.calls[0].url,'/api/chat/models');assert.equal(f.calls[0].args.body.append.id,'legacy');
  assert.equal(f.calls.length,1);assert.equal(entry._unsaved,false);assert.equal(f.s.qaCli.model_id,'legacy');
});

test('adopting CLI defaults resets skill baselines before creating an independent override',async()=>{
  const f=fixture('codex'),q=f.s.qaCli;
  Object.assign(q,{model_id:'answer-b',model_name:'B',inherit:false,initialOverrides:{'native:x':false}});
  q.settings.skill_overrides={'native:x':false};q.defaults={...q.settings,mode:'custom',skills:true,skill_overrides:{}};
  q.skills=[{id:'native:x',enabled:false,inherited_enabled:true}];
  const click=mode=>f.tab.click({target:{closest:s=>s==='[data-qa-mode]'?{dataset:{qaMode:mode}}:null}},f.s,f.root);
  await click('inherit');assert.equal(q.skills[0].enabled,true);assert.deepEqual({...q.initialOverrides},{});
  await click('custom');
  const box={dataset:{qaSkill:'native:x'},checked:true};
  f.root.querySelectorAll=selector=>selector==='[data-qa-skill]'?[box]:[];
  f.tab.sync(f.s,f.root);assert.deepEqual({...q.settings.skill_overrides},{});
});

test('cancelled full-mode confirmation does not broaden capabilities',async()=>{
  const f=fixture();f.PR.confirm=async()=>false;
  const mode={dataset:{qaMode:'full'}};
  await f.tab.click({target:{closest:s=>s==='[data-qa-mode]'?mode:null}},f.s,f.root);
  assert.equal(f.s.qaCli.settings.mode,'default');assert.equal(f.calls.length,0);
});

test('Pi QA has accurate lean/full warnings and no sandbox, approval or web controls', async () => {
  const f=fixture('pi'), q=f.s.qaCli;
  for (const mode of ['default','custom','full']) {
    q.settings.mode=mode;
    const html=f.tab.render(f.s);
    assert.match(html,/Pi Agent/);assert.match(html,/没有内置沙箱/);assert.match(html,/不会在每次工具调用前请求批准/);
    assert.doesNotMatch(html,/不会自动绕过沙箱|data-qa="(?:sandbox|web_search|web_fetch|approval)"|Codex 没有通用/);
    assert.match(html,/data-qa="mcp"[^>]* disabled/);
    if(mode==='default') assert.match(html,/默认精简模式禁用扩展、MCP、上下文文件和 Skills/);
    if(mode==='custom') {
      for(const tool of q.tools) assert.match(html,new RegExp('data-qa-tool="'+tool+'"'));
      assert.doesNotMatch(html,/data-qa-tool="(?:bash|edit|write|web_search|powershell)"/);
    }
    if(mode==='full') assert.match(html,/不会自动授予项目信任/);
  }
});

test('Pi full confirmation preserves overrides on cancel, resets only native overrides on acceptance', async () => {
  const f=fixture('pi'),q=f.s.qaCli;
  q.settings.mode='custom';q.settings.skill_overrides={'native:n':false,'managed:m':false};
  q.initialOverrides={...q.settings.skill_overrides};
  q.skills=[{id:'native:n',name:'Native',enabled:false,inherited_enabled:true},{id:'managed:m',name:'Managed',enabled:false,inherited_enabled:true,managed:true}];
  const click=mode=>f.tab.click({target:{closest:s=>s==='[data-qa-mode]'?{dataset:{qaMode:mode}}:null}},f.s,f.root);
  let confirmation;
  f.PR.confirm=async options=>{confirmation=options;return false;};await click('full');
  assert.equal(q.settings.mode,'custom');assert.equal(q.settings.skill_overrides['native:n'],false);
  assert.match(confirmation.body,/没有内置沙箱/);assert.match(confirmation.body,/重置本页的原生 Skill 开关/);
  f.PR.confirm=async()=>true;await click('full');
  assert.equal(q.settings.mode,'full');assert.deepEqual({...q.settings.skill_overrides},{'managed:m':false});
  const html=f.tab.render(f.s);
  assert.match(html,/data-qa-skill="native:n"[^>]* disabled/);
  assert.match(html,/data-qa-skill="managed:m"[^>]*>/);assert.doesNotMatch(html,/data-qa-skill="managed:m"[^>]* disabled/);
  assert.match(html,/--skill/);assert.doesNotMatch(html,/本页的显式 Skill 开关优先/);
  await f.tab.save(f.s,f.root);
  assert.equal(f.calls[0].url,'/api/chat/cli/pi');assert.equal(f.calls[0].args.body.skill_overrides['managed:m'],false);
});

test('Pi custom saves only supported tools/skills on its scoped endpoint; inherit only resets entry policy', async () => {
  const f=fixture('pi'),q=f.s.qaCli;
  f.PR.settingsState=f.s;f.s.tab='chat';f.s.chat={models:[{id:'pi/entry',name:'Pi model'}]};
  await f.PR.openQaCli('pi','pi/entry');assert.equal(f.s.qaCli.engine,'pi');assert.equal(f.s.qaCli.model_id,'pi/entry');
  f.s.qaCli=q;f.s.tab='qa_cli';
  Object.assign(q,{model_id:'pi/entry',model_name:'Pi model',inherit:false,defaults:{...q.settings}});
  q.settings.mode='custom';q.settings.allowed_tools=['read','bash','write'];q.settings.mcp=true;
  f.s.cfg.qa_model_policies={other:{engine:'codex',settings:{mode:'full'}}};
  f.PR.api=async(url,args)=>{f.calls.push({url,args});return {engine:'pi',model_id:q.model_id,inherit:!!args.body.inherit,defaults:q.defaults,settings:args.body.inherit?q.defaults:args.body,skills:[],native:q.native};};
  await f.tab.save(f.s,f.root);
  const call=f.calls[0];assert.equal(call.url,'/api/chat/cli/pi/models/pi%2Fentry');
  assert.deepEqual([...call.args.body.allowed_tools],['read']);
  for(const key of ['mcp','web_search','web_fetch']) assert.equal(call.args.body[key],false);
  assert.equal(f.s.cfg.qa_model_policies.other.settings.mode,'full');
  q.inherit=true;await f.tab.save(f.s,f.root);
  assert.deepEqual({...f.calls[1].args.body},{inherit:true});
  assert.equal(Object.hasOwn(f.s.cfg.qa_model_policies,'pi/entry'),false);
});

test('Pi custom uses server tools and inherited full requires truthful confirmation', async () => {
  const f=fixture('pi'),q=f.s.qaCli;
  q.tools=['read','find'];
  const click=mode=>f.tab.click({target:{closest:s=>s==='[data-qa-mode]'?{dataset:{qaMode:mode}}:null}},f.s,f.root);
  await click('custom');assert.deepEqual([...q.settings.allowed_tools],['read','find']);
  Object.assign(q,{model_id:'a',inherit:false,defaults:{...q.settings,mode:'full'}});
  let confirmation;f.PR.confirm=async value=>{confirmation=value;return false;};
  await click('inherit');assert.equal(q.inherit,false);assert.equal(q.settings.mode,'custom');
  assert.match(confirmation.body,/每次工具调用前/);
});

test('Pi skills import uses the existing scoped endpoint and enforces the file limit', async () => {
  const f=fixture('pi'),q=f.s.qaCli;Object.assign(q,{model_id:'answer',inherit:false});
  const input={files:[{name:'SKILL.md',size:10}],click(){}};
  f.root.querySelector=selector=>selector==='#qaSkillFile'?input:null;
  f.PR.api=async(url,args)=>{f.calls.push({url,args});return {skills:[{id:'managed:s',managed:true}]};};
  await f.tab.click({target:{closest:s=>s==='[data-qa-import]'?{}:null}},f.s,f.root);
  await input.onchange();
  assert.equal(f.calls[0].url,'/api/chat/cli/pi/models/answer/skills/import');
  assert.deepEqual({...f.calls[0].args.body},{filename:'SKILL.md',content_b64:'IyBTa2lsbA=='});
  assert.equal(q.skills[0].managed,true);
  input.files[0].size=5*1024*1024+1;await input.onchange();
  assert.equal(f.calls.length,1);assert.match(q.message,/不能超过 5 MiB/);
});
