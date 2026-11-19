/* DOM/focus regression using the real reader and a disposable static sample.
   NODE_PATH=<playwright node_modules> node tests/browser/note-focus.cjs
   Keyboard height and composition events are simulated, not physical-device IME certification. */
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {chromium} = require('playwright');
const web = path.resolve(__dirname,'../../easyread/web');
const sample = {
  paper:{schema:2,meta:{title_en:'Note focus sample',page_count:1},translation:{done_pages:[1],gaps_checked:true},
    blocks:[{id:'p1',type:'para',page:1,en:'Focus sample sentence for annotations.',zh:'Focus sample sentence for annotations.'},
      {id:'figure1',type:'figure',page:1,src:'figures/sample.svg',caption_en:'Sample figure'}]},
  reader:{schema:2,rev:0,notes:{
    note1:{id:'note1',anchor:'p1',key:'p1',side:'zh',kind:'highlight',quote:'Focus sample sentence',body:'',color:'yellow'},
    note2:{id:'note2',anchor:'p1',key:'p1',side:'zh',kind:'note',body:'Second initial',color:'blue'}},
    edits:{},ink:{},stickers:{},progress:{}},
  discussion:{entries:[]},layout:{},item:{},
  images:{'figures/sample.svg':'data:image/svg+xml;base64,'+Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="teal"/></svg>').toString('base64')},
};
const html = fs.readFileSync(path.join(web,'reader.html'),'utf8').replace('<!--PR:DATA-->',
  '<script id="pr-data" type="application/json">'+JSON.stringify(sample).replace(/</g,'\\u003c')+'</script>');
const server = http.createServer((req,res)=>{
  const url = new URL(req.url,'http://localhost');
  if(url.pathname.startsWith('/read/')) {res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(url.pathname.startsWith('/web/')) {
    const file=path.resolve(web,decodeURIComponent(url.pathname.slice(5)));
    if(file.startsWith(web+path.sep)&&fs.existsSync(file)&&fs.statSync(file).isFile()) {
      res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.json':'application/json','.svg':'image/svg+xml','.woff2':'font/woff2','.png':'image/png'})[path.extname(file)]||'application/octet-stream');
      fs.createReadStream(file).pipe(res);return;
    }
  }
  res.setHeader('Content-Type','application/json');res.end('{}');
});
(async()=>{
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:process.env.CHROME || '/usr/bin/google-chrome',headless:true});
  const context=await browser.newContext({isMobile:true,hasTouch:true,viewport:{width:390,height:844}});
  const errors=[], results=[]; let sequence=0;
  async function open(width, panel=false) {
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.setViewportSize({width,height:844});
    await page.goto('http://127.0.0.1:'+server.address().port+'/read/note-focus-'+(++sequence));
    await page.waitForFunction(()=>!!PR.blockById?.p1 && !!PR.openNoteEditor);
    await page.evaluate(()=>document.fonts.ready);
    await page.waitForTimeout(220); // Let initial layout and the existing 150ms resize debounce settle.
    await page.evaluate(panel=>{
      if(panel){PR.openAnnotations();PR.renderNotesPanel('note1');} else PR.openNoteEditor('note1');
    },panel);
    return page;
  }
  async function remember(page, text='Draft before keyboard') {
    await page.locator('.card.editing textarea:visible').first().fill(text);
    await page.evaluate(()=>{
      window.focusEditor=document.querySelector('.card.editing textarea');
      focusEditor.setSelectionRange(2,7);
    });
  }
  async function assertEditor(page, text='Draft before keyboard', selection=true) {
    const state=await page.evaluate(()=>({connected:focusEditor.isConnected,focused:document.activeElement===focusEditor,value:focusEditor.value,
      start:focusEditor.selectionStart,end:focusEditor.selectionEnd,compact:document.body.classList.contains('note-editor-compact')}));
    assert.equal(state.connected,true);assert.equal(state.focused,true);assert.equal(state.value,text);
    if(selection){assert.equal(state.start,2);assert.equal(state.end,7);}
    return state;
  }
  async function composition(page) {
    const previous=await page.evaluate(()=>PR.state.reader.notes.note1.body);
    await page.evaluate(()=>{
      focusEditor.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:''}));
      focusEditor.value='ni';
      focusEditor.dispatchEvent(new InputEvent('input',{bubbles:true,data:'ni',isComposing:true,inputType:'insertCompositionText'}));
      focusEditor.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Escape',isComposing:true}));
      focusEditor.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Enter',ctrlKey:true,keyCode:229}));
      PR.renderMargin();PR.renderMargin();
    });
    await page.waitForTimeout(700);
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.body),previous,'Do not autosave intermediate IME text');
    await assertEditor(page,'ni',false);
    await page.evaluate(()=>{
      focusEditor.value='你好';
      focusEditor.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'你好'}));
      focusEditor.dispatchEvent(new InputEvent('input',{bubbles:true,data:'你好',inputType:'insertText'}));
      focusEditor.dispatchEvent(new KeyboardEvent('keydown',{bubbles:true,cancelable:true,key:'Escape',keyCode:229}));
    });
    await page.waitForFunction(()=>PR.state.reader.notes.note1.body==='你好');
    await assertEditor(page,'你好',false);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>focusEditor.isConnected),false,'Normal Escape closes after composition ends');
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.body),'你好');
  }
  try {
    for(const [width,panel] of [[390,false],[1024,false],[390,true]]) {
      const page=await open(width,panel);await remember(page);
      await page.setViewportSize({width,height:500});await page.waitForTimeout(220);
      await assertEditor(page);
      await page.evaluate(()=>{
        PR.renderMargin();PR.emit('job-finished',{kind:'answer',note:'unrelated',state:'done'});PR.emit('ui-changed');
        PR.state.discussion.entries.push({id:'remote-reply',kind:'explain',anchor:'p1',body:'Background reply'});
        PR.emit('remote',['discussion']);
      });
      await assertEditor(page);
      await page.waitForFunction(()=>PR.state.reader.notes.note1.body==='Draft before keyboard');
      await composition(page);
      results.push({width,panel,heightResize:true,incidentalRender:true,composition:true,autosave:true});await page.close();
    }
    for(const [from,to] of [[1024,390],[390,1024]]) {
      const page=await open(from);await remember(page);
      await page.evaluate(()=>focusEditor.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})));
      await page.setViewportSize({width:to,height:500});await page.waitForTimeout(220);
      const state=await assertEditor(page);
      if(from===1024) {
        assert.equal(state.compact,true);
        const bounds=await page.evaluate(()=>{const r=focusEditor.closest('.card').getBoundingClientRect();return {x:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth,height:innerHeight,scroll:document.documentElement.scrollWidth};});
        assert.ok(bounds.x>=0 && bounds.right<=bounds.width+1 && bounds.top>=0 && bounds.bottom<=bounds.height,bounds);
        assert.ok(bounds.scroll<=bounds.width+2,JSON.stringify(bounds));
        await page.evaluate(()=>{
          Object.defineProperty(visualViewport,'height',{value:300,configurable:true});
          visualViewport.dispatchEvent(new Event('resize'));
        });
        await assertEditor(page);
        assert.ok(await page.evaluate(()=>focusEditor.closest('.card').getBoundingClientRect().bottom<=300));
        await page.evaluate(()=>{delete visualViewport.height;visualViewport.dispatchEvent(new Event('resize'));});
        if(process.env.NOTE_FOCUS_SCREENSHOT) await page.screenshot({path:process.env.NOTE_FOCUS_SCREENSHOT});
        await page.setViewportSize({width:from,height:844});await page.waitForTimeout(220);
        assert.equal((await assertEditor(page)).compact,false);
        await page.setViewportSize({width:to,height:500});await page.waitForTimeout(220);
        await assertEditor(page);
      }
      await page.evaluate(()=>focusEditor.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true})));
      await page.locator('.card.editing [data-a="done"]').click();
      assert.equal(await page.evaluate(()=>PR.editingNote),null);
      assert.equal(await page.evaluate(()=>document.body.classList.contains('note-editor-compact')),false);
      assert.equal(await page.evaluate(()=>document.body.classList.contains('margin-wide')),to===1024);
      assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.body),'Draft before keyboard');
      results.push({rotation:[from,to],focus:true,draft:true,layoutAfterClose:true});await page.close();
    }
    const longPage=await open(1024);await remember(longPage);
    await longPage.setViewportSize({width:390,height:500});await longPage.waitForTimeout(220);
    const longText=('Long wrapped draft must remain fully visible. ').repeat(45);
    await longPage.locator('.card.editing textarea').fill(longText);
    await longPage.evaluate(()=>focusEditor.setSelectionRange(2,7));
    for(const width of [1360,1024]) {
      await longPage.setViewportSize({width,height:700});await longPage.waitForTimeout(220);
      await assertEditor(longPage,longText);
      assert.ok(await longPage.evaluate(()=>focusEditor.scrollHeight<=focusEditor.clientHeight+2),'Autosize retained text after width changes');
    }
    await longPage.locator('.card.editing [data-a="done"]').click();
    assert.equal(await longPage.evaluate(()=>PR.state.reader.notes.note1.body),longText.trim());
    results.push({longDraft:true,autosizeAfterRotation:true});await longPage.close();
    const transfer=await open(390);await remember(transfer,'Draft transferred to panel');
    await transfer.evaluate(()=>{PR.openAnnotations();PR.renderNotesPanel('note1');});
    assert.equal(await transfer.evaluate(()=>PR.editingNote),null);
    assert.equal(await transfer.locator('#notespanel .card.editing textarea').inputValue(),'Draft transferred to panel');
    await transfer.locator('#notespanel .card.editing textarea').fill('New panel draft');
    await transfer.evaluate(()=>{
      focusEditor=document.querySelector('#notespanel .card.editing textarea');focusEditor.setSelectionRange(2,7);
      PR.renderNotesPanel('note1');
    });
    await transfer.waitForTimeout(220);await assertEditor(transfer,'New panel draft');
    await transfer.locator('#notespanel .card.editing [data-color="pink"]').click();
    assert.equal(await transfer.evaluate(()=>PR.state.reader.notes.note1.color),'pink');
    await transfer.evaluate(()=>{PR.toggleNotesPanel(false);PR.openNoteEditor('note1');});
    await transfer.waitForTimeout(220);
    assert.equal(await transfer.locator('.inline-notes .card.editing textarea').inputValue(),'New panel draft');
    await transfer.locator('.inline-notes .card.editing [data-a="done"]').click();
    assert.equal(await transfer.evaluate(()=>PR.state.reader.notes.note1.body),'New panel draft');
    results.push({surfaceTransfer:true,panelSameNoteSelection:true,panelExplicitControls:true});await transfer.close();
    const page=await open(390);await remember(page,'Unsaved before controls');
    await page.locator('.card.editing [data-color="green"]').click();
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.body),'Unsaved before controls');
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.color),'green');
    await page.locator('.card.editing [data-k="question"]').click();
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.kind),'question');
    await page.evaluate(()=>{focusEditor=document.querySelector('.card.editing textarea');focusEditor.setSelectionRange(2,7);PR.openNoteEditor('note1');});
    await assertEditor(page,'Unsaved before controls');
    await page.locator('.card.editing [data-note-media="add"]').click();
    await page.evaluate(()=>PR.renderMargin());
    await page.locator('[data-pick-media="figure1"]').click();
    await assertEditor(page,'Unsaved before controls');
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.media_refs[0].id),'figure1');
    await page.locator('.card.editing textarea').fill('Saved when switching notes');
    await page.evaluate(()=>PR.openNoteEditor('note2'));
    assert.equal(await page.evaluate(()=>PR.state.reader.notes.note1.body),'Saved when switching notes');
    assert.equal(await page.locator('.card.editing textarea').inputValue(),'Second initial');
    await page.evaluate(()=>{PR.state.reader.notes.extra={id:'extra',anchor:'p1',kind:'note',body:'Deferred note update'};PR.renderMargin();});
    await page.locator('.card.editing [data-a="done"]').click();
    assert.ok(await page.locator('.card[data-note="extra"]').count());
    results.push({explicitControls:true,sameNoteSelection:true,mediaPicker:true,noteSwitch:true,deferredUpdate:true});
    await page.close();assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,results,errors,scope:'Chromium mobile viewport and synthetic composition; no physical-device IME certification'},null,2));
  } finally {await context.close();await browser.close();server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
