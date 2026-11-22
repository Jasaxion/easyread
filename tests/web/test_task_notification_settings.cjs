const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const read = file => fs.readFileSync('easyread/web/js/common/' + file, 'utf8');
function fixture(initial = {}) {
  const storage = new Map(Object.entries(initial)), events = {}, writes = [], saves = [], emitted = [];
  const PR = {t: s => s, ls: {get: (k,d) => storage.has(k) ? storage.get(k) : d,
    set: (k,v) => {storage.set(k,v);writes.push(k);}}, savePrefs: (...args) => saves.push(args), emit: name => emitted.push(name)};
  vm.runInNewContext(read('features.js'), {window: {PR,addEventListener: (name,fn) => {events[name]=fn;}}});
  return {PR,storage,writes,saves,emitted,events};
}

test('notification capsule defaults on and the Reading settings render its existing feature switch', () => {
  const f = fixture(); assert.equal(f.PR.feature('task_notifications'), true);
  const definition = f.PR.FEATURES.find(([id]) => id === 'task_notifications');
  assert.equal(definition[1], '显示通知胶囊'); assert.equal(definition[2], true);
  f.PR.settingsTabs = {}; f.PR.TYPE_DEFAULTS = {}; f.PR.typeValues = () => []; f.PR.opt = () => '';
  vm.runInNewContext(read('settings-tabs.js'), {window: {PR:f.PR},location:{protocol:'http:'}});
  const s = {type:{},ui:{features:{task_notifications:false}}};
  const html = f.PR.settingsTabs.reading.render(s);
  assert.match(html, /data-feat="task_notifications"/);
  assert.doesNotMatch(html, /data-feat="task_notifications" checked/);
  f.PR.settingsTabs.reading.change({target:{dataset:{feat:'task_notifications'},checked:true}},s);
  assert.equal(s.ui.features.task_notifications,true);
});

test('notification preference persists through the existing UI preferences, including legacy defaults', () => {
  const f = fixture(); f.PR.applyUi({features:{...f.PR.features,task_notifications:false}},true);
  assert.equal(f.PR.feature('task_notifications'),false);
  assert.equal(f.saves[0][0],'ui'); assert.equal(f.saves[0][1].features.task_notifications,false);
  const reopened=fixture(Object.fromEntries(f.storage));assert.equal(reopened.PR.feature('task_notifications'),false);
  reopened.PR.useServerUi({ui:{features:{chat:false}}});
  assert.equal(reopened.PR.feature('task_notifications'),true);assert.equal(reopened.PR.feature('chat'),false);
});

test('other tabs update their capsule preference without echoing writes or changing jobs', () => {
  const f=fixture(); f.events.storage({key:'easyread-ui',newValue:JSON.stringify({features:{task_notifications:false}})});
  assert.equal(f.PR.feature('task_notifications'),false);assert.deepEqual(f.writes,[]);assert.deepEqual(f.saves,[]);
  assert.deepEqual(f.emitted,['ui-changed']);
  f.events.storage({key:'easyread-ui',newValue:'bad json'});assert.equal(f.PR.feature('task_notifications'),false);
  f.events.storage({key:'easyread-ui',newValue:null});assert.equal(f.PR.feature('task_notifications'),true);
  f.events.storage({key:null});assert.equal(f.PR.feature('task_notifications'),true);
});

test('a late server preference response cannot undo a newer local or sibling-tab switch', () => {
  for(const local of [true,false]){
    const f=fixture(), snapshot=JSON.stringify(f.storage.get('easyread-ui')||{});
    const latest={features:{...f.PR.features,task_notifications:false},keys_on:true};
    if(local) f.PR.applyUi(latest,true);
    else f.storage.set('easyread-ui',latest); // Even if the storage event is still queued.
    f.PR.useServerUi({_uiAtLoad:snapshot,ui:{features:{task_notifications:true}}});
    assert.equal(f.PR.feature('task_notifications'),false);
  }
});

test('loadPrefs carries a non-serialized snapshot for stale-response protection', async () => {
  const source=read('util.js'), start=source.indexOf('  PR.loadPrefs = async function () {'), end=source.indexOf('\n  };',start)+5;
  const storage=new Map(), PR={ls:{get:(k,d)=>storage.get(k)||d,set:(k,v)=>storage.set(k,v)},api:async()=>({ui:{features:{task_notifications:false}}})};
  vm.runInNewContext(source.slice(start,end),{PR,location:{protocol:'https:'}});
  const prefs=await PR.loadPrefs();assert.equal(prefs._uiAtLoad,'{}');assert.ok(!JSON.stringify(prefs).includes('_uiAtLoad'));
});
