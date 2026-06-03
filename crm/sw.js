/* Veliora CRM — PWA shell + données hors connexion + surveillance crawl */
const CACHE = "veliora-crm-v7";
const API_CACHE = "veliora-crm-api-v2";
const KEEP_CACHES = [CACHE, API_CACHE];
const SHELL = [
  "/crm",
  "/crm/manifest.webmanifest",
  "/vitrine/favicon.svg",
];

const WATCH_INTERVAL_MS = 2500;

let watchState = null;
let watchTimer = null;

function isVitrinePath(pathname) {
  return (
    pathname === "/" ||
    pathname === "/accueil" ||
    pathname.startsWith("/vitrine") ||
    pathname === "/landing"
  );
}

function clearWatchTimer() {
  if (watchTimer) {
    clearInterval(watchTimer);
    watchTimer = null;
  }
}

async function notifyAllClients(payload) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clients) {
    client.postMessage(payload);
  }
}

function buildNotification(job, label) {
  const ok = job.status === "completed";
  const title = ok ? `Veliora — veille terminée` : `Veliora — veille interrompue`;
  let body = job.message || label || "Veille portails";
  if (ok && (job.leads_saved || job.leads_updated)) {
    const parts = [];
    if (job.leads_saved) parts.push(`${job.leads_saved} nouveau(x)`);
    if (job.leads_updated) parts.push(`${job.leads_updated} mis à jour`);
    body = `${label} — ${parts.join(", ")}`;
  }
  return { title, body };
}

async function showDoneNotification(job, label) {
  const { title, body } = buildNotification(job, label);
  try {
    await self.registration.showNotification(title, {
      body,
      icon: "/vitrine/favicon.svg",
      badge: "/vitrine/favicon.svg",
      tag: "veliora-crawl",
      renotify: true,
      data: { jobId: job.id, url: "/crm" },
    });
  } catch (err) {
    console.warn("Notification crawl:", err);
  }
}

async function pollCrawlJobOnce() {
  if (!watchState?.jobId || !watchState?.apiBase) return;

  const headers = { Accept: "application/json" };
  if (watchState.token) headers.Authorization = `Bearer ${watchState.token}`;

  const url = `${watchState.apiBase}/crawler/jobs/${encodeURIComponent(watchState.jobId)}?lite=1`;
  let res;
  try {
    res = await fetch(url, { headers, credentials: "same-origin" });
  } catch {
    return;
  }
  if (res.status === 401 || res.status === 403) {
    clearWatchTimer();
    watchState = null;
    return;
  }
  if (!res.ok) return;

  let job;
  try {
    job = await res.json();
  } catch {
    return;
  }
  if (!job?.status) return;

  if (job.status === "running" || job.status === "pending") {
    await notifyAllClients({
      type: "CRAWL_PROGRESS",
      job,
      label: watchState.label,
    });
    return;
  }

  if (job.status === "completed" || job.status === "failed") {
    const label = watchState.label;
    clearWatchTimer();
    watchState = null;
    await showDoneNotification(job, label);
    await notifyAllClients({ type: "CRAWL_DONE", job, label });
  }
}

function startCrawlWatch(data) {
  watchState = {
    jobId: data.jobId,
    label: data.label || "Veille",
    token: data.token || "",
    apiBase: data.apiBase || "/api",
  };
  clearWatchTimer();
  pollCrawlJobOnce();
  watchTimer = setInterval(pollCrawlJobOnce, WATCH_INTERVAL_MS);
}

function stopCrawlWatch() {
  clearWatchTimer();
  watchState = null;
}

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {})));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => !KEEP_CACHES.includes(k)).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (e) => {
  const data = e.data || {};
  if (data.type === "CRAWL_WATCH_START") {
    startCrawlWatch(data);
    return;
  }
  if (data.type === "CRAWL_WATCH_STOP") {
    stopCrawlWatch();
    return;
  }
  if (data.type === "CRAWL_WATCH_PING") {
    e.source?.postMessage({ type: "CRAWL_WATCH_ACK", watching: Boolean(watchState) });
  }
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = e.notification.data?.url || "/crm";
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes("/crm") && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    }),
  );
});

/* Endpoints API non rejouables hors connexion : polling crawl, flux live,
   images, exports et fichiers — on ne les met pas en cache (churn / binaire). */
function isCacheableApi(pathname) {
  if (pathname.includes("/crawler/")) return false;
  if (
    pathname.endsWith("/image") ||
    pathname.includes("/export") ||
    pathname.includes("/live-frame") ||
    pathname.includes("/dossier-files")
  ) {
    return false;
  }
  return true;
}

/* Données CRM en lecture : réseau d'abord, repli sur la dernière copie connue.
   Permet de consulter leads / clients / mandats / sources hors connexion. */
async function apiNetworkFirst(request, pathname) {
  try {
    const res = await fetch(request);
    if (res.ok && isCacheableApi(pathname)) {
      const contentType = res.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const clone = res.clone();
        caches.open(API_CACHE).then((c) => c.put(request, clone)).catch(() => {});
      }
    }
    return res;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({
        ok: false,
        offline: true,
        error: "Hors connexion — données indisponibles pour le moment.",
      }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "X-Veliora-Offline": "1",
        },
      },
    );
  }
}

/* Coquille de l'app : réseau d'abord (pour récupérer les mises à jour),
   repli sur le cache puis sur /crm si tout échoue. */
async function shellNetworkFirst(request) {
  try {
    const res = await fetch(request);
    if (res.ok) {
      const clone = res.clone();
      caches.open(CACHE).then((c) => c.put(request, clone)).catch(() => {});
    }
    return res;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    const shell = await caches.match("/crm");
    if (shell) return shell;
    return new Response("Hors connexion", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  if (url.pathname.startsWith("/api/")) {
    e.respondWith(apiNetworkFirst(e.request, url.pathname));
    return;
  }
  if (isVitrinePath(url.pathname)) return;
  if (!url.pathname.startsWith("/crm")) return;

  e.respondWith(shellNetworkFirst(e.request));
});
