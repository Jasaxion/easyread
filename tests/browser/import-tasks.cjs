/* Run only against scripts/preview_fixture.py --with-web in a disposable library.
   NODE_PATH=<playwright node_modules> IMPORT_TEST_URL=http://127.0.0.1:18885 node tests/browser/import-tasks.cjs */
const assert = require('node:assert/strict');
const http = require('node:http'), net = require('node:net');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
const {chromium} = require('playwright');
(async () => {
  let release, fail = true;
  const gate = new Promise(resolve => {release=resolve;});
  const counts = {};
  const site = http.createServer(async (req,res) => {
    const key = req.url; counts[key]=(counts[key]||0)+1;
    if (key === '/slow') await gate;
    if (key === '/fail' && fail) {res.writeHead(503);res.end('Controlled failure');return;}
    if (key === '/figure.svg') {res.writeHead(200,{'Content-Type':'image/svg+xml'});res.end('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="teal"/></svg>');return;}
    res.writeHead(200,{'Content-Type':'text/html'});
    res.end('<html><head><title>Async fixture '+key+'</title></head><body><article><h1>Async fixture '+key+'</h1><p>'+('A saved article retains text and notes while its import happens in the background. '.repeat(5))+'</p><img src="/figure.svg" alt="fixture"></article></body></html>');
  });
  await new Promise(resolve => site.listen(0,'127.0.0.1',resolve));
  const source='http://127.0.0.1:'+site.address().port;
  const backend=new URL(process.env.IMPORT_TEST_URL || 'http://127.0.0.1:18885');
  let frozen=false, posted=0;
  const sockets=new Set();
  // A real proxy, rather than Playwright routing (which can be bypassed by a SW).
  const proxy=http.createServer((req,res)=>{
    if(frozen && req.url==='/api/research/v1/monitor') {res.writeHead(503);res.end('Monitor temporarily unavailable');return;}
    const lose=req.method==='POST' && req.url==='/api/import-tasks' && ++posted===1;
    const upstream=http.request({hostname:backend.hostname,port:backend.port,path:req.url,method:req.method,headers:req.headers,agent:false},response=>{
      if(lose) {response.resume();response.on('end',()=>{res.writeHead(504,{'Content-Type':'application/json'});res.end('{"error":"Simulated lost acceptance response"}');});}
      else {res.writeHead(response.statusCode,response.headers);response.pipe(res);}
    });
    upstream.on('error',()=>{if(!res.headersSent) res.writeHead(502);res.end();});
    res.on('close',()=>upstream.destroy()); req.pipe(upstream);
  });
  proxy.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
  proxy.on('upgrade',(req,socket,head)=>{
    const upstream=net.connect(Number(backend.port),backend.hostname,()=>{
      upstream.write(req.method+' '+req.url+' HTTP/1.1\r\n'+req.rawHeaders.reduce((s,v,i)=>s+v+(i%2?'\r\n':': '),'')+'\r\n');
      if(head.length) upstream.write(head);
      socket.pipe(upstream).pipe(socket);
    });
    upstream.on('error',()=>socket.destroy()); socket.on('close',()=>upstream.destroy());
  });
  await new Promise(resolve=>proxy.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+proxy.address().port;
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'easyread-import-browser-'));
  const context=await chromium.launchPersistentContext(profile,{executablePath:process.env.CHROME || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
  const page=await context.newPage(), errors=[], browserWarnings=[];
  page.on('pageerror',error => {
    // Chromium may cancel its CSS cross-document animation during test reloads.
    if(error.message==='Transition was skipped') browserWarnings.push(error.message);
    else errors.push(error.message);
  });
  async function waitAsync(check) {
    const end=Date.now()+15000;
    while(Date.now()<end) { if(await check()) return; await new Promise(resolve=>setTimeout(resolve,60)); }
    throw new Error('Timed out waiting for import state');
  }
  async function listing() { return (await (await context.request.get(base+'/api/import-tasks')).json()).tasks; }
  async function add(relative) {
    await page.locator('#importBtn').click();
    await page.locator('#arxivRef').fill(source+relative);
    await page.locator('#impKind').selectOption('web');
    await page.locator('#arxivGo').click();
    await page.waitForFunction(()=>!document.querySelector('#importDlg').classList.contains('open'));
  }
  try {
    const data=await (await context.request.get(base+'/api/library')).json();
    const location=await (await context.request.get(base+'/api/library/location')).json();
    assert.equal(location.temp,true,'Refusing to use a real library');
    assert.equal(data.engine,'none','Fixture must not call a model');
    assert.ok(data.items.some(item=>item.id==='sample-paper'));
    await page.goto(base+'/');
    await page.waitForFunction(()=>PR.pwa?.shellReady && PR.lib?.loadStatus==='ready');
    await page.locator('.row[data-id="sample-paper"]').click();
    frozen=true;
    await add('/slow');
    await page.waitForFunction(()=>PR.importTasks.list().some(task=>task.state==='unconfirmed'));
    assert.equal(posted,1);
    await page.locator('#pr-research-task-entry button').click();
    await page.getByRole('button',{name:'确认或重试',exact:true}).click();
    await page.waitForFunction(()=>PR.importTasks.list().some(task=>task.state==='running'||task.state==='queued'));
    assert.equal(posted,1,'Confirming a lost response must discover the accepted task, not POST again');
    await page.locator('[data-task-continue]').first().click(); frozen=false;
    await add('/second'); await add('/fail'); await add('/cancel');
    await waitAsync(async()=> (await listing()).length===4);
    const cancelled=(await listing()).find(task=>task.ref.endsWith('/cancel'));
    await page.evaluate(id=>PR.researchTaskMonitor.show(PR.importTasks.list().find(task=>task.id===id)),cancelled.id);
    await page.getByRole('button',{name:'取消导入',exact:true}).click();
    await waitAsync(async()=> (await listing()).find(task=>task.id===cancelled.id).state==='cancelled');
    await page.locator('[data-task-continue]').first().click();
    await page.waitForFunction(()=>document.querySelector('#pr-research-task-entry').innerText.includes('3 项任务正在导入'));
    await page.screenshot({path:process.env.IMPORT_SCREENSHOT || path.join(os.tmpdir(),'easyread-import-desktop.png')});
    await page.setViewportSize({width:390,height:844});
    await page.locator('#pr-research-task-entry button').click();
    await page.screenshot({path:path.join(os.tmpdir(),'easyread-import-mobile.png')});
    assert.ok(await page.evaluate(()=>document.querySelector('.pr-task-dialog').getBoundingClientRect().right<=innerWidth+1));
    await page.locator('[data-task-continue]').first().click();
    release();
    await waitAsync(async()=> (await listing()).filter(task=>task.state==='completed').length===2 && (await listing()).some(task=>task.state==='failed'));
    await page.waitForFunction(()=>PR.lib.items.some(item=>item.title_en==='Async fixture /slow'));
    assert.equal(await page.evaluate(()=>PR.lib.selected),'sample-paper');
    assert.equal(new URL(page.url()).pathname,'/');
    assert.equal(counts['/slow'],1); assert.equal(counts['/cancel']||0,0);
    const first=(await listing()).find(task=>task.ref.endsWith('/slow'));
    await page.evaluate(id=>PR.researchTaskMonitor.show(PR.importTasks.list().find(task=>task.id===id)),first.id);
    await page.getByRole('button',{name:'打开文献',exact:true}).click();
    await page.waitForURL('**/read/'+first.paper_id);
    await page.waitForLoadState('load');
    await page.waitForFunction(()=>!!PR.state?.paper);
    await page.reload();
    await page.waitForFunction(()=>PR.importTasks?.list().some(task=>task.state==='failed'));
    const failed=(await listing()).find(task=>task.ref.endsWith('/fail'));
    fail=false;
    await page.evaluate(id=>PR.researchTaskMonitor.show(PR.importTasks.list().find(task=>task.id===id)),failed.id);
    await page.getByRole('button',{name:'重试',exact:true}).click();
    await waitAsync(async()=> (await listing()).some(task=>task.retry_of===failed.id && task.state==='completed'));
    assert.equal(new URL(page.url()).pathname,'/read/'+first.paper_id,'Completion must not interrupt reading');
    assert.equal(counts['/fail'],2);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,checks:['dialog closes before fetching finishes','lost 504 response reconciles without duplicate POST','three-task capsule','queued cancellation avoids fetch','success/error stay distinct','library refresh preserves selection','mobile details','reload and reader-page manual retry','no automatic navigation'],requests:counts,browserWarnings},null,2));
  } finally {
    release(); await context.close(); fs.rmSync(profile,{recursive:true,force:true});
    for(const socket of sockets) socket.destroy();
    await Promise.all([new Promise(resolve=>{proxy.close(resolve);proxy.closeAllConnections?.();}),
      new Promise(resolve=>{site.close(resolve);site.closeAllConnections?.();})]);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
