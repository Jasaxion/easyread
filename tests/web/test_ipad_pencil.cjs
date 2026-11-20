const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {createIPadPencilPreview,createIPadInkRenderer} = require('../../easyread/web/js/reader/ipad-pencil.js');

function fixture({ipad=true, enabled=true}={}) {
  const frames=new Map(), callbacks=[], events={};let seq=0, paints=0, hashes=0, reads=0, resize;
  const target=()=>({listeners:{},addEventListener(k,f){(this.listeners[k]||=[]).push(f);},emit(k,e={}){for(const f of this.listeners[k]||[]) f(e);}});
  function node(){return {...target(),dataset:{},attrs:{},style:{},children:[],classList:{toggle(){},remove(){}},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},
    getBoundingClientRect(){return {left:0,top:0,width:300,height:400};},
    insertBefore(child,before){child.remove?.();const index=this.children.indexOf(before);this.children.splice(index<0?this.children.length:index,0,child);child.parentNode=this;},
    append(child){child.remove?.();this.children.push(child);child.parentNode=this;},remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(c=>c!==this);this.parentNode=null;}}};}
  const layer={...node(),replaceChildren(){for(const child of this.children) child.parentNode=null;this.children=[];paints++;},setPointerCapture(){},hasPointerCapture(){return false;}};
  const tools={...node(),querySelector(){return null;}}, toggle=node();
  const stage={clientWidth:300,scrollHeight:1000,scrollTop:0,getBoundingClientRect:()=>({left:0,top:0})};
  const block={dataset:{id:'p1'},isConnected:true,innerText:'text',getBoundingClientRect(){reads++;return {left:20,top:50,bottom:150,width:200,height:100};}};
  const document={...target(),body:node(),hidden:false,getElementById:id=>({inkLayer:layer,inkTools:tools,inkToggle:toggle,stage,paper:block,'b-p1':block}[id]),createElementNS:node};
  const window={...target(),document,innerWidth:300,innerHeight:400,navigator:{userAgent:''},scrollX:0,scrollY:0,
    requestAnimationFrame(fn){const id=++seq;frames.set(id,fn);callbacks.push(fn);return id;},cancelAnimationFrame(id){frames.delete(id);}};
  const reader={ink:{saved:{id:'saved',anchor:'p1',ref:[200,100],textHash:'text',points:[[1,1],[20,20]],color:'#1d1d1d',width:2}},stickers:{}};
  const PR={state:{reader},createIPadPencilPreview,createIPadInkRenderer,feature:()=>enabled,readerInput:{ipadSafari:ipad,configure(){},acceptsCanvas:e=>e.pointerType==='pen',panning:()=>false},
    $$:()=>[block],t:s=>s,uid:()=>`new-${++seq}`,nowIso:()=>'',hashText:s=>{hashes++;return s;},
    debounce(fn){const f=(...args)=>fn(...args);f.cancel=()=>{};f.flush=()=>fn();return f;},
    on(k,fn){(events[k]||=[]).push(fn);},emit(k,value){for(const fn of events[k]||[])fn(value);},
    commit(op){if(op.op==='ink')reader.ink[op.stroke.id]={...op.stroke,deleted:false};if(op.op==='ink_del')reader.ink[op.id]={...reader.ink[op.id],deleted:true};this.emit('reader',op);}};
  window.PR=PR;
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/ink.js','utf8'),{window,document,ResizeObserver:class{constructor(fn){resize=fn;}observe(){}},Date,Math,Number});
  PR.setInkActive(true);PR.setInkInputMode('pen');PR.renderInk();
  for(const fn of [...frames.values()])fn();frames.clear();paints=hashes=reads=0;callbacks.length=0;
  const send=(type,x,y,extra={})=>layer.emit(type,{type,pointerType:'pen',button:0,buttons:type==='pointerup'?0:1,pointerId:7,clientX:x,clientY:y,preventDefault(){},...extra});
  return {PR,reader,layer,block,stage,window,send,frames,callbacks,preview:()=>document.body.children[0]?.children[0],resize:()=>resize(),stats:()=>({paints,hashes,reads}),frame(){const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn());},last:()=>Object.values(reader.ink).find(s=>s.id!=='saved'&&!s.deleted)};
}

