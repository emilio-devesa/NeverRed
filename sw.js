/* NeverRed service worker: la carcasa funciona sin conexión; la API siempre va a red.
 * HTML/JS/CSS en red-primero para recibir actualizaciones; el resto, caché-primero. */
const CACHE = 'neverred-v10';
const MAX_ENTRIES = 40;
const ASSETS = ['./', 'index.html', 'styles.css', 'app.js', 'lib/contabilidad.js', 'icon.svg', 'manifest.webmanifest'];
const SHELL = new Set(['/', '/index.html', '/styles.css', '/app.js', '/lib/contabilidad.js', '/icon.svg', '/manifest.webmanifest']);
const NET_FIRST = /(\.html|\.js|\.css|\.webmanifest|\/)$/;

function isShell(key) {
  try { return SHELL.has(new URL(key.url).pathname); } catch { return false; }
}

async function trim(cache) {
  const keys = await cache.keys();
  const extra = keys.length - MAX_ENTRIES;
  if (extra <= 0) return;
  // Primero fuera lo prescindible; la carcasa (offline) se conserva.
  const victims = keys.filter(k => !isShell(k)).slice(0, extra);
  if (victims.length < extra) {
    victims.push(...keys.filter(isShell).slice(0, extra - victims.length));
  }
  await Promise.all(victims.map(k => cache.delete(k)));
}

function stash(cache, req, res) {
  // Jamás cachear errores (404s pegajosos) ni respuestas que piden no-store.
  if (!res || !res.ok || req.method !== 'GET') return;
  if ((res.headers.get('Cache-Control') || '').includes('no-store')) return;
  const copy = res.clone();
  caches.open(cache)
    .then(c => c.put(req, copy).then(() => trim(c)))
    .catch(() => {});
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
      }).catch(() => caches.match(e.request, { ignoreSearch: true }).then(hit => hit || caches.match('index.html')))
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
