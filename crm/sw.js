/* Veliora — service worker (CRM uniquement, pas la vitrine) */
const CACHE = "veliora-crm-v3";
const SHELL = [
  "/crm",
  "/crm/assets/css/styles.css",
  "/crm/assets/css/mobile.css",
];

function isVitrinePath(pathname) {
  return (
    pathname === "/" ||
    pathname === "/accueil" ||
    pathname.startsWith("/vitrine") ||
    pathname === "/landing"
  );
}

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)),
      ),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return;
  if (isVitrinePath(url.pathname)) return;
  if (!url.pathname.startsWith("/crm")) return;

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() =>
        caches.match(e.request).then((r) => r || caches.match("/crm")),
      ),
  );
});
