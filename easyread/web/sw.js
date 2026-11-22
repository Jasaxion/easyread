/* PWA shell only. API responses and writes always go to the server. */
/*PWA_CONFIG*/
importScripts("/web/js/common/pwa-db.js");
const SHELL = "easyread-shell-" + BUILD;
const LIBRARY = "/pwa/shell/library", READER = "/pwa/shell/reader";
const allowed = new Set(ASSETS), deniedScopes = new Set();
self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    // Bounded concurrency avoids flooding a slow connection during installation.
    const urls = [...ASSETS, LIBRARY, READER];
    async function fill() {
      while (urls.length) {
        const url = urls.shift(), response = await fetch(url, {cache: "reload"});
        if (!response.ok || response.redirected) throw new Error("Incomplete application shell: " + url);
        await cache.put(url, response);
      }
    }
    await Promise.all(Array.from({length: 4}, fill));
  })());
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) if (name.startsWith("easyread-shell-") && name !== SHELL) await caches.delete(name);
    await self.clients.claim();
  })());
});
self.addEventListener("message", event => {
  if (event.data?.type !== "scope" || !/^[a-f0-9]{24}$/.test(event.data.scope || "") || !event.source?.id) return;
  event.waitUntil((async () => {
    await self.EasyReadOfflineDB.put("meta", "client:" + event.source.id, event.data.scope);
    if (event.data.authorized) deniedScopes.delete(event.data.scope);
    event.ports[0]?.postMessage({ok:true, blocked:deniedScopes.has(event.data.scope)});
  })());
});
async function deny(response, clientId) {
  // Navigation Requests use manual redirects; auth redirects can be opaque
  // (status 0), not response.redirected. These shell/media routes only redirect to login.
  if (response.type === "opaqueredirect" || response.status === 401 || response.status === 403 || (response.redirected && new URL(response.url).pathname === "/login")) {
    const db = self.EasyReadOfflineDB;
    const scope = (clientId && await db.get("meta", "client:" + clientId)) || await db.get("meta", "active");
    if (scope) { deniedScopes.add(scope); await db.put("meta", "locked:" + scope, Date.now()); }
  }
  return response;
}
async function network(request) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 6000);
  try { return await fetch(request, {signal: controller.signal}); }
  finally { clearTimeout(timer); }
}
self.addEventListener("fetch", event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (allowed.has(url.pathname)) {
    event.respondWith((async () => (await (await caches.open(SHELL)).match(url.pathname)) || fetch(request))());
    return;
  }
  if (/^\/p\/[A-Za-z0-9_-]+\/(figures\/[^/]+|chat-media\/[a-f0-9]{32}\.png)$/.test(url.pathname)) {
    event.respondWith((async () => {
      let response;
      // Let the browser finish visible media normally. The old six-second
      // deadline could cancel an online image before it had any cached copy.
      try { response = await fetch(request); }
      catch (_) {
        const db = self.EasyReadOfflineDB, scope = await db.get("meta", "client:" + event.clientId);
        if (!scope || deniedScopes.has(scope) || await db.get("meta", "locked:" + scope)) return Response.error();
        return (await (await caches.open("easyread-media-" + scope)).match(url.pathname)) || Response.error();
      }
      // A received denial is never a transport failure, even if persisting its lock fails.
      return deny(response, event.clientId).catch(() => response);
    })());
    return;
  }
  const shell = url.pathname === "/" || url.pathname === "/index.html" ? LIBRARY
    : /^\/read\/[A-Za-z0-9_-]+$/.test(url.pathname) ? READER : null;
  if (request.mode === "navigate" && shell) {
    event.respondWith((async () => {
      const cache = await caches.open(SHELL);
      let response;
      try { response = await network(request); }
      catch (_) { return (await cache.match(shell)) || Response.error(); }
      if (!response.ok || response.redirected) return deny(response, event.clientId).catch(() => response);
      try {
        if (response.headers.get("X-EasyRead-Web-Build") === BUILD) {
          await cache.put(shell, response.clone()); // Keep the last server-selected UI language.
          return response;
        }
        // An old controller must not combine new HTML with its old script bundle.
        return (await cache.match(shell)) || Response.error();
      } catch (_) { return (await cache.match(shell)) || Response.error(); }
    })());
  }
});