test('iPad preview batches samples into one frame without repainting saved paths or rehashing text',()=>{
  const f=fixture();f.send('pointerdown',30,60);const saved=f.layer.children.find(c=>c.dataset.stroke==='saved'), preview=f.preview(), before=f.stats();
  for(let n=1;n<=30;n++) f.send('pointermove',30+n*3,60,{getCoalescedEvents:()=>[{clientX:30+n*3-1.5,clientY:62},{clientX:30+n*3,clientY:60}]});
  assert.equal(f.frames.size,1);assert.equal(f.stats().paints,before.paints);assert.equal(f.stats().hashes,before.hashes);
  assert.equal(f.stats().reads-before.reads,30,"one anchor check per dispatch, not per saved stroke or coalesced sample");
  f.frame();assert.equal(f.preview(),preview);assert.equal(saved.parentNode,f.layer);
  assert.equal(f.stats().paints,0);assert.equal(f.stats().hashes,1);
  f.send('pointerup',120.3,60);
  const stroke=f.last();assert.ok(stroke.points.length>30);assert.deepEqual(Array.from(stroke.points.at(-1)),[100.3,10]);
  assert.equal(f.stats().paints,0,'ordinary pen-up inserts one path without rebuilding history');
  assert.equal(saved.parentNode,f.layer);assert.equal(f.preview(),undefined);
  assert.ok(stroke.points.every(p=>p.length===2&&p.every(Number.isFinite)));
  assert.equal(f.PR.inkHistory(false),true);assert.equal(f.reader.ink[stroke.id].deleted,true);
  assert.equal(f.PR.inkHistory(true),true);assert.equal(f.reader.ink[stroke.id].deleted,false);
});

test('non-iPad and opt-out retain the legacy per-event preview and endpoint behavior',()=>{
  for(const options of [{ipad:false},{enabled:false}]) {
    const f=fixture(options);f.send('pointerdown',30,60);
    f.send('pointermove',50,60,{getCoalescedEvents:()=>[{clientX:35,clientY:65}]});
    assert.equal(f.stats().paints,1);assert.equal(f.frames.size,0);
    f.send('pointerup',50.3,60);
    assert.deepEqual(Array.from(f.last().points.at(-1)),[30,10]);
    assert.equal(f.last().points.length,2);
  }
});

test('iPad keeps fine subpixel samples while filtering duplicate jitter',()=>{
  const f=fixture();f.send('pointerdown',30,60);
  for(const x of [30.6,30.8,31.2]) f.send('pointermove',x,60);
  f.send('pointerup',31.3,60);
  assert.deepEqual(Array.from(f.last().points,p=>Array.from(p)),[[10,10],[10.6,10],[11.2,10],[11.3,10]]);
});

test('coalesced coordinates do not backtrack to a rounded parent event',()=>{
  const f=fixture();f.send('pointerdown',30,60);
  f.send('pointermove',32,60,{getCoalescedEvents:()=>[{clientX:31.4,clientY:60},{clientX:32.8,clientY:60}]});
  f.send('pointerup',32.8,60);
  assert.deepEqual(Array.from(f.last().points,p=>Array.from(p)),[[10,10],[11.4,10],[12.8,10]]);
});

test('delayed animation frame fallback paints once and cannot resurrect a completed preview',()=>{
  const f=fixture(), timers=new Map();let id=0;
  f.window.setTimeout=(fn,ms)=>{assert.equal(ms,150);timers.set(++id,fn);return id;};
  f.window.clearTimeout=id=>timers.delete(id);
  f.send('pointerdown',30,60);const before=f.preview().attrs.d;f.send('pointermove',50,60);
  const staleFrame=f.callbacks.at(-1), fallback=[...timers.values()][0];fallback();
  assert.notEqual(f.preview().attrs.d,before);assert.equal(timers.size,0);assert.equal(f.frames.size,0);
  const after=f.preview().attrs.d;staleFrame();assert.equal(f.preview().attrs.d,after);
  f.send('pointermove',60,60);const staleTimer=[...timers.values()][0];f.send('pointerup',61,60);
  staleTimer();assert.equal(f.preview(),undefined);assert.equal(timers.size,0);
});

test('missing or throwing coalesced APIs fall back to dispatched points and preserve a short tail',()=>{
  for(const extra of [{},{getCoalescedEvents(){throw Error('unavailable');}}]) {
    const f=fixture();f.send('pointerdown',30,60);f.send('pointermove',32,60,extra);
    f.send('pointerup',32.4,60);
    assert.deepEqual(Array.from(f.last().points.at(-1)),[12.4,10]);
  }
});

test('cancelled frames never restore a preview after pointer cancellation or a new stroke',()=>{
  const f=fixture();f.send('pointerdown',30,60);f.send('pointermove',50,60);
  const old=f.callbacks[0];f.send('pointercancel',0,0);assert.equal(f.frames.size,0);
  assert.deepEqual(Array.from(f.last().points.at(-1)),[30,10]);
  f.send('pointerdown',70,60);const preview=f.preview();
  old();assert.equal(f.preview(),preview);assert.equal(f.frames.size,0);
  f.send('pointerup',71,60);
});

