/**
 * Veliora — surveillance crawl en arrière-plan (Service Worker + notifications).
 */
(function (global) {
  const WATCH_KEY = "veliora_crawl_watch";

  function getApiBase() {
    const { protocol, hostname, port } = window.location;
    if (protocol === "file:") return `http://127.0.0.1:8000/api`;
    const devPorts = new Set(["5500", "5501", "5173", "3000", "8080", "4173"]);
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    if (isLocal && devPorts.has(port)) return `http://${hostname}:8000/api`;
    if (isLocal && port && port !== "8000") return `http://${hostname}:8000/api`;
    return "/api";
  }

  function readToken() {
    try {
      return localStorage.getItem("propscout_token") || "";
    } catch {
      return "";
    }
  }

  function persistWatch(payload) {
    try {
      if (payload) localStorage.setItem(WATCH_KEY, JSON.stringify(payload));
      else localStorage.removeItem(WATCH_KEY);
    } catch {
      /* ignore */
    }
  }

  function loadWatch() {
    try {
      const raw = localStorage.getItem(WATCH_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  async function serviceWorkerRegistration() {
    if (!("serviceWorker" in navigator)) return null;
    try {
      return await navigator.serviceWorker.ready;
    } catch {
      return null;
    }
  }

  async function postToWorker(message) {
    const reg = await serviceWorkerRegistration();
    if (!reg) return false;
    const target = reg.active || reg.waiting || reg.installing;
    if (!target) return false;
    target.postMessage(message);
    return true;
  }

  const CrawlWatch = {
    async requestPermission() {
      if (!("Notification" in window)) return "unsupported";
      if (Notification.permission === "granted") return "granted";
      if (Notification.permission === "denied") return "denied";
      try {
        return await Notification.requestPermission();
      } catch {
        return "denied";
      }
    },

    async start(jobId, label) {
      if (!jobId) return false;
      const token = readToken();
      const apiBase = getApiBase();
      const payload = {
        type: "CRAWL_WATCH_START",
        jobId,
        label: label || "Veille",
        token,
        apiBase,
        startedAt: Date.now(),
      };
      persistWatch({
        jobId,
        label: payload.label,
        apiBase,
        startedAt: payload.startedAt,
      });
      const ok = await postToWorker(payload);
      if (!ok) persistWatch(null);
      return ok;
    },

    async stop() {
      persistWatch(null);
      await postToWorker({ type: "CRAWL_WATCH_STOP" });
    },

    async syncFromStorage() {
      const w = loadWatch();
      if (!w?.jobId) return false;
      return this.start(w.jobId, w.label);
    },

    showLocalNotification(job, label) {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      if (!job) return;
      const ok = job.status === "completed";
      const title = ok ? `Veille terminée — ${label}` : `Veille interrompue — ${label}`;
      const body =
        job.message ||
        (ok
          ? `${job.leads_saved || 0} nouveau(x), ${job.leads_found || 0} analysée(s)`
          : "Ouvrez Veliora pour les détails");
      try {
        const n = new Notification(title, {
          body,
          icon: "/vitrine/favicon.svg",
          tag: "veliora-crawl-done",
          renotify: true,
        });
        n.onclick = () => {
          window.focus();
          n.close();
        };
      } catch {
        /* ignore */
      }
    },

    setupClientListener(onEvent) {
      if (!("serviceWorker" in navigator)) return;
      navigator.serviceWorker.addEventListener("message", (event) => {
        const data = event.data || {};
        if (data.type === "CRAWL_DONE") {
          persistWatch(null);
          if (typeof onEvent === "function") onEvent(data.job, data.label, "done");
          return;
        }
        if (data.type === "CRAWL_PROGRESS" && typeof onEvent === "function") {
          onEvent(data.job, data.label, "progress");
        }
      });
    },
  };

  global.CrawlWatch = CrawlWatch;
})(window);
