/* Rendering/routing regression with the real reader and synthetic pencil samples.
   NODE_PATH=<playwright node_modules> node tests/browser/ipad-ink.cjs
   This measures DOM work in Chromium, not physical Apple Pencil/Safari latency. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {chromium} = require('playwright');
const web = path.resolve(__dirname,'../../easyread/web');
const sample={paper:{schema:2,meta:{title_en:'Pencil performance fixture',page_count:1},translation:{done_pages:[1],gaps_checked:true},
  blocks:Array.from({length:120},(_,i)=>({id:'p'+i,type:'para',page:1,en:'Synthetic reader paragraph for testing pen input. '.repeat(5),zh:'Synthetic reader paragraph for testing pen input. '.repeat(5)}))},
  reader:{schema:2,rev:0,notes:{},edits:{},ink:{},stickers:{},progress:{}},discussion:{entries:[]},layout:{},item:{}};
const html=fs.readFileSync(path.join(web,'reader.html'),'utf8').replace('<!--PR:DATA-->','<script id="pr-data" type="application/json">'+JSON.stringify(sample)+'</script>');
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/read/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(url.pathname.startsWith('/web/')){
    const file=path.resolve(web,decodeURIComponent(url.pathname.slice(5)));
    if(file.startsWith(web+path.sep)&&fs.existsSync(file)&&fs.statSync(file).isFile()){
      res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.woff2':'font/woff2','.png':'image/png'})[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);return;
    }
  }
  res.setHeader('Content-Type','application/json');res.end('{}');
});
async function checkInterleavedContact(page) {
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.waitForTimeout(200);
  const start=await page.evaluate(()=>{
    const path=document.querySelector('#inkLayer [data-stroke]'), p=path.getPointAtLength(path.getTotalLength()/2);
    const point=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());
    window.penDownEvidence=null;window.fullDraws=0;
    window.addEventListener('pointerdown',e=>{if(e.pointerType==='pen')penDownEvidence={id:e.pointerId,trusted:e.isTrusted,child:e.target!==document.getElementById('inkLayer')};},{capture:true,once:true});
    window.sendPalm=(type,id)=>document.getElementById('bar').dispatchEvent(new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'touch',pointerId:id,clientX:30,clientY:20,buttons:type==='pointerup'?0:1}));
    return {x:point.x,y:point.y,before:Object.keys(PR.state.reader.ink).length};
  });
  const cdp=await page.context().newCDPSession(page);
  const pen=(type,x,y)=>cdp.send('Input.dispatchMouseEvent',{type,x,y,pointerType:'pen',button:type==='mouseMoved'?'none':'left',buttons:type==='mouseReleased'?0:1,force:type==='mouseReleased'?0:.5});
  await pen('mousePressed',start.x,start.y);
  const evidence=await page.evaluate(()=>penDownEvidence);assert.ok(evidence?.trusted&&evidence.child,'trusted pen hits an existing SVG path, not a synthetic layer target');
  assert.equal(await page.evaluate(id=>document.getElementById('inkLayer').hasPointerCapture(id),evidence.id),true);
  await page.evaluate(id=>{
    sendPalm('pointerdown',7001);sendPalm('pointerdown',7002);
    document.getElementById('inkLayer').releasePointerCapture(id);
  },evidence.id);
  for(let i=1;i<=8;i++){
    await pen('mouseMoved',start.x+i*3,start.y+Math.sin(i)*5);
    await page.evaluate(()=>{PR.renderInk();window.dispatchEvent(new Event('resize'));});
    await page.evaluate(()=>new Promise(requestAnimationFrame));
    assert.equal(await page.evaluate(()=>fullDraws),0,'interleaved resize/data requests cannot rebuild history during contact');
    assert.equal(await page.evaluate(id=>PR.readerInput.keepsPenContact(id),evidence.id),true);
  }
  const surface=await page.locator('.ipad-ink-preview').evaluate(el=>({parent:el.parentElement.tagName,height:el.getBoundingClientRect().height,viewport:innerHeight,pointerEvents:getComputedStyle(el).pointerEvents}));
  assert.equal(surface.parent,'BODY');assert.equal(surface.height,surface.viewport);assert.equal(surface.pointerEvents,'none');
  await pen('mouseMoved',start.x+30,20); // uncaptured pointer outside the canvas still reaches ink exactly once
  await pen('mouseReleased',start.x+31,20);
  assert.equal(await page.evaluate(()=>PR.readerInput.penContact()),false);
  assert.equal(await page.locator('.ipad-ink-preview').count(),0);
  assert.equal(await page.evaluate(()=>Object.keys(PR.state.reader.ink).length),start.before+1,'capture transfer cannot split or drop the stroke');
  // A fresh gesture may clear the compatibility tail, but cannot rehabilitate the held palms.
  await page.evaluate(()=>{sendInk('pointerdown',100,300,{pointerType:'touch',pointerId:7003});sendInk('pointerup',100,300,{pointerType:'touch',pointerId:7003});});
  const palms=await page.evaluate(()=>{
    const rejected=[!sendPalm('pointermove',7001),!sendPalm('lostpointercapture',7002),!sendPalm('pointerup',7001),!sendPalm('pointerup',7002)];
    return {rejected,fresh:sendPalm('pointerdown',7004)};
  });
  assert.deepEqual(palms.rejected,[true,true,true,true]);assert.equal(palms.fresh,true);
  await page.evaluate(()=>sendPalm('pointerup',7004));await cdp.detach();
}
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.CHROME||'/usr/bin/google-chrome',headless:true});
  const safari='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.6 Safari/605.1.15';
  const profiles=[{name:'ipad-optimized',ua:safari,platform:'MacIntel',touch:5,opt:true},
    {name:'ipad-opt-out',ua:safari,platform:'MacIntel',touch:5,opt:false},
    {name:'android-unchanged',ua:'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/140.0 Safari/537.36',platform:'Linux armv8l',touch:5,opt:true},
    {name:'desktop-unchanged',platform:'Linux x86_64',touch:0,opt:true}];
  const results=[],errors=[];
  try{
    for(const profile of profiles){
      const context=await browser.newContext({viewport:{width:1024,height:768},hasTouch:!!profile.touch,...(profile.ua?{userAgent:profile.ua}:{})});
      await context.addInitScript(({platform,touch})=>{
        Object.defineProperty(navigator,'platform',{get:()=>platform});Object.defineProperty(navigator,'maxTouchPoints',{get:()=>touch});
      },profile);
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
      await page.goto('http://127.0.0.1:'+server.address().port+'/read/'+profile.name);
      await page.waitForFunction(()=>!!PR.blockById?.p0);await page.evaluate(()=>document.fonts.ready);
      await page.evaluate(opt=>{PR.features.ipad_ink=opt;PR.setInkActive(true);PR.setInkInputMode("pen");},profile.opt);
      await page.waitForFunction(()=>document.getElementById("b-p0")?.innerText.includes("Synthetic reader paragraph"));
      await page.evaluate(()=>document.fonts.ready);
      await page.waitForTimeout(250);
      await page.evaluate(async ()=>{
        const block=document.getElementById('b-p0'), rect=block.getBoundingClientRect();
        const data=JSON.parse(document.getElementById('pr-data').textContent);
        for(let i=0;i<250;i++) data.reader.ink['saved-'+i]={id:'saved-'+i,anchor:'p0',ref:[rect.width,rect.height],textHash:PR.hashText(block.innerText),points:[[1,i%30],[30,i%30+2]],color:'#1d1d1d',width:2};
        document.getElementById('pr-data').textContent=JSON.stringify(data);await PR.load();
        PR.renderInk();
        window.savedInk=JSON.stringify(PR.state.reader.ink);
        window.sendInk=(type,x,y,extra={})=>{
          const e=new PointerEvent(type,{bubbles:true,cancelable:true,pointerType:'pen',pointerId:77,button:0,buttons:type==='pointerup'?0:1,pressure:type==='pointerup'?0:.5,clientX:x,clientY:y,screenX:x,screenY:y,...extra});
          if(extra.samples)Object.defineProperty(e,'getCoalescedEvents',{value:()=>extra.samples});
          document.getElementById('inkLayer').dispatchEvent(e);
        };
      });
      await page.waitForTimeout(250);
      assert.equal(await page.locator("#inkLayer [data-stroke]").count(),250,"seed rendered geometry, not lazy placeholders");
      const move=await page.evaluate(()=>{
        const layer=document.getElementById('inkLayer'), r=document.getElementById('b-p0').getBoundingClientRect();
        window.fullDraws=0;const original=layer.replaceChildren.bind(layer);layer.replaceChildren=(...args)=>{fullDraws++;return original(...args);};
        window.oldPath=layer.querySelector('[data-stroke]');
        if(!oldPath)throw Error(JSON.stringify({ink:Object.values(PR.state.reader.ink).slice(0,1),rect:{width:r.width,height:r.height},hash:PR.hashText(document.getElementById('b-p0').innerText),active:PR.inkActive()}));
        window.startPoint={x:r.left+10,y:r.top+20};
        const start=performance.now();sendInk('pointerdown',startPoint.x,startPoint.y);
        for(let i=1;i<=80;i++){
          const x=startPoint.x+i*6,y=startPoint.y+Math.sin(i*.3)*8;
          sendInk('pointermove',x,y,{samples:[{clientX:x-4.5,clientY:y-1},{clientX:x-3,clientY:y-1},{clientX:x-1.5,clientY:y},{clientX:x,clientY:y}]});
        }
        return {fullDraws,elapsedMs:performance.now()-start,oldPathConnected:oldPath.isConnected,ipad:PR.readerInput.ipadSafari};
      });
      await page.evaluate(()=>new Promise(requestAnimationFrame));
      if(profile.name==='ipad-optimized'){assert.equal(move.fullDraws,0);assert.equal(move.oldPathConnected,true);}
      else assert.equal(move.fullDraws,80,'Legacy platforms/opt-out retain their original render path');
      const saved=await page.evaluate(()=>{
        sendInk('pointerup',startPoint.x+480.3,startPoint.y+Math.sin(80*.3)*8);
        const added=Object.values(PR.state.reader.ink).find(s=>!s.id.startsWith('saved-'));
        const previous=Object.fromEntries(Object.entries(PR.state.reader.ink).filter(([id])=>id.startsWith('saved-')));
        return {id:added.id,points:added.points.length,tail:added.points.at(-1),previous:JSON.stringify(previous),draws:fullDraws};
      });
      assert.equal(saved.previous,await page.evaluate(()=>savedInk));
      assert.ok(saved.points<=2000);
      if(profile.name==='ipad-optimized') {assert.ok(saved.points>81);assert.equal(saved.draws,0,'ordinary pen-up adds one path without rebuilding history');}
      else assert.equal(saved.points,81);
      await page.evaluate(id=>{PR.inkHistory(false);if(!PR.state.reader.ink[id].deleted)throw Error('Undo failed');PR.inkHistory(true);if(PR.state.reader.ink[id].deleted)throw Error('Redo failed');},saved.id);
      if(profile.name==='ipad-optimized'){
        await page.evaluate(()=>window.scrollTo({top:300,behavior:'instant'}));await page.waitForTimeout(200);
        await page.evaluate(()=>{
          sendInk('pointerdown',180,400,{pointerType:'touch',pointerId:20,screenY:500});
          sendInk('pointermove',180,350,{pointerType:'touch',pointerId:20,screenY:450});
        });
        await page.evaluate(()=>new Promise(requestAnimationFrame));assert.equal(await page.evaluate(()=>scrollY),350);
        await page.evaluate(()=>sendInk('pointermove',180,200,{pointerType:'touch',pointerId:20,screenY:450}));
        await page.evaluate(()=>new Promise(requestAnimationFrame));assert.equal(await page.evaluate(()=>scrollY),350,'client-coordinate drift must not add movement');
        await page.evaluate(()=>{
          sendInk('pointermove',180,180,{pointerType:'touch',pointerId:20,screenY:430});
          sendInk('pointerdown',180,350,{pointerId:21});
          window.dispatchEvent(new Event('scroll')); // stale notification, same offset
        });
        await page.evaluate(()=>new Promise(requestAnimationFrame));assert.equal(await page.evaluate(()=>scrollY),350,'Pencil contact cancels queued finger movement');
        await page.evaluate(()=>{
          sendInk('pointermove',200,352,{pointerId:21});sendInk('pointerup',200.3,352,{pointerId:21});
          sendInk('pointerup',180,180,{pointerType:'touch',pointerId:20});
        });
        assert.equal(await page.evaluate(()=>Object.values(PR.state.reader.ink).filter(s=>!s.id.startsWith('saved-')&&!s.deleted).length),2,'fingers must not create strokes');
        assert.equal(await page.locator('#inkLayer').evaluate(el=>getComputedStyle(el).touchAction),'none');
        await page.evaluate(firstId=>{
          const stroke=Object.values(PR.state.reader.ink).find(s=>!s.id.startsWith('saved-')&&s.id!==firstId&&!s.deleted);
          const path=document.querySelector('[data-stroke="'+CSS.escape(stroke.id)+'"]');
          const local=path.getPointAtLength(path.getTotalLength()/2), point=new DOMPoint(local.x,local.y).matrixTransform(path.getScreenCTM());
          PR.toggleInkEraser();sendInk('pointerdown',point.x,point.y);sendInk('pointerup',point.x,point.y);
          if(!PR.state.reader.ink[stroke.id].deleted)throw Error('Eraser failed after finger-to-Pencil transition');
          PR.inkHistory(false);if(PR.state.reader.ink[stroke.id].deleted)throw Error('Eraser undo failed');
          PR.toggleInkEraser();
        },saved.id);
        await checkInterleavedContact(page);
      }
      results.push({profile:profile.name,move,savedPoints:saved.points});await context.close();
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,results,errors,scope:'Chromium with simulated platform identifiers, synthetic palms and trusted CDP pen/capture events; physical iPad Safari/Apple Pencil requires user-device verification'},null,2));
  }finally{await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
