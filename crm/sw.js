/* Veliora CRM — PWA shell + surveillance crawl en arrière-plan */
const CACHE = "veliora-crm-v5";
const SHELL = [
  "/crm",
  "/crm/assets/css/styles.css?v=39",
  "/crm/assets/css/mobile.css?v=33",
  "/crm/assets/js/crawl-watch.js",
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
  const title = ok ? `Veliora — crawl terminé` : `Veliora — crawl en échec`;
  let body = job.message || label || "Crawl";
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
    label: data.label || "Crawl",
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
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
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
