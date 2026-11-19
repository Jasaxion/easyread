const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {createIPadPencilPreview} = require('../../easyread/web/js/reader/ipad-pencil.js');

function fixture({ipad=true, enabled=true}={}) {
  const frames=new Map(), callbacks=[], events={};let seq=0, paints=0, hashes=0, reads=0, resize;
  const target=()=>({listeners:{},addEventListener(k,f){(this.listeners[k]||=[]).push(f);},emit(k,e={}){for(const f of this.listeners[k]||[]) f(e);}});
  function node(){return {...target(),dataset:{},attrs:{},style:{},children:[],classList:{toggle(){},remove(){}},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},
    append(child){child.remove?.();this.children.push(child);child.parentNode=this;},remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(c=>c!==this);this.parentNode=null;}}};}
  const layer={...node(),replaceChildren(){for(const child of this.children) child.parentNode=null;this.children=[];paints++;},setPointerCapture(){},hasPointerCapture(){return false;}};
  const tools={...node(),querySelector(){return null;}}, toggle=node();
  const stage={clientWidth:300,scrollHeight:1000,scrollTop:0,getBoundingClientRect:()=>({left:0,top:0})};
  const block={dataset:{id:'p1'},isConnected:true,innerText:'text',getBoundingClientRect(){reads++;return {left:20,top:50,bottom:150,width:200,height:100};}};
  const document={...target(),hidden:false,getElementById:id=>({inkLayer:layer,inkTools:tools,inkToggle:toggle,stage,paper:block,'b-p1':block}[id]),createElementNS:node};
  const window={...target(),document,navigator:{userAgent:''},scrollX:0,scrollY:0,
    requestAnimationFrame(fn){const id=++seq;frames.set(id,fn);callbacks.push(fn);return id;},cancelAnimationFrame(id){frames.delete(id);}};
  const reader={ink:{saved:{id:'saved',anchor:'p1',ref:[200,100],textHash:'text',points:[[1,1],[20,20]],color:'#1d1d1d',width:2}},stickers:{}};
  const PR={state:{reader},createIPadPencilPreview,feature:()=>enabled,readerInput:{ipadSafari:ipad,configure(){},acceptsCanvas:e=>e.pointerType==='pen',panning:()=>false},
    $$:()=>[block],t:s=>s,uid:()=>`new-${++seq}`,nowIso:()=>'',hashText:s=>{hashes++;return s;},
    debounce(fn){const f=(...args)=>fn(...args);f.cancel=()=>{};f.flush=()=>fn();return f;},
    on(k,fn){(events[k]||=[]).push(fn);},emit(k,value){for(const fn of events[k]||[])fn(value);},
    commit(op){if(op.op==='ink')reader.ink[op.stroke.id]={...op.stroke,deleted:false};if(op.op==='ink_del')reader.ink[op.id]={...reader.ink[op.id],deleted:true};this.emit('reader',op);}};
  window.PR=PR;
  vm.runInNewContext(fs.readFileSync('easyread/web/js/reader/ink.js','utf8'),{window,document,ResizeObserver:class{constructor(fn){resize=fn;}observe(){}},Date,Math,Number});
  PR.setInkActive(true);PR.setInkInputMode('pen');paints=hashes=reads=0;
  const send=(type,x,y,extra={})=>layer.emit(type,{type,pointerType:'pen',button:0,buttons:type==='pointerup'?0:1,pointerId:7,clientX:x,clientY:y,preventDefault(){},...extra});
  return {PR,reader,layer,block,window,send,frames,callbacks,resize:()=>resize(),stats:()=>({paints,hashes,reads}),frame(){const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn());},last:()=>Object.values(reader.ink).find(s=>s.id!=='saved'&&!s.deleted)};
}

test('iPad preview batches samples into one frame without repainting saved paths or rehashing text',()=>{
  const f=fixture();f.send('pointerdown',30,60);const saved=f.layer.children.find(c=>c.dataset.stroke==='saved'), preview=f.layer.children.at(-1), before=f.stats();
  for(let n=1;n<=30;n++) f.send('pointermove',30+n*3,60,{getCoalescedEvents:()=>[{clientX:30+n*3-1.5,clientY:62},{clientX:30+n*3,clientY:60}]});
  assert.equal(f.frames.size,1);assert.equal(f.stats().paints,before.paints);assert.equal(f.stats().hashes,before.hashes);
  assert.equal(f.stats().reads-before.reads,30,"one anchor check per dispatch, not per saved stroke or coalesced sample");
  f.frame();assert.equal(f.layer.children.at(-1),preview);assert.equal(saved.parentNode,f.layer);
  assert.equal(f.stats().paints,0);assert.equal(f.stats().hashes,1);
  f.send('pointerup',120.3,60);
  const stroke=f.last();assert.ok(stroke.points.length>30);assert.deepEqual(Array.from(stroke.points.at(-1)),[100.3,10]);
  assert.equal(f.stats().paints,1,'only one saved-layer redraw at commit');
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
  f.send('pointerdown',70,60);const preview=f.layer.children.at(-1);
  old();assert.equal(f.layer.children.at(-1),preview);assert.equal(f.frames.size,0);
  f.send('pointerup',71,60);
});

test('stale scroll events do not cut off a new pencil contact, but real scrolling does',()=>{
  const f=fixture();f.send('pointerdown',30,60);f.window.emit('scroll');
  assert.equal(f.last(),undefined);
  f.send('pointermove',50,60);f.window.scrollY=20;f.window.emit('scroll');
  assert.ok(f.last());assert.equal(f.frames.size,0);
  f.send('pointerup',80,80);assert.deepEqual(Array.from(f.last().points.at(-1)),[30,10]);
});

test('invalid samples are ignored, point storage remains bounded, and external redraw retains the live path',()=>{
  const f=fixture();f.send('pointerdown',30,60);
  f.send('pointermove',32,60,{getCoalescedEvents:()=>[{clientX:NaN,clientY:4},{clientX:Infinity,clientY:2}]});
  const preview=f.layer.children.at(-1);f.PR.renderInk();assert.equal(f.layer.children.at(-1),preview);
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

test('Safari repaint waits for the iPad pan to end and does not write any strokes',()=>{
  const f=fixture();let panning=true;f.PR.readerInput.panning=()=>panning;
  f.window.emit('scroll');assert.equal(f.stats().paints,0);
  panning=false;f.PR.emit('ink-pan-end');assert.equal(f.stats().paints,1);
  assert.equal(Object.keys(f.reader.ink).length,1);
});
