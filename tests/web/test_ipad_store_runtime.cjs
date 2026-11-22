const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const web='easyread/web/js/';
const copy=value=>structuredClone(value);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const stamp=n=>'2026-11-20T00:00:'+String(n).padStart(2,'0')+'Z';
const ink=(id,n=1)=>({op:'ink',at:stamp(n),stroke:{id,anchor:'p0',ref:[200,100],textHash:'text',points:[[n,1],[n+2,3]],color:'#111111',width:2}});

async function fixture({ipad=true,enabled=true,seed=0,storage,db}={}) {
  const counters={clones:0,cloneBytes:0,reads:0,writes:0,deletes:0,snapshots:0};
  const flags={quota:false,snapshotFail:false,offline:false,busy:false};
  let uid=0,version=1,reader={__fixtureReader:true,rev:1,notes:{},ink:{},progress:{ratio:.1,at:stamp(0)}};
  for(let i=0;i<seed;i++)reader.ink['old-'+i]={...ink('old-'+i).stroke,points:Array.from({length:120},(_,j)=>[j,j%20])};
  const values=storage||{},database=db||new Map(),events={},requests=[],pendingTimers=new Map();let timer=0;
  const localStorage={};
  Object.defineProperties(localStorage,{
    getItem:{value:k=>{counters.reads++;return values[k]??null;}},
    setItem:{value:(k,v)=>{if(flags.quota||flags.remainingWrites===0)throw Error('quota');if(Number.isFinite(flags.remainingWrites))flags.remainingWrites--;counters.writes++;values[k]=v;localStorage[k]=v;}},
    removeItem:{value:k=>{counters.deletes++;delete values[k];delete localStorage[k];}}
  });
  Object.assign(localStorage,values);
  const state=()=>({paper:{meta:{title_en:'fixture'},blocks:[]},engine:'openai',reader:copy(reader),discussion:{entries:[]},layout:{},item:{},job:{},images:{},versions:{reader:String(version)},token:'test',library_id:'a'.repeat(24)});
  const hooks={};
  function apply(op){
    if(op.op==='ink')reader.ink[op.stroke.id]={...copy(op.stroke),updated:op.at};
    if(op.op==='ink_del')reader.ink[op.id]={...reader.ink[op.id],id:op.id,deleted:true,updated:op.at};
    if(op.op==='progress')reader.progress={...reader.progress,...Object.fromEntries(Object.entries(op).filter(([k])=>['block','off','ratio','at'].includes(k)))};
    if(op.op==='paper_note')reader.paper_note={body:op.body,at:op.at};
  }
  const fetch=async(url,options={})=>{
    requests.push(url);if(flags.offline)throw Error('offline');
    if(url.endsWith('/ops')){JSON.parse(options.body).ops.forEach(apply);reader.rev=++version;}
    let data=url.endsWith('/state')?state():url.endsWith('/versions')?{reader:String(version)}:
      url.endsWith('/part/reader')?{data:copy(reader),version:String(version)}:{rev:reader.rev,versions:{reader:String(version)}};
    if(hooks.fetch)data=await hooks.fetch(url,data);
    return {ok:true,status:200,redirected:false,json:async()=>copy(data)};
  };
  const PR={t:s=>s,uid:()=>String(++uid),nowIso:()=>stamp(1),features:{ipad_ink:enabled},feature:()=>enabled,
    on:(name,fn)=>(events[name]||=[]).push(fn),emit:(name,value)=>{for(const fn of events[name]||[])fn(value);},
    readerInput:{ipadSafari:ipad,ipadBusy:()=>flags.busy},
    ls:{get:(k,f)=>{const v=localStorage.getItem(k);return v===null?f:JSON.parse(v);},set:(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v));return true;}catch{return false;}},del:k=>localStorage.removeItem(k)}};
  const document={hidden:false,readyState:'loading',getElementById:()=>null};
  const timers=(fn,ms)=>ms===40?setTimeout(fn,ms):(pendingTimers.set(++timer,{fn,ms}),timer);
  const clear=id=>typeof id==='number'?pendingTimers.delete(id):clearTimeout(id);
  const context={window:{PR,isSecureContext:true,addEventListener(){}},document,localStorage,
    location:{protocol:'https:',pathname:'/read/fixture'},navigator:{serviceWorker:{controller:null}},
    EasyReadOfflineDB:{get:async(s,k)=>copy(database.get(s+':'+k)),all:async s=>[...database].filter(([k])=>k.startsWith(s+':')).map(([,v])=>copy(v)),
      put:async(s,k,v)=>{if(s==='papers'){counters.snapshots++;if(flags.snapshotFail)throw flags.snapshotError||Error('snapshot failed');if(hooks.snapshot)await hooks.snapshot(v);}database.set(s+':'+k,copy(v));},
      del:async(s,k)=>database.delete(s+':'+k),update:async(s,k,fn)=>{database.set(s+':'+k,fn(copy(database.get(s+':'+k))));}},
    caches:{open:async()=>({match:async()=>null,keys:async()=>[],delete:async()=>true})},
    fetch,Blob,AbortController,setTimeout:timers,clearTimeout:clear,Date,
    JSON:{parse:JSON.parse,stringify:(value,...args)=>{const text=JSON.stringify(value,...args);if(value?.__fixtureReader){counters.clones++;counters.cloneBytes+=text.length;}return text;}}};
  const ctx=vm.createContext(context);
  for(const file of ['common/connection.js','common/pwa.js','reader/ipad-runtime.js','reader/store.js'])vm.runInContext(fs.readFileSync(web+file,'utf8'),ctx);
  await PR.load();Object.keys(counters).forEach(k=>counters[k]=0);requests.length=0;
  const durable=()=>Object.keys(values).filter(k=>k.startsWith('pr-outbox-')&&k.includes('-fixture-'));
  return {PR,flags,counters,requests,hooks,document,durable,values,database,
    remote(change){change(reader);reader.rev=++version;},state:()=>JSON.parse(JSON.stringify(PR.state.reader)),
    resetCounts(){Object.keys(counters).forEach(k=>counters[k]=0);},timers:pendingTimers};
}

