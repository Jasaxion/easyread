const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const database = new Map(), cache = new Map(), timers = new Map(), requests = [], events = {};
  const scope = 'a'.repeat(24); let timer = 0;
  const data = {library_id: scope, token: 'test', paper: {meta: {}, blocks: [1,2].map(n => ({src: `figures/${n}.png`}))}, reader: {}, versions: {}};
  const db = {
    get: async (s,k) => structuredClone(database.get(s+':'+k)),
    put: async (s,k,v) => {database.set(s+':'+k,structuredClone(v));},
    update: async (s,k,fn) => {const v=fn(database.get(s+':'+k));database.set(s+':'+k,v);return v;},
    all: async s => [...database].filter(([k]) => k.startsWith(s+':')).map(([,v]) => structuredClone(v))
  };
  const PR = {t: s => s, features: {}, ls: {get: (k,f) => f},
    connection: {offline: false, json: async () => structuredClone(data)},
    on: (name,fn) => {events[name]=fn;}, emit() {}};
  vm.runInNewContext(fs.readFileSync('easyread/web/js/common/pwa.js','utf8'), {
    window: {PR,isSecureContext:true,addEventListener(){}},navigator:{serviceWorker:{}},document:{getElementById:()=>null,readyState:'loading'},
    location:{protocol:'https:'},EasyReadOfflineDB:db,localStorage:{},Blob,Response,DOMException,AbortController,
    setTimeout:(fn,ms)=>{timers.set(++timer,{fn,ms});return timer;},clearTimeout:id=>timers.delete(id),
    caches:{open:async()=>({match:async url=>cache.get(url)?.clone(),put:async(url,value)=>cache.set(url,value),delete:async url=>cache.delete(url)})},
    fetch:(url,options)=>new Promise((resolve,reject)=>{
      requests.push({url,options,resolve,reject});
      if(options.signal.aborted)reject(options.signal.reason);
      else options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true});
    })});
  return {PR,requests,database,cache,events,timers,record:()=>database.get('papers:'+scope+':paper'),
    timeout(){const [id,t]=[...timers].find(([,t])=>t.ms===60000);timers.delete(id);t.fn();}};
}

test('one resource timeout leaves a partial snapshot and still downloads later images without a reading error',async()=>{
  const f=fixture(), saving=f.PR.pwa.download('paper');await tick();
  assert.equal(f.requests.length,1);f.timeout();await tick();
  assert.equal(f.requests.length,2);assert.equal(f.requests[1].options.signal.aborted,false);
  f.requests[1].resolve(new Response('second image'));await saving;
  assert.equal(f.record().complete,false);assert.equal(f.record().missing,1);
  assert.equal(f.PR.pwa.error,undefined);assert.match(f.PR.pwa.downloadError,/部分资源/);
  assert.ok(f.cache.has('/p/paper/figures/2.png'));
  const retry=f.PR.pwa.download('paper');await tick();
  f.requests[2].resolve(new Response('first image'));await retry;
  assert.equal(f.record().complete,true);assert.equal(f.PR.pwa.downloadError,'');
});

test('user cancellation is quiet and does not prevent the next download',async()=>{
  const f=fixture(), saving=f.PR.pwa.download('paper');await tick();
  f.PR.pwa.cancel();await saving;
  assert.equal(f.PR.pwa.downloading,false);assert.equal(f.PR.pwa.error,undefined);assert.equal(f.PR.pwa.downloadError,'');
  const retry=f.PR.pwa.download('paper');await tick();f.requests[1].resolve(new Response('one'));await tick();
  f.requests[2].resolve(new Response('two'));await retry;assert.equal(f.record().complete,true);
});

test('resource authorization denial locks the library and stops caching',async()=>{
  for(const status of [401,403]){
    const f=fixture(), saving=f.PR.pwa.download('paper').catch(e=>e);await tick();
    f.requests[0].resolve(new Response('denied',{status}));const error=await saving;
    assert.equal(error.http,true);assert.equal(f.PR.pwa.blocked,true);
    assert.equal(f.requests.length,1);assert.equal(f.cache.size,0);
    assert.ok(f.database.get('meta:locked:'+'a'.repeat(24)));
  }
});
