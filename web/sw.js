// Minimaler Service Worker: statische Dateien offline verfügbar (App-Shell); Live-Daten nie gecacht.
const CACHE = "exse-harp-mobile-1.0.0-r10";   // Version + Revision; bei jeder Web-Änderung erhöhen
const ASSETS = ["/", "/static/style.css", "/static/app.js", "/static/vendor/uPlot.iife.min.js",
  "/static/vendor/uPlot.min.css", "/static/icon.svg", "/manifest.webmanifest"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS))));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) =>
  Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.pathname.startsWith("/api") || u.pathname.startsWith("/ws")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
