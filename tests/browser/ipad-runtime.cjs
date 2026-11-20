/* Real server-mode reader + PWA/IndexedDB/localStorage, with an isolated fake HTTP backend.
   NODE_PATH=<playwright node_modules> node tests/browser/ipad-runtime.cjs
   No real library or model service. Chromium is not physical iPad acceptance. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright');
const web=path.resolve(__dirname,'../../easyread/web');
const state={paper:{schema:2,meta:{title_en:'Runtime fixture',page_count:1},translation:{done_pages:[1],gaps_checked:true},
  blocks:Array.from({length:120},(_,i)=>({id:'p'+i,type:'para',page:1,en:'Testing isolated handwriting persistence. '.repeat(6),zh:'Testing isolated handwriting persistence. '.repeat(6)}))},
  reader:{schema:2,rev:1,notes:{},edits:{},ink:{},stickers:{},progress:{}},discussion:{entries:[]},layout:{},item:{},job:{},images:{},engine:'none',token:'fixture',library_id:'b'.repeat(24)};
let posts=0,delay=0;
const versions=()=>({reader:String(state.reader.rev),paper:'1',discussion:'1',layout:'1',item:'1',job:'1'});
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  const json=value=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
  if(url.pathname.startsWith('/read/')){res.setHeader('Content-Type','text/html');res.end(fs.readFileSync(path.join(web,'reader.html'),'utf8'));return;}
  if(url.pathname==='/sw.js'){res.setHeader('Content-Type','text/javascript');res.end("self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('message',e=>e.ports[0]?.postMessage({ok:true}));");return;}
  if(url.pathname.startsWith('/web/')){
    const file=path.resolve(web,decodeURIComponent(url.pathname.slice(5)));
    if(file.startsWith(web+path.sep)&&fs.existsSync(file)&&fs.statSync(file).isFile()){
      res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.woff2':'font/woff2','.png':'image/png'})[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);return;
    }
  }
  if(url.pathname.endsWith('/state'))return json({...state,versions:versions()});
  if(url.pathname.endsWith('/versions'))return json(versions());
  const part=url.pathname.match(/\/part\/(\w+)$/);if(part)return json({data:state[part[1]]||{},version:versions()[part[1]]});
  if(url.pathname.endsWith('/ops')){
    let body='';for await(const chunk of req)body+=chunk;
    for(const op of JSON.parse(body).ops){
      if(op.op==='ink')state.reader.ink[op.stroke.id]={...op.stroke,updated:op.at};
      if(op.op==='ink_del')state.reader.ink[op.id]={...state.reader.ink[op.id],deleted:true,updated:op.at};
      if(op.op==='progress')state.reader.progress={block:op.block,off:op.off,ratio:op.ratio,at:op.at};
    }
    state.reader.rev++;posts++;if(delay)await new Promise(r=>setTimeout(r,delay));
    return json({rev:state.reader.rev,versions:versions()});
  }
  json({});
});
const frame=page=>page.evaluate(()=>new Promise(requestAnimationFrame));
async function ready(page){
  await page.waitForFunction(()=>window.PR?.store.mode==='server'&&!!PR.pwa?.scope&&document.getElementById('b-p0')?.innerText.includes('Testing isolated'));
  await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(800);
  await page.evaluate(()=>{PR.setInkActive(true);PR.setInkInputMode('pen');});
  await page.evaluate(()=>PR.flushPending());await page.waitForTimeout(150);
}
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const browser=await chromium.launch({executablePath:process.env.CHROME||'/usr/bin/google-chrome',headless:true});
  const errors=[];
  try{
    const context=await browser.newContext({viewport:{width:1024,height:768},hasTouch:true,userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.6 Safari/605.1.15'});
    await context.addInitScript(()=>{Object.defineProperty(navigator,'platform',{get:()=> 'MacIntel'});Object.defineProperty(navigator,'maxTouchPoints',{get:()=>5});});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
    const url='http://127.0.0.1:'+server.address().port+'/read/fixture';await page.goto(url);await ready(page);
    const ref=await page.evaluate(()=>{const b=document.getElementById('b-p0'),r=b.getBoundingClientRect();return {ref:[r.width,r.height],textHash:PR.hashText(b.innerText)};});
    for(let i=0;i<300;i++)state.reader.ink['old-'+i]={id:'old-'+i,anchor:'p0',...ref,points:Array.from({length:128},(_,j)=>[j,j%30]),color:'#111111',width:2};
    state.reader.rev++;await page.reload();await ready(page);
    assert.equal(await page.locator('#inkLayer [data-stroke]').count(),300);
    await page.evaluate(()=>{
      const rect=document.getElementById('b-p0').getBoundingClientRect();window.xy={x:rect.left+20,y:rect.top+40};
      window.metrics={clones:0,reads:0,fullPaints:0,remote:0,commitMs:[]};
      const stringify=JSON.stringify;JSON.stringify=function(v,...args){if(v?.ink&&v?.notes&&v?.progress&&v.rev!==undefined)metrics.clones++;return stringify.call(this,v,...args);};
      const get=Storage.prototype.getItem;Storage.prototype.getItem=function(key){if(key.startsWith(PR.pwa.outboxKey(PR.pid)))metrics.reads++;return get.call(this,key);};
      const layer=document.getElementById('inkLayer'),paint=layer.replaceChildren.bind(layer);layer.replaceChildren=(...args)=>{metrics.fullPaints++;return paint(...args);};
      const commit=PR.commit;PR.commit=function(op){const t=performance.now();try{return commit(op);}finally{if(op.op==='ink')metrics.commitMs.push(performance.now()-t);}};
      PR.on('remote',()=>metrics.remote++);
      window.draw=(type,dx=0,extra={})=>layer.dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'pen',pointerId:77,isPrimary:true,button:0,buttons:type==='pointerup'?0:1,pressure:type==='pointerup'?0:.5,clientX:xy.x+dx,clientY:xy.y,...extra}));
    });
    const beforePosts=posts;
    for(let i=0;i<40;i++){
      await page.evaluate(()=>{draw('pointerdown');draw('pointermove',8);draw('pointerup',12);});await frame(page);
    }
    const rapid=await page.evaluate(()=>({...metrics,pending:PR.store.pending,keys:Object.keys(localStorage).filter(k=>k.startsWith(PR.pwa.outboxKey(PR.pid))).length}));
    assert.equal(rapid.clones,0);assert.equal(rapid.reads,40);assert.equal(rapid.pending,40);assert.equal(rapid.keys,40);assert.equal(posts,beforePosts);
    state.reader.rev++;await page.waitForTimeout(135);await page.evaluate(()=>PR.poll());
    assert.equal(await page.evaluate(()=>metrics.remote),0,'skipped reader response must not rerender the actual document');
    delay=180;const startPosts=posts;await page.evaluate(()=>{window.pendingFlush=PR.flush();});
    const deadline=Date.now()+5000;while(posts===startPosts&&Date.now()<deadline)await new Promise(r=>setTimeout(r,10));assert.ok(posts>startPosts);
    await page.evaluate(()=>{draw('pointerdown');draw('pointermove',4,{buttons:0,pressure:0});});
    await page.waitForTimeout(240);
    assert.equal(await page.evaluate(()=>PR.readerInput.keepsPenContact(77)),true,'ambiguous zero move cannot finish the contact');
    assert.equal(await page.locator('.ipad-ink-preview').count(),1);
    assert.equal(await page.evaluate(()=>metrics.clones),0,'in-flight acknowledgement waits instead of cloning during the next stroke');
    await page.evaluate(()=>{draw('pointermove',10);draw('pointerup',14);});
    await page.evaluate(()=>PR.flushPending());delay=0;
    assert.equal(await page.evaluate(()=>PR.store.pending),0);
    assert.equal(await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith(PR.pwa.outboxKey(PR.pid))).length),0);
    assert.equal(Object.keys(state.reader.ink).length,341);
    await page.reload();await ready(page);assert.equal(await page.locator('#inkLayer [data-stroke]').count(),341,'all strokes survive acknowledged reload');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,rapid,confirmedStrokes:341,errors,scope:'Isolated server-mode HTTP + real PWA persistence in Chromium; not physical iPad timing'},null,2));
    await context.close();
  }finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
})().catch(error=>{console.error(error);process.exitCode=1;});