test('server/PWA rapid pen-ups persist only new operations and avoid full history clones on iPad',async()=>{
  for(const options of [{ipad:true},{ipad:false},{ipad:true,enabled:false}]) {
    const f=await fixture({...options,seed:300});
    for(let i=0;i<40;i++)f.PR.commit(ink('new-'+i));
    const optimized=options.ipad&&options.enabled!==false;
    assert.equal(f.counters.clones,optimized?0:40);
    assert.equal(f.counters.reads,optimized?40:820,'real PWA immutable-key lookup cost is linear, not triangular');
    assert.equal(f.counters.writes,40);assert.equal(f.durable().length,40);assert.equal(f.PR.store.pending,40);
    assert.equal(Object.keys(f.PR.state.reader.ink).length,340);
  }
});

test('incremental ink and compacted progress match the original state rebuild, including omitted/older fields',async()=>{
  const fast=await fixture(),legacy=await fixture({ipad:false});
  const ops=[ink('a',2),{op:'ink_del',id:'a',at:stamp(3)},ink('a',4),ink('a',1),
    {op:'progress',block:'p1',ratio:.8,off:.4,at:stamp(5)},
    {op:'progress',block:'p2',at:stamp(6)},
    {op:'progress',block:'p3',ratio:.3,at:stamp(2)},
    {op:'paper_note',body:'kept',at:stamp(7)},ink('b',8)];
  for(const op of ops){fast.PR.commit(copy(op));legacy.PR.commit(copy(op));assert.deepEqual(fast.state(),legacy.state());}
  assert.equal(fast.PR.state.reader.paper_note.body,'kept');
});

test('quota failure retains superseded durable ink until its replacement is persisted and survives reload',async()=>{
  const f=await fixture();f.PR.commit(ink('a',2));const old=f.durable()[0];
  f.flags.quota=true;f.PR.commit(ink('a',3));
  assert.equal(f.PR.store.status,'error');assert.ok(f.values[old]);assert.equal(f.durable().length,1);
  assert.equal(f.PR.exportOps().ops.find(op=>op.op==='ink').stroke.points[0][0],3);
  f.flags.quota=false;f.PR.commit(ink('b',4));assert.equal(f.values[old],undefined);assert.equal(f.durable().length,2);
  f.flags.offline=true;await f.PR.load();
  assert.equal(f.PR.state.reader.ink.a.points[0][0],3);assert.ok(f.PR.state.reader.ink.b);
});

