// App shell cache-first, data network-first (falls back to the last saved data offline).
const SHELL = 'yz12-shell-v1';
const DATA = 'yz12-data';
const ASSETS = ['./', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css', 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'];
self.addEventListener('install', e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(ASSETS)).catch(() => {})); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.pathname.includes('/data/')) {
    const key = url.origin + url.pathname;
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(DATA).then(c => c.put(key, copy)); return r; })
      .catch(() => caches.match(key)));
    return;
  }
  if (url.hostname.includes('basemaps.cartocdn.com')) return; // map tiles: browser cache only
  e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(r => {
    if (r.ok && (url.origin === location.origin)) { const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); }
    return r;
  })));
});
