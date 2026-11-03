/* KCU Service Worker – zeigt Push-Benachrichtigungen an */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

// Seitenaufrufe immer frisch aus dem Netz – nur ohne Netz kommt ein kurzer Hinweis.
// (Der Handler macht die App außerdem auf allen Android-Versionen installierbar.)
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>KCU · offline</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#111113;color:#f3f3f3;font-family:system-ui;text-align:center">' +
  '<div><div style="font-size:3rem">⚽</div><h1 style="font-size:1.3rem">Gerade keine Verbindung</h1>' +
  '<p style="color:#9b9ba3">Sobald du wieder online bist, geht’s weiter.</p><button onclick="location.reload()" style="background:#e44235;color:#fff;border:0;border-radius:10px;padding:10px 18px;font-size:1rem">Nochmal versuchen</button></div>';
self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});

self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: 'KCU', body: event.data && event.data.text() }; }
  const title = data.title || 'KCU';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/static/img/icon-192.png',
    badge: '/static/img/favicon.png',
    tag: data.tag || undefined,
    renotify: !!data.tag,
    data: { url: data.url || '/' }
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) {
      if (c.url.startsWith(self.location.origin) && 'focus' in c) { c.navigate(url); return c.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
