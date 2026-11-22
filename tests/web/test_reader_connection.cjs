const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture() {
  const requests=[],storage=new Map(),events=[];
  const PR={t:s=>s,uid:()=> 'client',nowIso:()=> '2026-11-22T00:00:00Z',emit:(name,value)=>events.push({name,value}),
    ls:{get:(k,f)=>storage.get(k)||f,set:(k,v)=>{storage.set(k,structuredClone(v));return true;}}};
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/store.js','utf8'),{
    window:{PR,addEventListener(){}},location:{pathname:'/read/paper'},document:{hidden:false,getElementById:()=>null},setTimeout(){},clearTimeout(){},
    fetch:async(url,options)=>{
      if(url.endsWith('/state'))return {ok:true,json:async()=>({paper:{meta:{}},reader:{rev:1},versions:{reader:'1'},token:'token'})};
      return new Promise((resolve,reject)=>requests.push({url,options,reject,reply:(data,status=200)=>resolve({ok:status===200,status,json:async()=>data})}));
    }});
  await PR.load();return {PR,requests,storage,events};
}

test('ordinary browser reader serializes overlapping polls, without PWA or iPad mode',async()=>{
  const f=await fixture(),first=f.PR.poll();await f.PR.poll();await f.PR.poll();
  assert.equal(f.requests.length,1);f.requests[0].reply({reader:'1'});await first;
  const next=f.PR.poll();assert.equal(f.requests.length,2);f.requests[1].reply({reader:'1'});await next;
});

test('part failures are caught and a later poll recovers without false offline or lost versions',async()=>{
  const f=await fixture(),first=f.PR.poll();f.requests[0].reply({reader:'2'});await tick();
  f.requests[1].reply({error:'busy'},500);await first;
  assert.equal(f.PR.store.status,'retry');assert.equal(f.PR.state.versions.reader,'1');
  const next=f.PR.poll();f.requests[2].reply({reader:'2'});await tick();
  f.requests[3].reply({version:'2',data:{rev:2,paper_note:{body:'remote'}}});await next;
  assert.equal(f.PR.store.status,'saved');assert.equal(f.PR.state.reader.paper_note.body,'remote');
});

test('a slow desktop poll cannot overwrite a newer acknowledged note',async()=>{
  const f=await fixture(),poll=f.PR.poll();f.requests[0].reply({reader:'2'});await tick();
  assert.match(f.requests[1].url,/part\/reader/);
  f.PR.commit({op:'paper_note',body:'new local note'});const saving=f.PR.flush();
  f.requests[2].reply({rev:3,versions:{reader:'3'}});await saving;
  f.requests[1].reply({version:'2',data:{rev:2,paper_note:{body:'stale'}}});await poll;
  assert.equal(f.PR.state.reader.paper_note.body,'new local note');assert.equal(f.PR.state.reader.rev,3);
  assert.equal(f.PR.state.versions.reader,'3');
});

test('HTTP save errors retain the outbox without pretending the browser is offline',async()=>{
  const f=await fixture();f.PR.commit({op:'paper_note',body:'keep me'});
  const first=f.PR.flush();f.requests[0].reply({error:'server busy'},500);assert.equal(await first,false);
  assert.equal(f.PR.store.status,'retry');assert.equal(f.PR.store.pending,1);assert.equal(f.storage.get('pr-outbox-paper').length,1);
  const retry=f.PR.flush();f.requests[1].reply({rev:2});assert.equal(await retry,true);
  assert.equal(f.PR.store.status,'saved');assert.equal(f.PR.store.pending,0);
});

test('authorization failure is not displayed as offline',async()=>{
  const f=await fixture(),poll=f.PR.poll();f.requests[0].reply({},401);await poll;
  assert.equal(f.PR.store.status,'error');assert.match(f.events.at(-1).value.text,/重新授权/);
});
