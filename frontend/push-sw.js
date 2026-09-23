self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {}
  event.waitUntil(self.registration.showNotification(payload.title || 'Movienotes', {
    body: payload.body || 'Yeni bir bildirimin var.',
    // Bildirim yüzeyi hem açık hem koyu olabiliyor; şeffaf zeminli marka ikisinde
    // de doğru duruyor. `badge` alfa kanalından tek renkli maskeye çevrildiği
    // için ayrı ve küçük bir boy veriliyor.
    icon: '/static/movienotes-notify-192.png',
    badge: '/static/movienotes-badge-96.png',
    // Adres, uygulamanın kendi yönlendirmesiyle aynı olmalı; `#notifications`
    // tanınmadığı için bildirime dokunmak akışa düşürüyordu.
    data: { url: payload.url || '/#/bildirimler' },
  }));
});

// Movienotes is an installable web app. The worker intentionally does not
// cache API responses; it only establishes the PWA scope and handles push.
self.addEventListener('fetch', () => {});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const existing = windows.find(client => client.url.startsWith(self.location.origin));
    return existing ? existing.focus() : clients.openWindow(event.notification.data?.url || '/');
  }));
});
