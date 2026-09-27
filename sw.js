/* NeverRed service worker: la carcasa funciona sin conexión; la API siempre va a red.
 * HTML/JS en red-primero para recibir actualizaciones; el resto, caché-primero. */
const CACHE = 'neverred-v3';
const ASSETS = ['./', 'index.html', 'styles.css', 'app.js', 'lib/contabilidad.js', 'icon.svg', 'manifest.webmanifest'];
const NET_FIRST = /(\.html|\.js|\.webmanifest|\/)$/;
function stash(cache, req, res) {
  if (!res || !res.ok) return; // jamás cachear errores (404s pegajosos)
  const copy = res.clone();
  caches.open(cache).then(c => c.put(req, copy));
}

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.pathname.startsWith('/api/')) return;
  if (NET_FIRST.test(u.pathname)) {
    e.respondWith(
      fetch(e.request).then(r => {
        stash(CACHE, e.request, r);
        return r;
      }).catch(() => caches.match(e.request).then(hit => hit || caches.match('index.html')))
    );
    return;
  }
  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {
      stash(CACHE, e.request, r);
      return r;
    }).catch(() => caches.match('index.html')))
  );
});