test('viewport translations rebase pencil coordinates without ending the contact',()=>{
  const f=fixture();f.send('pointerdown',30,60);f.window.emit('scroll');
  assert.equal(f.last(),undefined);
  f.send('pointermove',50,60);f.window.scrollY=20;
  f.block.getBoundingClientRect=()=>({left:20,top:30,bottom:130,width:200,height:100});
  f.stage.getBoundingClientRect=()=>({left:0,top:-20});
  f.window.emit('scroll');f.frame();assert.equal(f.last(),undefined);
  f.send('pointerup',52,40);assert.deepEqual(Array.from(f.last().points.at(-1)),[32,10]);
});

test('invalid samples are ignored, point storage remains bounded, and external redraw retains the live path',()=>{
  const f=fixture();f.send('pointerdown',30,60);
  f.send('pointermove',32,60,{getCoalescedEvents:()=>[{clientX:NaN,clientY:4},{clientX:Infinity,clientY:2}]});
  const preview=f.preview();f.PR.renderInk();assert.equal(f.preview(),preview);
  for(let i=0;i<2200;i++) f.send('pointermove',30+i*2,60);
  f.send('pointerup',4490,60);assert.equal(f.last().points.length,2000);
  assert.ok(f.last().points.every(p=>p.every(Number.isFinite)));
});

test('changed anchor geometry ends the optimized gesture before accepting stale samples',()=>{
  for(const notify of [false,true]) {
    const f=fixture();f.send('pointerdown',30,60);f.send('pointermove',50,60);
    f.block.getBoundingClientRect=()=>({left:20,top:80,bottom:180,width:200,height:100});
    if(notify) f.resize();
    f.send('pointermove',70,90);f.frame();
    assert.ok(f.last());assert.deepEqual(Array.from(f.last().points.at(-1)),[30,10]);
    assert.equal(f.frames.size,0);
  }
});

test('interleaved redraw requests wait for pen-up and keep the live SVG separate from history',()=>{
  const f=fixture(), saved=f.layer.children[0];f.send('pointerdown',30,60);
  const preview=f.preview(), surface=preview.parentNode;
  assert.notEqual(surface,f.layer);assert.equal(surface.attrs.viewBox,'0 0 300 400');
  for(let i=1;i<=20;i++) {
    f.PR.renderInk();f.PR.emit('reader',{op:'ink'});f.send('pointermove',30+i*2,60);f.frame();
    assert.equal(f.stats().paints,0);assert.equal(saved.parentNode,f.layer);assert.equal(f.preview(),preview);
  }
  f.send('pointerup',71,60);f.frame();assert.equal(f.stats().paints,1);assert.equal(f.preview(),undefined);
});

test('geometry invalidation publishes saved ink before disposing the live surface',()=>{
  const f=fixture();f.send('pointerdown',30,60);f.send('pointermove',50,60);
  const surface=f.preview().parentNode, remove=surface.remove.bind(surface);let checked=false;
  surface.remove=()=>{assert.ok(f.layer.children.some(n=>n.dataset.stroke!=='saved'));checked=true;remove();};
  f.block.getBoundingClientRect=()=>({left:20,top:80,bottom:180,width:200,height:100});
  f.resize();assert.equal(checked,true);assert.ok(f.last());assert.equal(f.preview(),undefined);
});

test('incremental pen-up inserts the new ink below foreground stickers without replacing old paths',()=>{
  const f=fixture();f.PR.renderStickers=(svg,_sr,_stage,phase)=>svg.append({dataset:{sticker:phase}});
  f.PR.renderInk();f.frame();const saved=f.layer.children.find(n=>n.dataset.stroke==='saved'), front=f.layer.children.at(-1);
  const before=f.stats().paints;f.send('pointerdown',30,60);f.send('pointerup',50,60);
  assert.equal(f.stats().paints,before);assert.equal(saved.parentNode,f.layer);
  assert.equal(f.layer.children.at(-1),front);assert.equal(f.layer.children.at(-2).dataset.stroke,f.last().id);
});

test('Safari repaint waits for the iPad pan to end and does not write any strokes',()=>{
  const f=fixture();let panning=true;f.PR.readerInput.panning=()=>panning;
  f.window.emit('scroll');assert.equal(f.stats().paints,0);
  panning=false;f.PR.emit('ink-pan-end');f.frame();assert.equal(f.stats().paints,1);
  assert.equal(Object.keys(f.reader.ink).length,1);
});
