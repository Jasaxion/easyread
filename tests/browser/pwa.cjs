/* Run against scripts/preview_fixture.py in an isolated library, never real data.
   NODE_PATH=<playwright node_modules> node tests/browser/pwa.cjs */
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs'), os = require('node:os'), path = require('node:path');
(async () => {
  // A disposable normal profile: Chromium intentionally forbids PWA install in incognito.
  const profile = fs.mkdtempSync(path.join(os.tmpdir(),'easyread-pwa-'));
  const context = await chromium.launchPersistentContext(profile,{executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless:true, args:['--no-sandbox']});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const base = process.env.PWA_TEST_URL || 'http://127.0.0.1:18865';
  // Explicitly await async predicates: some Playwright pollers treat a Promise
  // as truthy before it resolves (even async () => false can finish immediately).
  async function waitAsync(predicate) {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (await page.evaluate(predicate)) return;
      await new Promise(resolve => setTimeout(resolve,100));
    }
    throw new Error('Timed out waiting for durable offline state');
  }
  try {
    const admission = await context.request.post(base + '/api/access/login', {headers:{Origin:base},data:{code:'pwa-test-code'}});
    assert.equal(admission.status(),200,'Use only the disposable PWA fixture');
    const initial = await (await context.request.get(base + '/api/library')).json();
    assert.ok(initial.items.length && initial.items.every(item => ['sample-paper','sample-web'].includes(item.id)), 'Refusing to mutate a non-fixture library');
    await context.request.post(base + '/api/access',{headers:{Origin:base,'X-Token':initial.token},data:{enabled:false,current_code:'pwa-test-code'}});
    await page.goto(base + '/');
    await page.waitForFunction(() => PR.pwa?.shellReady && PR.lib?.loadStatus === 'ready', {timeout:45000});
    const install = await page.evaluate(async () => ({manifest: await (await fetch('/manifest.webmanifest')).json(), registration:!!(await navigator.serviceWorker.getRegistration('/'))}));
    assert.equal(install.manifest.display,'fullscreen'); assert.ok(install.registration);
    const cdp = await context.newCDPSession(page);
    const eligibility = await cdp.send('Page.getInstallabilityErrors');
    assert.deepEqual(eligibility.installabilityErrors,[]);
    await cdp.detach();
    await page.goto(base + '/read/sample-paper');
    await page.waitForFunction(() => PR.state?.paper?.meta && document.querySelector('#paper').innerText.includes('Observation'));
    await waitAsync(async () => !PR.pwa.downloading && (await PR.pwa.read('sample-paper'))?.complete === true);
    await page.evaluate(() => PR.pwa.download('sample-paper'));
    await page.evaluate(async () => { PR.commit({op:'paper_note',body:'Online PWA note'}); await PR.flush(); });
    assert.equal(await page.evaluate(() => PR.store.pending),0);
    await context.setOffline(true);
    await page.reload();
    await page.waitForFunction(() => PR.pwa?.offline && PR.state?.reader?.paper_note?.body === 'Online PWA note');
    assert.equal(await page.evaluate(() => PR.feature('pages')),false);
    await page.evaluate(() => PR.commit({op:'paper_note',body:'Offline PWA note'}));
    await page.reload();
    await page.waitForFunction(() => PR.state?.reader?.paper_note?.body === 'Offline PWA note');
    assert.ok(await page.evaluate(() => PR.store.pending));
    assert.equal(await page.evaluate(async () => { try { await PR.pwa.remove('sample-paper'); return false; } catch (_) { return true; } }),true);
    await context.setOffline(false);
    await page.evaluate(async () => { await PR.poll(); await PR.flush(); });
    await page.waitForFunction(() => PR.store.pending === 0);
    const saved = await page.evaluate(async () => (await (await fetch('/api/p/sample-paper/part/reader')).json()).data);
    assert.equal(saved.paper_note.body,'Offline PWA note');
    await context.setOffline(true); await page.reload();
    await page.waitForFunction(() => PR.state?.reader?.paper_note?.body === 'Offline PWA note');
    await page.evaluate(() => EasyReadOfflineDB.del('lists',PR.pwa.scope));
    await page.goto(base + '/');
    await page.waitForFunction(() => PR.lib?.offline && PR.lib.items.length === 1);
    assert.equal(await page.evaluate(async () => {
      const names = (await caches.keys()).filter(n => n.startsWith('easyread-media-'));
      for (const name of names) for (const req of await (await caches.open(name)).keys()) if (/\.pdf|\/pages\//.test(req.url)) return true;
      return false;
    }), false);
    await page.getByRole('button',{name:'安装与离线阅读',exact:true}).click();
    await page.getByRole('dialog',{name:'安装与离线阅读',exact:true}).waitFor();
    await page.screenshot({path:process.env.PWA_SCREENSHOT || '/tmp/easyread-pwa-desktop.png'});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'/tmp/easyread-pwa-mobile.png'});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2),false);
    await context.setOffline(false);
    await page.goto(base + '/read/sample-web');
    await waitAsync(async () => PR.state?.paper?.meta?.source_type === 'web' && !PR.pwa.downloading && (await PR.pwa.read('sample-web'))?.complete === true);
    assert.ok(await page.evaluate(async () => !!(await (await caches.open('easyread-media-' + PR.pwa.scope)).match('/p/sample-web/source.html'))));
    await context.setOffline(true); await page.reload();
    await page.waitForFunction(() => PR.state?.web_snapshot?.includes('Offline web sample'));
    assert.ok(await page.evaluate(async () => (await (await fetch('/p/sample-web/figures/sample.svg')).text()).includes('teal')));
    // A missing original HTML snapshot must still leave readable extracted text.
    await page.evaluate(async () => (await caches.open('easyread-media-' + PR.pwa.scope)).delete('/p/sample-web/source.html'));
    await page.reload();
    await page.waitForFunction(() => PR.pwa.offline && PR.state?.paper?.meta?.source_type === 'web' && document.querySelector('[data-web-layout="native"]')?.disabled);
    assert.ok(await page.locator('#paper').innerText().then(text => text.includes('saved web article')));
    // A different last-active library cannot supply this reader's image bytes.
    assert.equal(await page.evaluate(async () => {
      const other = 'f'.repeat(24), cache = await caches.open('easyread-media-' + other);
      await cache.put('/p/sample-web/figures/sample.svg',new Response('WRONG LIBRARY'));
      await EasyReadOfflineDB.put('meta','active',other);
      return (await (await fetch('/p/sample-web/figures/sample.svg')).text()).includes('teal');
    }),true);
    // Immutable queue keys prevent one tab's acknowledgement removing another edit.
    assert.ok(await page.evaluate(() => {
      const a = {op:'progress', at:'2026-01-01',block:'a'}, b = {op:'progress',at:'2026-01-02',block:'b'};
      PR.pwa.persistOutbox(PR.pid,[a]); PR.pwa.persistOutbox(PR.pid,[b]); PR.pwa.ack(PR.pid,[a]);
      const kept = PR.pwa.loadOutbox(PR.pid).some(op => op.offline_id === b.offline_id);
      PR.pwa.ack(PR.pid,[b]); return kept;
    }));
    await context.setOffline(false);
    // Fresh deep links can bind to the actual server, despite a remembered old scope.
    await page.reload();
    await page.waitForFunction(() => !!PR.state?.paper?.meta?.source_type && !PR.pwa.blocked);
    await waitAsync(async () => !PR.pwa.downloading && !!(await PR.pwa.read('sample-web')));
    assert.notEqual(await page.evaluate(() => PR.pwa.scope),'f'.repeat(24));
    await page.evaluate(async () => {
      await PR.poll(); PR.ls.set('easyread-offline-budget',0);
      PR.commit({op:'paper_note',body:'Quota-safe note'}); await PR.flush();
    });
    assert.ok(await page.evaluate(() => PR.store.pending));
    await page.evaluate(async () => { PR.ls.set('easyread-offline-budget',128); await PR.flush(); });
    assert.equal(await page.evaluate(() => PR.store.pending),0);
    // A real IndexedDB write failure must retain operations after server acknowledgement.
    assert.ok(await page.evaluate(async () => {
      const put = EasyReadOfflineDB.put;
      EasyReadOfflineDB.put = (store,...args) => store === 'papers' ? Promise.reject(new Error('Injected IDB failure')) : put(store,...args);
      try { PR.commit({op:'paper_note',body:'IDB-safe note'}); await PR.flush(); return PR.store.pending > 0; }
      finally { EasyReadOfflineDB.put = put; }
    }));
    await page.evaluate(() => PR.flush());
    assert.equal(await page.evaluate(() => PR.store.pending),0);
    // Even with unavailable metadata storage, token refresh cannot rebind live notes.
    assert.ok(await page.evaluate(async () => {
      const scope = PR.pwa.scope, put = EasyReadOfflineDB.put, network = window.fetch;
      try {
        EasyReadOfflineDB.put = (store,...args) => store === 'meta' ? Promise.reject(new Error('Injected metadata failure')) : put(store,...args);
        const data = await PR.pwa.json('/api/p/sample-web/state');
        if (PR.pwa.scope !== scope) return false;
        window.fetch = async () => new Response(JSON.stringify({...data,library_id:'f'.repeat(24),token:'wrong-library-token'}),{headers:{'Content-Type':'application/json'}});
        try { await PR.pwa.json('/api/p/sample-web/state'); return false; }
        catch (_) { return PR.pwa.blocked && PR.pwa.scope === scope; }
      } finally { EasyReadOfflineDB.put=put; window.fetch=network; }
    }));
    await page.reload();
    await waitAsync(async () => !PR.pwa.downloading && (await PR.pwa.read('sample-web'))?.complete);
    // Removing a copy also removes media orphaned by an interrupted metadata write.
    assert.ok(await page.evaluate(async () => {
      const cache = await caches.open('easyread-media-' + PR.pwa.scope), id='orphan-test', url='/p/orphan-test/figures/orphan.svg';
      await EasyReadOfflineDB.put('papers',PR.pwa.scope+':'+id,{id,scope:PR.pwa.scope,title:'Orphan fixture',bytes:0,dataBytes:0,data:{paper:{meta:{},blocks:[]},reader:{}}});
      await cache.put(url,new Response('orphan'));
      await PR.pwa.remove(id);
      return !await cache.match(url);
    }));
    // Simulate server-side session revocation, not the UI's intentional local cleanup.
    const auth = await (await context.request.get(base + '/api/library')).json();
    assert.equal((await context.request.post(base + '/api/access',{headers:{Origin:base,'X-Token':auth.token},data:{enabled:true,code:'pwa-test-code'}})).status(),200);
    assert.equal((await context.request.post(base + '/api/access/logout',{headers:{Origin:base,'X-Token':auth.token},data:{}})).status(),200);
    // Background UI requests may already navigate to login after revocation.
    await page.goto(base + '/login');
    await page.evaluate(scope => new Promise(resolve => {
      const channel = new MessageChannel(); channel.port1.onmessage = () => { channel.port1.close(); resolve(); };
      navigator.serviceWorker.controller.postMessage({type:'scope',scope},[channel.port2]);
    }),auth.library_id);
    const worker = context.serviceWorkers()[0];
    await worker.evaluate(async scope => {
      await EasyReadOfflineDB.put('meta','locked:'+scope,false);
      deniedScopes.delete(scope);
      self.originalOfflinePut = EasyReadOfflineDB.put;
      EasyReadOfflineDB.put = (store,key,value) => key.startsWith('locked:') ? Promise.reject(new Error('Injected auth-lock write failure')) : self.originalOfflinePut(store,key,value);
    },auth.library_id);
    try {
      assert.equal(await page.evaluate(async () => (await (await fetch('/p/sample-web/figures/sample.svg',{cache:'no-store'})).text()).includes('fill="teal"')),false);
    } finally { await worker.evaluate(() => { EasyReadOfflineDB.put=self.originalOfflinePut; delete self.originalOfflinePut; }); }
    await page.goto(base + '/read/sample-web');
    assert.ok(page.url().includes('/login'));
    await context.setOffline(true); await page.goto(base + '/read/sample-web');
    await page.waitForFunction(() => PR.pwa?.offline && document.querySelector('#paper').textContent.includes('读不到论文'));
    assert.equal(await page.evaluate(async () => (await PR.pwa.entries()).length),0);
    assert.equal(await page.evaluate(() => PR.state.reader?.paper_note?.body),undefined);
    // Restore the disposable server for repeatable runs.
    await context.setOffline(false); await page.goto(base + '/login');
    await page.evaluate(async () => {
      await fetch('/api/access/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:'pwa-test-code'})});
      const data = await (await fetch('/api/library')).json();
      await fetch('/api/access',{method:'POST',headers:{'Content-Type':'application/json','X-Token':data.token},body:JSON.stringify({enabled:false,current_code:'pwa-test-code'})});
    });
    console.log(JSON.stringify({passed:true,checks:['manifest','service worker','offline cold reload','durable notes','sync and offline snapshot','pending eviction protection','offline library','no PDFs cached','mobile dialog','webpage snapshot and text fallback','offline figure','cross-tab queue acknowledgement','quota-safe sync','cross-library media isolation','fresh deep-link scope','IDB failure durability and isolation','orphan cleanup','auth-lock write failure cannot serve media','denied navigation locks private cache'],errors},null,2));
    assert.deepEqual(errors,[]);
  } catch (error) {
    console.error('PWA state at failure', await page.evaluate(async () => ({url:location.href, offline:window.PR?.pwa?.offline, scope:window.PR?.pwa?.scope, pending:window.PR?.store?.pending, ops:window.PR?.pwa?.scope ? window.PR.pwa.loadOutbox(window.PR.pid) : [], lock:await window.EasyReadOfflineDB?.get('meta','locked:'+window.PR?.pwa?.scope), all:await window.EasyReadOfflineDB?.all('papers'), active:await window.EasyReadOfflineDB?.get('meta','active'), error:window.PR?.pwa?.error, text:document.querySelector('#paper')?.innerText?.slice(0,200), snapshot:window.PR?.state?.web_snapshot?.slice(0,100), record:await window.PR?.pwa?.read(window.PR?.pid)})).catch(() => null));
    throw error;
  } finally { await context.close(); fs.rmSync(profile,{recursive:true,force:true}); }
})().catch(error => {console.error(error);process.exitCode=1;});