test('acknowledgement retires partially persisted keys absent from the successful-write snapshot',async()=>{
  const f=await fixture();f.flags.quota=true;f.PR.commit(ink('a',2));assert.equal(f.durable().length,0);
  f.flags.quota=false;f.flags.remainingWrites=1;f.PR.commit(ink('b',3));
  assert.equal(f.durable().length,1);assert.equal(f.PR.store.pending,2);
  f.flags.remainingWrites=Infinity;assert.equal(await f.PR.flushPending(),true);
  assert.equal(f.durable().length,0);assert.equal(f.PR.store.pending,0);
  await f.PR.load();assert.equal(f.PR.store.pending,0);assert.ok(f.PR.state.reader.ink.a&&f.PR.state.reader.ink.b);
});

test('opt-out cannot release background work during an already retained iPad gesture',async()=>{
  const f=await fixture();f.PR.commit(ink('a',2));f.flags.busy=true;const saving=f.PR.flush();await tick();
  f.PR.feature=()=>false;assert.equal(f.PR.ipadRuntime.enabled(),true);assert.equal(f.PR.store.inputBusy(),true);
  await f.PR.poll();assert.equal(f.requests.length,0);
  f.flags.busy=false;await saving;assert.equal(f.PR.ipadRuntime.enabled(),false);assert.equal(f.durable().length,0);
});

test('snapshot failure never retires acknowledged durable operations; retry drains safely',async()=>{
  const f=await fixture();f.PR.commit(ink('a',2));f.flags.snapshotFail=true;
  assert.equal(await f.PR.flushPending(),false);assert.equal(f.durable().length,1);assert.equal(f.PR.store.pending,1);
  f.flags.snapshotFail=false;assert.equal(await f.PR.flushPending(),true);
  assert.equal(f.durable().length,0);assert.equal(f.PR.store.pending,0);assert.ok(f.PR.state.reader.ink.a);
});

test('IndexedDB AbortError remains visible and durable until the corresponding snapshot succeeds',async()=>{
  for(const ipad of [false,true]){
    const f=await fixture({ipad});f.PR.commit(ink('a',2));f.flags.snapshotFail=true;
    f.flags.snapshotError=new DOMException('Transaction aborted','AbortError');
    assert.equal(await f.PR.flushPending(),false);assert.equal(f.durable().length,1);assert.equal(f.PR.store.pending,1);
    assert.match(f.PR.pwa.error,/离线操作未完成/);assert.notEqual(f.PR.store.status,'offline');
    f.flags.snapshotFail=false;assert.equal(await f.PR.flushPending(),true);
    assert.equal(f.durable().length,0);assert.equal(f.PR.pwa.error,'');
  }
});

test('cached startup with a reachable health endpoint recovers token and live capabilities',async()=>{
  const f=await fixture({ipad:false});
  f.hooks.fetch=async(url,data)=>{if(url.endsWith('/state'))throw new TypeError('state unavailable');return data;};
  await f.PR.load();assert.equal(f.PR.pwa.offline,false);assert.equal(f.PR.pwa.cachedReader,true);
  assert.equal(f.PR.token,'');assert.equal(f.PR.state.engine,'none');assert.equal(f.PR.store.status,'retry');
  assert.equal(f.PR.canAsk(),false);
  delete f.hooks.fetch;await f.PR.poll();
  assert.equal(f.PR.pwa.cachedReader,false);assert.equal(f.PR.token,'test');assert.equal(f.PR.state.engine,'openai');
  assert.equal(f.PR.store.status,'saved');assert.equal(f.PR.canAsk(),true);
});

test('new ink during an acknowledgement snapshot remains durable and keeps confirmed remote data',async()=>{
  const f=await fixture();f.PR.commit(ink('a',2));
  f.remote(reader=>{reader.ink.peer={...ink('peer').stroke,updated:stamp(1)};});
  let unblock,entered=false;
  f.hooks.snapshot=async()=>{if(!entered){entered=true;await new Promise(resolve=>{unblock=resolve;});}};
  const flush=f.PR.flush({immediate:true});while(!entered)await tick();
  f.PR.commit(ink('b',3));assert.equal(f.durable().length,2);
  unblock();assert.equal(await flush,true);
  assert.ok(f.PR.state.reader.ink.a);assert.ok(f.PR.state.reader.ink.b);assert.ok(f.PR.state.reader.ink.peer);
  assert.equal(f.PR.store.pending,1);assert.equal(f.durable().length,1);
  assert.equal(await f.PR.flushPending(),true);assert.equal(f.durable().length,0);
});

