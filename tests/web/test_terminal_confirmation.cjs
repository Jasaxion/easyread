const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

test('an old confirmation cannot dispose a newer terminal', async () => {
  const calls = [], mount = { replaceChildren(frame) { this.frame = frame; } };
  let confirm;
  const PR = {settingsTabs:{}, terminalTheme:{register:()=>()=>{},bindControl:()=>()=>{}}, t:x=>x, esc:x=>x, toast(){}, cliName:x=>x, piInstallHelp:()=>'', confirm:()=>new Promise(r=>{confirm=r;}),
    api: async (url, options) => { calls.push({url, body:options?.body}); return {url:'/terminal/session-'+calls.length+'/'}; }};
  const root={classList:{contains:()=>true},querySelector:selector=>selector==='#terminalMount'?mount:{}};
  const document={addEventListener(){},createElement:()=>({src:'',setAttribute(){},getAttribute(){return this.src;},remove(){}})};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../easyread/web/js/common/settings-terminal.js'),'utf8'),
    {window:{PR,addEventListener(){}},document,setTimeout:()=>1,clearTimeout(){}});
  const tab=PR.settingsTabs.terminal, state={tab:'terminal'};
  const event={target:{closest:s=>s==='[data-cli-action]'?{dataset:{cliAction:'shell'}}:null}};
  await tab.click(event,state,root);
  const old=tab.click(event,state,root);
  await tab.dispose();
  await tab.click(event,state,root);
  const current=mount.frame;
  const before=calls.length;
  confirm(true); await old;
  assert.equal(mount.frame,current);
  assert.equal(calls.length,before);
});
