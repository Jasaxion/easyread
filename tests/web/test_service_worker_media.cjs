const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const origin='https://reader.test', url=origin+'/p/paper/figures/test.png';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture() {
  const events={},timers=[],requests=[],database=new Map([['client:reader','scope-a'],['active','scope-b']]);
  const flags={dbReadFailure:false,dbWriteFailure:false};
  const self={location:{origin},addEventListener:(name,fn)=>{events[name]=fn;},EasyReadOfflineDB:{
    get:async(s,k)=>{if(flags.dbReadFailure)throw Error('storage failed');return database.get(k);},
    put:async(s,k,v)=>{if(flags.dbWriteFailure)throw Error('storage failed');database.set(k,v);}
  }};
  vm.runInNewContext(fs.readFileSync('easyread/web/sw.js','utf8'),{self,BUILD:'test',ASSETS:[],importScripts(){},URL,Response,AbortController,
    setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout(){},
    caches:{open:async name=>({match:async()=>new Response(name==='easyread-media-scope-a'?'CORRECT LIBRARY':'WRONG LIBRARY')})},
    fetch:(request,options)=>new Promise((resolve,reject)=>requests.push({request,options,resolve,reject}))});
  const get=(clientId='reader')=>{let result;events.fetch({request:new Request(url),clientId,respondWith:p=>{result=p;}});return result;};
  return {get,requests,timers,database,flags};
}

test('visible images have no six-second cancellation even when a cached copy exists',async()=>{
  const f=fixture(), read=f.get();await tick();
  assert.equal(f.timers.length,0);assert.equal(f.requests[0].options,undefined);
  f.requests[0].resolve(new Response('slow but current image'));
  assert.equal(await(await read).text(),'slow but current image');
});

test('real network failure can fall back only to the requesting client library',async()=>{
  for(const id of ['reader','unknown','']){
    const f=fixture(), read=f.get(id);await tick();f.requests[0].reject(new TypeError('offline'));
    const result=await read;
    if(id==='reader')assert.equal(await result.text(),'CORRECT LIBRARY');
    else assert.equal(result.type,'error');
  }
});

test('HTTP denials, login redirects and server errors never fall back to cached private bytes',async()=>{
  for(const denied of [new Response('401',{status:401}),new Response('403',{status:403}),new Response('500',{status:500}),
    {status:200,redirected:true,url:origin+'/login'}, {status:0,type:'opaqueredirect'}]){
    const f=fixture(), read=f.get();await tick();f.requests[0].resolve(denied);
    assert.equal(await read,denied);
  }
});

test('failed authorization-lock writes cannot turn a denial or its later retries into cached success',async()=>{
  const f=fixture();f.flags.dbWriteFailure=true;
  const denied=new Response('denied',{status:403}), first=f.get();await tick();f.requests[0].resolve(denied);
  assert.equal(await first,denied);
  const retry=f.get();await tick();f.requests[1].reject(new TypeError('offline'));
  assert.equal((await retry).type,'error');
});

test('a locked scope or unreadable scope metadata never serves cached private images',async()=>{
  const locked=fixture();locked.database.set('locked:scope-a',true);
  const first=locked.get();await tick();locked.requests[0].reject(new TypeError('offline'));assert.equal((await first).type,'error');
  const unreadable=fixture();unreadable.flags.dbReadFailure=true;
  const second=unreadable.get().catch(e=>e);await tick();unreadable.requests[0].reject(new TypeError('offline'));
  assert.match((await second).message,/storage failed/);
});