test('explicit flushPending can release an already waiting automatic flush during retained pen contact',async()=>{
  const f=await fixture();f.PR.commit(ink('a',2));f.flags.busy=true;
  const background=f.PR.flush();await tick();assert.equal(f.requests.filter(p=>p.endsWith('/ops')).length,0);
  assert.equal(await f.PR.flushPending(),true);assert.equal(await background,true);
  assert.equal(f.PR.store.pending,0);assert.equal(f.durable().length,0);
});

test('enabling the fast path during a legacy acknowledgement respects the advanced reader base',async()=>{
  const f=await fixture({enabled:false});f.PR.commit(ink('a',2));
  f.remote(reader=>{reader.ink.peer=ink('peer').stroke;});
  let unblock,entered=false;f.hooks.snapshot=async()=>{if(!entered){entered=true;await new Promise(resolve=>{unblock=resolve;});}};
  const flush=f.PR.flush({immediate:true});while(!entered)await tick();
  f.PR.feature=()=>true;f.PR.commit(ink('b',3));
  assert.ok(f.PR.state.reader.ink.peer,'base mismatch must rebuild rather than silently retain stale reader data');
  unblock();await flush;assert.ok(f.PR.state.reader.ink.b);
});

test('input starting during a poll delays atomic application until contact ends',async()=>{
  const f=await fixture(),changes=[];f.PR.on('remote',value=>changes.push([...value]));
  f.remote(r=>{r.ink.peer=ink('peer').stroke;});
  let unblock,entered=false;f.hooks.fetch=async(url,data)=>{if(url.endsWith('/part/reader')){entered=true;await new Promise(resolve=>{unblock=resolve;});}return data;};
  const poll=f.PR.poll();while(!entered)await tick();f.flags.busy=true;unblock();await tick();
  assert.equal(f.PR.state.versions.reader,'1');assert.deepEqual(changes,[]);assert.ok(!f.PR.state.reader.ink.peer);
  f.flags.busy=false;await poll;assert.ok(f.PR.state.reader.ink.peer);assert.deepEqual(changes,[['reader']]);
});

test('an older staged reader cannot overwrite a newer acknowledgement',async()=>{
  const f=await fixture(),changes=[];f.PR.on('remote',value=>changes.push([...value]));
  f.remote(r=>{r.ink.peer=ink('peer').stroke;});
  let unblock,entered=false;f.hooks.fetch=async(url,data)=>{if(url.endsWith('/part/reader')&&!entered){entered=true;await new Promise(resolve=>{unblock=resolve;});}return data;};
  const poll=f.PR.poll();while(!entered)await tick();f.PR.commit(ink('new',3));await f.PR.flushPending();
  const revision=f.PR.state.reader.rev;unblock();await poll;
  assert.equal(f.PR.state.reader.rev,revision);assert.ok(f.PR.state.reader.ink.new);assert.deepEqual(changes,[]);
});

test('iPad polls never emit a phantom reader change while that reader was skipped',async()=>{
  const f=await fixture(),changes=[];f.PR.on('remote',value=>changes.push([...value]));
  f.PR.commit({op:'paper_note',body:'pending',at:stamp(2)});f.remote(r=>{r.ink.peer=ink('peer').stroke;});
  await f.PR.poll();assert.deepEqual(changes,[]);assert.equal(f.PR.state.versions.reader,'1');
  assert.ok(!f.requests.some(p=>p.endsWith('/part/reader')));
  f.flags.busy=true;f.requests.length=0;await f.PR.poll();assert.deepEqual(f.requests,[]);
});

test('iPad stages remote data, serializes overlapping polls and rechecks writes before applying',async()=>{
  const f=await fixture(),changes=[];f.PR.on('remote',value=>changes.push([...value]));
  f.remote(r=>{r.ink.peer=ink('peer').stroke;});
  let unblock,entered=false;
  f.hooks.fetch=async(url,data)=>{if(url.endsWith('/part/reader')){entered=true;await new Promise(resolve=>{unblock=resolve;});}return data;};
  const first=f.PR.poll();while(!entered)await tick();
  await f.PR.poll();assert.equal(f.requests.filter(p=>p.endsWith('/versions')).length,1);
  f.PR.commit({op:'paper_note',body:'new local note',at:stamp(2)});unblock();await first;
  assert.deepEqual(changes,[]);assert.equal(f.PR.state.versions.reader,'1');
  assert.equal(f.PR.state.reader.paper_note.body,'new local note');
});
