const CACHE = 'cambrify-shell-v2';
const OFFLINE = '/static/offline.html';
const SHELL = [
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/builder.js',
  '/static/js/offline-queue.js',
  '/static/img/mark.svg',
  OFFLINE
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
    return;
  }
  if (event.request.mode === 'navigate') {
    // Protected HTML is never cached; offline drafts arrive in the planning phase.
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));
  }
});
