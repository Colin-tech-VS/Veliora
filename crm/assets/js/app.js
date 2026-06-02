/* Veliora — Application Logic */

const PROPSCOUT_PORT = 8000;

/** URL API : même origine si Flask (port 8000), sinon http://127.0.0.1:8000/api */
function getApiBase() {
  const { protocol, hostname, port } = window.location;
  if (protocol === "file:") {
    return `http://127.0.0.1:${PROPSCOUT_PORT}/api`;
  }
  const devPorts = new Set(["5500", "5501", "5173", "3000", "8080", "4173"]);
  const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
  if (isLocal && devPorts.has(port)) {
    return `http://${hostname}:${PROPSCOUT_PORT}/api`;
  }
  if (isLocal && port && port !== String(PROPSCOUT_PORT)) {
    return `http://${hostname}:${PROPSCOUT_PORT}/api`;
  }
  return "/api";
}

const API = getApiBase();
const AUTH_TOKEN_KEY = "propscout_token";
const AUTH_USER_KEY = "propscout_user";
const DVF_APP_URL = "https://app.dvf.etalab.gouv.fr/";

const state = {
  user: null,
  settings: null,
  appStats: null,
  currentView: "dashboard",
  leadsFilter: "all",
  leadsView: "table",
  searchQuery: "",
  selectedLead: null,
  drawerEditExpanded: false,
  crawlerRunning: false,
  loading: false,
  pendingCrawlUrl: null,
  sourceCityPreview: {},
  sourceCityPreviewCity: "",
};

const POLL_IDLE_MS = 18000;
const POLL_CRAWL_MS = 12000;
/** Pendant un crawl, le worker Flask est occupé — poll léger, timeouts longs. */
const CRAWL_JOB_POLL_MS = 3000;
const CRAWL_JOB_POLL_TIMEOUT_MS = 90000;
const CRAWL_LEADS_REFRESH_MS = 20000;
let backgroundPollTimer = null;

/** Crawl en arrière-plan — navigation libre pendant le job */
const crawlState = {
  active: false,
  minimized: false,
  compact: false,
  jobId: null,
  sourceId: null,
  label: "",
  pollTimer: null,
  pagePollPaused: false,
  pollOptions: {},
  startedAt: null,
  lastSavedCount: 0,
  lastFoundCount: 0,
  lastLeadCount: 0,
  leadsRefreshTimer: null,
  lastJob: null,
  drawerShowAllFields: false,
  estimatorLeadId: null,
};

/** Script d'appel — panneau repliable (fermer / arrière-plan / rouvrir) */
const scriptPanelState = {
  visible: false,
  minimized: false,
  leadId: null,
  copyText: "",
};

const viewTitles = {
  dashboard: { title: "Radar automatique", subtitle: "Mode 1 — opportunités, alertes, briefing du matin" },
  analyze: { title: "Analyse à la demande", subtitle: "Mode 2 — Score Mandat™ sur une URL" },
  playbook: { title: "Scripts d'appel", subtitle: "Opportunités du marché et discours à tenir" },
  leads: { title: "Opportunités", subtitle: "Classées par Score Mandat™ (vendeurs détectés)" },
  crawler: { title: "Sources", subtitle: "Alimenter le radar (Mode 1)" },
  pipeline: { title: "Pipeline", subtitle: "Glissez-déposez vos dossiers — du premier contact au mandat" },
  map: {
    title: "Carte",
    subtitle: "Vos annonces, votre agence et votre position (mobile & bureau)",
  },
  estimateur: {
    title: "Estimateur de prix",
    subtitle: "Fourchette indicative DVF (ventes réelles) + critères du bien",
  },
  mandates: { title: "Mandats", subtitle: "Mandats de vente et de location" },
  clients: {
    title: "Acheteurs / Locataires",
    subtitle: "Ajout manuel ou import CSV / Excel",
  },
};

function apiErrorMessage(status, path, body, res) {
  if (body?.error) return body.error;
  if (status === 405) {
    return (
      "Erreur 405 — le serveur n’accepte pas cette action. " +
      "Arrêtez l’ancien serveur (Ctrl+C) puis relancez : python app.py"
    );
  }
  const notJson = res && !(res.headers.get("content-type") || "").includes("application/json");
  if (status === 404) {
    if (notJson) {
      const onWrongPort =
        window.location.port &&
        window.location.port !== String(PROPSCOUT_PORT) &&
        window.location.protocol !== "file:";
      if (onWrongPort) {
        return (
          `Page ouverte sur le port ${window.location.port} — l’API est sur http://localhost:${PROPSCOUT_PORT}. ` +
          "Lancez python app.py (ou demarrer.bat) puis ouvrez ce lien."
        );
      }
      return (
        "API inaccessible (404). Lancez python app.py puis ouvrez http://localhost:8000 — " +
        "pas Live Server ni python -m http.server."
      );
    }
    if (body?.error?.includes("Route API introuvable")) {
      return (
        "Serveur obsolète sur le port 8000 — dans le terminal : Ctrl+C, puis python app.py (ou demarrer.bat)."
      );
    }
    return body.error || `Ressource introuvable (${path}).`;
  }
  if (status === 503 || body?.code === "database_busy") {
    return (
      body?.error ||
      "Serveur occupé (crawl ou forte charge) — réessayez dans quelques secondes."
    );
  }
  if (status === 500) {
    return body?.error || `Erreur serveur (${path}) — réessayez ou relancez python app.py`;
  }
  return `Erreur ${status}${path ? ` (${path})` : ""}`;
}

function getAuthHeaders() {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function redirectToLogin() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/crm/auth?next=${next}`;
}

async function parseApiResponse(res, path) {
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await res.json().catch(() => ({})) : {};
  if (res.status === 401 && !path.startsWith("/auth/")) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
    redirectToLogin();
    throw new Error("Session expirée — reconnectez-vous");
  }
  if (res.status === 402 && body.code === "subscription_required") {
    window.location.href = "/crm/auth?next=" + encodeURIComponent(
      window.location.pathname + window.location.search
    );
    throw new Error("SUBSCRIPTION_REDIRECT");
  }
  if (res.status === 402 && body.code === "portal_coming_soon") {
    throw new Error(body.error || "Portail protégé — crawl bientôt disponible");
  }
  if (!res.ok) {
    throw new Error(apiErrorMessage(res.status, path, body, res));
  }
  return body;
}

function isNetworkFetchError(err) {
  return (
    err instanceof TypeError ||
    err?.name === "AbortError" ||
    (err?.message && /failed to fetch|networkerror|load failed|aborted/i.test(err.message))
  );
}

const API_FETCH_TIMEOUT_MS = 20000;

/** "il y a 14 min" / "il y a 3h" pour un lead récent (retourne null si > 48h). */
function leadFreshness(isoStr) {
  if (!isoStr) return null;
  try {
    const dt = new Date(isoStr.endsWith("Z") ? isoStr : isoStr + "Z");
    const mins = Math.round((Date.now() - dt.getTime()) / 60000);
    if (mins < 1) return "à l'instant";
    if (mins < 60) return `il y a ${mins} min`;
    const h = Math.round(mins / 60);
    if (h < 24) return `il y a ${h}h`;
    if (h < 48) return "hier";
    return null;
  } catch {
    return null;
  }
}

async function fetchRoiStats() {
  try {
    const data = await api("/roi/stats");
    if (data?.ok) renderRoiBanner(data);
  } catch {
    /* ignore — non-bloquant */
  }
}

function renderRoiBanner(stats) {
  const banner = document.getElementById("roi-banner");
  if (!banner) return;
  const calls = stats.calls ?? 0;
  const rdvs = stats.rdvs ?? 0;
  const mandats = stats.mandats ?? 0;
  const roi = stats.roi_multiple ?? 0;

  document.getElementById("roi-calls").textContent = calls;
  document.getElementById("roi-rdvs").textContent = rdvs;
  document.getElementById("roi-mandats").textContent = mandats;

  const multipleEl = document.getElementById("roi-multiple");
  const verdictEl = document.getElementById("roi-verdict");
  if (mandats > 0) {
    multipleEl.textContent = roi + " ×";
    verdictEl.classList.add("roi-verdict-active");
  } else if (calls > 0 || rdvs > 0) {
    multipleEl.textContent = "—";
    verdictEl.classList.remove("roi-verdict-active");
  } else {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
}

function fetchWithTimeout(url, options = {}, timeoutMs = API_FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const { signal: _ignored, ...rest } = options;
  return fetch(url, { ...rest, signal: ctrl.signal }).finally(() => clearTimeout(timer));
}

async function api(path, options = {}) {
  const url = `${API}${path}`;
  const isPost = (options.method || "GET").toUpperCase() !== "GET";
  const maxAttempts = isPost ? 3 : 2;
  const timeoutMs = options.timeoutMs ?? API_FETCH_TIMEOUT_MS;
  const { timeoutMs: _drop, ...fetchOptions } = options;
  let lastError;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      const res = await fetchWithTimeout(
        url,
        {
          ...fetchOptions,
          headers: {
            "Content-Type": "application/json",
            ...getAuthHeaders(),
            ...(fetchOptions.headers || {}),
          },
        },
        timeoutMs,
      );
      return parseApiResponse(res, path);
    } catch (err) {
      lastError = err;
      if (isNetworkFetchError(err) && attempt < maxAttempts - 1) {
        await sleep(400 + attempt * 300);
        continue;
      }
      break;
    }
  }

  if (isNetworkFetchError(lastError)) {
    throw new Error(
      `Connexion perdue avec le serveur — gardez http://localhost:${PROPSCOUT_PORT} ouvert ` +
        `et vérifiez que python app.py tourne encore (pas d’erreur dans le terminal).`,
    );
  }
  throw lastError;
}

async function deleteLeadApi(leadId) {
  const id = encodeURIComponent(leadId);
  const headers = { "Content-Type": "application/json", ...getAuthHeaders() };
  let res = await fetch(`${API}/leads/${id}`, { method: "DELETE", headers });
  if (res.status === 405) {
    res = await fetch(`${API}/leads/${id}/delete`, { method: "POST", headers });
  }
  return parseApiResponse(res, `/leads/${id}`);
}

async function deleteAllLeadsApi() {
  return api("/leads/delete-all", {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  });
}

async function deleteLeadById(leadId, ownerName) {
  const name = ownerName || "ce prospect";
  if (!confirm(`Supprimer « ${name} » ?`)) return;
  try {
    const result = await deleteLeadApi(leadId);
    LEADS = result.leads;
    if (state.selectedLead?.id === leadId) closeDrawer();
    await refreshAppData();
    showToast(`${name} supprimé`, "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function deleteSourceApi(sourceId) {
  const id = encodeURIComponent(sourceId);
  const headers = { "Content-Type": "application/json", ...getAuthHeaders() };
  const path = `/sources/${id}`;

  let res = await fetch(`${API}${path}`, { method: "DELETE", headers });
  if (res.status === 405) {
    res = await fetch(`${API}/sources/${id}/delete`, { method: "POST", headers });
  }
  if (res.status === 404) {
    const probe = await res.clone().json().catch(() => ({}));
    if (!probe.error) {
      res = await fetch(`${API}/sources/remove`, {
        method: "POST",
        headers,
        body: JSON.stringify({ id: sourceId }),
      });
    }
  }
  return parseApiResponse(res, path);
}

const sourceUrlDirty = new Set();
const sourceUrlSaving = new Set();

function applySourcesFromApi(sources, updatedSource) {
  SOURCES = sources || SOURCES;
  if (updatedSource) {
    const idx = SOURCES.findIndex((s) => s.id === updatedSource.id);
    if (idx >= 0) SOURCES[idx] = updatedSource;
    else SOURCES.push(updatedSource);
  }
}

async function saveSourceUrl(sourceId, url) {
  const id = encodeURIComponent(sourceId);
  const body = JSON.stringify({ url });
  let result;

  try {
    result = await api(`/sources/${id}`, { method: "PATCH", body });
  } catch (err) {
    const msg = err.message || "";
    const retry =
      msg.includes("405") ||
      msg.includes("Route API introuvable") ||
      msg.includes("Ressource introuvable");
    if (!retry) throw err;
    result = await api(`/sources/${id}/url`, { method: "POST", body });
  }

  applySourcesFromApi(result.sources, result.source);
  sourceUrlDirty.delete(sourceId);
  refreshSourceCard(sourceId, { saved: true });
  updateCrawlerSummary();
  return result;
}

function updateCrawlerSummary() {
  const inDb = SOURCES.reduce((a, s) => a + (s.leads_count ?? s.found ?? 0), 0);
  const updatedToday = SOURCES.reduce((a, s) => a + (s.leads_updated_today ?? s.today ?? 0), 0);
  const active = SOURCES.filter((s) => s.enabled).length;
  const elToday = document.getElementById("crawler-found-today");
  const elActive = document.getElementById("crawler-active-sources");
  const elTotal = document.getElementById("crawler-total-leads");
  if (elToday) elToday.textContent = updatedToday;
  if (elActive) elActive.textContent = active;
  if (elTotal) elTotal.textContent = LEADS.length || inDb;
  updateSidebarCount();
}

function countLeadsForSource(source) {
  if (!source) return 0;
  const id = source.id;
  const name = (source.name || "").trim().toLowerCase();
  const domain = (source.domain || "").replace(/^www\./, "").toLowerCase();
  return LEADS.filter((l) => {
    if (l.source_id && l.source_id === id) return true;
    if (!l.source_id && name && (l.source || "").trim().toLowerCase() === name) return true;
    const url = (l.source_url || "").toLowerCase();
    if (domain && url.includes(domain)) return true;
    return false;
  }).length;
}

function getSourceDisplayStats(source, job = null) {
  const inDb = Math.max(source.leads_count ?? source.found ?? 0, countLeadsForSource(source));
  const updatedToday = source.leads_updated_today ?? source.today ?? 0;
  const createdToday = source.leads_created_today ?? 0;
  const isActiveSource =
    crawlState.active && job?.source_id && job.source_id === source.id && job.status === "running";
  return { inDb, updatedToday, createdToday, isActiveSource, job: isActiveSource ? job : null };
}

function getSourceSavedUrl(source) {
  return (source?.search_url || source?.base_url || "").trim();
}

function getSourceDisplayUrl(source) {
  const saved = getSourceSavedUrl(source);
  if (!source?.id || sourceUrlDirty.has(source.id)) {
    const input = document.getElementById(`source-url-${source.id}`);
    if (input?.value?.trim()) return input.value.trim();
    return saved;
  }
  const city = getCrawlCity();
  if (
    city &&
    state.sourceCityPreviewCity &&
    state.sourceCityPreviewCity.toLowerCase() === city.toLowerCase() &&
    state.sourceCityPreview[source.id]
  ) {
    return state.sourceCityPreview[source.id];
  }
  return saved;
}

let sourcePreviewUrlsTimer = null;

async function refreshSourceUrlsForCity() {
  const city = getCrawlCity();
  if (!city) {
    state.sourceCityPreview = {};
    state.sourceCityPreviewCity = "";
    updateAllSourceCardUrls();
    return;
  }
  try {
    const data = await api(`/sources/preview-urls?city=${encodeURIComponent(city)}`);
    state.sourceCityPreview = data.urls || {};
    state.sourceCityPreviewCity = city;
    updateAllSourceCardUrls();
  } catch {
    /* garde l’aperçu précédent */
  }
}

function scheduleSourceUrlsForCity() {
  clearTimeout(sourcePreviewUrlsTimer);
  sourcePreviewUrlsTimer = setTimeout(() => refreshSourceUrlsForCity(), 180);
}

function updateSourceCardUrlDom(sourceId, url) {
  const card = document.querySelector(
    `.source-card[data-source-id="${CSS.escape(sourceId)}"]`,
  );
  if (!card || sourceUrlDirty.has(sourceId)) return;
  const trimmed = (url || "").trim();
  card.dataset.searchUrl = trimmed;
  const input = card.querySelector(".source-url-input");
  if (input && !sourceUrlSaving.has(sourceId)) {
    input.value = trimmed;
  }
  const meta = card.querySelector(".source-url-meta");
  if (!meta) return;
  let openLink = meta.querySelector(".source-url-open");
  const city = getCrawlCity();
  const hint = meta.querySelector(".source-url-hint");
  if (trimmed) {
    if (!openLink) {
      openLink = document.createElement("a");
      openLink.className = "source-url-open";
      openLink.target = "_blank";
      openLink.rel = "noopener noreferrer";
      openLink.textContent = "Ouvrir le lien";
      meta.insertBefore(openLink, hint || null);
    }
    openLink.href = trimmed;
    openLink.hidden = false;
  } else if (openLink) {
    openLink.hidden = true;
  }
  if (hint) {
    hint.textContent = city
      ? `Recherche ${city} — lien mis à jour (utilisé au prochain crawl)`
      : "Modifiez puis Entrée, clic dehors ou Enregistrer";
  }
}

function updateAllSourceCardUrls() {
  for (const s of SOURCES) {
    updateSourceCardUrlDom(s.id, getSourceDisplayUrl(s));
  }
}

function markSourceUrlDirty(sourceId, inputEl) {
  const saved = getSourceSavedUrl(SOURCES.find((s) => s.id === sourceId));
  const current = (inputEl?.value || "").trim();
  const card = inputEl?.closest(".source-card");
  if (current && current !== saved) {
    sourceUrlDirty.add(sourceId);
    card?.classList.add("source-card--dirty");
    card?.classList.remove("source-card--saved");
  } else {
    sourceUrlDirty.delete(sourceId);
    card?.classList.remove("source-card--dirty");
  }
}

async function saveSourceUrlFromInput(sourceId, inputEl, { quiet = false } = {}) {
  const url = (inputEl?.value || "").trim();
  if (!url) {
    if (!quiet) showToast("Collez un lien de liste ou de recherche", "warning");
    return null;
  }
  const saved = getSourceSavedUrl(SOURCES.find((s) => s.id === sourceId));
  if (url === saved) {
    sourceUrlDirty.delete(sourceId);
    inputEl?.closest(".source-card")?.classList.remove("source-card--dirty");
    return null;
  }
  if (sourceUrlSaving.has(sourceId)) return null;

  sourceUrlSaving.add(sourceId);
  const card = inputEl?.closest(".source-card");
  const saveBtn = card?.querySelector(".source-save-url-btn");
  if (saveBtn) saveBtn.disabled = true;
  inputEl.disabled = true;
  card?.classList.add("source-card--saving");

  try {
    const result = await saveSourceUrl(sourceId, url);
    if (!quiet) {
      showToast(`Lien enregistré — ${result.source.search_url}`, "success");
    }
    return result;
  } catch (err) {
    if (!quiet) showToast(err.message, "error");
    throw err;
  } finally {
    sourceUrlSaving.delete(sourceId);
    if (saveBtn) saveBtn.disabled = false;
    inputEl.disabled = false;
    card?.classList.remove("source-card--saving");
  }
}

function isMobileLayout() {
  return window.matchMedia("(max-width: 900px)").matches;
}

function applyMobileLeadsLayout() {
  if (!isMobileLayout()) return;
  state.leadsView = "grid";
  document.querySelectorAll(".view-toggle button").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === "grid");
  });
}

/** Briefing radar — silencieux si route absente (ancien serveur). */
async function fetchRadarBriefing() {
  try {
    const res = await fetchWithTimeout(`${API}/radar/briefing`, {
      headers: { ...getAuthHeaders(), Accept: "application/json" },
    }, 12000);
    if (!res.ok) return buildClientBriefing();
    const body = await res.json().catch(() => null);
    if (!body || body.error) return buildClientBriefing();
    return body;
  } catch {
    return buildClientBriefing();
  }
}

function activeLeads(leads = LEADS) {
  return (leads || []).filter((l) => (l.status || "").toLowerCase() !== "retire");
}

function computeLiveRadarCounts(leads = LEADS) {
  const enriched = activeLeads(leads);
  const particuliers = enriched.filter((l) => l.type !== "agence");
  const newSansAgence = particuliers.filter(
    (l) =>
      l.status === "nouveau" ||
      (l.alert_tags || []).includes("nouveau") ||
      (l.alert_tags || []).includes("sans_agence"),
  );
  return {
    new_without_agency: newSansAgence.length,
    price_drops: enriched.filter((l) => (l.alert_tags || []).includes("baisse_prix")).length,
    hot_mandate: enriched.filter((l) => (l.mandate_score || l.score || 0) >= 85).length,
    old_listings: particuliers.filter(
      (l) =>
        (l.days_on_market || 0) >= 30 &&
        ["nouveau", "a_contacter"].includes(l.pipeline || l.status || "nouveau"),
    ).length,
    total_opportunities: enriched.length,
    sans_agence: particuliers.length,
    mandats_month: enriched.filter((l) => l.pipeline === "mandat" || l.status === "mandat").length,
    dvf_sous_marche: enriched.filter(
      (l) =>
        (l.alert_tags || []).includes("dvf_sous_marche") ||
        ["sous_marche", "leger_sous_marche"].includes(l.dvf_verdict),
    ).length,
    dvf_compared: enriched.filter((l) => l.dvf_verdict).length,
  };
}

function liveRadarPriorities(leads = LEADS, limit = 20) {
  return [...activeLeads(leads)]
    .sort((a, b) => (b.mandate_score || b.score || 0) - (a.mandate_score || a.score || 0))
    .slice(0, limit);
}

/** Met à jour RADAR depuis LEADS (sans requête /radar) — live pendant/après crawl. */
function syncRadarFromLeads(leads = LEADS) {
  const briefing = buildClientBriefing(leads);
  if (!RADAR) {
    RADAR = briefing;
    return;
  }
  RADAR.counts = briefing.counts;
  RADAR.priorities = briefing.priorities;
  RADAR.date = briefing.date;
  if (!RADAR.agency_name) RADAR.agency_name = briefing.agency_name;
}

function buildClientBriefing(leads = LEADS) {
  const enriched = liveRadarPriorities(leads, 50);
  const today = new Date().toISOString().slice(0, 10);

  return {
    agency_name: state.user?.agency_name || RADAR?.agency_name || "",
    date: today,
    counts: computeLiveRadarCounts(leads),
    priorities: enriched.slice(0, 20),
    alerts: RADAR?.alerts || [],
    _clientFallback: true,
  };
}

function applyBootstrapPayload(data) {
  if (!data || typeof data !== "object") return false;
  if (!Array.isArray(data.leads) || !Array.isArray(data.sources)) return false;
  LEADS = data.leads;
  SOURCES = data.sources;
  syncRadarFromLeads(LEADS);
  const stats = data.stats || {};
  state.appStats = stats;
  ACTIVITIES = data.activities || [];
  SOURCE_STATS = data.source_stats || [];
  if (data.crawler && typeof data.crawler === "object") {
    state.crawlerRunning = !!data.crawler.running;
  }
  if (data.settings && typeof data.settings === "object") {
    state.settings = normalizeSettingsPayload({ settings: data.settings });
    applyAgencyCityToCrawl();
  }
  scheduleDrawerCacheWarm();
  return true;
}

async function fetchBootstrap() {
  try {
    const res = await fetchWithTimeout(`${API}/bootstrap`, {
      headers: { ...getAuthHeaders(), Accept: "application/json" },
    }, 45000);
    if (!res.ok) return null;
    return res.json().catch(() => null);
  } catch {
    return null;
  }
}

async function loadDataCore() {
  const bootstrap = await fetchBootstrap();
  if (applyBootstrapPayload(bootstrap)) {
    return;
  }

  const [coreResult, settingsResult] = await Promise.all([
    Promise.allSettled([api("/leads"), api("/stats"), api("/sources"), api("/crawler/status")]),
    refreshAgencySettings().catch(() => {
      applyAgencyCityToCrawl();
    }),
  ]);

  const [leadsResult, statsResult, sourcesResult, crawlerResult] = coreResult;

  if (leadsResult.status === "rejected") throw leadsResult.reason;
  if (sourcesResult.status === "rejected") throw sourcesResult.reason;

  LEADS = leadsResult.value;
  SOURCES = sourcesResult.value;

  if (statsResult.status === "fulfilled") {
    state.appStats = statsResult.value.stats || null;
    ACTIVITIES = statsResult.value.activities || [];
    SOURCE_STATS = statsResult.value.source_stats || [];
  } else {
    console.warn("Stats indisponibles", statsResult.reason);
    ACTIVITIES = ACTIVITIES || [];
    SOURCE_STATS = SOURCE_STATS || [];
  }

  if (crawlerResult.status === "fulfilled") {
    state.crawlerRunning = crawlerResult.value.running;
  } else {
    console.warn("État crawl indisponible", crawlerResult.reason);
  }

  if (settingsResult.status === "rejected") {
    applyAgencyCityToCrawl();
  }
  scheduleDrawerCacheWarm();
}

async function fetchRadarSummary() {
  try {
    const res = await fetchWithTimeout(`${API}/radar/summary`, {
      headers: { ...getAuthHeaders(), Accept: "application/json" },
    }, 30000);
    if (!res.ok) return null;
    const body = await res.json().catch(() => null);
    if (!body?.briefing) return null;
    return body;
  } catch {
    return null;
  }
}

async function loadRadarAndPlaybook() {
  const summary = await fetchRadarSummary();
  if (summary) {
    RADAR = summary.briefing;
    if (summary.playbook?.guide?.length) {
      PLAYBOOK = summary.playbook;
    } else {
      PLAYBOOK = await fetchPlaybook().catch(() => PLAYBOOK);
    }
    return;
  }

  const [radarResult, playbookResult] = await Promise.allSettled([
    fetchRadarBriefing(),
    fetchPlaybook(),
  ]);
  if (radarResult.status === "fulfilled") RADAR = radarResult.value;
  if (playbookResult.status === "fulfilled" && playbookResult.value) {
    PLAYBOOK = playbookResult.value;
  }
}

let radarPlaybookLoadPromise = null;

function scheduleRadarPlaybookLoad() {
  if (radarPlaybookLoadPromise) return radarPlaybookLoadPromise;
  radarPlaybookLoadPromise = loadRadarAndPlaybook()
    .then(() => {
      renderRadarBriefing();
      if (state.currentView === "playbook") renderPlaybook();
      if (RADAR?._clientFallback && !sessionStorage.getItem("veliora_radar_warn")) {
        sessionStorage.setItem("veliora_radar_warn", "1");
        showToast(
          "Briefing en mode local — relancez python app.py (ou demarrer.bat) pour le radar complet",
          "warning",
          7000,
        );
      }
    })
    .catch((err) => console.warn("Radar / playbook", err))
    .finally(() => {
      radarPlaybookLoadPromise = null;
    });
  return radarPlaybookLoadPromise;
}

async function loadData() {
  await loadDataCore();
  applyMobileLeadsLayout();
  scheduleRadarPlaybookLoad();
}

/** Recharge tout (y compris radar) — après crawl, import, etc. */
async function reloadCrmData() {
  radarPlaybookLoadPromise = null;
  await loadDataCore();
  await loadRadarAndPlaybook();
  applyMobileLeadsLayout();
}

function normalizeSettingsPayload(data) {
  if (!data || typeof data !== "object") return {};
  if (data.settings && typeof data.settings === "object") return data.settings;
  return data;
}

function agencyPrimaryCity() {
  const s = state.settings || {};
  const fromApi = (s.primary_city || "").trim();
  if (fromApi) return fromApi;
  const cities = s.target_cities || [];
  const first = cities.find((c) => c && String(c).trim());
  return first ? String(first).trim() : "";
}

/** Affiche la ville de crawl (lecture seule — réglée via Territoire ou Fiche agence). */
function updateCrawlCityDisplay() {
  const display = document.getElementById("crawl-city-display");
  const hint = document.getElementById("crawl-city-territory-hint");
  if (!display) return;

  const city = agencyPrimaryCity();
  const cities = (state.settings?.target_cities || []).map((c) => String(c).trim()).filter(Boolean);

  if (city) {
    display.textContent =
      cities.length > 1 ? `${city} (+ ${cities.length - 1} autre${cities.length > 2 ? "s" : ""})` : city;
    display.classList.remove("is-wide");
    if (hint) {
      hint.textContent =
        "Filtre actif sur cette ville (1ʳᵉ ville du territoire). Pour modifier : Territoire (Radar) ou Fiche agence → Enregistrer. Pour tout le pays, videz les villes dans Territoire.";
    }
  } else {
    display.textContent = "Toute la France — aucune ville définie";
    display.classList.add("is-wide");
    if (hint) {
      hint.textContent =
        "Définissez au moins une ville dans Territoire (Radar) ou Fiche agence, puis enregistrez — le crawl s’alignera automatiquement.";
    }
  }
}

function applyAgencyCityToCrawl() {
  updateCrawlCityDisplay();
}

/** Recharge les réglages agence depuis l’API et synchronise le champ crawl. */
async function refreshAgencySettings() {
  try {
    const data = await api("/radar/settings");
    state.settings = normalizeSettingsPayload(data);
  } catch {
    /* garde state.settings actuel */
  }
  applyAgencyCityToCrawl();
  scheduleSourceUrlsForCity();
  return state.settings;
}

window.VelioraRefreshAgencySettings = refreshAgencySettings;
window.VelioraScheduleSourceUrlsForCity = scheduleSourceUrlsForCity;

function getCrawlCity() {
  const city = agencyPrimaryCity();
  return city || null;
}

function crawlBodyExtra() {
  const city = getCrawlCity();
  return city ? { city } : {};
}

function countEnabledCrawlSources(list = SOURCES) {
  return (list || []).filter(
    (s) =>
      s.enabled !== false &&
      String(s.search_url || s.base_url || "").trim().startsWith("http"),
  ).length;
}

/** Portails recommandés — seuls inclus dans « Crawler tout ». */
function countRecommendedCrawlSources(list = SOURCES) {
  return (list || []).filter(
    (s) =>
      s.enabled !== false &&
      !s.is_custom &&
      !s.is_antibot &&
      String(s.search_url || s.base_url || "").trim().startsWith("http"),
  ).length;
}

/** Met à jour le titre du modal si le job serveur indique le nombre de sites. */
function applyCrawlLabelFromJobMessage(label, jobMessage) {
  if (!jobMessage) return label;
  const m = String(jobMessage).match(/(\d+)\s+site/i);
  if (!m) return label;
  const n = m[1];
  const city = getCrawlCity();
  return city ? `Portails recommandés (${n}) — ${city}` : `Portails recommandés (${n})`;
}

function setCrawlModalTitles(label, { prefix = "Crawl — " } = {}) {
  const title = `${prefix}${label}`;
  crawlState.label = label;
  const loaderTitle = document.getElementById("crawl-loader-title");
  const dockTitle = document.getElementById("crawl-dock-title");
  if (loaderTitle) loaderTitle.textContent = title;
  if (dockTitle) dockTitle.textContent = title;
}

function formatEtaRemaining(job) {
  if (!job?.eta_seconds) return "";
  const elapsed = crawlState.startedAt ? (Date.now() - crawlState.startedAt) / 1000 : 0;
  const progress = Math.min(99, Math.max(0, job.progress || 0));
  let remaining = job.eta_seconds;
  if (progress > 5) {
    remaining = Math.max(30, Math.round((job.eta_seconds * (100 - progress)) / 100));
  } else {
    remaining = Math.max(0, job.eta_seconds - Math.round(elapsed));
  }
  if (remaining < 60) return `~${remaining} s restantes`;
  const m = Math.ceil(remaining / 60);
  if (m < 120) return `~${m} min restantes`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `~${h} h ${rm} min restantes` : `~${h} h restantes`;
}

function updateEtaDisplay(job) {
  const text = formatEtaRemaining(job);
  const totalHint = job?.eta_seconds
    ? `Durée totale estimée : ${formatEtaTotal(job.eta_seconds)}`
    : "";
  ["crawl-loader-eta", "crawl-dock-eta"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text || totalHint;
  });
  const right = document.getElementById("crawl-dock-eta-right");
  if (right) right.textContent = job?.listings_total ? `${job.listings_done || 0}/${job.listings_total} ann.` : "";
}

function formatEtaTotal(sec) {
  if (sec < 3600) return `~${Math.ceil(sec / 60)} min`;
  const h = Math.floor(sec / 3600);
  const m = Math.ceil((sec % 3600) / 60);
  return m ? `~${h} h ${m} min` : `~${h} h`;
}

function setupMobileNav() {
  const toggle = document.getElementById("mobile-nav-toggle");
  const sidebar = document.getElementById("sidebar");
  const overlay = document.getElementById("sidebar-overlay");
  const close = () => {
    sidebar?.classList.remove("open");
    overlay?.classList.remove("open");
  };
  toggle?.addEventListener("click", () => {
    sidebar?.classList.toggle("open");
    overlay?.classList.toggle("open");
  });
  overlay?.addEventListener("click", close);
  document.querySelectorAll(".sidebar .nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", close);
  });

  document.querySelectorAll(".mobile-bottom-nav button").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchView(btn.dataset.view);
      close();
      document.querySelectorAll(".mobile-bottom-nav button").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
    });
  });
}

function scriptCopyText(script) {
  if (!script) return "";
  if (typeof script === "string") return script.replace(/\*\*([^*]+)\*\*/g, "$1");
  if (script.full_text_plain) return script.full_text_plain;
  if (script.full_text) return script.full_text.replace(/\*\*([^*]+)\*\*/g, "$1");
  return [script.opening, script.observation, script.value, script.closing]
    .filter(Boolean)
    .map((s) => String(s).replace(/\*\*([^*]+)\*\*/g, "$1"))
    .join("\n\n");
}

/** Affiche le script avec **gras** → <strong> */
function formatScriptRichText(text) {
  if (!text) return "";
  return escapeHtml(String(text)).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function buildScriptPanelHtml(script) {
  if (!script) return "<p class=\"text-muted\">Script indisponible.</p>";
  if (typeof script === "string") {
    return script
      .split("\n")
      .filter((l) => l.trim())
      .map((l) => `<p class="script-panel-p">${formatScriptRichText(l)}</p>`)
      .join("");
  }
  let html = "";

  // En-tête : probabilité de signature + quand appeler.
  const plan = script.plan || null;
  if (script.signature_probability != null) {
    const p = script.signature_probability;
    html += `<div class="script-proba ${signatureClass(p)}">
      <div class="script-proba-pct">${p}<span>%</span></div>
      <div class="script-proba-text">
        <strong>de chance de signer le mandat</strong>
        <span>${escapeHtml(script.signature_band || signatureBandLabel(p))}${
      plan?.priority ? " · " + escapeHtml(priorityLabel(plan.priority)) : ""
    }</span>
      </div>
    </div>`;
  }
  if (plan?.when_to_call) {
    const w = plan.when_to_call;
    const windows = (w.windows || [])
      .map((win) => `<li><strong>${escapeHtml(win.label)}</strong> — ${escapeHtml(win.detail)}</li>`)
      .join("");
    html += `<div class="script-section script-when">
      <div class="script-section-title">📞 Quand appeler</div>
      ${w.best_window ? `<p class="script-best-window">${escapeHtml(w.best_window)}</p>` : ""}
      ${windows ? `<ul class="script-windows">${windows}</ul>` : ""}
      ${w.avoid ? `<p class="script-avoid">${escapeHtml(w.avoid)}</p>` : ""}
    </div>`;
  }

  const steps = [
    ["1", script.opening],
    ["2", script.observation],
    ["3", script.value],
    ["4", script.closing],
  ].filter(([, t]) => t);
  if (steps.length) {
    html += `<div class="script-section"><div class="script-section-title">🗣️ Script d'appel</div>`;
    html += steps
      .map(
        ([n, t]) =>
          `<div class="playbook-script-step"><span>${n}</span><p>${formatScriptRichText(t)}</p></div>`,
      )
      .join("");
    html += `</div>`;
  }
  if (script.advice?.length) {
    html += `<ul class="script-panel-advice">${script.advice.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>`;
  }
  if (script.objections?.length) {
    html += `<div class="script-panel-objections"><strong>Objections</strong>${script.objections
      .map(
        (obj) =>
          `<div class="playbook-objection"><strong>« ${escapeHtml(obj.q || "")} »</strong><p>${escapeHtml(obj.a || "")}</p></div>`,
      )
      .join("")}</div>`;
  }

  // SMS prêt à envoyer (avec bouton copier).
  if (plan?.sms) {
    html += `<div class="script-section script-channel">
      <div class="script-section-title">💬 SMS prêt à envoyer
        <button type="button" class="btn btn-ghost btn-xs script-copy-channel" data-copy="${escapeAttr(plan.sms)}">Copier</button>
      </div>
      <p class="script-message">${escapeHtml(plan.sms)}</p>
    </div>`;
  }
  // Email prêt à envoyer (objet + corps).
  if (plan?.email) {
    const full = `Objet : ${plan.email.subject}\n\n${plan.email.body}`;
    html += `<div class="script-section script-channel">
      <div class="script-section-title">✉️ Email prêt à envoyer
        <button type="button" class="btn btn-ghost btn-xs script-copy-channel" data-copy="${escapeAttr(full)}">Copier</button>
      </div>
      <p class="script-email-subject"><strong>Objet :</strong> ${escapeHtml(plan.email.subject)}</p>
      <pre class="script-email-body">${escapeHtml(plan.email.body)}</pre>
    </div>`;
  }
  // Cadence de relance (quoi faire, quand).
  if (plan?.cadence?.length) {
    const rows = plan.cadence
      .map(
        (c) =>
          `<li><strong>${escapeHtml(c.step)}</strong> — ${escapeHtml(c.detail)}</li>`,
      )
      .join("");
    html += `<div class="script-section script-cadence">
      <div class="script-section-title">📆 Plan de relance</div>
      <ul class="script-cadence-list">${rows}</ul>
    </div>`;
  }

  return html || `<p class="script-panel-p">${escapeHtml(scriptCopyText(script))}</p>`;
}

function priorityLabel(priority) {
  return (
    {
      urgent: "Priorité immédiate",
      high: "Priorité haute",
      medium: "À traiter cette semaine",
      low: "À surveiller",
    }[priority] || ""
  );
}

function syncScriptPanelUi() {
  const panel = document.getElementById("script-panel");
  const dock = document.getElementById("script-dock");
  if (!panel || !dock) return;

  const expanded = scriptPanelState.visible && !scriptPanelState.minimized;
  const minimized = scriptPanelState.visible && scriptPanelState.minimized;

  panel.hidden = !expanded;
  panel.classList.toggle("open", expanded);
  dock.hidden = !minimized;
  dock.classList.toggle("open", minimized);
}

function openScriptPanel(lead, script) {
  scriptPanelState.visible = true;
  scriptPanelState.minimized = false;
  scriptPanelState.leadId = lead?.id ?? null;
  scriptPanelState.copyText = scriptCopyText(script);

  const title = document.getElementById("script-panel-title");
  const scenario = document.getElementById("script-panel-scenario");
  const body = document.getElementById("script-panel-body");
  const dockLabel = document.getElementById("script-dock-label");
  const openLeadBtn = document.getElementById("script-panel-open-lead");

  const label =
    lead?.property_title || lead?.address || lead?.owner || "Script d'appel";
  if (title) title.textContent = label;
  if (dockLabel) dockLabel.textContent = label.length > 36 ? `${label.slice(0, 34)}…` : label;
  if (scenario) {
    scenario.textContent =
      typeof script === "object" && script?.scenario_label
        ? script.scenario_label
        : "";
    scenario.hidden = !scenario.textContent;
  }
  if (body) body.innerHTML = buildScriptPanelHtml(script);
  if (openLeadBtn) {
    openLeadBtn.hidden = !scriptPanelState.leadId;
  }

  syncScriptPanelUi();
}

function minimizeScriptPanel() {
  if (!scriptPanelState.visible) return;
  scriptPanelState.minimized = true;
  syncScriptPanelUi();
}

function expandScriptPanel() {
  if (!scriptPanelState.visible) return;
  scriptPanelState.minimized = false;
  syncScriptPanelUi();
}

function closeScriptPanel() {
  scriptPanelState.visible = false;
  scriptPanelState.minimized = false;
  scriptPanelState.leadId = null;
  scriptPanelState.copyText = "";
  syncScriptPanelUi();
}

async function loadScriptForLead(lead) {
  if (!lead?.id) {
    showToast("Prospect introuvable", "error");
    return;
  }
  try {
    const res = await api(`/radar/leads/${lead.id}/script`);
    const script = res?.script ?? res;
    openScriptPanel(lead, script);
  } catch (err) {
    showToast(err.message, "error");
  }
}

function setupScriptPanel() {
  document.getElementById("script-panel-close")?.addEventListener("click", closeScriptPanel);
  document.getElementById("script-panel-minimize")?.addEventListener("click", minimizeScriptPanel);
  document.getElementById("script-dock-close")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeScriptPanel();
  });
  document.getElementById("script-dock-expand")?.addEventListener("click", expandScriptPanel);
  document.getElementById("script-panel-copy")?.addEventListener("click", async () => {
    const text = scriptPanelState.copyText;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast("Script copié", "success", 2500);
    } catch {
      showToast("Copie impossible", "error");
    }
  });
  document.getElementById("script-panel-open-lead")?.addEventListener("click", () => {
    if (scriptPanelState.leadId) openDrawer(scriptPanelState.leadId);
  });
  // Copie d'un canal précis (SMS / email) depuis le corps du panneau.
  document.getElementById("script-panel-body")?.addEventListener("click", async (e) => {
    const btn = e.target.closest(".script-copy-channel");
    if (!btn) return;
    const text = btn.getAttribute("data-copy") || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast("Copié", "success", 2000);
    } catch {
      showToast("Copie impossible", "error");
    }
  });
}

function pausePageCrawlPoll() {
  if (crawlState.pollTimer) {
    clearTimeout(crawlState.pollTimer);
    crawlState.pollTimer = null;
  }
}

async function handoffCrawlToBackground() {
  if (!crawlState.active || !crawlState.jobId) return;
  crawlState.pagePollPaused = true;
  pausePageCrawlPoll();
  minimizeCrawlUI({ notify: true });
  if (window.CrawlWatch) {
    await CrawlWatch.start(crawlState.jobId, crawlState.label);
  }
}

/** Annule le job côté serveur (non bloquant, timeout court). */
function requestCrawlCancelOnServer(jobId) {
  const headers = { "Content-Type": "application/json", ...getAuthHeaders() };
  const opts = { method: "POST", headers };
  const calls = [fetch(`${API}/crawler/jobs/cancel`, opts)];
  if (jobId) {
    calls.unshift(
      fetch(`${API}/crawler/jobs/${encodeURIComponent(jobId)}/cancel`, opts),
    );
  }
  Promise.allSettled(calls).catch(() => {});
}

/** Arrête le crawl serveur, coupe le polling et ferme modal + dock. */
function stopCrawlJobAndCloseUi({ toastMessage = "Crawl arrêté" } = {}) {
  const wasAnalyzeImport = Boolean(
    crawlState.pollOptions?.goToAnalyze && crawlState.pollOptions?.importUrl,
  );
  const jobId = crawlState.jobId;

  pausePageCrawlPoll();
  if (window.CrawlWatch) CrawlWatch.stop();

  if (wasAnalyzeImport) {
    hideCrawlLoader();
    cancelAnalyzeImport({ silent: true });
    showToast("Import annulé", "info");
    return;
  }

  hideCrawlLoader();
  if (toastMessage) showToast(toastMessage, "success", 3500);
  requestCrawlCancelOnServer(jobId);
}

function dismissCrawlUI() {
  stopCrawlJobAndCloseUi({ toastMessage: "Crawl fermé et arrêté" });
}

async function finishCrawlFromJob(job, label, options = {}) {
  if (crawlState._finishing) return;
  if (!crawlState.active && !job?.id) return;
  crawlState._finishing = true;
  try {
    const jobId = job?.id || crawlState.jobId;
    const lbl = label || crawlState.label;
    setCrawlLoaderStep(job?.message || "Terminé");
    const finalJob = jobId
      ? await api(`/crawler/jobs/${jobId}?lite=1`).catch(() => job)
      : job;
    try {
      await refreshAppData();
    } catch (err) {
      console.warn("refresh after crawl", err);
    }
    notifyCrawlResult(finalJob, lbl);
    if (window.CrawlWatch) {
      CrawlWatch.showLocalNotification(finalJob, lbl);
      await CrawlWatch.stop();
    }
    if (options.goToAnalyze && options.importUrl) {
      clearUrlSearchInputs();
      switchView("analyze");
      await completeOnDemandAnalysisAfterImport(options.importUrl);
    } else if (options.goToLeads) {
      clearUrlSearchInputs();
      switchView("leads");
    }
    if (options.openImportedLead && options.importUrl && !options.goToAnalyze) {
      const target = normalizeUrlKey(options.importUrl);
      const lead = LEADS.find((l) => normalizeUrlKey(l.source_url || "") === target);
      if (lead) openDrawer(lead.id);
    }
  } finally {
    hideCrawlLoader();
    crawlState._finishing = false;
  }
}

function setupCrawlBackground() {
  const loader = document.getElementById("crawl-loader");
  loader?.addEventListener("click", (e) => {
    if (e.target === loader) dismissCrawlUI();
  });
  document.getElementById("crawl-close-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    dismissCrawlUI();
  });
  document.getElementById("crawl-minimize-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    minimizeCrawlUI({ notify: true });
  });
  document.getElementById("crawl-cancel-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    cancelStaleCrawlUi();
  });
  document.getElementById("crawl-dock-close")?.addEventListener("click", (e) => {
    e.stopPropagation();
    dismissCrawlUI();
  });
  document.getElementById("crawl-dock-compact")?.addEventListener("click", (e) => {
    e.stopPropagation();
    crawlState.compact = !crawlState.compact;
    document.getElementById("crawl-dock")?.classList.toggle("compact", crawlState.compact);
    e.currentTarget.setAttribute("aria-pressed", crawlState.compact ? "true" : "false");
    e.currentTarget.textContent = crawlState.compact ? "Agrandir" : "Réduire +";
  });
  document.getElementById("crawl-dock")?.addEventListener("click", (e) => {
    if (e.target.closest(".crawl-dock-close, .crawl-dock-compact")) return;
    expandCrawlUI();
  });

  if (window.CrawlWatch) {
    CrawlWatch.setupClientListener((job, label, eventType) => {
      if (!job) return;
      if (eventType === "progress") {
        updateCrawlLoaderUI(job, `Crawl — ${label || crawlState.label}`);
        return;
      }
      finishCrawlFromJob(job, label, crawlState.pollOptions || {});
    });
  }

  document.addEventListener("visibilitychange", () => {
    if (!crawlState.active || !crawlState.jobId || !window.CrawlWatch) return;
    if (document.hidden) {
      crawlState.pagePollPaused = true;
      pausePageCrawlPoll();
      CrawlWatch.start(crawlState.jobId, crawlState.label);
    } else {
      CrawlWatch.stop();
      crawlState.pagePollPaused = false;
      if (!crawlState.pollTimer) {
        startCrawlPolling(
          crawlState.jobId,
          crawlState.label,
          crawlState.pollOptions || {},
        );
      }
    }
  });

  window.addEventListener("pagehide", () => {
    if (crawlState.active && crawlState.jobId && window.CrawlWatch) {
      CrawlWatch.start(crawlState.jobId, crawlState.label);
    }
  });

  setupPwaInstallBanner();
}

function setupPwaInstallBanner() {
  const banner = document.getElementById("pwa-install-banner");
  const btn = document.getElementById("pwa-install-btn");
  const dismiss = document.getElementById("pwa-install-dismiss");
  if (!banner || !btn) return;

  let deferredPrompt = null;
  const isStandalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  if (isStandalone) return;

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    banner.hidden = false;
  });

  btn.addEventListener("click", async () => {
    if (!deferredPrompt) {
      showToast(
        "Installez Veliora depuis le menu du navigateur (Ajouter à l’écran d’accueil)",
        "info",
        6000,
      );
      return;
    }
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    banner.hidden = true;
  });

  dismiss?.addEventListener("click", () => {
    banner.hidden = true;
    try {
      localStorage.setItem("veliora_pwa_install_dismissed", "1");
    } catch {
      /* ignore */
    }
  });

  try {
    if (localStorage.getItem("veliora_pwa_install_dismissed") === "1") return;
  } catch {
    /* ignore */
  }

  if (/iphone|ipad|ipod/i.test(navigator.userAgent)) {
    banner.hidden = false;
    btn.textContent = "Comment installer";
  }
}

async function minimizeCrawlUI(opts = {}) {
  if (!crawlState.active) return;
  crawlState.minimized = true;
  document.getElementById("crawl-loader")?.classList.add("minimized");
  document.getElementById("crawl-loader")?.classList.remove("open");
  const dock = document.getElementById("crawl-dock");
  dock?.classList.add("open");
  dock?.classList.toggle("compact", crawlState.compact);

  if (opts.notify !== false && window.CrawlWatch && crawlState.jobId) {
    const perm = await CrawlWatch.requestPermission();
    await CrawlWatch.start(crawlState.jobId, crawlState.label);
    if (perm === "granted") {
      showToast(
        "Crawl en arrière-plan — vous pouvez quitter l’app, notification à la fin",
        "info",
        5500,
      );
    } else {
      showToast(
        "Le crawl continue sur le serveur — revenez sur Veliora ou autorisez les notifications",
        "info",
        7000,
      );
    }
  }
}

function expandCrawlUI() {
  if (!crawlState.active) return;
  crawlState.minimized = false;
  document.getElementById("crawl-loader")?.classList.remove("minimized");
  document.getElementById("crawl-loader")?.classList.add("open");
  document.getElementById("crawl-dock")?.classList.remove("open");
}

function leadQuickFactsHtml(lead) {
  const bits = [];
  if (lead.city || lead.postcode) bits.push(escapeHtml([lead.postcode, lead.city].filter(Boolean).join(" ")));
  if (lead.surface) bits.push(`${lead.surface} m²`);
  if (lead.rooms) bits.push(`${lead.rooms} pièce${lead.rooms > 1 ? "s" : ""}`);
  if (lead.bedrooms) bits.push(`${lead.bedrooms} ch`);
  if (lead.days_on_market != null) bits.push(`${lead.days_on_market} j en ligne`);
  return bits.length
    ? `<div class="property-meta property-meta-facts">${bits.join(" · ")}</div>`
    : "";
}

async function ensureAuth() {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (!token) {
    redirectToLogin();
    return false;
  }
  try {
    const me = await api("/auth/me");
    state.user = me.user;
    state.settings = normalizeSettingsPayload(me.settings || state.settings || {});
    applyAgencyCityToCrawl();
    scheduleSourceUrlsForCity();
    const cached = localStorage.getItem(AUTH_USER_KEY);
    if (cached) {
      try {
        state.user = { ...JSON.parse(cached), ...state.user };
      } catch (_) {
        /* ignore */
      }
    }
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(state.user));
    updateAuthHeader();
    if (me.billing?.requires_payment && !me.billing?.active) {
      window.location.href = "/crm/auth?next=" + encodeURIComponent(
        window.location.pathname + window.location.search
      );
      return false;
    }
    return true;
  } catch (err) {
    if (/session|connecté|401/i.test(err.message)) return false;
    throw err;
  }
}

function updateAuthHeader() {
  const u = state.user;
  const avatar = document.getElementById("header-avatar");
  const agencyEl = document.getElementById("header-agency-name");
  if (agencyEl && u?.agency_name) agencyEl.textContent = u.agency_name;
  if (avatar && u) {
    const initials = [u.first_name, u.last_name].filter(Boolean).map((n) => n[0]).join("")
      || (u.email || "AG").slice(0, 2).toUpperCase();
    avatar.textContent = initials;
    avatar.title = u.email || "";
  }
  const inviteBtn = document.getElementById("btn-invite-collab");
  if (inviteBtn) inviteBtn.hidden = u?.role !== "admin";
}

async function logout() {
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (token) {
    try {
      await fetch(`${API}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      /* hors ligne — on efface quand même le token local */
    }
  }
  localStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_USER_KEY);
  window.location.href = "/";
}

async function init() {
  setupNavigation();
  setupMobileNav();
  setupSearch();
  setupFilters();
  setupViewToggle();
  setupDrawer();
  setupDrawerPrefetch();
  setupEstimateur();
  setupLeadRefresh();
  setupLeadsActions();
  setupLeadsListDelegation();
  setupCrawler();
  setupCrawlBackground();
  setupScriptPanel();
  setupCustomUrlCrawl();
  setupProductModes();
  setupAnalyzeForm();
  setupCityAutocompletes();
  setupCrawlUrlModal();
  document.getElementById("btn-logout")?.addEventListener("click", logout);
  document.getElementById("btn-invite-collab")?.addEventListener("click", () => {
    document.getElementById("invite-modal")?.classList.add("open");
  });
  document.getElementById("invite-modal-close")?.addEventListener("click", () => {
    document.getElementById("invite-modal")?.classList.remove("open");
  });
  document.getElementById("invite-form")?.addEventListener("submit", submitInvite);
  setupRadar();
  setupPlaybook();
  setupOnboarding();
  setupAccountMenu();
  window.addEventListener("resize", () => {
    applyMobileLeadsLayout();
    renderLeads();
  });

  if (!(await ensureAuth())) return;

  try {
    const health = await api("/health").catch(() => null);
    if (!health) {
      showWrongServerBanner();
      showToast(
        `API indisponible — lancez python app.py puis http://localhost:${PROPSCOUT_PORT}`,
        "error",
        12000,
      );
    } else if (!health.mandates || !health.clients || (health.api_version || 0) < 6) {
      showWrongServerBanner(true);
      showToast(
        "Serveur obsolète — fermez l’ancien terminal (Ctrl+C), relancez demarrer.bat ou python app.py (api_version 6, module clients requis)",
        "warning",
        14000,
      );
    } else if (!health.radar_analyze_url || (health.api_version || 0) < 7) {
      showToast(
        "Mode 2 (Score Mandat™) nécessite un redémarrage — Ctrl+C puis python app.py (api_version 7)",
        "warning",
        12000,
      );
    } else if (!health.delete_leads || (health.api_version || 0) < 5) {
      showWrongServerBanner(true);
      showToast(
        "Serveur obsolète sur le port 8000 — Ctrl+C puis python app.py ou demarrer.bat",
        "warning",
        12000,
      );
    } else if (!health.radar) {
      showToast(
        "Module Radar indisponible — relancez le serveur avec la dernière version (python app.py)",
        "warning",
        8000,
      );
    } else if (API !== "/api") {
      showToast(
        `API : ${API} (page ouverte ailleurs que :8000)`,
        "info",
        5000,
      );
    } else if (!health.delete_sources) {
      showToast(
        "Serveur partiellement obsolète — relancez python app.py",
        "warning",
        8000,
      );
    }
    if (health?.ok && health.delete_leads) hideWrongServerBanner();
    if (typeof initVelioraClients === "function") {
      initVelioraClients({
        api,
        showToast,
        escapeHtml,
        getAuthHeaders,
        API,
      });
    }
    if (typeof initVelioraMandates === "function") {
      initVelioraMandates({
        api,
        showToast,
        escapeHtml,
        getAuthHeaders,
        API,
        refreshAgencySettings,
        scheduleSourceUrlsForCity,
      });
    }
    await loadData();
    await checkServerLeadRefreshCapability();
    renderAll();
    syncCrawlerUI();
    fetchRoiStats();
    await syncAccountBillingButton();
    await refreshOnboardingUi();
    if (!onboardingDidAutoNav && onboardingCache && !onboardingCache.settings?.onboarding_completed) {
      const current = currentOnboardingStep(onboardingProgress(onboardingCache));
      const meta = ONBOARDING_STEPS.find((s) => s.step === current);
      if (meta && state.currentView !== meta.view) {
        onboardingDidAutoNav = true;
        await switchView(meta.view);
      }
    }
    startPolling();
    await resumeActiveCrawlIfAny();
  } catch (err) {
    if (err?.message === "SUBSCRIPTION_REDIRECT") return;
    showToast(err.message || "Impossible de charger les données — lancez python app.py", "error");
    renderAll();
  }
}

function setupNavigation() {
  document.querySelectorAll(".nav-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
}

async function switchView(view) {
  if (
    state.currentView === "analyze" &&
    view !== "analyze" &&
    analyzeImportState.active
  ) {
    cancelAnalyzeImport({ silent: true });
  }
  // Quitter la carte : couper le suivi GPS live (batterie).
  if (state.currentView === "map" && view !== "map") {
    window.VelioraMap?.leave?.();
  }
  state.currentView = view;
  syncProductModeTabs(view);
  document.querySelectorAll(".nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });
  document.querySelectorAll(".mobile-bottom-nav button").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === `view-${view}`);
  });
  const meta = viewTitles[view] || { title: view, subtitle: "" };
  document.getElementById("header-title").textContent = meta.title;
  document.getElementById("header-subtitle").textContent = meta.subtitle;
  if (view === "dashboard") {
    fetchRoiStats();
  }
  if (view === "crawler") {
    refreshAgencySettings().catch(() => {
      applyAgencyCityToCrawl();
      scheduleSourceUrlsForCity();
    });
  }
  if (view === "clients" && typeof loadClients === "function") {
    try {
      await loadClients();
    } catch (err) {
      showToast(err.message, "error");
    }
  }
  if (view === "mandates" && typeof loadMandates === "function") {
    try {
      await loadMandates();
    } catch (err) {
      showToast(err.message, "error");
    }
  }
  if (view === "playbook") {
    const prev = PLAYBOOK;
    if (!PLAYBOOK?.guide?.length) {
      PLAYBOOK = await fetchPlaybook().catch(() => prev);
      notifyPlaybookLoadIssues(PLAYBOOK);
    } else {
      void fetchPlaybook()
        .then((pb) => {
          if (!pb?.guide?.length) return;
          PLAYBOOK = pb;
          if (state.currentView === "playbook") renderPlaybook();
        })
        .catch(() => {});
    }
  }
  renderAll();
  if (view === "map" && window.VelioraMap?.enter) {
    try {
      await window.VelioraMap.enter();
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (state.currentView === "map") window.VelioraMap?.resize?.();
        });
      });
    } catch (err) {
      showToast(err?.message || "Carte indisponible", "error");
    }
  }
  if (view === "dashboard") {
    markOnboardingStep3Seen();
  }
  if (
    view === "analyze" &&
    !state.onDemandAnalysis?.analysis &&
    !analyzeImportState.active
  ) {
    setAnalyzeUiState("empty");
  }
  refreshOnboardingUi();
}

function setupSearch() {
  const input = document.getElementById("global-search");
  let debounce;
  input.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter") return;
    const value = e.target.value.trim();
    if (!value) return;

    if (isUrl(value)) {
      e.preventDefault();
      await runOnDemandAnalysis(value);
      return;
    }

    state.searchQuery = value.toLowerCase();
    switchView("leads");
    renderLeads();
  });

  input.addEventListener("input", (e) => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.searchQuery = e.target.value.toLowerCase();
      if (state.searchQuery && !isUrl(state.searchQuery)) {
        if (state.currentView !== "leads") switchView("leads");
        renderLeads();
      }
    }, 200);
  });
}

function setupCustomUrlCrawl() {
  const form = document.getElementById("custom-crawl-form");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = document.getElementById("custom-crawl-url");
      await runOnDemandAnalysis(input.value.trim());
    });
  }
}

function normalizeUrlKey(url) {
  try {
    let u = url.trim();
    if (!u.startsWith("http://") && !u.startsWith("https://")) u = "https://" + u;
    const parsed = new URL(u);
    const path = parsed.pathname.replace(/\/$/, "") || "";
    return `${parsed.origin}${path}${parsed.search}`.toLowerCase();
  } catch {
    return url.toLowerCase().trim();
  }
}

function getDomainFromUrl(url) {
  try {
    let u = url.trim();
    if (!u.startsWith("http")) u = "https://" + u;
    return new URL(u).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

function guessSiteNameFromUrl(url) {
  const domain = getDomainFromUrl(url);
  const known = {
    "paruvendu.fr": "ParuVendu",
    "leboncoin.fr": "LeBonCoin",
    "pap.fr": "PAP",
    "seloger.com": "SeLoger",
    "logic-immo.com": "LogicImmo",
    "bienici.com": "BienIci",
    "lefigaro.fr": "Le Figaro Immobilier",
    "figaro.fr": "Le Figaro Immobilier",
  };
  if (known[domain]) return known[domain];
  const part = domain.split(".")[0];
  return part ? part.charAt(0).toUpperCase() + part.slice(1) : "Site";
}

function findSourceByUrl(url) {
  const key = normalizeUrlKey(url);
  return SOURCES.find((s) => normalizeUrlKey(s.search_url || s.base_url) === key);
}

function findSourceByDomain(url) {
  const domain = getDomainFromUrl(url);
  if (!domain) return null;
  return SOURCES.find((s) => getDomainFromUrl(s.base_url) === domain);
}

function setupCrawlUrlModal() {
  document.getElementById("crawl-url-close").addEventListener("click", closeCrawlUrlModal);
  document.getElementById("crawl-url-cancel").addEventListener("click", closeCrawlUrlModal);
  document.getElementById("crawl-url-modal").addEventListener("click", (e) => {
    if (e.target.id === "crawl-url-modal") closeCrawlUrlModal();
  });
  document.getElementById("crawl-url-once").addEventListener("click", () => confirmCrawlUrl(false));
  document.getElementById("crawl-url-add").addEventListener("click", () => confirmCrawlUrl(true));
}

function openCrawlUrlModal(url) {
  state.pendingCrawlUrl = url;
  const existing = findSourceByUrl(url);
  const sameDomain = findSourceByDomain(url);
  const name = guessSiteNameFromUrl(url);

  document.getElementById("crawl-url-preview").textContent = url;
  const hint = document.getElementById("crawl-url-hint");
  const btnAdd = document.getElementById("crawl-url-add");
  const btnOnce = document.getElementById("crawl-url-once");

  if (existing) {
    hint.textContent = `${existing.name} est déjà configuré avec cette URL. Le crawl va utiliser cette source.`;
    btnAdd.style.display = "none";
    btnOnce.textContent = "Lancer le crawl";
    btnOnce.className = "btn btn-primary";
  } else if (sameDomain) {
    hint.textContent = `${sameDomain.name} est déjà dans vos sources. Mettre à jour le lien « ${url} » ou crawler une seule fois ?`;
    btnAdd.textContent = "Mettre à jour et crawler";
    btnAdd.style.display = "";
    btnOnce.textContent = "Crawler une fois";
    btnOnce.className = "btn btn-secondary";
  } else {
    hint.textContent = `Ajouter ${name} à vos sources pour le crawler régulièrement, ou lancer un crawl unique ?`;
    btnAdd.textContent = "Ajouter aux sources et crawler";
    btnAdd.style.display = "";
    btnOnce.textContent = "Crawler une fois";
    btnOnce.className = "btn btn-secondary";
  }

  document.getElementById("crawl-url-modal").classList.add("open");
}

function closeCrawlUrlModal() {
  document.getElementById("crawl-url-modal").classList.remove("open");
  state.pendingCrawlUrl = null;
}

async function confirmCrawlUrl(addToSources) {
  const url = state.pendingCrawlUrl;
  if (!url) return;
  closeCrawlUrlModal();

  const existing = findSourceByUrl(url);
  const label = guessSiteNameFromUrl(url);
  const crawlOpts = { goToLeads: true };

  try {
    if (addToSources && !existing) {
      const result = await api("/sources", {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      await runCrawlJob(`/crawler/scan/${result.source.id}`, crawlBodyExtra(), result.source.name, crawlOpts);
    } else if (existing) {
      await runCrawlJob(`/crawler/scan/${existing.id}`, crawlBodyExtra(), existing.name, crawlOpts);
    } else {
      await runCrawlJob("/crawler/crawl-url", { url, ...crawlBodyExtra() }, label, crawlOpts);
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

function isLikelySearchPageUrl(url) {
  try {
    const u = new URL(url.trim());
    const path = u.pathname.toLowerCase();
    if (/\/(?:recherche|search|categorie|category|resultats|liste)(?:\/|$)/i.test(path)) {
      return !/\d{4,}/.test(path);
    }
    return false;
  } catch {
    return false;
  }
}

function setupProductModes() {
  document.querySelectorAll(".product-mode").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.mode;
      if (mode) switchView(mode);
    });
  });
  document.getElementById("leads-goto-analyze")?.addEventListener("click", () => switchView("analyze"));
}

function syncProductModeTabs(view) {
  const mode = view === "analyze" ? "analyze" : view === "dashboard" ? "dashboard" : null;
  document.querySelectorAll(".product-mode").forEach((btn) => {
    if (!mode) {
      btn.classList.remove("active");
      return;
    }
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  const bar = document.getElementById("product-modes");
  if (bar) {
    bar.hidden = view !== "dashboard" && view !== "analyze";
  }
}

function setAnalyzeUiState(phase) {
  const loading = document.getElementById("analyze-loading");
  const empty = document.getElementById("analyze-empty");
  const result = document.getElementById("analyze-result");
  if (loading) loading.hidden = phase !== "loading";
  if (empty) empty.hidden = phase !== "empty";
  if (result) result.hidden = phase !== "result";
}

/** Import fiche pour analyse — progression dans la vue Score Mandat (sans modal crawl) */
const analyzeImportState = {
  active: false,
  aborted: false,
  jobId: null,
  url: "",
  pollTimer: null,
  lastProgress: 0,
  lastMessage: "",
  networkFails: 0,
  pollErrors: 0,
};

const analyzeFeedState = {
  seenMessages: new Set(),
  seenLogIds: new Set(),
};

let analyzeWaitTimer = null;

const ANALYZE_WAIT_MSGS = [
  "Lecture de l'annonce…",
  "Extraction prix, surface et localisation…",
  "Comparatif DVF et signaux vendeur…",
  "Calcul du Score Mandat™…",
];

function setAnalyzeFormDisabled(disabled) {
  document.getElementById("analyze-url-submit")?.toggleAttribute("disabled", disabled);
  document.getElementById("analyze-url-input")?.toggleAttribute("disabled", disabled);
}

function startAnalyzeWaitAnimation() {
  stopAnalyzeWaitAnimation();
  let i = 0;
  const tick = () => {
    const el = document.getElementById("analyze-loading-msg");
    if (el) el.textContent = ANALYZE_WAIT_MSGS[i % ANALYZE_WAIT_MSGS.length];
    i += 1;
  };
  tick();
  analyzeWaitTimer = setInterval(tick, 2800);
}

function stopAnalyzeWaitAnimation() {
  if (analyzeWaitTimer) {
    clearInterval(analyzeWaitTimer);
    analyzeWaitTimer = null;
  }
}

function resetAnalyzeFeed() {
  analyzeFeedState.seenMessages = new Set();
  analyzeFeedState.seenLogIds = new Set();
  const feed = document.getElementById("analyze-loader-feed");
  if (feed) feed.innerHTML = "";
}

function appendAnalyzeFeedLine(text, type = "step") {
  if (!text || analyzeFeedState.seenMessages.has(text)) return;
  analyzeFeedState.seenMessages.add(text);
  const feed = document.getElementById("analyze-loader-feed");
  if (!feed) return;
  const li = document.createElement("li");
  li.className = `feed-${type} feed-enter`;
  li.innerHTML = `<span>${escapeHtml(text)}</span>`;
  feed.appendChild(li);
  feed.scrollTop = feed.scrollHeight;
}

function ingestAnalyzeJobLogs(logs) {
  if (!Array.isArray(logs)) return;
  for (const log of logs) {
    if (!log.id || analyzeFeedState.seenLogIds.has(log.id)) continue;
    analyzeFeedState.seenLogIds.add(log.id);
    const line = formatCrawlLogLine(log);
    appendAnalyzeFeedLine(line, crawlLogFeedType(log.status));
  }
}

function updateAnalyzeProgressUi(job) {
  const msg = job?.message || "Traitement…";
  analyzeImportState.lastProgress = job?.progress ?? analyzeImportState.lastProgress ?? 0;
  analyzeImportState.lastMessage = msg;
  const main = document.getElementById("analyze-loading-msg");
  if (main) main.textContent = msg;
  const phaseEl = document.getElementById("analyze-loader-phase");
  if (phaseEl) phaseEl.textContent = crawlActivityPhase(msg)[1];
  const progress = Math.min(100, Math.max(0, job?.progress ?? 0));
  const fill = document.getElementById("analyze-loader-fill");
  if (fill) fill.style.width = `${progress}%`;
  const pct = document.getElementById("analyze-loader-pct");
  if (pct) pct.textContent = `${progress}%`;
  if (msg !== analyzeImportState._lastFeedMsg) {
    analyzeImportState._lastFeedMsg = msg;
    appendAnalyzeFeedLine(msg, "step");
  }
  ingestAnalyzeJobLogs(job?.logs);
}

function stopAnalyzeImportPoll() {
  analyzeImportState.active = false;
  analyzeImportState.jobId = null;
  analyzeImportState._lastFeedMsg = "";
  if (analyzeImportState.pollTimer) {
    clearTimeout(analyzeImportState.pollTimer);
    analyzeImportState.pollTimer = null;
  }
  setAnalyzeFormDisabled(false);
}

function cancelAnalyzeImport({ silent = false } = {}) {
  analyzeImportState.aborted = true;
  const jobId = analyzeImportState.jobId;
  stopAnalyzeImportPoll();
  stopAnalyzeWaitAnimation();
  setAnalyzeUiState("empty");
  setAnalyzeFormDisabled(false);
  if (jobId) {
    api("/crawler/jobs/cancel", { method: "POST" }).catch(() => {});
  }
  if (!silent) {
    showToast("Analyse annulée", "info");
  }
}

function runAnalyzeImportPoll(jobId, url) {
  analyzeImportState.active = true;
  analyzeImportState.aborted = false;
  analyzeImportState.jobId = jobId;
  analyzeImportState.url = url;
  analyzeImportState.networkFails = 0;
  analyzeImportState.pollErrors = 0;
  analyzeImportState.lastProgress = 5;
  setAnalyzeUiState("loading");
  setAnalyzeFormDisabled(true);
  resetAnalyzeFeed();
  updateAnalyzeProgressUi({ progress: 5, message: "Connexion au portail — extraction de la fiche…" });

  const tick = async () => {
    if (!analyzeImportState.active || analyzeImportState.aborted) return;
    try {
      const job = await api(`/crawler/jobs/${jobId}?lite=1&logs=1`);
      updateAnalyzeProgressUi(job);
      analyzeImportState.networkFails = 0;
      analyzeImportState.pollErrors = 0;

      if (job.status === "completed" || job.status === "failed") {
        stopAnalyzeImportPoll();
        if (job.status === "failed") {
          setAnalyzeUiState("empty");
          const errMsg =
            job.errors?.[0]?.message || job.message || "Import de la fiche échoué";
          showToast(errMsg, "error", 8000);
          return;
        }
        await completeOnDemandAnalysisAfterImport(url);
        return;
      }
      analyzeImportState.pollTimer = setTimeout(tick, 600);
    } catch (err) {
      if (analyzeImportState.aborted) return;
      if (isNetworkFetchError(err) || err.message?.includes("Connexion perdue")) {
        analyzeImportState.networkFails += 1;
        updateAnalyzeProgressUi({
          progress: analyzeImportState.lastProgress,
          message: `Connexion interrompue (${analyzeImportState.networkFails}/40) — import serveur actif…`,
        });
        if (analyzeImportState.networkFails >= 40) {
          stopAnalyzeImportPoll();
          setAnalyzeUiState("empty");
          showToast("Connexion perdue — relancez l'analyse", "error");
          return;
        }
        analyzeImportState.pollTimer = setTimeout(tick, 1500);
        return;
      }
      if (/Erreur serveur|50\d|État du crawl/i.test(err.message || "")) {
        analyzeImportState.pollErrors += 1;
        updateAnalyzeProgressUi({
          progress: analyzeImportState.lastProgress,
          message: `Synchronisation (${analyzeImportState.pollErrors}/15) — import en cours sur le serveur…`,
        });
        if (analyzeImportState.pollErrors >= 15) {
          stopAnalyzeImportPoll();
          setAnalyzeUiState("empty");
          showToast(err.message || "Suivi interrompu — relancez l'analyse", "error");
          return;
        }
        analyzeImportState.pollTimer = setTimeout(tick, 2000);
        return;
      }
      stopAnalyzeImportPoll();
      setAnalyzeUiState("empty");
      showToast(err.message, "error");
    }
  };

  analyzeImportState.pollTimer = setTimeout(tick, 400);
}

function renderOnDemandAnalysis(analysis, lead) {
  if (!analysis) {
    setAnalyzeUiState("empty");
    return;
  }
  setAnalyzeUiState("result");
  state.onDemandAnalysis = { analysis, lead };

  const score = analysis.mandate_score ?? 0;
  const prob = signatureProbability(analysis);
  const scoreEl = document.getElementById("analyze-score-display");
  if (scoreEl) {
    scoreEl.innerHTML = `${prob}<span class="analyze-score-max">% signature</span>`;
    scoreEl.className = `analyze-score-value ${signatureClass(prob)}`;
    scoreEl.title = `Score mandat ${score}/100`;
  }
  const addr = document.getElementById("analyze-score-address");
  if (addr) {
    addr.textContent = analysis.address || analysis.owner || "Annonce analysée";
  }
  const meta = document.getElementById("analyze-score-meta");
  if (meta) {
    const bits = [
      analysis.portal,
      analysis.price_label,
      analysis.mandate_score_reason,
    ].filter(Boolean);
    meta.textContent = bits.join(" · ");
  }

  const ai = analysis.ai_analysis || {};
  const aiSub = document.getElementById("analyze-ai-subtitle");
  const aiBody = document.getElementById("analyze-ai-body");
  const aiDisclaimer = document.getElementById("analyze-ai-disclaimer");
  const aiCard = document.getElementById("analyze-ai-card");
  if (aiSub) aiSub.textContent = ai.subtitle || "Synthèse contextualisée";
  if (aiBody) {
    const paras = ai.paragraphs || [];
    if (!paras.length) {
      aiBody.innerHTML =
        '<p class="analyze-ai-p analyze-ai-p-muted">Analyse en cours de construction — relancez après import complet de la fiche.</p>';
    } else {
      aiBody.innerHTML = paras
        .map((p) => `<p class="analyze-ai-p">${escapeHtml(p)}</p>`)
        .join("");
    }
  }
  if (aiDisclaimer) {
    aiDisclaimer.textContent = ai.disclaimer || "";
    aiDisclaimer.hidden = !ai.disclaimer;
  }
  if (aiCard) aiCard.hidden = false;

  const factorsEl = document.getElementById("analyze-factors");
  const factors = analysis.positive_factors || [];
  if (factorsEl) {
    if (!factors.length) {
      factorsEl.innerHTML =
        '<li class="analyze-factor analyze-factor-muted"><span>Peu de signaux détectés — complétez la fiche ou relancez l’analyse.</span></li>';
    } else {
      factorsEl.innerHTML = factors
        .map(
          (f) => `
        <li class="analyze-factor">
          <span class="analyze-factor-check" aria-hidden="true">✓</span>
          <div>
            <strong>${escapeHtml(f.label)}</strong>
            <span>${escapeHtml(f.detail || "")}</span>
          </div>
        </li>`,
        )
        .join("");
    }
  }

  const reco = analysis.recommendation || {};
  const recoLabel = document.getElementById("analyze-reco-label");
  const recoDetail = document.getElementById("analyze-reco-detail");
  if (recoLabel) recoLabel.textContent = reco.label || mandateCallRecommendation(score);
  if (recoDetail) recoDetail.textContent = reco.detail || analysis.scenario_label || "";

  const openBtn = document.getElementById("analyze-open-lead");
  const dvfBtn = document.getElementById("analyze-compare-dvf");
  const lid = lead?.id || analysis.lead_id;
  if (openBtn) {
    openBtn.disabled = !lid;
    openBtn.onclick = () => {
      if (lid) openDrawer(lid);
    };
  }
  if (dvfBtn) {
    dvfBtn.disabled = !lid;
    dvfBtn.onclick = () => {
      if (lid) compareLeadDvf(lid);
    };
  }
}

function isMissingApiRouteError(err) {
  const msg = err?.message || "";
  return /Route API introuvable|Ressource introuvable|404|Not Found/i.test(msg);
}

function findLeadByListingUrl(url) {
  const key = normalizeUrlKey(url);
  if (!key) return null;
  return (
    LEADS.find((l) => normalizeUrlKey(l.source_url || "") === key) || null
  );
}

async function fetchOnDemandAnalysisFallback(url) {
  const normalized = url.trim();
  const existing = findLeadByListingUrl(normalized);
  if (existing?.id) {
    const res = await api(`/radar/leads/${existing.id}/analysis`);
    return {
      status: "ready",
      analysis: res.analysis,
      lead: res.lead || existing,
    };
  }
  const importRes = await api("/crawler/import-listing", {
    method: "POST",
    body: JSON.stringify({ url: normalized }),
  });
  const jobId = importRes.job_id || importRes.job?.id;
  if (!jobId) {
    throw new Error("Import de la fiche impossible — relancez python app.py");
  }
  return { status: "importing", job_id: jobId, url: normalized };
}

async function fetchOnDemandAnalysis(url) {
  const body = JSON.stringify({ url: url.trim() });
  const paths = ["/radar/analyze-url", "/crawler/analyze-listing"];
  let lastErr;
  for (const path of paths) {
    try {
      return await api(path, { method: "POST", body });
    } catch (err) {
      lastErr = err;
      if (!isMissingApiRouteError(err)) throw err;
    }
  }
  try {
    return await fetchOnDemandAnalysisFallback(url);
  } catch (err) {
    if (lastErr && isMissingApiRouteError(lastErr)) {
      throw new Error(
        "Mode 2 indisponible — Ctrl+C dans le terminal, puis python app.py ou demarrer.bat (api_version 7 + radar_analyze_url).",
      );
    }
    throw err;
  }
}

async function runOnDemandAnalysis(url, { skipViewSwitch } = {}) {
  if (!url) return;
  if (analyzeImportState.active) {
    showToast("Analyse déjà en cours…", "warning");
    return;
  }
  const normalized = url.trim();
  if (!isUrl(normalized)) {
    showToast("Collez un lien http(s) valide vers une fiche annonce", "error");
    return;
  }
  if (isLikelySearchPageUrl(normalized)) {
    showToast(
      "Collez le lien direct de la fiche (pas une page de recherche)",
      "warning",
      7000,
    );
    return;
  }
  if (!skipViewSwitch) switchView("analyze");
  const input = document.getElementById("analyze-url-input");
  if (input) input.value = normalized;
  analyzeImportState.aborted = false;
  setAnalyzeUiState("loading");
  resetAnalyzeFeed();
  setAnalyzeFormDisabled(true);
  startAnalyzeWaitAnimation();

  try {
    const res = await fetchOnDemandAnalysis(normalized);
    if (analyzeImportState.aborted) return;
    if (res.status === "importing" && res.job_id) {
      stopAnalyzeWaitAnimation();
      runAnalyzeImportPoll(res.job_id, normalized);
      return;
    }
    if (res.status === "ready" && res.analysis) {
      if (res.lead?.id) {
        const idx = LEADS.findIndex((l) => l.id === res.lead.id);
        if (idx >= 0) LEADS[idx] = res.lead;
        else LEADS.unshift(res.lead);
      }
      renderOnDemandAnalysis(res.analysis, res.lead);
      showToast(`${signatureProbability(res.analysis)} % de chance de signer le mandat`, "success", 5000);
      return;
    }
    setAnalyzeUiState("empty");
    showToast("Analyse indisponible — vérifiez l'URL", "warning");
  } catch (err) {
    setAnalyzeUiState("empty");
    showToast(err.message, "error");
  } finally {
    stopAnalyzeWaitAnimation();
    if (!analyzeImportState.active) {
      setAnalyzeFormDisabled(false);
    }
  }
}

async function completeOnDemandAnalysisAfterImport(url) {
  setAnalyzeUiState("loading");
  setAnalyzeFormDisabled(true);
  stopAnalyzeWaitAnimation();
  resetAnalyzeFeed();
  updateAnalyzeProgressUi({
    progress: 92,
    message: "Calcul du Score Mandat™ et comparatif DVF…",
  });
  try {
    await refreshAppData();
    if (analyzeImportState.aborted) return;
    const res = await fetchOnDemandAnalysis(url);
    if (analyzeImportState.aborted) return;
    if (res.status === "ready" && res.analysis) {
      renderOnDemandAnalysis(res.analysis, res.lead);
      showToast(`${signatureProbability(res.analysis)} % de chance de signer le mandat`, "success", 6000);
    } else {
      setAnalyzeUiState("empty");
      showToast("Fiche importée — relancez l'analyse si le score n'apparaît pas", "warning");
    }
  } catch (err) {
    setAnalyzeUiState("empty");
    showToast(err.message, "error");
  } finally {
    setAnalyzeFormDisabled(false);
  }
}

function setupCityAutocompletes() {
  if (typeof setupFrenchCityAutocomplete !== "function") return;
  setupFrenchCityAutocomplete(document.getElementById("radar-target-cities"), {
    multi: true,
  });
  setupFrenchCityAutocomplete(document.getElementById("client-cities"), { multi: true });
}

function setupAnalyzeForm() {
  const form = document.getElementById("analyze-url-form");
  if (!form) return;
  document.getElementById("analyze-cancel-btn")?.addEventListener("click", () => {
    cancelAnalyzeImport();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("analyze-url-input");
    await runOnDemandAnalysis(input?.value?.trim() || "");
  });
}

async function importListingUrl(url) {
  await runOnDemandAnalysis(url);
}

async function crawlCustomUrl(url) {
  if (!url || state.loading) return;
  await runOnDemandAnalysis(url);
}

function setupFilters() {
  document.querySelectorAll(".filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      state.leadsFilter = chip.dataset.filter;
      renderLeads();
    });
  });
}

function setupViewToggle() {
  document.querySelectorAll(".view-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".view-toggle button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.leadsView = btn.dataset.view;
      renderLeads();
    });
  });
}

function setupLeadsActions() {
  document.getElementById("leads-delete-all-btn")?.addEventListener("click", async () => {
    const count = LEADS.length;
    if (!count) {
      showToast("Aucun prospect à supprimer", "warning");
      return;
    }
    if (
      !confirm(
        `Supprimer les ${count} prospect(s) ?\n\nCette action est irréversible.`,
      )
    ) {
      return;
    }
    try {
      const result = await deleteAllLeadsApi();
      LEADS = result.leads;
      closeDrawer();
      await refreshAppData();
      showToast(`${result.deleted} prospect(s) supprimé(s)`, "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });

  document.getElementById("drawer-delete-lead-btn")?.addEventListener("click", async () => {
    const lead = state.selectedLead;
    if (!lead) return;
    await deleteLeadById(lead.id, lead.owner);
  });

  document.getElementById("leads-dvf-compare-all")?.addEventListener("click", () => {
    compareAllLeadsDvf().catch((err) => showToast(err.message, "error"));
  });

  const leadsView = document.getElementById("view-leads");
  if (leadsView) {
    leadsView.addEventListener("click", (e) => {
      if (e.target.closest(".lead-refresh-btn, #drawer-refresh-lead-btn")) return;
      const btn = e.target.closest(".lead-delete-btn");
      if (!btn) return;
      e.stopPropagation();
      e.preventDefault();
      deleteLeadById(parseInt(btn.dataset.id, 10), btn.dataset.name);
    });
  }
}

const drawerHtmlCache = new Map();
const DRAWER_CACHE_MAX = 48;

function drawerFingerprint(lead) {
  return [
    lead.id,
    lead.updated_at || "",
    lead.image_updated_at || "",
    lead.mandate_score || 0,
    lead.price || 0,
    lead.surface || "",
    lead.pipeline || "",
    lead.dvf_verdict || "",
    lead.dvf_compared_at || "",
    lead.has_image ? 1 : 0,
    state.drawerShowAllFields ? 1 : 0,
    state.drawerEditExpanded ? 1 : 0,
  ].join("|");
}

function prefetchDrawerHtml(lead) {
  if (!lead) return;
  const key = drawerFingerprint(lead);
  if (drawerHtmlCache.has(key)) return;
  drawerHtmlCacheSet(key, buildDrawerBodyHtml(lead));
}

function scheduleDrawerCacheWarm() {
  if (!LEADS?.length) return;
  const run = () => warmDrawerCache(10);
  if (typeof requestIdleCallback === "function") {
    requestIdleCallback(run, { timeout: 4000 });
  } else {
    setTimeout(run, 800);
  }
}

function warmDrawerCache(limit = 32) {
  const top = [...LEADS]
    .sort((a, b) => (b.mandate_score || 0) - (a.mandate_score || 0))
    .slice(0, limit);
  let i = 0;
  const step = () => {
    while (i < top.length && i < 6) {
      prefetchDrawerHtml(top[i++]);
    }
    if (i < top.length) {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(step, { timeout: 2000 });
      } else {
        setTimeout(step, 16);
      }
    }
  };
  step();
}

let drawerPrefetchTimer = null;
let drawerPrefetchLeadId = null;

function scheduleDrawerPrefetch(leadId) {
  if (!leadId || drawerPrefetchLeadId === leadId) return;
  drawerPrefetchLeadId = leadId;
  clearTimeout(drawerPrefetchTimer);
  drawerPrefetchTimer = setTimeout(() => {
    const lead = LEADS.find((l) => l.id === leadId);
    prefetchDrawerHtml(lead);
  }, 50);
}

function setupDrawerPrefetch() {
  const onOver = (e) => {
    const el = e.target.closest(
      "tr[data-id], .lead-card[data-id], .pipeline-card[data-id], .radar-priority-row[data-id], .crm-hero-featured-inner[data-id], #dashboard-top-leads tr[data-id]",
    );
    if (!el?.dataset?.id) return;
    scheduleDrawerPrefetch(parseInt(el.dataset.id, 10));
  };
  document.getElementById("view-leads")?.addEventListener("mouseover", onOver);
  document.getElementById("view-pipeline")?.addEventListener("mouseover", onOver);
  document.getElementById("view-dashboard")?.addEventListener("mouseover", onOver);
}

function drawerHtmlCacheSet(key, html) {
  if (drawerHtmlCache.size >= DRAWER_CACHE_MAX) {
    const first = drawerHtmlCache.keys().next().value;
    drawerHtmlCache.delete(first);
  }
  drawerHtmlCache.set(key, html);
}

function invalidateDrawerCache(leadId) {
  const prefix = `${leadId}|`;
  for (const key of [...drawerHtmlCache.keys()]) {
    if (key.startsWith(prefix)) drawerHtmlCache.delete(key);
  }
}

function setupDrawer() {
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer-overlay").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeDrawer();
      closeCrawlUrlModal();
      closeAddSourceModal();
    }
  });

  const drawer = document.getElementById("lead-drawer");
  drawer?.addEventListener("click", (e) => {
    const lead = state.selectedLead;
    if (!lead) return;

    const pipeBtn = e.target.closest("#drawer-pipeline-btns [data-pipeline]");
    if (pipeBtn) {
      e.preventDefault();
      patchLeadPipeline(lead.id, pipeBtn.dataset.pipeline).catch((err) =>
        showToast(err.message, "error"),
      );
      return;
    }

    if (e.target.closest("#drawer-dvf-compare-btn")) {
      e.preventDefault();
      compareLeadDvf(lead.id).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#drawer-open-estimator-btn")) {
      e.preventDefault();
      openEstimateurTab(lead.id);
      return;
    }
    if (e.target.closest("#drawer-call-script-btn")) {
      e.preventDefault();
      loadScriptForLead(lead).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#drawer-mandate-vente")) {
      e.preventDefault();
      openMandateFromLead(lead, "vente");
      return;
    }
    if (e.target.closest("#drawer-mandate-location")) {
      e.preventDefault();
      openMandateFromLead(lead, "location");
      return;
    }
    if (e.target.closest("#drawer-journey-mandat")) {
      e.preventDefault();
      openMandateFromLead(lead, lead.transaction_type === "location" ? "location" : "vente");
      return;
    }
    if (e.target.closest("#drawer-journey-livret")) {
      e.preventDefault();
      openMandateLivretFromLead(lead);
      return;
    }
    if (e.target.closest("#drawer-journey-call")) {
      e.preventDefault();
      const tel = (lead.phone || "").replace(/\s/g, "");
      if (tel && tel !== "—") window.location.href = `tel:${tel}`;
      else showToast("Pas de numéro de téléphone sur cette fiche", "warning");
      return;
    }
    if (e.target.closest("#drawer-journey-script")) {
      e.preventDefault();
      loadScriptForLead(lead).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#drawer-image-revert")) {
      e.preventDefault();
      revertDrawerLeadImage(lead).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#drawer-image-sync")) {
      e.preventDefault();
      syncDrawerLeadImage(lead).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#drawer-expand-edit")) {
      e.preventDefault();
      state.drawerEditExpanded = true;
      invalidateDrawerCache(lead.id);
      refreshDrawerBodyContent(lead);
      return;
    }
    if (e.target.closest("#drawer-toggle-all-fields")) {
      e.preventDefault();
      state.drawerShowAllFields = !state.drawerShowAllFields;
      invalidateDrawerCache(lead.id);
      refreshDrawerBodyContent(lead);
      return;
    }
    if (e.target.closest("#drawer-save-fields-btn")) {
      e.preventDefault();
      saveDrawerLeadFields(lead.id).catch((err) => showToast(err.message, "error"));
    }
  });

  drawer?.addEventListener("change", (e) => {
    const lead = state.selectedLead;
    if (!lead) return;
    if (e.target.id === "drawer-image-upload") {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (file) uploadDrawerLeadImage(lead, file).catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.id === "drawer-field-type") {
      const current = LEADS.find((l) => l.id === lead.id) || lead;
      const merged = { ...current, type: e.target.value };
      const idx = LEADS.findIndex((l) => l.id === lead.id);
      if (idx >= 0) LEADS[idx] = merged;
      state.selectedLead = merged;
      invalidateDrawerCache(lead.id);
      refreshDrawerBodyContent(merged);
    }
  });
}

function getSourcesGridRoots() {
  return [
    document.getElementById("sources-grid-reliable"),
    document.getElementById("sources-grid-antibot"),
    document.getElementById("sources-grid-custom"),
  ].filter(Boolean);
}

function setupCrawler() {
  const sourcesView = document.getElementById("view-crawler");
  document.getElementById("crawl-city-open-territory")?.addEventListener("click", () => {
    document.getElementById("radar-settings-btn")?.click();
  });
  document.getElementById("crawl-city-open-profile")?.addEventListener("click", () => {
    document.getElementById("btn-agency-legal-profile")?.click();
  });
  document.getElementById("crawler-toggle").addEventListener("click", toggleCrawler);
  document.getElementById("crawler-scan-btn").addEventListener("click", runManualScan);
  document.getElementById("crawler-all-btn").addEventListener("click", runManualScan);
  document.getElementById("add-source-btn").addEventListener("click", openAddSourceModal);
  document.getElementById("add-source-close").addEventListener("click", closeAddSourceModal);
  document.getElementById("add-source-cancel").addEventListener("click", closeAddSourceModal);
  document.getElementById("add-source-modal").addEventListener("click", (e) => {
    if (e.target.id === "add-source-modal") closeAddSourceModal();
  });
  document.getElementById("add-source-form").addEventListener("submit", submitAddSource);

  sourcesView?.addEventListener("change", async (e) => {
    if (!e.target.closest(".sources-grid")) return;
    if (!e.target.matches(".toggle-switch input")) return;
    const sourceId = e.target.dataset.source;
    try {
      await api(`/sources/${encodeURIComponent(sourceId)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: e.target.checked }),
      });
      await loadData();
      renderCrawler();
      showToast(`Source ${e.target.checked ? "activée" : "désactivée"}`);
    } catch (err) {
      showToast(err.message);
    }
  });

  sourcesView?.addEventListener("input", (e) => {
    if (!e.target.matches(".source-url-input")) return;
    markSourceUrlDirty(e.target.dataset.source, e.target);
  });

  sourcesView?.addEventListener("keydown", async (e) => {
    if (!e.target.matches(".source-url-input")) return;
    if (e.key !== "Enter" || !e.target.matches(".source-url-input")) return;
    e.preventDefault();
    const sourceId = e.target.dataset.source;
    try {
      await saveSourceUrlFromInput(sourceId, e.target);
    } catch {
      /* toast déjà affiché */
    }
  });

  sourcesView?.addEventListener(
    "focusout",
    async (e) => {
      if (!e.target.matches(".source-url-input")) return;
      const related = e.relatedTarget;
      if (
        related?.closest?.(
          ".source-save-url-btn, .source-crawl-btn, .source-delete-btn, .toggle-switch",
        )
      ) {
        return;
      }
      const sourceId = e.target.dataset.source;
      if (!sourceUrlDirty.has(sourceId)) return;
      try {
        await saveSourceUrlFromInput(sourceId, e.target, { quiet: true });
        showToast("Lien enregistré", "success", 2500);
      } catch {
        /* toast déjà affiché */
      }
    },
    true,
  );

  sourcesView?.addEventListener("click", async (e) => {
    if (!e.target.closest(".sources-grid")) return;
    const delBtn = e.target.closest(".source-delete-btn");
    if (delBtn) {
      const { source: sourceId, name } = delBtn.dataset;
      if (!confirm(`Supprimer la source « ${name} » ?\nLes prospects déjà trouvés restent dans la base.`)) return;
      try {
        const result = await deleteSourceApi(sourceId);
        SOURCES = result.sources;
        await refreshAppData();
        showToast(`${name} supprimée`, "success");
      } catch (err) {
        await loadData().catch(() => {});
        renderCrawler();
        showToast(err.message, "error");
      }
      return;
    }

    const saveBtn = e.target.closest(".source-save-url-btn");
    if (saveBtn) {
      e.preventDefault();
      const card = saveBtn.closest(".source-card");
      const input = card?.querySelector(".source-url-input");
      try {
        await saveSourceUrlFromInput(saveBtn.dataset.source, input);
      } catch {
        /* toast déjà affiché */
      }
      return;
    }

    const btn = e.target.closest(".source-crawl-btn");
    if (!btn || crawlState.active) return;
    await crawlSingleSource(btn.dataset.source, btn.dataset.name);
  });
}

function openAddSourceModal() {
  document.getElementById("add-source-modal").classList.add("open");
  document.getElementById("add-source-name").focus();
}

function closeAddSourceModal() {
  document.getElementById("add-source-modal").classList.remove("open");
  document.getElementById("add-source-form").reset();
}

async function submitAddSource(e) {
  e.preventDefault();
  const url = document.getElementById("add-source-url").value.trim();
  const name = document.getElementById("add-source-name").value.trim();

  if (!url) {
    showToast("Collez un lien, ex. https://www.paruvendu.fr/immobilier/", "warning");
    return;
  }

  try {
    const result = await api("/sources", {
      method: "POST",
      body: JSON.stringify({ url, name: name || undefined }),
    });
    SOURCES = result.sources;
    closeAddSourceModal();
    renderCrawler();
    showToast(`${result.source.name} ajouté — ${result.source.search_url}`, "success");
    await refreshOnboardingUi();
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function crawlSingleSource(sourceId, sourceName) {
  const src = SOURCES.find((s) => s.id === sourceId);
  if (src?.is_antibot) {
    showToast(
      `${sourceName} est protégé (anti-bot) — crawl bientôt disponible (pas encore activé).`,
      "warning",
      6000,
    );
    return;
  }
  const city = getCrawlCity();
  const label = city ? `${sourceName} — ${city}` : sourceName;
  await runCrawlJob(`/crawler/scan/${sourceId}`, crawlBodyExtra(), label);
}

async function runManualScan() {
  const city = getCrawlCity();
  let count = countRecommendedCrawlSources();
  if (!count) {
    try {
      const fresh = await api("/sources");
      if (Array.isArray(fresh)) SOURCES = fresh;
      count = countRecommendedCrawlSources();
    } catch {
      /* garde le label sans (0) */
    }
  }
  const countLabel = count > 0 ? String(count) : "…";
  const label = city
    ? `Portails recommandés (${countLabel}) — ${city}`
    : `Portails recommandés (${countLabel})`;
  await runCrawlJob("/crawler/scan", crawlBodyExtra(), label);
}

const crawlFeedState = {
  seenMessages: new Set(),
  seenLogIds: new Set(),
};

const CRAWL_WAIT_TIPS = [
  "On parcourt les annonces comme un bon agent de quartier…",
  "Vérification des surfaces, prix et dates de publication…",
  "On distingue les particuliers des agences pour vous…",
  "Pas de mélange : chaque fiche = une vraie annonce…",
  "On ignore les prix au m² pour ne garder que le bon montant…",
  "Comme une visite virtuelle, mais à l'échelle du portail…",
  "Les bonnes opportunités se cachent — on les débusque…",
  "On croise titre, m² et prix avant d'enregistrer…",
  "Patience : un crawl sérieux vaut mieux qu'une liste brouillonne…",
  "Repérage des mandats potentiels en cours…",
  "On lit les annonces plus vite qu'un acquéreur le dimanche matin…",
  "Chaque portail a ses secrets — on les connaît…",
  "On vérifie le téléphone et le type d'annonceur…",
  "Presque là : on peaufine les dernières fiches…",
  "Le marché ne dort jamais — nous non plus, le temps du scan…",
];

function pickCrawlWaitTip() {
  const idx = Math.floor(Math.random() * CRAWL_WAIT_TIPS.length);
  return CRAWL_WAIT_TIPS[idx];
}

function setCrawlWaitTip(text) {
  const tip = text || pickCrawlWaitTip();
  ["crawl-loader-tip", "crawl-dock-tip"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.textContent === tip) return;
    el.classList.add("tip-fade");
    setTimeout(() => {
      el.textContent = tip;
      el.classList.remove("tip-fade");
    }, 280);
  });
}

function startCrawlWaitAnimation() {
  stopCrawlWaitAnimation();
  setCrawlWaitTip(CRAWL_WAIT_TIPS[0]);
  crawlState.tipIndex = 0;
  crawlState.tipTimer = setInterval(() => {
    crawlState.tipIndex = (crawlState.tipIndex + 1) % CRAWL_WAIT_TIPS.length;
    setCrawlWaitTip(CRAWL_WAIT_TIPS[crawlState.tipIndex]);
  }, 4500);
}

function stopCrawlWaitAnimation() {
  if (crawlState.tipTimer) {
    clearInterval(crawlState.tipTimer);
    crawlState.tipTimer = null;
  }
}

function resetCrawlFeed() {
  crawlFeedState.seenMessages = new Set();
  crawlFeedState.seenLogIds = new Set();
  const feed = document.getElementById("crawl-loader-feed");
  if (feed) feed.innerHTML = "";
}

function formatCrawlLogLine(log) {
  const msg = log.message || "";
  switch (log.status) {
    case "ok":
      return `Prospect enregistré — ${msg}`;
    case "duplicate":
      return `Doublon — ${msg}`;
    case "incomplete":
      return `Annonce incomplète — ${msg}`;
    case "error":
      return `Erreur — ${msg}`;
    case "completed":
      return msg;
    case "updated":
      return `Mis à jour — ${msg}`;
    case "verify_failed":
      return `Vérification refusée — ${msg}`;
    case "withdrawn":
      return `Retiré du radar — ${msg}`;
    case "rejected":
      return msg.startsWith("Annonce") ? msg : `Rejeté — ${msg}`;
    default:
      return msg;
  }
}

function crawlLogFeedType(status) {
  if (status === "ok" || status === "updated") return "ok";
  if (
    status === "verify_failed" ||
    status === "rejected" ||
    status === "withdrawn" ||
    status === "skip_url"
  ) {
    return "warn";
  }
  if (status === "duplicate" || status === "incomplete") return "warn";
  if (status === "error") return "error";
  return "step";
}

function setLeadsLiveIndicator(on) {
  const el = document.getElementById("leads-live-indicator");
  if (el) el.hidden = !on;
}

function scheduleLeadsRefreshDuringCrawl(job) {
  if (crawlState.leadsRefreshTimer) return;
  crawlState.leadsRefreshTimer = setTimeout(async () => {
    crawlState.leadsRefreshTimer = null;
    await refreshLeadsDuringCrawl(job);
  }, CRAWL_LEADS_REFRESH_MS);
}

async function refreshLeadsDuringCrawl(job) {
  if (crawlState.pagePollPaused) return;
  try {
    crawlState.lastJob = job || crawlState.lastJob;
    const prevCount = LEADS.length;
    const prevFp = leadsDataFingerprint(LEADS);
    const leads = await api("/leads", { timeoutMs: CRAWL_JOB_POLL_TIMEOUT_MS });
    const fp = leadsDataFingerprint(leads);
    const changed = fp !== prevFp || leads.length !== prevCount;

    LEADS = leads;
    crawlState.lastLeadCount = leads.length;
    syncRadarFromLeads(leads);
    updateSourceCardsLive(crawlState.lastJob);
    updateCrawlerSummary();
    await refreshStats();
    updateSidebarCount();
    updateBadges();

    if (state.currentView === "dashboard") {
      renderRadarBriefing();
      renderDashboardTopLeads();
      renderStats();
    } else if (state.currentView === "playbook") {
      renderRadarBriefing();
      renderPlaybook();
    } else if (state.currentView === "leads") {
      renderLeads();
      if (changed) {
        document.getElementById("leads-table-wrapper")?.classList.add("leads-live-flash");
        setTimeout(
          () => document.getElementById("leads-table-wrapper")?.classList.remove("leads-live-flash"),
          600,
        );
      }
    } else if (state.currentView === "pipeline") {
      renderPipeline();
    }

    if (!changed) return;

    const added = Math.max(0, leads.length - prevCount);
    renderActivity();

    if (added > 0) {
      appendCrawlFeedLine(
        `+${added} prospect(s) ajouté(s) — total ${leads.length}`,
        "ok",
      );
      if (state.currentView !== "leads" && added > 0) {
        showToast(`${added} nouveau(x) prospect(s) — voir Prospects`, "success", 3500);
      }
    }

    if (job) {
      crawlState.lastSavedCount = job.leads_saved || 0;
      crawlState.lastFoundCount = job.leads_found || 0;
    }
  } catch {
    /* ignore transient errors during crawl */
  }
}

function appendCrawlFeedLine(text, type = "step") {
  if (!text || crawlFeedState.seenMessages.has(text)) return;
  crawlFeedState.seenMessages.add(text);

  const feed = document.getElementById("crawl-loader-feed");
  if (!feed) return;

  const li = document.createElement("li");
  li.className = `feed-${type} feed-enter`;
  li.innerHTML = `<span class="feed-dot" aria-hidden="true"></span><span>${escapeHtml(text)}</span>`;
  feed.appendChild(li);
  feed.scrollTop = feed.scrollHeight;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const LEAD_ICON_EXTERNAL = `<svg class="btn-icon" xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3"/></svg>`;
const LEAD_ICON_REFRESH = `<svg class="btn-icon" xmlns="http://www.w3.org/2000/svg" width="15" height="15" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>`;

function getLeadListingLinkHtml(lead, label = "Voir") {
  const url = (lead.source_url || "").trim();
  if (!url) {
    return `<span class="btn btn-view-listing btn-sm is-disabled" title="URL d'annonce indisponible">${LEAD_ICON_EXTERNAL}<span>${label}</span></span>`;
  }
  return `<a href="${escapeHtml(url)}" class="btn btn-view-listing btn-sm" target="_blank" rel="noopener noreferrer" title="Ouvrir l'annonce sur le portail" onclick="event.stopPropagation()">${LEAD_ICON_EXTERNAL}<span>${label}</span></a>`;
}

function getLeadRefreshButtonHtml(lead) {
  const url = (lead.source_url || "").trim();
  if (!url) return "";
  return `<button type="button" class="btn btn-recrawl btn-sm lead-refresh-btn" data-id="${lead.id}" title="Recrawler l'annonce sur le portail" onclick="event.stopPropagation()">${LEAD_ICON_REFRESH}<span>Recrawler</span></button>`;
}

function getLeadActionsHtml(lead) {
  const view = getLeadListingLinkHtml(lead);
  const refresh = getLeadRefreshButtonHtml(lead);
  if (!refresh && view.includes("is-disabled")) {
    return `<div class="lead-actions-group">${view}</div>`;
  }
  return `<div class="lead-actions-group">${view}${refresh}</div>`;
}

const DRAWER_EDIT_FIELD_DEFS = [
  { key: "first_name", label: "Prénom", type: "text", get: (l) => l.first_name || "" },
  { key: "last_name", label: "Nom", type: "text", get: (l) => l.last_name || "" },
  { key: "phone", label: "Téléphone", type: "tel", get: (l) => (l.phone && l.phone !== "—" ? l.phone : "") },
  { key: "email", label: "Email", type: "email", get: (l) => (l.email && l.email !== "—" ? l.email : "") },
  { key: "address", label: "Adresse", type: "text", get: (l) => (l.address && l.address !== "—" ? l.address : "") },
  { key: "city", label: "Ville", type: "text", get: (l) => l.city || "" },
  { key: "postcode", label: "Code postal", type: "text", get: (l) => l.postcode || "" },
  { key: "surface", label: "Surface (m²)", type: "number", get: (l) => (l.surface != null ? l.surface : "") },
  { key: "price", label: "Prix (€)", type: "number", get: (l) => (l.price ? l.price : "") },
  { key: "type", label: "Type annonceur", type: "select", options: ["particulier", "agence"], get: (l) => l.type || "particulier" },
  { key: "agency", label: "Nom agence", type: "text", get: (l) => l.agency || "", when: (l) => l.type === "agence" },
  { key: "source_url", label: "Lien annonce", type: "url", get: (l) => l.source_url || "" },
  { key: "notes", label: "Notes", type: "textarea", get: (l) => l.notes || "" },
];

function isDrawerFieldEmpty(lead, key) {
  const missing = new Set(lead.missing_fields || []);
  if (missing.has(key)) return true;
  switch (key) {
    case "phone":
    case "email":
    case "address":
      return !lead[key] || lead[key] === "—";
    case "surface":
      return lead.surface == null || lead.surface <= 0;
    case "price":
      return !lead.price;
    case "first_name":
    case "last_name":
      return !(lead[key] || "").trim();
    case "source_url":
      return !(lead.source_url || "").trim();
    default:
      return !(lead[key] || "").toString().trim();
  }
}

function renderDrawerEditFieldInput(def, lead) {
  const val = def.get(lead);
  const missing = isDrawerFieldEmpty(lead, def.key);
  const cls = `drawer-edit-input${missing ? " is-missing" : ""}`;
  if (def.type === "select") {
    const opts = (def.options || []).map(
      (o) => `<option value="${o}"${val === o ? " selected" : ""}>${o === "agence" ? "Agence" : "Particulier"}</option>`,
    );
    return `<select class="${cls}" data-field="${def.key}" id="drawer-field-${def.key}">${opts.join("")}</select>`;
  }
  if (def.type === "textarea") {
    return `<textarea class="${cls}" data-field="${def.key}" id="drawer-field-${def.key}" rows="2" placeholder="${missing ? "À compléter" : ""}">${escapeHtml(val)}</textarea>`;
  }
  return `<input class="${cls}" type="${def.type}" data-field="${def.key}" id="drawer-field-${def.key}" value="${escapeAttr(String(val))}" placeholder="${missing ? "À compléter" : ""}">`;
}

function renderDrawerEditSection(lead) {
  const defs = DRAWER_EDIT_FIELD_DEFS.filter((d) => !d.when || d.when(lead));
  const missingCount = defs.filter((d) => isDrawerFieldEmpty(lead, d.key)).length;

  if (!state.drawerEditExpanded) {
    const label =
      missingCount > 0
        ? `Compléter la fiche (${missingCount} champ${missingCount > 1 ? "s" : ""} manquant${missingCount > 1 ? "s" : ""})`
        : "Modifier les champs de la fiche";
    return `
    <div class="drawer-section drawer-edit-section drawer-edit-section--collapsed">
      <button type="button" class="btn btn-secondary btn-sm btn-block" id="drawer-expand-edit">${escapeHtml(label)}</button>
    </div>`;
  }

  const visible = state.drawerShowAllFields
    ? defs
    : defs.filter((d) => isDrawerFieldEmpty(lead, d.key));

  let fieldsHtml = "";
  if (!visible.length) {
    fieldsHtml = `<p class="drawer-edit-empty">Tous les champs principaux sont renseignés. Utilisez « Tous les champs » pour modifier.</p>`;
  } else {
    fieldsHtml = visible
      .map(
        (d) => `
      <label class="drawer-edit-field${isDrawerFieldEmpty(lead, d.key) ? " is-missing" : ""}">
        <span class="drawer-edit-label">${escapeHtml(d.label)}${isDrawerFieldEmpty(lead, d.key) ? ' <em class="drawer-missing-tag">manquant</em>' : ""}</span>
        ${renderDrawerEditFieldInput(d, lead)}
      </label>`,
      )
      .join("");
  }

  return `
    <div class="drawer-section drawer-edit-section">
      <div class="drawer-section-head">
        <span class="drawer-section-title">Compléter la fiche</span>
        <button type="button" class="btn btn-ghost btn-sm" id="drawer-toggle-all-fields">
          ${state.drawerShowAllFields ? "Champs manquants seulement" : "Tous les champs"}
        </button>
      </div>
      ${missingCount ? `<p class="drawer-edit-hint">${missingCount} champ${missingCount > 1 ? "s" : ""} à compléter — saisissez puis enregistrez.</p>` : ""}
      <form id="drawer-edit-form" class="drawer-edit-form" onsubmit="return false">${fieldsHtml}</form>
      <button type="button" class="btn btn-secondary btn-sm" id="drawer-save-fields-btn">Enregistrer les modifications</button>
    </div>`;
}

function bindDrawerEditHandlers(_lead) {
  /* Délégation globale dans setupDrawer() — plus de re-bind à chaque ouverture. */
}

async function saveDrawerLeadFields(leadId) {
  const form = document.getElementById("drawer-edit-form");
  if (!form) return;
  const payload = {};
  form.querySelectorAll("[data-field]").forEach((el) => {
    const key = el.dataset.field;
    if (!key) return;
    payload[key] = el.value;
  });
  const btn = document.getElementById("drawer-save-fields-btn");
  if (btn) btn.disabled = true;
  try {
    const result = await api(`/leads/${leadId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify(payload),
    });
    const prev = LEADS.find((l) => l.id === leadId);
    if (result.lead) {
      const idx = LEADS.findIndex((l) => l.id === leadId);
      if (idx >= 0) LEADS[idx] = result.lead;
      applyLeadLiveUpdate(result.lead, prev);
      state.selectedLead = result.lead;
      const section = document.querySelector(".drawer-edit-section");
      if (section) {
        state.drawerEditExpanded = true;
        invalidateDrawerCache(leadId);
        refreshDrawerBodyContent(result.lead);
      }
      patchDrawerLeadFields(result.lead, prev);
      renderLeads();
      showToast("Fiche enregistrée", "success");
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

const leadRefreshState = {
  busy: false,
  leadId: null,
  jobId: null,
  seenLogs: new Set(),
  lastFingerprint: "",
};

const LEAD_REFRESH_FIELD_DEFS = [
  { key: "price", label: "Prix", has: (l) => !!l.price },
  { key: "surface", label: "Surface", has: (l) => l.surface != null && l.surface > 0 },
  { key: "published_at", label: "Publication", has: (l) => !!l.published_at },
  { key: "phone", label: "Tél.", has: (l) => l.phone && l.phone !== "—" },
  { key: "email", label: "Email", has: (l) => l.email && l.email !== "—" },
  { key: "address", label: "Adresse", has: (l) => l.address && l.address !== "—" },
];

function leadLiveFingerprint(lead) {
  if (!lead) return "";
  return [
    lead.id,
    lead.updated_at || "",
    lead.price || 0,
    lead.surface || 0,
    lead.phone || "",
    lead.email || "",
    lead.score || 0,
    lead.address || "",
    lead.published_at || "",
  ].join("|");
}

function resolveLeadRefreshId(btn) {
  const raw = btn?.dataset?.id ?? state.selectedLead?.id;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

function findLeadById(leadId) {
  const id = Number(leadId);
  return LEADS.find((l) => Number(l.id) === id) || null;
}

function setupLeadRefresh() {
  if (setupLeadRefresh._wired) return;
  setupLeadRefresh._wired = true;
  leadRefreshState.busy = false;

  document.addEventListener(
    "click",
    (e) => {
      const btn = e.target.closest(".lead-refresh-btn, #drawer-refresh-lead-btn");
      if (!btn || btn.disabled) return;
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      const id = resolveLeadRefreshId(btn);
      if (!id) {
        showToast("Fiche prospect introuvable — rouvrez le prospect", "warning");
        return;
      }
      refreshLeadDeep(id);
    },
    true,
  );
}

function showLeadRefreshPanel(leadId) {
  const panel = document.getElementById("lead-refresh-panel");
  const drawer = document.getElementById("lead-drawer");
  if (!panel) return;
  panel.hidden = false;
  drawer?.classList.add("lead-drawer--refreshing");
  setLeadsLiveIndicator(true);
  updateLeadRefreshProgress(12, "Lancement du recrawl…");
  renderLeadRefreshFieldChips(findLeadById(leadId), null);
  appendLeadRefreshFeedLine("Connexion au portail immobilier…");
  markLeadRowRefreshing(leadId, true);
}

function hideLeadRefreshPanel(leadId) {
  const panel = document.getElementById("lead-refresh-panel");
  const drawer = document.getElementById("lead-drawer");
  panel && (panel.hidden = true);
  drawer?.classList.remove("lead-drawer--refreshing");
  if (!crawlState.active) setLeadsLiveIndicator(false);
  const feed = document.getElementById("lead-refresh-feed");
  if (feed) feed.innerHTML = "";
  if (leadId) markLeadRowRefreshing(leadId, false);
  leadRefreshState.seenLogs.clear();
}

function showLeadRefreshDock(label) {
  const title = `Mise à jour — ${label}`;
  document.getElementById("crawl-dock-title").textContent = title;
  document.getElementById("crawl-dock-step").textContent = "Lancement du recrawl…";
  document.getElementById("crawl-dock-fill").style.width = "8%";
  document.getElementById("crawl-dock-pct").textContent = "8%";
  document.getElementById("crawl-dock")?.classList.add("open");
  setLeadsLiveIndicator(true);
}

function hideLeadRefreshDock() {
  if (!crawlState.active) {
    document.getElementById("crawl-dock")?.classList.remove("open");
    setLeadsLiveIndicator(false);
  }
}

function syncLeadRefreshDock(job) {
  const pct = job.progress || 0;
  document.getElementById("crawl-dock-step").textContent = job.message || "…";
  document.getElementById("crawl-dock-fill").style.width = `${pct}%`;
  document.getElementById("crawl-dock-pct").textContent = `${pct}%`;
  setCrawlStat("cds-analyzed", job.listings_done || 0);
  setCrawlStat("cds-verified", job.leads_found || 0);
  setCrawlStat("cds-new", job.leads_saved || 0);
}

function updateLeadRefreshProgress(pct, message) {
  const fill = document.getElementById("lead-refresh-bar-fill");
  const step = document.getElementById("lead-refresh-step");
  const bar = document.querySelector(".lead-refresh-bar");
  const n = Math.min(100, Math.max(4, pct || 0));
  if (fill) fill.style.width = `${n}%`;
  if (bar) {
    bar.setAttribute("aria-valuenow", String(Math.round(n)));
    bar.setAttribute("aria-valuetext", message || "");
  }
  if (step && message) step.textContent = message;
}

function appendLeadRefreshFeedLine(text) {
  const feed = document.getElementById("lead-refresh-feed");
  if (!feed || !text || leadRefreshState.seenLogs.has(text)) return;
  leadRefreshState.seenLogs.add(text);
  const li = document.createElement("li");
  li.textContent = text;
  feed.appendChild(li);
  while (feed.children.length > 6) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function renderLeadRefreshFieldChips(lead, prevLead) {
  const box = document.getElementById("lead-refresh-fields");
  if (!box) return;
  box.innerHTML = LEAD_REFRESH_FIELD_DEFS.map((def) => {
    const filled = lead && def.has(lead);
    const wasFilled = prevLead && def.has(prevLead);
    let cls = "lead-refresh-chip";
    if (!filled) cls += " is-pending";
    else if (!wasFilled && filled) cls += " is-done is-new";
    else if (leadRefreshState.busy) cls += " is-active";
    else cls += " is-done";
    const val =
      def.key === "price" && filled
        ? formatPrice(lead)
        : def.key === "surface" && filled
          ? `${lead.surface} m²`
          : def.key === "published_at" && filled
            ? formatPublishedDate(lead) || ""
            : "";
    return `<span class="${cls}" data-chip="${def.key}">${def.label}${val ? ` · ${escapeHtml(val)}` : ""}</span>`;
  }).join("");
}

function markLeadRowRefreshing(leadId, on) {
  document.querySelector(`tr[data-id="${leadId}"]`)?.classList.toggle("lead-row-refreshing", on);
  document.querySelector(`.lead-card[data-id="${leadId}"]`)?.classList.toggle("lead-card-refreshing", on);
}

function patchDrawerLeadFields(lead, prevLead) {
  if (!lead || Number(state.selectedLead?.id) !== Number(lead.id)) return;
  const fieldFormatters = {
    price: (l) => `${formatPrice(l)} ${getTransactionBadge(l)}`,
    surface: (l) => (l.surface ? `${l.surface} m²` : "—"),
    phone: (l) => l.phone || "—",
    email: (l) => l.email || "—",
    owner: (l) => escapeHtml(l.owner || "—"),
    address: (l) => escapeHtml(l.address || "—"),
    score: (l) =>
      `<span class="score-pill ${getScoreClass(l.score || 0)}">${l.score || 0}/100</span>`,
  };
  for (const [key, fmt] of Object.entries(fieldFormatters)) {
    const el = document.querySelector(`[data-drawer-field="${key}"] .drawer-live-value`);
    if (!el) continue;
    const nextHtml = fmt(lead);
    if (el.innerHTML === nextHtml) continue;
    const prevHtml = prevLead ? fmt(prevLead) : null;
    el.innerHTML = nextHtml;
    if (!prevLead || prevHtml !== nextHtml) {
      el.closest(".detail-row")?.classList.add("drawer-field-flash");
      setTimeout(() => el.closest(".detail-row")?.classList.remove("drawer-field-flash"), 900);
    }
  }
  const title = document.getElementById("drawer-title");
  if (title) {
    title.textContent = lead.property_title || lead.listing_title || lead.address || title.textContent;
  }
}

function patchLeadRowInList(lead, prevLead) {
  const row = document.querySelector(`tr[data-id="${lead.id}"]`);
  if (row) {
    const priceEl = row.querySelector(".price-tag");
    if (priceEl && (!prevLead || prevLead.price !== lead.price)) {
      priceEl.textContent = formatPrice(lead);
      row.classList.add("lead-row-flash");
      setTimeout(() => row.classList.remove("lead-row-flash"), 900);
    }
    const details = row.querySelector(".lead-property .details");
    if (details && lead.surface && prevLead?.surface !== lead.surface) {
      const base = escapeHtml(lead.property_detail || lead.property || "");
      details.textContent = `${base} · ${formatPublishedLine(lead)}`;
    }
  }
  const card = document.querySelector(`.lead-card[data-id="${lead.id}"]`);
  if (card) {
    const meta = card.querySelector(".property-meta");
    if (meta) {
      meta.innerHTML = `${escapeHtml(lead.property_detail || lead.property)} · ${formatPrice(lead)} ${getTransactionBadge(lead)}`;
    }
  }
}

function applyLeadLiveUpdate(lead, prevLead) {
  const idx = LEADS.findIndex((l) => l.id === lead.id);
  if (idx >= 0) LEADS[idx] = lead;
  state.selectedLead = lead;
  patchDrawerLeadFields(lead, prevLead);
  patchLeadRowInList(lead, prevLead);
  renderLeadRefreshFieldChips(lead, prevLead);
  const imgChanged =
    !prevLead ||
    prevLead.has_image !== lead.has_image ||
    prevLead.image_updated_at !== lead.image_updated_at ||
    prevLead.image_custom !== lead.image_custom;
  if (
    imgChanged &&
    document.getElementById("lead-drawer")?.classList.contains("open") &&
    state.selectedLead?.id === lead.id
  ) {
    refreshDrawerBodyContent(lead);
    updateDrawerChrome(lead);
  }
  if (imgChanged && state.currentView === "leads") renderLeads();
}

async function fetchLeadSnapshot(leadId) {
  try {
    return await api(`/leads/${leadId}`);
  } catch {
    return findLeadById(leadId);
  }
}

async function startLeadRefreshJob(leadId) {
  const id = Number(leadId);
  const attempts = [
    { path: `/crawler/leads/${id}/refresh`, body: null },
    { path: `/crawler/refresh-lead/${id}`, body: null },
    { path: `/leads/${id}/refresh`, body: null },
    { path: "/leads/refresh", body: JSON.stringify({ lead_id: id }) },
  ];
  let lastErr = null;
  for (const { path, body } of attempts) {
    try {
      return await api(path, {
        method: "POST",
        ...(body ? { body } : {}),
      });
    } catch (err) {
      lastErr = err;
      if (!/404|introuvable|Route API|Not Found/i.test(err.message || "")) {
        throw err;
      }
    }
  }
  throw (
    lastErr ||
    new Error(
      "Mise à jour indisponible — fermez le terminal Veliora (Ctrl+C), relancez demarrer.bat, puis Ctrl+F5 sur le CRM.",
    )
  );
}

async function checkServerLeadRefreshCapability() {
  try {
    const health = await api("/health");
    state.serverLeadRefresh = !!health.lead_refresh;
    state.serverApiVersion = health.api_version;
    if (!health.lead_refresh) {
      showStaleServerBanner(
        "Serveur Veliora obsolète — le bouton « Mettre à jour » nécessite api_version 7. " +
          "Ctrl+C dans le terminal, puis double-clic sur demarrer.bat.",
      );
    } else {
      hideStaleServerBanner();
    }
  } catch {
    state.serverLeadRefresh = null;
  }
}

function showStaleServerBanner(message) {
  let el = document.getElementById("server-stale-banner");
  if (!el) {
    el = document.createElement("div");
    el.id = "server-stale-banner";
    el.className = "server-warning-banner";
    el.style.background = "#92400e";
    document.body.prepend(el);
  }
  el.innerHTML = `<strong>Mise à jour requise</strong> — ${escapeHtml(message)}`;
  el.hidden = false;
}

function hideStaleServerBanner() {
  const el = document.getElementById("server-stale-banner");
  if (el) el.hidden = true;
}

function ingestJobLogs(job) {
  const logs = job.logs || [];
  for (const entry of logs.slice(-4)) {
    const msg = entry.message || entry.status || "";
    if (msg) appendLeadRefreshFeedLine(msg);
  }
}

function setLeadRefreshButtonsLoading(loading, leadId) {
  document.querySelectorAll(".lead-refresh-btn, #drawer-refresh-lead-btn").forEach((btn) => {
    const match = !leadId || btn.id === "drawer-refresh-lead-btn" || parseInt(btn.dataset.id, 10) === leadId;
    if (!match) return;
    btn.disabled = loading;
    btn.classList.toggle("is-loading", loading);
    if (!btn.dataset.refreshLabel) btn.dataset.refreshLabel = btn.textContent.trim();
    btn.textContent = loading ? "Mise à jour…" : btn.dataset.refreshLabel;
  });
}

async function pollLeadRefreshJob(jobId, leadId, initialLead) {
  const maxAttempts = 120;
  let prevLead = initialLead;
  let lastProgress = 8;

  for (let i = 0; i < maxAttempts; i++) {
    const [job, lead] = await Promise.all([
      api(i % 2 === 0 ? `/crawler/jobs/${jobId}` : `/crawler/jobs/${jobId}?lite=1`),
      fetchLeadSnapshot(leadId),
    ]);

    if (job.message) {
      updateLeadRefreshProgress(job.progress || lastProgress, job.message);
      lastProgress = job.progress || lastProgress;
    }
    syncLeadRefreshDock(job);
    ingestJobLogs(job);

    if (lead) {
      const fp = leadLiveFingerprint(lead);
      if (fp !== leadRefreshState.lastFingerprint) {
        applyLeadLiveUpdate(lead, prevLead);
        leadRefreshState.lastFingerprint = fp;
        if (prevLead && fp !== leadLiveFingerprint(prevLead)) {
          appendLeadRefreshFeedLine("Données mises à jour en direct");
        }
        prevLead = lead;
      }
    }

    if (job.status === "completed") {
      updateLeadRefreshProgress(100, job.message || "Terminé");
      if (Array.isArray(job.leads)) {
        LEADS = job.leads;
        const fresh = job.leads.find((l) => Number(l.id) === Number(leadId));
        if (fresh) applyLeadLiveUpdate(fresh, prevLead);
      }
      return job;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.errors?.[0]?.message || job.message || "Échec de la mise à jour");
    }

    await new Promise((r) => setTimeout(r, 650));
  }
  throw new Error("Délai dépassé — réessayez dans un instant");
}

async function refreshLeadDeep(leadId) {
  const id = Number(leadId);
  if (!Number.isFinite(id) || id <= 0) {
    showToast("Identifiant prospect invalide", "error");
    return;
  }
  if (leadRefreshState.busy) {
    showToast("Une mise à jour est déjà en cours…", "warning");
    return;
  }
  let lead = findLeadById(id);
  if (!lead) {
    try {
      lead = await fetchLeadSnapshot(id);
      if (lead) {
        const idx = LEADS.findIndex((l) => Number(l.id) === id);
        if (idx >= 0) LEADS[idx] = lead;
        else LEADS.push(lead);
      }
    } catch {
      /* ignore */
    }
  }
  if (!lead) {
    showToast("Prospect introuvable", "error");
    return;
  }
  // La copie en cache (LEADS / live-patch) peut être partielle et ne pas porter
  // source_url alors que le serveur l'a bien : on revérifie côté serveur avant
  // d'abandonner pour éviter un faux « pas de lien d'annonce ».
  if (!(lead.source_url || "").trim()) {
    const fresh = await fetchLeadSnapshot(id);
    if (fresh && (fresh.source_url || "").trim()) {
      lead = fresh;
      const idx = LEADS.findIndex((l) => Number(l.id) === id);
      if (idx >= 0) LEADS[idx] = fresh;
    }
  }
  if (!(lead.source_url || "").trim()) {
    showToast("Ce prospect n'a pas de lien d'annonce", "warning");
    return;
  }

  if (crawlState.active) {
    showToast("Un crawl est déjà en cours — attendez la fin ou annulez-le", "warning");
    return;
  }

  leadRefreshState.busy = true;
  leadRefreshState.leadId = id;
  leadRefreshState.lastFingerprint = leadLiveFingerprint(lead);

  const label = lead.owner || lead.property_title || "Prospect";
  const drawerOpen =
    state.selectedLead?.id != null && Number(state.selectedLead.id) === id;

  if (drawerOpen) showLeadRefreshPanel(id);
  showLeadRefreshDock(label);
  markLeadRowRefreshing(id, true);
  setLeadRefreshButtonsLoading(true, id);
  showToast(`Recrawl lancé — ${label}`, "info", 2800);

  void (async () => {
    try {
      const start = await startLeadRefreshJob(id);
      const jobId = start.job_id || start.job?.id;
      if (!jobId) throw new Error("Impossible de lancer la mise à jour");
      leadRefreshState.jobId = jobId;

      appendLeadRefreshFeedLine("Crawl navigateur démarré…");
      const job = await pollLeadRefreshJob(jobId, id, lead);

      await refreshAppData();
      const updated = findLeadById(id) || (await fetchLeadSnapshot(id));
      if (updated) {
        applyLeadLiveUpdate(updated, lead);
        if (drawerOpen && Number(state.selectedLead?.id) === id) {
          patchDrawerLeadFields(updated, lead);
        }
      }

      if (job.leads_updated > 0) {
        showToast(`${label} — prospect mis à jour`, "success", 5000);
      } else if (job.warnings?.length) {
        showToast(job.warnings.map((w) => w.message).join(" · "), "warning", 6000);
      } else if (job.errors?.length) {
        showToast(job.errors[0].message, "warning", 6000);
      } else {
        showToast(`${label} — recrawl terminé`, "info", 4000);
      }
    } catch (err) {
      showToast(err.message || "Erreur mise à jour", "error", 8000);
      appendLeadRefreshFeedLine(err.message || "Erreur");
    } finally {
      leadRefreshState.busy = false;
      leadRefreshState.jobId = null;
      setLeadRefreshButtonsLoading(false, id);
      hideLeadRefreshPanel(id);
      hideLeadRefreshDock();
      markLeadRowRefreshing(id, false);
    }
  })();
}

function setCrawlLoaderStep(message) {
  const step = document.getElementById("crawl-loader-step");
  const textEl = document.getElementById("crawl-loader-step-text") || step;
  if (!textEl) return;
  const text = message || "…";
  if (textEl.textContent !== text) {
    textEl.textContent = text;
    if (step) {
      step.classList.remove("pulse");
      void step.offsetWidth;
      step.classList.add("pulse");
    }
    appendCrawlFeedLine(text, "step");
  }
}

// Déduit le site et le détail (annonce) en cours à partir du message serveur.
const CRAWL_SITE_RE = /(?:Site\s+\d+\/\d+\s+[—-]\s+|—\s+)([A-Za-zÀ-ÿ'’.\s]+?)(?:…|\.\.\.|$|—)/;
function updateCrawlTarget(job) {
  const msg = job?.message || "";
  const siteEl = document.getElementById("crawl-loader-site");
  const detailEl = document.getElementById("crawl-loader-detail");
  if (!siteEl || !detailEl) return;

  let site = "";
  const mSite = msg.match(/Site\s+\d+\/\d+\s+[—-]\s+([^…—]+)/);
  if (mSite) site = mSite[1].trim();
  else if (job?.source_id) site = String(job.source_id).split("_").pop();

  const mAnn = msg.match(/Annonce\s+(\d+)\/(\d+)/);
  let detail = "";
  if (mAnn) detail = `Annonce ${mAnn[1]}/${mAnn[2]}`;
  const mPath = msg.match(/«\s*([^»]+?)\s*»/);
  if (mPath) detail = (detail ? detail + " · " : "") + mPath[1];

  if (site) {
    siteEl.textContent = site;
    siteEl.hidden = false;
  } else {
    siteEl.hidden = true;
  }
  detailEl.textContent = detail;
  updateCrawlActivity(job, site, detail);
}

// Vue d'activité live : déduit la phase (scroll / clic / extraction / vérif / dvf)
// à partir du message serveur et anime le mini-navigateur en conséquence.
function crawlActivityPhase(msg) {
  const m = (msg || "").toLowerCase();
  if (/dvf|comparatif/.test(m)) return ["dvf", "Comparatif DVF"];
  if (/v[ée]rif|contr[ôo]le qualit|rejet|incohér/.test(m)) return ["verify", "Vérification des données"];
  if (/extraction|champs|coordonn/.test(m)) return ["extract", "Extraction des champs"];
  if (/t[ée]l[ée]phone|num[ée]ro|contact|clic/.test(m)) return ["click", "Affichage du numéro"];
  if (/annonce|lecture|chargement de l/.test(m)) return ["scroll", "Lecture de l'annonce"];
  if (/exploration|page|recherche|parcour/.test(m)) return ["scroll", "Parcours des pages"];
  if (/session|s[ée]curis|échauff|ouverture|pr[ée]paration|d[ée]marrage/.test(m)) return ["loading", "Connexion au site"];
  return ["loading", "Travail en cours"];
}

function updateCrawlActivity(job, site, detail) {
  const panel = document.getElementById("crawl-activity");
  if (!panel) return;
  const [phase, label] = crawlActivityPhase(job?.message || "");
  if (panel.dataset.phase !== phase) panel.dataset.phase = phase;
  const phaseEl = document.getElementById("crawl-activity-phase");
  if (phaseEl) phaseEl.textContent = label;
  const urlEl = document.getElementById("crawl-activity-url");
  if (urlEl) {
    const host = site ? site.toLowerCase().replace(/\s+/g, "") : "site";
    urlEl.textContent = detail ? `${host} › ${detail}` : (job?.message || host).slice(0, 60);
  }
}

// ─── Heatmap réel : captures live de la page que crawle le bot ───
let liveFrameTimer = null;
let liveFrameMisses = 0;
let liveFrameObjUrl = null;

function hideLiveFrame() {
  const img = document.getElementById("crawl-activity-img");
  const panel = document.getElementById("crawl-activity");
  const badge = document.getElementById("crawl-activity-live");
  if (img) {
    img.hidden = true;
    img.removeAttribute("src");
  }
  panel?.classList.remove("has-live");
  if (badge) badge.hidden = true;
}

function startLiveFramePolling() {
  stopLiveFramePolling();
  liveFrameMisses = 0;
  const tick = async () => {
    if (!crawlState.active) return;
    try {
      const res = await fetch(`${API}/crawler/live-frame`, {
        headers: getAuthHeaders(),
        cache: "no-store",
      });
      if (res.status === 200) {
        const blob = await res.blob();
        if (blob && blob.size > 0) {
          const img = document.getElementById("crawl-activity-img");
          const url = URL.createObjectURL(blob);
          if (img) {
            const prev = liveFrameObjUrl;
            img.onload = () => {
              if (prev) URL.revokeObjectURL(prev);
            };
            liveFrameObjUrl = url;
            img.src = url;
            img.hidden = false;
          }
          document.getElementById("crawl-activity")?.classList.add("has-live");
          const badge = document.getElementById("crawl-activity-live");
          if (badge) badge.hidden = false;
          liveFrameMisses = 0;
        }
      } else {
        liveFrameMisses += 1;
      }
    } catch {
      liveFrameMisses += 1;
    }
    if (liveFrameMisses >= 5) hideLiveFrame();
    if (crawlState.active) liveFrameTimer = setTimeout(tick, 5000);
  };
  liveFrameTimer = setTimeout(tick, 1200);
}

function stopLiveFramePolling() {
  if (liveFrameTimer) {
    clearTimeout(liveFrameTimer);
    liveFrameTimer = null;
  }
  hideLiveFrame();
  if (liveFrameObjUrl) {
    URL.revokeObjectURL(liveFrameObjUrl);
    liveFrameObjUrl = null;
  }
}

function ingestCrawlJobLogs(logs) {
  if (!Array.isArray(logs)) return;
  for (const log of logs) {
    if (!log.id || crawlFeedState.seenLogIds.has(log.id)) continue;
    crawlFeedState.seenLogIds.add(log.id);
    const line = formatCrawlLogLine(log);
    appendCrawlFeedLine(line, crawlLogFeedType(log.status));
  }
}

function updateCrawlLoaderUI(job, title) {
  crawlState.lastJob = job;
  if (job?.source_id) crawlState.sourceId = job.source_id;
  if (!crawlState.minimized) {
    document.getElementById("crawl-loader")?.classList.add("open");
  }
  document.getElementById("crawl-loader-title").textContent = title;
  document.getElementById("crawl-dock-title").textContent = title;
  setCrawlLoaderStep(job.message || "Traitement…");
  document.getElementById("crawl-dock-step").textContent = job.message || "…";
  updateCrawlTarget(job);
  ingestCrawlJobLogs(job.logs);
  const progress = job.progress || 0;
  document.getElementById("crawl-loader-fill").style.width = `${progress}%`;
  document.getElementById("crawl-loader-pct").textContent = `${progress}%`;
  document.getElementById("crawl-dock-fill").style.width = `${progress}%`;
  document.getElementById("crawl-dock-pct").textContent = `${progress}%`;
  updateCrawlStats(job);
  updateEtaDisplay(job);
  updateSourceCardsLive(job);
}

function setCrawlStat(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const next = Number(value || 0);
  if (el._val === next) return;
  el._val = next;
  el.textContent = next;
  el.classList.remove("bump");
  void el.offsetWidth; // relance l'animation
  el.classList.add("bump");
}

function updateCrawlStats(job) {
  const analyzed = job.listings_done || 0;
  const verified = job.leads_found || 0;
  const created = job.leads_saved || 0;
  const updated = job.leads_updated || 0;
  setCrawlStat("cls-analyzed", analyzed);
  setCrawlStat("cls-verified", verified);
  setCrawlStat("cls-new", created);
  setCrawlStat("cls-updated", updated);
  setCrawlStat("cds-analyzed", analyzed);
  setCrawlStat("cds-verified", verified);
  setCrawlStat("cds-new", created);
}

function showCrawlLoader(title, message, progress) {
  resetCrawlFeed();
  crawlState.minimized = false;
  crawlState.lastSavedCount = 0;
  crawlState.lastFoundCount = 0;
  crawlState.lastLeadCount = LEADS.length;
  setLeadsLiveIndicator(true);
  document.getElementById("crawl-loader")?.classList.remove("minimized");
  document.getElementById("crawl-dock")?.classList.remove("open");
  updateCrawlLoaderUI({ message, progress, logs: [] }, title);
  crawlState.active = true;
  startCrawlWaitAnimation();
  startLiveFramePolling();
  state.loading = true;
  document.querySelectorAll(".source-crawl-btn").forEach((b) => (b.disabled = true));
}

function hideCrawlLoader() {
  const analyzeHandoff =
    crawlState.pollOptions?.goToAnalyze &&
    crawlState.pollOptions?.importUrl &&
    crawlState.jobId
      ? { jobId: crawlState.jobId, url: crawlState.pollOptions.importUrl }
      : null;

  crawlState.active = false;
  crawlState.minimized = false;
  crawlState.jobId = null;
  crawlState.sourceId = null;
  crawlState.lastJob = null;
  crawlState.startedAt = null;
  crawlState.pagePollPaused = false;
  crawlState.pollOptions = {};
  stopCrawlWaitAnimation();
  stopLiveFramePolling();
  setLeadsLiveIndicator(false);
  if (window.CrawlWatch) CrawlWatch.stop();
  if (crawlState.leadsRefreshTimer) {
    clearTimeout(crawlState.leadsRefreshTimer);
    crawlState.leadsRefreshTimer = null;
  }
  pausePageCrawlPoll();
  document.getElementById("crawl-loader")?.classList.remove("open", "minimized");
  document.getElementById("crawl-dock")?.classList.remove("open");
  state.loading = false;
  document.querySelectorAll(".source-crawl-btn").forEach((b) => (b.disabled = false));

  if (analyzeHandoff && !analyzeImportState.active) {
    setAnalyzeUiState("loading");
    runAnalyzeImportPoll(analyzeHandoff.jobId, analyzeHandoff.url);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function pollCrawlJobOnce(jobId, label, lastLogsFetch) {
  const now = Date.now();
  const wantLogs = now - lastLogsFetch >= 4000;
  const qs = wantLogs ? "?lite=1&logs=1" : "?lite=1";
  const job = await api(`/crawler/jobs/${jobId}${qs}`, {
    timeoutMs: CRAWL_JOB_POLL_TIMEOUT_MS,
  });
  const newFetch = wantLogs || job.logs?.length ? now : lastLogsFetch;
  let displayLabel = label;
  if (label.startsWith("Portails recommandés") || label.startsWith("Tous les sites")) {
    displayLabel = applyCrawlLabelFromJobMessage(label, job.message) || label;
  }
  const title =
    job.status === "completed" || job.status === "failed"
      ? `Crawl terminé — ${displayLabel}`
      : `Crawl — ${displayLabel}`;
  updateCrawlLoaderUI(job, title);
  return { job, lastLogsFetch: newFetch, displayLabel };
}

function crawlPollBackoffMs(networkFails) {
  return Math.min(12000, CRAWL_JOB_POLL_MS + networkFails * 1200);
}

function startCrawlPolling(jobId, label, options = {}) {
  let networkFails = 0;
  let pollErrors = 0;
  let lastLogsFetch = 0;
  let displayLabel = label;
  crawlState.pollOptions = options;

  const tick = async () => {
    if (!crawlState.active || crawlState.jobId !== jobId) return;
    if (crawlState.pagePollPaused) return;

    try {
      const polled = await pollCrawlJobOnce(jobId, displayLabel, lastLogsFetch);
      const { job, lastLogsFetch: lf } = polled;
      if (polled.displayLabel) displayLabel = polled.displayLabel;
      lastLogsFetch = lf;
      networkFails = 0;
      pollErrors = 0;

      const savedDelta = (job.leads_saved || 0) !== crawlState.lastSavedCount;
      const foundDelta = (job.leads_found || 0) !== crawlState.lastFoundCount;
      if (savedDelta || foundDelta || job.status === "running") {
        scheduleLeadsRefreshDuringCrawl(job);
      }

      if (job.status === "completed" || job.status === "failed") {
        await finishCrawlFromJob(job, displayLabel, options);
        return;
      }

      crawlState.pollTimer = setTimeout(tick, CRAWL_JOB_POLL_MS);
    } catch (err) {
      if (isNetworkFetchError(err) || err.message?.includes("Connexion perdue")) {
        networkFails += 1;
        if (networkFails >= 2) {
          const msg =
            networkFails >= 6
              ? `Serveur très occupé (${networkFails}/40) — le crawl continue en arrière-plan…`
              : `Synchronisation lente (${networkFails}/40) — le crawl continue sur le serveur…`;
          setCrawlLoaderStep(msg);
          const dockStep = document.getElementById("crawl-dock-step");
          if (dockStep) dockStep.textContent = msg;
        }
        if (networkFails >= 10) {
          await handoffCrawlToBackground();
          showToast(
            "Suivi allégé — le crawl continue. Notification à la fin.",
            "warning",
            9000,
          );
          return;
        }
        crawlState.pollTimer = setTimeout(tick, crawlPollBackoffMs(networkFails));
        return;
      }
      if (/Erreur serveur|50\d|État du crawl/i.test(err.message || "")) {
        pollErrors += 1;
        const msg = `Synchronisation du crawl (${pollErrors}/15) — le scan continue sur le serveur…`;
        setCrawlLoaderStep(msg);
        const dockStep = document.getElementById("crawl-dock-step");
        if (dockStep) dockStep.textContent = msg;
        if (pollErrors >= 15) {
          await handoffCrawlToBackground();
          showToast(
            "Suivi UI interrompu — le crawl continue. Rechargez la page ou attendez la notification.",
            "warning",
            9000,
          );
          return;
        }
        crawlState.pollTimer = setTimeout(tick, 2000);
        return;
      }
      showToast(err.message, "error");
      hideCrawlLoader();
    }
  };

  crawlState.pagePollPaused = false;
  crawlState.pollTimer = setTimeout(tick, 400);
}

function cancelStaleCrawlUi() {
  stopCrawlJobAndCloseUi({ toastMessage: "Crawl annulé" });
}

async function resumeActiveCrawlIfAny() {
  try {
    const res = await api("/crawler/jobs/active");
    const job = res?.job;
    if (!job || job.status !== "running" || !job.started_at) {
      if (crawlState.active) await cancelStaleCrawlUi();
      return;
    }

    const started = new Date(job.started_at).getTime();
    if (Number.isNaN(started) || Date.now() - started > 12 * 60 * 60 * 1000) {
      await cancelStaleCrawlUi();
      return;
    }

    crawlState.active = true;
    crawlState.jobId = job.id;
    crawlState.label = job.source_id || job.message?.slice(0, 40) || "Crawl";
    crawlState.startedAt = started;
    crawlState.minimized = true;
    state.loading = true;
    document.querySelectorAll(".source-crawl-btn").forEach((b) => (b.disabled = true));

    minimizeCrawlUI({ notify: true });
    startCrawlWaitAnimation();
    startLiveFramePolling();
    updateCrawlLoaderUI(job, `Crawl — reprise`);
    startCrawlPolling(job.id, crawlState.label);
    showToast("Crawl en cours — arrière-plan + notification à la fin", "info", 4500);
  } catch {
    if (crawlState.active) hideCrawlLoader();
  }
}

async function runCrawlJob(endpoint, body, label, options = {}) {
  if (crawlState.active) {
    showToast("Un crawl est déjà en cours — réduisez le panneau pour naviguer", "warning");
    return;
  }

  const useExisting = Boolean(options.existingJobId);
  showCrawlLoader(
    useExisting ? label : `Crawl — ${label}`,
    useExisting ? "Extraction de l'annonce…" : "Lancement (mode humain, peut être long)…",
    0,
  );

  try {
    let jobId = options.existingJobId;
    let start = {};
    if (!jobId) {
      if (!endpoint) throw new Error("Impossible de démarrer le crawl");
      start = await api(endpoint, {
        method: "POST",
        body: JSON.stringify(body || {}),
      });
      jobId = start.job_id || start.job?.id;
    }
    if (!jobId) throw new Error("Impossible de démarrer le crawl");

    crawlState.jobId = jobId;
    crawlState.label = label;
    crawlState.startedAt = Date.now();

    if (window.CrawlWatch) {
      CrawlWatch.requestPermission();
    }

    if (start.job?.eta_seconds) {
      updateEtaDisplay(start.job);
    }

    let pollingLabel = label;
    if (label.startsWith("Tous les sites") && start.job?.message) {
      pollingLabel = applyCrawlLabelFromJobMessage(label, start.job.message) || label;
      setCrawlModalTitles(pollingLabel);
    }

    startCrawlPolling(jobId, pollingLabel, options);
  } catch (err) {
    const msg = err.message || "Erreur crawl";
    showToast(msg, "error", msg.length > 80 ? 12000 : 6000);
    hideCrawlLoader();
  }
}

function notifyCrawlResult(job, label) {
  const updated = job.leads_updated || 0;
  if (job.leads_saved > 0 || updated > 0) {
    const parts = [];
    if (job.leads_saved > 0) parts.push(`${job.leads_saved} nouveau(x)`);
    if (updated > 0) parts.push(`${updated} mis à jour`);
    showToast(
      `${label} — ${parts.join(", ")} sur ${job.leads_found} annonce(s) analysée(s)`,
      "success",
      6000
    );
    return;
  }

  if (job.warnings?.length && !job.errors?.length) {
    showToast(job.warnings.map((w) => w.message).join(" · "), "warning", 6000);
    return;
  }

  if (job.errors?.length) {
    const reasons = job.errors
      .slice(0, 3)
      .map((e) => e.message)
      .join("\n");
    showToast(`${label} — échec du crawl\n${reasons}`, "error", 9000);
    return;
  }

  showToast(`${label} — crawl terminé, aucune annonce complète trouvée`, "warning");
}

async function toggleCrawler() {
  try {
    if (state.crawlerRunning) {
      await api("/crawler/stop", { method: "POST" });
      state.crawlerRunning = false;
      showToast("Crawler en pause");
    } else {
      await api("/crawler/start", { method: "POST" });
      state.crawlerRunning = true;
      showToast("Crawler démarré — surveillance active");
    }
    syncCrawlerUI();
  } catch (err) {
    showToast(err.message);
  }
}

function syncCrawlerUI() {
  const btn = document.getElementById("crawler-toggle");
  const dot = document.querySelector(".sidebar-footer .status-dot");
  const label = document.querySelector(".sidebar-footer .status-row p");

  if (state.crawlerRunning) {
    btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Pause`;
    dot?.classList.remove("paused");
    if (label) label.textContent = "Crawler actif";
  } else {
    btn.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> Démarrer`;
    dot?.classList.add("paused");
    if (label) label.textContent = "Crawler en pause";
  }
}

async function refreshStats() {
  try {
    const statsData = await api("/stats");
    state.appStats = statsData.stats || state.appStats;
    ACTIVITIES = statsData.activities || [];
    SOURCE_STATS = statsData.source_stats || [];
  } catch (err) {
    console.warn("refreshStats", err);
  }
}

/** Recharge leads, sources, stats et met à jour toute l’interface. */
async function refreshAppData() {
  await reloadCrmData();
  renderAll();
  syncCrawlerUI();
  await refreshOnboardingUi();
}

function clearUrlSearchInputs() {
  const globalSearch = document.getElementById("global-search");
  if (globalSearch) globalSearch.value = "";
  state.searchQuery = "";
  const customUrl = document.getElementById("custom-crawl-url");
  if (customUrl) customUrl.value = "";
  const importUrl = document.getElementById("import-listing-url");
  if (importUrl) importUrl.value = "";
}

function getFilteredLeads() {
  let leads = [...LEADS];

  if (state.leadsFilter === "particulier") leads = leads.filter((l) => l.type === "particulier");
  else if (state.leadsFilter === "sans-agence") leads = leads.filter((l) => l.type !== "agence");
  else if (state.leadsFilter === "avec-agence") leads = leads.filter((l) => l.type === "agence");
  else if (state.leadsFilter === "vente") leads = leads.filter((l) => (l.transaction_type || "vente") === "vente");
  else if (state.leadsFilter === "location") leads = leads.filter((l) => l.transaction_type === "location");
  else if (state.leadsFilter === "nouveau") leads = leads.filter((l) => l.status === "nouveau");
  else if (state.leadsFilter === "retire") leads = leads.filter((l) => l.status === "retire");
  else if (state.leadsFilter === "hot-mandate")
    leads = leads.filter((l) => (l.mandate_score || 0) >= 85);
  else if (state.leadsFilter === "price-drop")
    leads = leads.filter((l) => (l.alert_tags || []).includes("baisse_prix"));
  else if (state.leadsFilter === "dvf-sous-marche")
    leads = leads.filter((l) =>
      ["sous_marche", "leger_sous_marche"].includes(l.dvf_verdict),
    );

  if (state.searchQuery && !isUrl(state.searchQuery)) {
    leads = leads.filter((l) => leadMatchesSearchQuery(l, state.searchQuery));
  }

  return leads.sort((a, b) => (b.mandate_score || 0) - (a.mandate_score || 0));
}

function getLeadSearchText(lead) {
  return [
    lead.owner,
    lead.first_name,
    lead.last_name,
    lead.address,
    lead.city,
    lead.postcode,
    lead.property,
    lead.property_title,
    lead.property_detail,
    lead.source,
    lead.type,
    lead.transaction_type,
    lead.phone,
    lead.email,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function parseFieldToken(token) {
  const m = token.match(/^([a-z0-9_]+)\s*([:<>]=?|=)\s*(.+)$/i);
  if (!m) return null;
  return {
    field: (m[1] || "").toLowerCase(),
    op: m[2],
    rawValue: (m[3] || "").trim().toLowerCase(),
  };
}

function parseNumberValue(raw) {
  const cleaned = String(raw || "").replace(",", ".").replace(/[^\d.-]/g, "");
  const num = parseFloat(cleaned);
  return Number.isFinite(num) ? num : null;
}

function matchNumericField(actual, op, expected) {
  if (!Number.isFinite(actual) || !Number.isFinite(expected)) return false;
  if (op === ">" || op === ":>") return actual > expected;
  if (op === "<" || op === ":<") return actual < expected;
  if (op === ">=") return actual >= expected;
  if (op === "<=") return actual <= expected;
  return actual === expected;
}

function matchFieldToken(lead, token) {
  const parsed = parseFieldToken(token);
  if (!parsed) return null;
  const value = parsed.rawValue;
  const textOps = [":", "="];
  const typeValue = (lead.type || "").toLowerCase();
  const txValue = (lead.transaction_type || "vente").toLowerCase();

  if (["ville", "city"].includes(parsed.field)) {
    return (lead.city || "").toLowerCase().includes(value);
  }
  if (["cp", "postcode", "codepostal"].includes(parsed.field)) {
    return (lead.postcode || "").toLowerCase().includes(value);
  }
  if (["nom", "owner", "proprietaire", "proprio"].includes(parsed.field)) {
    return (lead.owner || "").toLowerCase().includes(value);
  }
  if (["type"].includes(parsed.field)) {
    if (!textOps.includes(parsed.op)) return false;
    return typeValue.includes(value);
  }
  if (["transaction", "tx", "vente", "location"].includes(parsed.field)) {
    if (!textOps.includes(parsed.op)) return false;
    if (parsed.field === "vente") return txValue === "vente";
    if (parsed.field === "location") return txValue === "location";
    return txValue.includes(value);
  }
  if (["source", "portail"].includes(parsed.field)) {
    return (lead.source || "").toLowerCase().includes(value);
  }
  if (["m2", "surface", "surface_m2"].includes(parsed.field)) {
    return matchNumericField(Number(lead.surface || 0), parsed.op, parseNumberValue(value));
  }
  if (["prix", "price"].includes(parsed.field)) {
    return matchNumericField(Number(lead.price || 0), parsed.op, parseNumberValue(value));
  }
  if (["score", "mandat", "mandate"].includes(parsed.field)) {
    return matchNumericField(Number(lead.mandate_score || 0), parsed.op, parseNumberValue(value));
  }
  return null;
}

function leadMatchesSearchQuery(lead, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return true;
  const tokens = q.split(/\s+/).filter(Boolean);
  const haystack = getLeadSearchText(lead);
  return tokens.every((token) => {
    const fieldMatch = matchFieldToken(lead, token);
    if (fieldMatch !== null) return fieldMatch;
    return haystack.includes(token);
  });
}

function renderView(view = state.currentView) {
  updateBadges();
  updateSidebarCount();
  renderStats();
  renderActivity();
  switch (view) {
    case "dashboard":
      renderRadarBriefing();
      renderDashboardTopLeads();
      renderSourceChart();
      break;
    case "playbook":
      renderPlaybook();
      break;
    case "leads":
      renderLeads();
      break;
    case "pipeline":
      renderPipeline();
      break;
    case "crawler":
      renderCrawler();
      break;
    case "mandates":
      if (typeof renderMandatesModule === "function") renderMandatesModule();
      break;
    case "clients":
      if (typeof renderClientsModule === "function") renderClientsModule();
      break;
    case "estimateur":
      renderEstimateurView();
      break;
    default:
      break;
  }
  if (typeof requestIdleCallback === "function") {
    requestIdleCallback(() => {
      if (view !== "dashboard") {
        renderRadarBriefing();
        renderSourceChart();
      }
      if (view !== "playbook" && PLAYBOOK?.guide?.length) {
        /* playbook déjà chargé en arrière-plan */
      }
    });
  }
}

function renderAll() {
  renderView(state.currentView);
}

function leadImageUrl(lead) {
  if (!lead?.has_image && !lead?.image_url) return null;
  const base = lead.image_url || `/api/leads/${lead.id}/image`;
  const token = localStorage.getItem(AUTH_TOKEN_KEY);
  if (!token) return base;
  const sep = base.includes("?") ? "&" : "?";
  return `${base}${sep}access_token=${encodeURIComponent(token)}`;
}

function leadThumbHtml(lead, className = "lead-thumb") {
  const url = leadImageUrl(lead);
  if (!url) {
    return `<div class="${className} ${className}--empty" aria-hidden="true"></div>`;
  }
  return `<img class="${className}" src="${escapeHtml(url)}" alt="" loading="lazy" decoding="async" width="80" height="60">`;
}

function mergeLeadInCache(lead) {
  const idx = LEADS.findIndex((l) => l.id === lead.id);
  if (idx >= 0) LEADS[idx] = { ...LEADS[idx], ...lead };
  if (state.selectedLead?.id === lead.id) state.selectedLead = LEADS[idx] || lead;
  syncRadarFromLeads(LEADS);
  if (state.currentView === "dashboard") renderRadarBriefing();
  return idx;
}

function renderStats() {
  const elLeads = document.getElementById("stat-leads");
  if (!elLeads) return;

  const s = state.appStats;
  const total = s?.total ?? LEADS.length;
  const sansAgence = s?.sans_agence ?? LEADS.filter((l) => l.type !== "agence").length;
  const mandats =
    s?.mandats ?? LEADS.filter((l) => l.status === "mandat" || l.pipeline === "mandat").length;
  const nouveaux = s?.nouveaux ?? LEADS.filter((l) => l.status === "nouveau").length;

  elLeads.textContent = total;
  const elSa = document.getElementById("stat-sans-agence");
  if (elSa) elSa.textContent = sansAgence;
  const elM = document.getElementById("stat-mandats");
  if (elM) elM.textContent = mandats;
  const elN = document.getElementById("stat-nouveaux");
  if (elN) elN.textContent = nouveaux;
}

function renderViewLight(view = state.currentView) {
  updateBadges();
  updateSidebarCount();
  if (view === "leads") renderLeads();
  else if (view === "pipeline") renderPipeline();
  else if (view === "dashboard") {
    renderDashboardTopLeads();
    renderRadarBriefing();
    renderStats();
  } else if (view === "playbook") renderPlaybook();
}

function mandateCallRecommendation(score) {
  const s = score || 0;
  if (s >= 85) return "À appeler aujourd'hui";
  if (s >= 65) return "À appeler sous 48h";
  if (s >= 45) return "À traiter cette semaine";
  return "À surveiller";
}

function renderMandatePill(lead, opts = {}) {
  const s = lead.mandate_score || 0;
  const p = signatureProbability(lead);
  const reason = lead.mandate_score_reason || "";
  const showMax = opts.showMax !== false;
  const large = opts.large ? " score-pill-lg" : "";
  const label = showMax ? `${p}<span class="score-max">%</span>` : `${p}%`;
  const title = escapeAttr(
    `${p} % de chance de signer le mandat · Score ${s}/100${reason ? " · " + reason : ""}`,
  );
  return `<span class="score-pill mandate ${signatureClass(p)}${large}" title="${title}">${label}</span>`;
}

function renderDvfBadge(lead) {
  if (!lead.dvf_verdict) {
    return `<span class="dvf-badge unknown" title="Lancer le comparatif DVF">DVF ?</span>`;
  }
  const v = lead.dvf_verdict;
  const label = escapeHtml(lead.dvf_verdict_label || v);
  const delta = lead.dvf_delta_pct != null ? ` (${lead.dvf_delta_pct > 0 ? "+" : ""}${lead.dvf_delta_pct} %)` : "";
  const ctx = [lead.dvf_sector || lead.sector, lead.dvf_reference_period ? `ventes ${lead.dvf_reference_period}` : ""]
    .filter(Boolean)
    .join(" · ");
  return `<span class="dvf-badge ${v}" title="${label}${delta}${ctx ? " — " + ctx : ""}">${label}</span>`;
}

async function compareLeadDvf(leadId) {
  showToast("Analyse DVF en cours (données Etalab)…", "info", 3000);
  const result = await api(`/dvf/compare/${leadId}`, { method: "POST" });
  if (result.lead) {
    const idx = LEADS.findIndex((l) => l.id === leadId);
    if (idx >= 0) LEADS[idx] = result.lead;
    mergeLeadInCache(result.lead);
    invalidateDrawerCache(leadId);
  }
  void loadRadarAndPlaybook().then(() => {
    if (state.currentView === "dashboard") renderRadarBriefing();
    if (state.currentView === "playbook") renderPlaybook();
  });
  renderView(state.currentView);
  const c = result.comparison || {};
  if (c.available) {
    showToast(`${c.verdict_label} — ${c.delta_pct}% vs ${c.dvf_median_m2} €/m² (DVF)`, "success", 8000);
    if (state.currentView === "analyze") {
      try {
        const fresh = await api(`/radar/leads/${leadId}/analysis`);
        if (fresh.analysis) renderOnDemandAnalysis(fresh.analysis, fresh.lead || result.lead);
      } catch {
        /* garde l'analyse affichée */
      }
    } else if (state.selectedLead?.id === leadId) {
      refreshDrawerBodyContent(result.lead);
      updateDrawerChrome(result.lead);
    }
  } else {
    showToast(c.reason || "Comparatif DVF indisponible", "warning", 7000);
  }
  return result;
}

async function compareAllLeadsDvf() {
  const ventes = LEADS.filter(
    (l) => (l.transaction_type || "vente") === "vente" && l.price > 0 && l.surface > 0,
  );
  if (!ventes.length) {
    showToast("Aucune annonce en vente avec prix et surface", "warning");
    return;
  }
  showToast(`Comparatif DVF sur ${Math.min(ventes.length, 25)} annonces…`, "info", 4000);
  const result = await api("/dvf/compare-all", {
    method: "POST",
    body: JSON.stringify({ limit: 25 }),
  });
  LEADS = result.leads || LEADS;
  await loadRadarAndPlaybook();
  renderAll();
  showToast(
    `DVF : ${result.compared || 0} annonce(s) analysée(s)${result.errors ? ` (${result.errors} erreurs)` : ""}`,
    "success",
    6000,
  );
}

function pulseHeroStat(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const next = String(value ?? 0);
  if (el.textContent !== next) {
    el.textContent = next;
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }
}

function renderRadarBriefing() {
  const counts =
    LEADS.length > 0 ? computeLiveRadarCounts(LEADS) : RADAR?.counts || computeLiveRadarCounts([]);
  const set = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = v ?? 0;
  };
  set("radar-count-sans-agence", counts.sans_agence ?? counts.new_without_agency);
  set("radar-count-new", counts.new_without_agency);
  set("radar-count-drops", counts.price_drops);
  set("radar-count-hot", counts.hot_mandate);
  set("radar-count-dvf", counts.dvf_sous_marche);

  const list =
    LEADS.length > 0
      ? liveRadarPriorities(LEADS, 12)
      : RADAR?.priorities?.length
        ? RADAR.priorities
        : [];

  const totalOpps = counts.total_opportunities ?? activeLeads().length;
  const hotCount = counts.hot_mandate ?? 0;
  const callToday = hotCount;

  pulseHeroStat("radar-total-opps", totalOpps);
  pulseHeroStat("radar-hero-hot", hotCount);
  pulseHeroStat("radar-hero-call-today", callToday);

  const greeting = document.getElementById("radar-greeting");
  if (greeting) {
    const name = RADAR?.agency_name || state.user?.agency_name || "votre agence";
    greeting.textContent = `Opportunités du marché — ${name}`;
  }
  const dateEl = document.getElementById("radar-date");
  if (dateEl) {
    const d = RADAR?.date ? new Date(`${RADAR.date}T08:00:00`) : new Date();
    dateEl.textContent = d.toLocaleDateString("fr-FR", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }

  const prioMeta = document.getElementById("radar-prio-meta");
  if (prioMeta) {
    prioMeta.textContent = totalOpps
      ? `${totalOpps} opportunité(s) sur votre marché · tri Score Mandat™`
      : list.length
        ? `${list.length} vendeur(s) · tri Score Mandat™`
        : "Classées par Score Mandat™";
  }

  const featuredEl = document.getElementById("radar-hero-featured");
  if (featuredEl) {
    const top = list[0];
    if (top) {
      const ms = top.mandate_score || 0;
      featuredEl.hidden = false;
      featuredEl.innerHTML = `
        <div class="crm-hero-featured-inner" data-id="${top.id}" role="button" tabindex="0">
          <span class="crm-hero-featured-label">Priorité n°1</span>
          <div class="crm-hero-featured-score">${renderMandatePill(top, { large: true })}</div>
          <p class="crm-hero-featured-reco">${escapeHtml(mandateCallRecommendation(ms))}</p>
          <p class="crm-hero-featured-title">${escapeHtml(top.property_title || top.address || top.owner)}</p>
          <p class="crm-hero-featured-reason">${escapeHtml(top.mandate_score_reason || "")}</p>
          <span class="btn btn-primary btn-sm">Ouvrir la fiche</span>
        </div>`;
      const inner = featuredEl.querySelector(".crm-hero-featured-inner");
      const openTop = () => openDrawer(parseInt(top.id, 10));
      inner?.addEventListener("click", openTop);
      inner?.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openTop();
        }
      });
    } else {
      featuredEl.hidden = true;
      featuredEl.innerHTML = "";
    }
  }

  const prioEl = document.getElementById("radar-priorities");
  if (prioEl) {
    if (!list.length) {
      prioEl.innerHTML = `<div class="empty-state"><p>Ajoutez une source ou lancez un crawl pour détecter les opportunités de votre marché.</p></div>`;
    } else {
      prioEl.innerHTML = list
        .map(
          (l) => {
            const ms = l.mandate_score || 0;
            const fresh = leadFreshness(l.created_at);
            const freshBadge = fresh
              ? `<span class="freshness-badge" title="Date de détection">⚡ Détecté ${fresh}</span>`
              : "";
            const alsoOn = (l._also_on && l._portal_count > 1)
              ? `<span class="also-on-badge" title="Même bien détecté sur plusieurs portails">Aussi sur ${l._portal_count} portails</span>`
              : "";
            return `
        <div class="radar-priority-row" data-id="${l.id}">
          <div class="radar-priority-main">
            ${renderMandatePill(l, { large: true })}
            <div>
              <div class="radar-priority-title">${escapeHtml(l.property_title || l.address || l.owner)} ${freshBadge}${alsoOn}</div>
              <div class="radar-priority-meta">${escapeHtml(l.mandate_score_reason || "")} · ${formatPrice(l)} ${renderDvfBadge(l)}</div>
              <span class="radar-priority-reco">${escapeHtml(mandateCallRecommendation(ms))}</span>
            </div>
          </div>
          <button type="button" class="btn btn-ghost btn-sm radar-open-btn">Appeler</button>
        </div>`;
          },
        )
        .join("");
      prioEl.querySelectorAll(".radar-priority-row").forEach((row) => {
        row.addEventListener("click", (e) => {
          if (e.target.closest(".radar-open-btn") || e.target === row) {
            openDrawer(parseInt(row.dataset.id, 10));
          }
        });
      });
    }
  }

  const alertsEl = document.getElementById("radar-alerts");
  if (alertsEl) {
    const alerts = RADAR?.alerts || [];
    if (!alerts.length) {
      alertsEl.innerHTML = `<div class="empty-state"><p>Aucune alerte pour le moment</p></div>`;
    } else {
      alertsEl.innerHTML = alerts
        .slice(0, 8)
        .map(
          (a) => `
        <div class="radar-alert radar-alert-${a.priority}" data-lead-id="${a.lead_id || ""}">
          <strong>${escapeHtml(a.title)}</strong>
          <p>${escapeHtml(a.message)}</p>
        </div>`,
        )
        .join("");
      alertsEl.querySelectorAll("[data-lead-id]").forEach((el) => {
        const id = parseInt(el.dataset.leadId, 10);
        if (id) el.addEventListener("click", () => openDrawer(id));
      });
    }
  }
}

async function fetchPlaybookStatic() {
  try {
    const res = await fetchWithTimeout(`${API}/radar/playbook/static`, {
      headers: { ...getAuthHeaders(), Accept: "application/json" },
    }, 10000);
    if (!res.ok) return null;
    const body = await res.json().catch(() => null);
    if (!body?.guide?.length) return null;
    return body;
  } catch {
    return null;
  }
}

function buildClientPlaybook() {
  const active = LEADS.filter((l) => (l.status || "").toLowerCase() !== "retire");
  const particuliers = active.filter((l) => l.type !== "agence");
  const prioritized = [...particuliers].sort(
    (a, b) => (b.mandate_score || b.score || 0) - (a.mandate_score || a.score || 0),
  );
  const opportunities = prioritized.slice(0, 25).map((l) => ({
    lead_id: l.id,
    address: l.address || "—",
    city: l.city || "",
    price_label: formatPrice(l),
    mandate_score: l.mandate_score || l.score || 0,
    mandate_score_reason: l.mandate_score_reason || "",
    alert_tags: l.alert_tags || [],
    dvf_verdict: l.dvf_verdict,
    dvf_verdict_label: l.dvf_verdict_label,
    dvf_delta_pct: l.dvf_delta_pct,
    days_on_market: l.days_on_market,
    scenario: "default",
    scenario_label: "Premier contact",
    advice: ["Ouvrez la fiche prospect pour générer le script d'appel complet."],
    script: {
      opening: "Bonjour, je me permets de vous appeler concernant votre annonce.",
      observation: "J'ai repéré votre bien dans le secteur.",
      value: "Nous accompagnons des vendeurs avec de bons résultats sur des biens similaires.",
      closing: "Seriez-vous disponible cette semaine pour une estimation gratuite ?",
      objections: [],
      full_text: "",
    },
  }));
  return {
    agency_name: state.user?.agency_name || "",
    date: new Date().toISOString().slice(0, 10),
    guide: [],
    script_templates: {},
    opportunities,
    counts: {
      total: active.length,
      particuliers: particuliers.length,
      with_script: opportunities.length,
      hot: active.filter((l) => (l.mandate_score || l.score || 0) >= 85).length,
      dvf_sous_marche: active.filter((l) => (l.alert_tags || []).includes("dvf_sous_marche")).length,
    },
    _clientFallback: true,
  };
}

function notifyPlaybookLoadIssues(pb) {
  if (!pb) return;
  if (pb._partial && !sessionStorage.getItem("veliora_playbook_partial")) {
    sessionStorage.setItem("veliora_playbook_partial", "1");
    showToast(
      "Guide chargé — scripts personnalisés partiels (rechargez après mise à jour serveur)",
      "warning",
      6000,
    );
  }
  if (pb._clientFallback && !pb.guide?.length && !sessionStorage.getItem("veliora_playbook_fb")) {
    sessionStorage.setItem("veliora_playbook_fb", "1");
    showToast(
      "Guide en mode local — relancez le serveur pour le contenu complet",
      "warning",
      7000,
    );
  }
}

async function fetchPlaybook() {
  try {
    const res = await fetchWithTimeout(`${API}/radar/playbook`, {
      headers: { ...getAuthHeaders(), Accept: "application/json" },
    }, 20000);
    if (res.ok) {
      const body = await res.json().catch(() => null);
      if (body?.guide?.length) return body;
      if (body && Array.isArray(body.opportunities)) return body;
    }
    const shell = await fetchPlaybookStatic();
    if (shell) {
      const merged = buildClientPlaybook();
      return {
        ...shell,
        opportunities: merged.opportunities,
        counts: merged.counts,
        _clientFallback: true,
      };
    }
    return buildClientPlaybook();
  } catch {
    const shell = await fetchPlaybookStatic();
    if (shell) return shell;
    return buildClientPlaybook();
  }
}

function renderPlaybookTag(tag) {
  const labels = {
    sans_agence: "Sans agence",
    nouveau: "Nouveau",
    ancienne: "Ancienne",
    baisse_prix: "Baisse",
    dvf_sous_marche: "DVF −",
    dvf_sur_marche: "DVF +",
  };
  return `<span class="playbook-tag">${escapeHtml(labels[tag] || tag)}</span>`;
}

function renderPlaybookGuide() {
  const el = document.getElementById("playbook-guide");
  if (!el) return;
  const guide = PLAYBOOK?.guide || [];
  if (!guide.length) {
    el.innerHTML = `<div class="empty-state"><p>Guide indisponible — relancez le serveur</p></div>`;
    return;
  }
  el.innerHTML = guide
    .map(
      (section) => `
    <details class="playbook-guide-block" open>
      <summary>
        <span class="playbook-guide-emoji">${section.emoji || "📘"}</span>
        <span>${escapeHtml(section.title)}</span>
      </summary>
      <p class="playbook-guide-summary">${escapeHtml(section.summary || "")}</p>
      <div class="playbook-guide-items">
        ${(section.blocks || [])
          .map(
            (b) => `
          <div class="playbook-guide-item playbook-tone-${b.tone || "medium"}">
            <strong>${escapeHtml(b.label)}</strong>
            <p>${escapeHtml(b.detail)}</p>
          </div>`,
          )
          .join("")}
      </div>
      ${(section.tips || []).length ? `<ul class="playbook-tips">${section.tips.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>` : ""}
    </details>`,
    )
    .join("");
}

function renderPlaybookTemplates() {
  const el = document.getElementById("playbook-templates");
  if (!el) return;
  const templates = PLAYBOOK?.script_templates || {};
  const keys = Object.keys(templates);
  if (!keys.length) {
    el.innerHTML = `<div class="empty-state"><p>Aucun modèle de script</p></div>`;
    return;
  }
  el.innerHTML = keys
    .map((key) => {
      const t = templates[key];
      return `
      <details class="playbook-template">
        <summary>${escapeHtml(t.label || key)}</summary>
        <div class="playbook-template-body">
          <p><strong>Observation</strong> — ${escapeHtml(t.hook || "")}</p>
          <p><strong>Proposition de valeur</strong> — ${escapeHtml(t.value || "")}</p>
          <p><strong>Closing</strong> — ${escapeHtml(t.closing || "")}</p>
          ${(t.objections || [])
            .map(
              (o) => `
            <div class="playbook-objection">
              <strong>« ${escapeHtml(o.q || o[0] || "")} »</strong>
              <p>${escapeHtml(o.a || o[1] || "")}</p>
            </div>`,
            )
            .join("")}
          <button type="button" class="btn btn-ghost btn-sm playbook-copy-btn" data-copy-template="${escapeHtml(key)}">Copier le script type</button>
        </div>
      </details>`;
    })
    .join("");
  el.querySelectorAll(".playbook-copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tplKey = btn.dataset.copyTemplate;
      const t = PLAYBOOK?.script_templates?.[tplKey];
      const text = t
        ? [`Accroche : ${t.hook}`, `Valeur : ${t.value}`, `Closing : ${t.closing}`].join("\n\n")
        : "";
      copyPlaybookText(text, btn);
    });
  });
}

function renderPlaybookOpportunities() {
  const el = document.getElementById("playbook-opportunities");
  const countEl = document.getElementById("playbook-opp-count");
  if (!el) return;
  const opps = PLAYBOOK?.opportunities || [];
  if (countEl) countEl.textContent = `${opps.length} prospect(s)`;
  if (!opps.length) {
    el.innerHTML = `<div class="empty-state"><p>Lancez un crawl pour alimenter vos opportunités, puis revenez ici pour vos scripts personnalisés.</p></div>`;
    return;
  }
  el.innerHTML = opps
    .map((o) => {
      const script = o.script || {};
      const advice = (o.advice || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("");
      const tags = (o.alert_tags || []).map(renderPlaybookTag).join("");
      const dvf =
        o.dvf_verdict_label
          ? `<span class="playbook-dvf">${escapeHtml(o.dvf_verdict_label)}${o.dvf_delta_pct != null ? ` (${o.dvf_delta_pct > 0 ? "+" : ""}${o.dvf_delta_pct} %)` : ""}</span>`
          : "";
      return `
      <article class="playbook-opp" data-lead-id="${o.lead_id}">
        <header class="playbook-opp-head">
          <div>
            <span class="score-pill mandate ${signatureClass(signatureProbability(o))}" title="Score mandat ${o.mandate_score || 0}/100">${signatureProbability(o)}%</span>
            <span class="playbook-scenario">${escapeHtml(o.scenario_label || "")}</span>
          </div>
          <div class="playbook-opp-actions">
            <button type="button" class="btn btn-ghost btn-sm playbook-open-lead">Fiche</button>
            <button type="button" class="btn btn-secondary btn-sm playbook-copy-btn" data-copy-lead="${o.lead_id}">Copier script</button>
          </div>
        </header>
        <h3 class="playbook-opp-title">${escapeHtml(o.address || "—")}</h3>
        <p class="playbook-opp-meta">${escapeHtml(o.mandate_score_reason || "")} · ${escapeHtml(o.price_label || "")} ${dvf}</p>
        <div class="playbook-opp-tags">${tags}</div>
        ${advice ? `<ul class="playbook-advice">${advice}</ul>` : ""}
        <div class="playbook-script">
          <div class="playbook-script-step"><span>1</span><p>${formatScriptRichText(script.opening || "")}</p></div>
          <div class="playbook-script-step"><span>2</span><p>${formatScriptRichText(script.observation || "")}</p></div>
          <div class="playbook-script-step"><span>3</span><p>${formatScriptRichText(script.value || "")}</p></div>
          <div class="playbook-script-step"><span>4</span><p>${formatScriptRichText(script.closing || "")}</p></div>
        </div>
        ${(script.objections || []).length ? `
          <details class="playbook-opp-objections">
            <summary>Objections fréquentes</summary>
            ${script.objections.map((obj) => `<div class="playbook-objection"><strong>« ${escapeHtml(obj.q || "")} »</strong><p>${escapeHtml(obj.a || "")}</p></div>`).join("")}
          </details>` : ""}
      </article>`;
    })
    .join("");

  el.querySelectorAll(".playbook-open-lead").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = parseInt(btn.closest(".playbook-opp")?.dataset.leadId, 10);
      if (id) openDrawer(id);
    });
  });
  el.querySelectorAll(".playbook-copy-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const leadId = parseInt(btn.dataset.copyLead, 10);
      const tplKey = btn.dataset.copyTemplate;
      let text = "";
      if (leadId) {
        const opp = (PLAYBOOK?.opportunities || []).find((o) => o.lead_id === leadId);
        text = opp?.script?.full_text || "";
      } else if (tplKey) {
        const t = PLAYBOOK?.script_templates?.[tplKey];
        if (t) text = [`Accroche : ${t.hook}`, `Valeur : ${t.value}`, `Closing : ${t.closing}`].join("\n\n");
      }
      copyPlaybookText(text, btn);
    });
  });
  el.querySelectorAll(".playbook-opp").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      const id = parseInt(card.dataset.leadId, 10);
      if (id) openDrawer(id);
    });
  });
}

function renderPlaybookStats() {
  const el = document.getElementById("playbook-stats");
  if (!el) return;
  const c = PLAYBOOK?.counts || {};
  el.innerHTML = `
    <div class="playbook-stat-card">
      <span class="playbook-stat-value">${c.particuliers ?? 0}</span>
      <span class="playbook-stat-label">Particuliers</span>
    </div>
    <div class="playbook-stat-card hot">
      <span class="playbook-stat-value">${c.hot ?? 0}</span>
      <span class="playbook-stat-label">Fort potentiel</span>
    </div>
    <div class="playbook-stat-card dvf">
      <span class="playbook-stat-value">${c.dvf_sous_marche ?? 0}</span>
      <span class="playbook-stat-label">Sous marché DVF</span>
    </div>
    <div class="playbook-stat-card">
      <span class="playbook-stat-value">${c.with_script ?? 0}</span>
      <span class="playbook-stat-label">Scripts prêts</span>
    </div>`;
}

function renderPlaybook() {
  const sub = document.getElementById("playbook-subtitle");
  if (sub && PLAYBOOK) {
    const agency = PLAYBOOK.agency_name || state.user?.agency_name || "votre agence";
    sub.textContent = `${agency} — conseils, opportunités et discours à tenir`;
  }
  renderPlaybookStats();
  renderPlaybookGuide();
  renderPlaybookTemplates();
  renderPlaybookOpportunities();
}

async function copyPlaybookText(text, btn) {
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    showToast("Script copié dans le presse-papier", "success", 2500);
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "Copié ✓";
      setTimeout(() => {
        btn.textContent = prev;
      }, 1800);
    }
  } catch {
    showToast(text, "info", 12000);
  }
}

function setupPlaybook() {
  document.getElementById("playbook-refresh-btn")?.addEventListener("click", async () => {
    showToast("Actualisation du guide…", "info", 2000);
    PLAYBOOK = await fetchPlaybook().catch(() => PLAYBOOK);
    if (!PLAYBOOK?.guide?.length) {
      showToast("Guide indisponible — vérifiez la connexion ou relancez le serveur", "warning");
      return;
    }
    notifyPlaybookLoadIssues(PLAYBOOK);
    renderPlaybook();
    showToast("Guide actualisé", "success");
  });
}

async function patchLeadPipeline(leadId, pipeline) {
  const result = await api(`/leads/${leadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ pipeline }),
  });
  const idx = LEADS.findIndex((l) => l.id === leadId);
  const prev = idx >= 0 ? LEADS[idx] : null;
  if (idx >= 0 && result.lead) LEADS[idx] = result.lead;

  renderPipeline();
  updateBadges();
  if (state.currentView === "leads") renderLeads();

  if (state.selectedLead?.id === leadId && result.lead) {
    state.selectedLead = result.lead;
    invalidateDrawerCache(leadId);
    updateDrawerChrome(result.lead);
    refreshDrawerBodyContent(result.lead);
  }
  return result;
}

function setupRadar() {
  document.querySelectorAll(".radar-stat").forEach((card) => {
    card.addEventListener("click", () => {
      const filter = card.dataset.filter;
      if (filter) {
        state.leadsFilter = filter;
        document.querySelectorAll(".filter-chip").forEach((c) => {
          c.classList.toggle("active", c.dataset.filter === filter);
        });
        switchView("leads");
      }
    });
  });

  document.getElementById("radar-playbook-btn")?.addEventListener("click", () => switchView("playbook"));

  document.getElementById("radar-settings-btn")?.addEventListener("click", async () => {
    const modal = document.getElementById("radar-settings-modal");
    try {
      const settings = normalizeSettingsPayload(await api("/radar/settings"));
      document.getElementById("radar-target-cities").value = (settings.target_cities || []).join(", ");
      document.getElementById("radar-mandate-goal").value = settings.mandate_goal_month || 5;
    } catch {
      /* ignore */
    }
    modal?.classList.add("open");
  });

  document.getElementById("radar-settings-close")?.addEventListener("click", () => {
    document.getElementById("radar-settings-modal")?.classList.remove("open");
  });

  document.getElementById("radar-settings-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = document.getElementById("radar-target-cities").value;
    const cities = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const goal = parseInt(document.getElementById("radar-mandate-goal").value, 10) || 5;
    try {
      const res = await api("/radar/settings", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", ...getAuthHeaders() },
        body: JSON.stringify({ target_cities: cities, mandate_goal_month: goal }),
      });
      state.settings = normalizeSettingsPayload(
        res?.settings ? res : { target_cities: cities, mandate_goal_month: goal },
      );
      if (!state.settings.target_cities?.length) {
        state.settings.target_cities = cities;
      }
      updateCrawlCityDisplay();
      scheduleSourceUrlsForCity();
      showToast("Territoire enregistré — liens sources mis à jour", "success");
      document.getElementById("radar-settings-modal")?.classList.remove("open");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}

function renderActivity() {
  const container = document.getElementById("activity-feed");
  if (!ACTIVITIES.length) {
    container.innerHTML = `<div class="empty-state"><p>Aucune activité — lancez un crawl pour commencer</p></div>`;
    return;
  }

  container.innerHTML = ACTIVITIES.map(
    (a) => `
    <div class="activity-item">
      <div class="activity-icon ${a.type}">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
          ${a.type === "new" ? '<path d="M12 4v16m8-8H4"/>' : a.type === "contact" ? '<path d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>' : '<path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>'}
        </svg>
      </div>
      <div class="activity-content">
        <p>${a.text}</p>
        <div class="time">${a.time}</div>
      </div>
    </div>`
  ).join("");
}

function renderSourceChart() {
  const container = document.getElementById("source-chart");
  if (!SOURCE_STATS.length || SOURCE_STATS.every((s) => s.count === 0)) {
    container.innerHTML = `<div class="empty-state"><p>Aucune source n'a encore produit de leads</p></div>`;
    return;
  }

  container.innerHTML = SOURCE_STATS.map(
    (s) => `
    <div class="source-bar-item">
      <div class="source-bar-header">
        <span>${s.name}</span>
        <span>${s.count}</span>
      </div>
      <div class="source-bar-track">
        <div class="source-bar-fill ${s.key}" style="width: ${s.pct}%"></div>
      </div>
    </div>`
  ).join("");
}

function renderDashboardTopLeads() {
  const container = document.getElementById("dashboard-top-leads");
  const topLeads = LEADS.filter((l) => l.type !== "agence").sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 5);

  if (!topLeads.length) {
    container.innerHTML = `<tr><td colspan="6"><div class="empty-state"><p>Aucun prospect — lancez le crawler</p></div></td></tr>`;
    return;
  }

  container.innerHTML = topLeads.map((lead) => `
    <tr data-id="${lead.id}">
      <td>
        <div class="lead-owner">
          <div class="lead-avatar" style="background:${getAvatarColor(lead.id)}">${getInitials(lead.owner)}</div>
          <div class="lead-info"><div class="name">${lead.owner}</div><div class="phone">${lead.phone}</div></div>
        </div>
      </td>
      <td><div class="lead-property"><div class="address">${escapeHtml(lead.property_title || lead.address)}</div><div class="details">${escapeHtml(lead.property_detail || lead.property)}</div></div></td>
      <td><span class="price-tag">${formatPrice(lead)}</span> ${getTransactionBadge(lead)}</td>
      <td><span class="source-tag">${lead.source}</span></td>
      <td><span class="score-pill ${getScoreClass(lead.score || 0)}">${lead.score || 0}</span></td>
      <td class="lead-actions-cell">${getLeadActionsHtml(lead)}</td>
    </tr>`).join("");
}

function getLeadDeleteButtonHtml(lead) {
  return `<button type="button" class="btn btn-ghost lead-delete-btn" data-id="${lead.id}" data-name="${escapeAttr(lead.owner)}" title="Supprimer ce prospect" aria-label="Supprimer">
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path d="M3 6h18M8 6V4h8v2m-1 0v14H9V6"/></svg>
  </button>`;
}

function renderLeadRow(lead) {
  const ms = lead.mandate_score || 0;
  const fresh = leadFreshness(lead.created_at);
  const freshBadge = fresh
    ? `<span class="freshness-badge freshness-badge-sm" title="Détecté ${fresh}">⚡ ${fresh}</span>`
    : "";
  const alsoOnBadge = lead._portal_count > 1
    ? `<span class="also-on-badge also-on-badge-sm" title="Détecté sur ${lead._portal_count} portails">${lead._portal_count} portails</span>`
    : "";
  return `
    <tr data-id="${lead.id}">
      <td class="col-score-mandat">
        <div class="lead-score-cell">
          ${renderMandatePill(lead, { large: true })}
          <span class="lead-score-reco">${escapeHtml(mandateCallRecommendation(ms))}</span>
          <span class="lead-score-reason" title="${escapeAttr(lead.mandate_score_reason || "")}">${escapeHtml((lead.mandate_score_reason || "").slice(0, 48))}${(lead.mandate_score_reason || "").length > 48 ? "…" : ""}</span>
        </div>
      </td>
      <td>
        <div class="lead-owner">
          <div class="lead-avatar" style="background:${getAvatarColor(lead.id)}">${getInitials(lead.owner)}</div>
          <div class="lead-info">
            <div class="name">${lead.owner}</div>
            <div class="phone">${lead.phone}</div>
          </div>
        </div>
      </td>
      <td>
        <div class="lead-property lead-property--with-thumb">
          ${leadThumbHtml(lead)}
          <div class="lead-property-text">
            <div class="address">${escapeHtml(lead.property_title || lead.address)} ${freshBadge}${alsoOnBadge}</div>
            <div class="details">${escapeHtml(lead.property_detail || lead.property)} · ${formatPublishedLine(lead)}</div>
          </div>
        </div>
      </td>
      <td><span class="price-tag">${formatPrice(lead)}</span> ${getTransactionBadge(lead)}</td>
      <td>${getTypeBadge(lead)}</td>
      <td>${getStatusBadge(lead.status)}</td>
      <td><span class="source-tag">${lead.source}</span></td>
      <td>${renderDvfBadge(lead) || '<span class="text-muted">—</span>'}</td>
      <td class="lead-actions-cell">${getLeadActionsHtml(lead)}</td>
      <td class="lead-actions-cell lead-delete-cell">${getLeadDeleteButtonHtml(lead)}</td>
    </tr>`;
}

function renderLeads() {
  const leads = getFilteredLeads();
  const tableContainer = document.getElementById("leads-table-body");
  const gridContainer = document.getElementById("leads-grid");
  const tableWrapper = document.getElementById("leads-table-wrapper");
  const gridWrapper = document.getElementById("leads-grid-wrapper");

  const useTable = state.leadsView === "table" && !isMobileLayout();

  if (useTable) {
    tableWrapper.style.display = "block";
    gridWrapper.style.display = "none";

    if (!leads.length) {
      tableContainer.innerHTML = `<tr><td colspan="10"><div class="empty-state"><p>Aucune opportunité — configurez vos sources pour analyser le marché</p></div></td></tr>`;
      return;
    }

    tableContainer.innerHTML = leads.map(renderLeadRow).join("");
  } else {
    tableWrapper.style.display = "none";
    gridWrapper.style.display = "block";

    if (!leads.length) {
      gridContainer.innerHTML = `<div class="empty-state"><p>Aucun prospect</p></div>`;
      return;
    }

    gridContainer.innerHTML = leads.map((lead) => `
      <div class="lead-card" data-id="${lead.id}">
        <div class="lead-card-header">
          <div class="lead-owner">
            ${
              lead.has_image
                ? leadThumbHtml(lead, "lead-card-thumb")
                : `<div class="lead-avatar" style="background:${getAvatarColor(lead.id)}">${getInitials(lead.owner)}</div>`
            }
            <div class="lead-info">
              <div class="name">${lead.owner}</div>
              <div class="phone">${lead.phone}</div>
            </div>
          </div>
          <div class="lead-card-header-actions">
            ${renderMandatePill(lead, { large: true })}
            ${getLeadDeleteButtonHtml(lead)}
          </div>
        </div>
        <div class="lead-card-body">
          <div class="property-title">${escapeHtml(lead.property_title || lead.address)}</div>
          <div class="property-meta">${escapeHtml(lead.mandate_score_reason || "")}</div>
          ${leadQuickFactsHtml(lead)}
          <div class="property-meta">${escapeHtml(lead.property_detail || lead.property)} · ${formatPrice(lead)} ${getTransactionBadge(lead)} ${renderDvfBadge(lead)}</div>
          <div style="display:flex;gap:0.5rem;flex-wrap:wrap">${getTypeBadge(lead)} ${getStatusBadge(lead.status)} <span class="radar-priority-reco">${escapeHtml(mandateCallRecommendation(lead.mandate_score || 0))}</span></div>
        </div>
        <div class="lead-card-footer">
          <span class="source-tag">${lead.source}</span>
          <span class="lead-card-footer-links">${getLeadActionsHtml(lead)}</span>
        </div>
      </div>`).join("");

  }
}

function setupLeadsListDelegation() {
  const table = document.getElementById("leads-table-body");
  if (table && table.dataset.delegate !== "1") {
    table.dataset.delegate = "1";
    table.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input, label")) return;
      const row = e.target.closest("tr[data-id]");
      if (row) openDrawer(parseInt(row.dataset.id, 10));
    });
  }
  const grid = document.getElementById("leads-grid");
  if (grid && grid.dataset.delegate !== "1") {
    grid.dataset.delegate = "1";
    grid.addEventListener("click", (e) => {
      if (e.target.closest("button, a, input, label")) return;
      const card = e.target.closest(".lead-card[data-id]");
      if (card) openDrawer(parseInt(card.dataset.id, 10));
    });
  }
}

const PIPELINE_STAGES = [
  { key: "nouveau", label: "À contacter", color: "#0ea5e9" },
  { key: "contacte", label: "Contacté", color: "#8b5cf6" },
  { key: "rdv", label: "RDV", color: "#f59e0b" },
  { key: "mandat", label: "Mandat", color: "#10b981" },
  { key: "perdu", label: "Perdu", color: "#94a3b8" },
];

let pipelineDragLeadId = null;
let pipelineSuppressClick = false;

function leadPipelineKey(lead) {
  const p = (lead?.pipeline || lead?.status || "nouveau").toLowerCase();
  if (p === "a_contacter") return "nouveau";
  if (p === "perdu") return "perdu";
  return PIPELINE_STAGES.some((s) => s.key === p) ? p : "nouveau";
}

function pipelineMatchesColumn(lead, key) {
  return leadPipelineKey(lead) === key;
}

function setupPipelineBoard() {
  const board = document.getElementById("pipeline-board");
  if (!board || board.dataset.dndInit === "1") return;
  board.dataset.dndInit = "1";

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".pipeline-card");
    if (!card) return;
    pipelineDragLeadId = parseInt(card.dataset.id, 10);
    card.classList.add("is-dragging");
    if (e.dataTransfer) {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(pipelineDragLeadId));
    }
  });

  board.addEventListener("dragend", () => {
    board.querySelectorAll(".pipeline-card.is-dragging").forEach((c) => c.classList.remove("is-dragging"));
    board.querySelectorAll(".pipeline-column.is-drop-target").forEach((c) =>
      c.classList.remove("is-drop-target"),
    );
    pipelineDragLeadId = null;
  });

  board.addEventListener("dragover", (e) => {
    const zone = e.target.closest(".pipeline-cards");
    if (!zone) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    zone.closest(".pipeline-column")?.classList.add("is-drop-target");
  });

  board.addEventListener("dragleave", (e) => {
    const col = e.target.closest(".pipeline-column");
    if (col && !col.contains(e.relatedTarget)) col.classList.remove("is-drop-target");
  });

  board.addEventListener("drop", async (e) => {
    e.preventDefault();
    const col = e.target.closest(".pipeline-column");
    if (!col || !pipelineDragLeadId) return;
    const stage = col.dataset.stage;
    col.classList.remove("is-drop-target");
    const lead = LEADS.find((l) => l.id === pipelineDragLeadId);
    if (!lead || leadPipelineKey(lead) === stage) return;
    pipelineSuppressClick = true;
    try {
      await patchLeadPipeline(pipelineDragLeadId, stage);
      const label = PIPELINE_STAGES.find((s) => s.key === stage)?.label || stage;
      showToast(`Déplacé : ${label}`, "success");
    } catch (err) {
      renderPipeline();
      showToast(err.message, "error");
    }
    setTimeout(() => {
      pipelineSuppressClick = false;
    }, 80);
  });

  board.addEventListener("click", (e) => {
    if (pipelineSuppressClick) return;
    const card = e.target.closest(".pipeline-card");
    if (!card) return;
    openDrawer(parseInt(card.dataset.id, 10));
  });
}

function renderPipeline() {
  const board = document.getElementById("pipeline-board");
  if (!board) return;

  board.innerHTML = PIPELINE_STAGES.map((col) => {
    const cards = LEADS.filter((l) => pipelineMatchesColumn(l, col.key)).sort(
      (a, b) => (b.mandate_score || 0) - (a.mandate_score || 0),
    );
    return `
      <div class="pipeline-column" data-stage="${col.key}">
        <div class="pipeline-column-header">
          <div class="pipeline-column-title">
            <span class="dot" style="background:${col.color}"></span>
            ${col.label}
          </div>
          <span class="pipeline-count">${cards.length}</span>
        </div>
        <div class="pipeline-cards">
          ${
            cards.length
              ? cards
                  .map(
                    (l) => `
            <div class="pipeline-card" data-id="${l.id}" draggable="true">
              <div class="drag-handle">⋮⋮ GLISSER</div>
              <div class="title">${escapeHtml(l.property_title || l.owner)}</div>
              <div class="meta">${escapeHtml(l.address || "—")}</div>
              <div class="footer">${renderMandatePill(l)} ${getTypeBadge(l)}<span class="price-tag">${formatPrice(l)}</span></div>
            </div>`,
                  )
                  .join("")
              : `<div class="empty-state" style="padding:1rem"><p style="font-size:0.8rem">Déposez une carte ici</p></div>`
          }
        </div>
      </div>`;
  }).join("");

  setupPipelineBoard();
}

function escapeAttr(str) {
  return escapeHtml(str ?? "").replace(/"/g, "&quot;");
}

function formatSourceScanHint(lastScan) {
  if (!lastScan) return "";
  try {
    const d = new Date(lastScan);
    if (Number.isNaN(d.getTime())) return "";
    return `Dernier scan : ${d.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" })}`;
  } catch {
    return "";
  }
}

function buildSourceCardHtml(s, { saved = false, job = null } = {}) {
  const stats = getSourceDisplayStats(s, job || crawlState.lastJob);
  const hasError = Boolean(s.last_error) && !stats.isActiveSource;
  const scanHint = formatSourceScanHint(s.last_scan);
  const displayUrl = getSourceDisplayUrl(s);
  const liveCrawl =
    stats.isActiveSource && stats.job
      ? `<p class="source-live-crawl">
          Crawl en cours —
          ${stats.job.listings_done || 0}/${stats.job.listings_total || "?"} annonces ·
          ${stats.job.leads_found || 0} analysées ·
          ${stats.job.leads_saved || 0} nouveaux ·
          ${stats.job.leads_updated || 0} màj
        </p>`
      : "";
  const extraClass = [
    hasError ? "source-card--error" : "",
    saved ? "source-card--saved" : "",
    sourceUrlDirty.has(s.id) ? "source-card--dirty" : "",
    sourceUrlSaving.has(s.id) ? "source-card--saving" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return `
    <div class="source-card ${extraClass}" data-source-id="${s.id}" data-search-url="${escapeAttr(displayUrl)}">
      <div class="source-card-header">
        <div class="source-name">
          <div class="source-logo-img">
            ${getSourceLogoHtml(s)}
          </div>
          <div class="source-name-text">
            <span class="source-title">${escapeHtml(s.name)}</span>
            ${s.is_custom ? '<span class="source-custom-badge">Personnalisé</span>' : ""}
            ${s.is_antibot && !s.is_custom ? '<span class="source-antibot-badge">Bientôt disponible</span>' : ""}
            ${s.is_default_portal && !s.is_custom && !s.is_antibot ? '<span class="source-reliable-badge">Recommandé</span>' : ""}
            ${hasError ? '<span class="source-status-badge source-status-badge--error">Erreur crawl</span>' : ""}
            ${saved ? '<span class="source-status-badge source-status-badge--ok">Lien enregistré</span>' : ""}
          </div>
        </div>
        <div class="source-card-actions">
          <label class="toggle-switch" title="Activer / désactiver">
            <input type="checkbox" data-source="${s.id}" ${s.enabled ? "checked" : ""}>
            <span class="toggle-slider"></span>
          </label>
          ${
            s.is_default_portal
              ? ""
              : `<button type="button" class="btn btn-ghost source-delete-btn" data-source="${s.id}" data-name="${escapeAttr(s.name)}" title="Supprimer la source">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path d="M3 6h18M8 6V4h8v2m-1 0v14H9V6"/></svg>
          </button>`
          }
        </div>
      </div>
      ${hasError ? `<p class="source-error-msg">${escapeHtml(s.last_error)}</p>` : ""}
      ${liveCrawl}
      ${scanHint ? `<p class="source-scan-hint">${escapeHtml(scanHint)}</p>` : ""}
      <div class="source-url-edit">
        <label class="source-url-label" for="source-url-${s.id}">Lien de liste / recherche</label>
        <div class="source-url-row">
          <input type="url" id="source-url-${s.id}" class="source-url-input" data-source="${s.id}" value="${escapeAttr(displayUrl)}" placeholder="https://www.exemple.fr/annonces/" autocomplete="off" spellcheck="false">
          <button type="button" class="btn btn-ghost btn-sm source-save-url-btn" data-source="${s.id}" title="Enregistrer le lien">Enregistrer</button>
        </div>
        <div class="source-url-meta">
          ${displayUrl ? `<a href="${escapeAttr(displayUrl)}" class="source-url-open" target="_blank" rel="noopener noreferrer">Ouvrir le lien</a>` : ""}
          <span class="source-url-hint">${saved ? "Utilisé pour le prochain crawl" : "Modifiez puis Entrée, clic dehors ou Enregistrer"}</span>
        </div>
      </div>
      <div class="source-stats">
        <div class="source-stat-item">
          <div class="label">Prospects en base</div>
          <div class="value source-stat-leads" data-source="${s.id}">${stats.inDb}</div>
        </div>
        <div class="source-stat-item">
          <div class="label">Màj aujourd'hui</div>
          <div class="value source-stat-today" data-source="${s.id}">${stats.updatedToday}</div>
        </div>
      </div>
      <div class="source-progress"><div class="bar"><div class="fill source-stat-progress" data-source="${s.id}" style="width:${stats.inDb > 0 ? 100 : 0}%"></div></div></div>
      ${
        s.is_antibot && !s.is_custom
          ? `<p class="source-paid-hint form-hint">Portail protégé (Cloudflare / anti-bot) — crawl bientôt disponible (pas encore activé).</p>`
          : `<button class="btn btn-secondary source-crawl-btn" data-source="${s.id}" data-name="${escapeAttr(s.name)}" ${crawlState.active ? "disabled" : ""}>
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor"><path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
        Crawler
      </button>`
      }
    </div>`;
}

function updateSourceCardsLive(job) {
  for (const s of SOURCES) {
    const stats = getSourceDisplayStats(s, job);
    const card = document.querySelector(`.source-card[data-source-id="${CSS.escape(s.id)}"]`);
    if (!card) continue;
    const leadsEl = card.querySelector(".source-stat-leads");
    const todayEl = card.querySelector(".source-stat-today");
    const progEl = card.querySelector(".source-stat-progress");
    if (leadsEl) leadsEl.textContent = String(stats.inDb);
    if (todayEl) todayEl.textContent = String(stats.updatedToday);
    if (progEl) progEl.style.width = `${stats.inDb > 0 ? 100 : 0}%`;

    let liveEl = card.querySelector(".source-live-crawl");
    if (stats.isActiveSource && stats.job) {
      const text = `Crawl en cours — ${stats.job.listings_done || 0}/${stats.job.listings_total || "?"} annonces · ${stats.job.leads_found || 0} analysées · ${stats.job.leads_saved || 0} nouveaux · ${stats.job.leads_updated || 0} màj`;
      if (!liveEl) {
        liveEl = document.createElement("p");
        liveEl.className = "source-live-crawl";
        const anchor = card.querySelector(".source-error-msg") || card.querySelector(".source-card-header");
        anchor?.insertAdjacentElement("afterend", liveEl);
      }
      liveEl.textContent = text;
      card.classList.add("source-card--live");
    } else {
      liveEl?.remove();
      card.classList.remove("source-card--live");
    }
  }
}

function refreshSourceCard(sourceId, { saved = false } = {}) {
  const s = SOURCES.find((x) => x.id === sourceId);
  if (!s) {
    renderCrawler();
    return;
  }
  const existing = document.querySelector(`.source-card[data-source-id="${CSS.escape(sourceId)}"]`);
  if (!existing) {
    renderCrawler();
    return;
  }
  const wrap = document.createElement("div");
  wrap.innerHTML = buildSourceCardHtml(s, { saved }).trim();
  const next = wrap.firstElementChild;
  existing.replaceWith(next);
  if (saved) {
    setTimeout(() => {
      const card = document.querySelector(`.source-card[data-source-id="${CSS.escape(sourceId)}"]`);
      card?.classList.remove("source-card--saved");
      card?.querySelector(".source-status-badge--ok")?.remove();
    }, 4000);
  }
}

function renderCrawler() {
  const reliable = SOURCES.filter((s) => !s.is_custom && !s.is_antibot);
  const antibot = SOURCES.filter((s) => !s.is_custom && s.is_antibot);
  const custom = SOURCES.filter((s) => s.is_custom);

  const reliableEl = document.getElementById("sources-grid-reliable");
  const antibotEl = document.getElementById("sources-grid-antibot");
  const customEl = document.getElementById("sources-grid-custom");
  const customWrap = document.getElementById("sources-custom-wrap");

  if (reliableEl) {
    reliableEl.innerHTML = reliable.length
      ? reliable.map((s) => buildSourceCardHtml(s)).join("")
      : '<p class="form-hint">Chargement des portails…</p>';
  }
  if (antibotEl) {
    antibotEl.innerHTML = antibot.map((s) => buildSourceCardHtml(s)).join("");
  }
  if (customEl && customWrap) {
    customWrap.hidden = custom.length === 0;
    customEl.innerHTML = custom.map((s) => buildSourceCardHtml(s)).join("");
  }

  updateCrawlerSummary();
  scheduleSourceUrlsForCity();
}

function updateBadges() {
  document.getElementById("badge-leads").textContent = LEADS.length;
  document.querySelectorAll(".filter-chip").forEach((chip) => {
    const filter = chip.dataset.filter;
    const countEl = chip.querySelector(".count");
    if (!countEl) return;
    let count = LEADS.length;
    if (filter === "particulier") count = LEADS.filter((l) => l.type === "particulier").length;
    else if (filter === "sans-agence") count = LEADS.filter((l) => l.type !== "agence").length;
    else if (filter === "avec-agence") count = LEADS.filter((l) => l.type === "agence").length;
    else if (filter === "vente") count = LEADS.filter((l) => (l.transaction_type || "vente") === "vente").length;
    else if (filter === "location") count = LEADS.filter((l) => l.transaction_type === "location").length;
    else if (filter === "nouveau") count = LEADS.filter((l) => l.status === "nouveau").length;
    else if (filter === "retire") count = LEADS.filter((l) => l.status === "retire").length;
    else if (filter === "hot-mandate")
      count = LEADS.filter((l) => l.status !== "retire" && (l.mandate_score || 0) >= 85).length;
    else if (filter === "price-drop")
      count = LEADS.filter((l) => (l.alert_tags || []).includes("baisse_prix")).length;
    else if (filter === "dvf-sous-marche")
      count = LEADS.filter((l) =>
        ["sous_marche", "leger_sous_marche"].includes(l.dvf_verdict),
      ).length;
    countEl.textContent = count;
  });
}

function updateSidebarCount() {
  const el = document.querySelector(".sidebar-footer .count");
  if (el) el.textContent = SOURCES.reduce((a, s) => a + s.today, 0);
}

function renderFactsVerificationHtml(lead) {
  const audit = lead.facts_audit;
  if (!audit) return "";

  const passed = audit.checks_passed || [];
  const failed = audit.checks_failed || [];
  const sources = audit.sources || {};
  const sourceCount = Object.values(sources).reduce((n, arr) => n + (arr?.length || 0), 0);
  const statusClass = failed.length ? "facts-audit-warn" : "facts-audit-ok";
  const statusLabel = failed.length
    ? `${failed.length} alerte${failed.length > 1 ? "s" : ""}`
    : `${passed.length} vérif. OK`;

  const chips = passed
    .slice(0, 6)
    .map((c) => `<span class="facts-chip facts-chip-ok">${escapeHtml(c)}</span>`)
    .join("");
  const alerts = failed
    .map((c) => `<span class="facts-chip facts-chip-warn">${escapeHtml(c)}</span>`)
    .join("");

  const sourceLines = ["title", "price", "surface", "published_at"]
    .filter((k) => sources[k]?.length)
    .map((k) => {
      const label =
        k === "title"
          ? "Titre"
          : k === "price"
            ? "Prix"
            : k === "surface"
              ? "Surface"
              : "Date";
      return `<div class="facts-source-row"><span>${label}</span><span>${escapeHtml([...new Set(sources[k])].join(", "))}</span></div>`;
    })
    .join("");

  return `<div class="drawer-facts-block ${statusClass}">
    <div class="drawer-facts-head">
      <strong>Vérification annonce</strong>
      <span class="facts-status-pill">${escapeHtml(statusLabel)} · ${sourceCount} sources</span>
    </div>
    ${lead.listing_title ? `<div class="facts-listing-title">${escapeHtml(lead.listing_title)}</div>` : ""}
    <div class="facts-chips">${chips}${alerts}</div>
    ${sourceLines ? `<div class="facts-sources">${sourceLines}</div>` : ""}
  </div>`;
}

function renderLeadJourneyHtml(lead) {
  const current = leadPipelineKey(lead);
  const currentIdx = PIPELINE_STAGES.findIndex((s) => s.key === current);
  const isLost = current === "perdu";
  const stepsHtml = PIPELINE_STAGES.filter((s) => s.key !== "perdu")
    .map((s, i) => {
      let cls = "lead-journey-step";
      if (isLost) cls += "";
      else if (i < currentIdx) cls += " is-done";
      else if (s.key === current) cls += " is-current";
      return `<span class="${cls}">${s.label}</span>`;
    })
    .join("");
  const lostHtml = isLost
    ? `<span class="lead-journey-step is-lost">Perdu</span>`
    : "";
  const notes = (lead.notes || "").trim();
  const notesPreview = notes
    ? escapeHtml(notes.length > 220 ? `${notes.slice(0, 220)}…` : notes)
    : "<em>Aucune note — complétez dans le formulaire ci-dessous.</em>";
  const txLabel = lead.transaction_type === "location" ? "location" : "vente";

  return `
    <div class="lead-journey" id="drawer-journey-block">
      <div class="lead-journey-title">Parcours de A à Z</div>
      <div class="lead-journey-steps">${stepsHtml}${lostHtml}</div>
      <div class="lead-journey-actions">
        <button type="button" class="btn btn-primary btn-sm" id="drawer-journey-call">Appeler</button>
        <button type="button" class="btn btn-secondary btn-sm" id="drawer-journey-script">Script</button>
        <button type="button" class="btn btn-secondary btn-sm" id="drawer-journey-mandat">Créer mandat</button>
        <button type="button" class="btn btn-secondary btn-sm" id="drawer-journey-livret">Livret PDF</button>
      </div>
      <div class="lead-journey-notes"><strong>Suivi :</strong> ${notesPreview}</div>
      <p class="form-hint" style="margin:0.5rem 0 0;font-size:0.75rem">Mandat ${txLabel} → dossier client, photos, notes → bouton Imprimer / PDF dans l’éditeur de mandat.</p>
    </div>`;
}

const ESTIMATOR_PROPERTY_TYPES = [
  ["appartement", "Appartement"],
  ["maison", "Maison"],
  ["studio", "Studio"],
  ["terrain", "Terrain"],
  ["autre", "Autre"],
];
const ESTIMATOR_CONDITIONS = [
  ["neuf", "Neuf / récent"],
  ["bon", "Bon état"],
  ["standard", "Standard"],
  ["rafraichir", "À rafraîchir"],
  ["renover", "À rénover"],
];
const ESTIMATOR_FEATURES = [
  ["has_elevator", "Ascenseur"],
  ["has_parking", "Parking / box"],
  ["has_outdoor", "Balcon / terrasse / jardin"],
  ["has_view", "Belle vue"],
  ["noise_nuisance", "Nuisances (bruit, vis-à-vis…)"],
  ["prime_sector", "Quartier très recherché"],
];

const drawerEstimates = new Map();

function guessLeadPropertyType(lead) {
  const t = (
    lead.property_title ||
    lead.listing_title ||
    lead.address ||
    ""
  ).toLowerCase();
  if (t.includes("studio")) return "studio";
  if (/\b(maison|villa|pavillon|longère)\b/.test(t)) return "maison";
  if (t.includes("terrain")) return "terrain";
  if (/\b(appart|duplex|loft|t[1-5]|f[1-5])\b/.test(t)) return "appartement";
  return "appartement";
}

function parseLeadRooms(lead) {
  const t = (
    lead.property_title ||
    lead.listing_title ||
    ""
  ).toLowerCase();
  const m = t.match(/\b(t|f)\s*(\d)\b/i) || t.match(/(\d+)\s*pi[eè]ce/);
  if (m) {
    const n = parseInt(m[2] || m[1], 10);
    if (n > 0 && n < 20) return n;
  }
  return "";
}

function fmtEuro(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n).toLocaleString("fr-FR")} €`;
}

function animateEstimatorTotal(scope) {
  const el = (scope || document).querySelector(".drawer-estimator-total[data-count-to]");
  if (!el) return;
  const target = Number(el.dataset.countTo) || 0;
  if (
    !target ||
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
  ) {
    el.textContent = fmtEuro(target);
    return;
  }
  el.removeAttribute("data-count-to");
  const duration = 850;
  const start = performance.now();
  const tick = (now) => {
    const p = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
    el.textContent = fmtEuro(Math.round(target * eased));
    if (p < 1) requestAnimationFrame(tick);
    else el.textContent = fmtEuro(target);
  };
  requestAnimationFrame(tick);
}

function collectEstimatorInputs(lead, prefix = "tab-est") {
  const form = document.getElementById(`${prefix}-form`);
  const surface =
    parseFloat(form?.querySelector(`#${prefix}-surface`)?.value) ||
    parseFloat(lead.surface) ||
    0;
  const inputs = {
    surface,
    property_type:
      form?.querySelector(`#${prefix}-property-type`)?.value || guessLeadPropertyType(lead),
    rooms: form?.querySelector(`#${prefix}-rooms`)?.value || parseLeadRooms(lead) || null,
    condition: form?.querySelector(`#${prefix}-condition`)?.value || "standard",
    address: form?.querySelector(`#${prefix}-address`)?.value || lead.address || "",
    city: form?.querySelector(`#${prefix}-city`)?.value || lead.city || "",
    postcode: form?.querySelector(`#${prefix}-postcode`)?.value || lead.postcode || "",
    sector: lead.sector || lead.dvf_sector || "",
  };
  ESTIMATOR_FEATURES.forEach(([key]) => {
    inputs[key] = !!form?.querySelector(`#${prefix}-${key}`)?.checked;
  });
  return inputs;
}

function renderPriceEstimateResultHtml(result) {
  if (!result?.ok) {
    return `<p class="drawer-estimator-error">${escapeHtml(result?.reason || result?.error || "Estimation indisponible")}</p>`;
  }
  const confCls = result.confidence || "low";
  const delta =
    result.delta_vs_estimate_pct != null
      ? `<p class="drawer-estimator-delta">Annonce : <strong>${fmtEuro(result.listing_price)}</strong> · écart <strong>${result.delta_vs_estimate_pct > 0 ? "+" : ""}${result.delta_vs_estimate_pct} %</strong> vs estimation</p>`
      : "";
  const adj =
    result.adjustments?.length > 0
      ? `<ul class="drawer-estimator-adj">${result.adjustments
          .map(
            (a) =>
              `<li>${escapeHtml(a.label)} <span>${a.pct > 0 ? "+" : ""}${a.pct} %</span></li>`,
          )
          .join("")}</ul>`
      : "";
  const method = (result.methodology || [])
    .map((line) => `<li>${escapeHtml(line)}</li>`)
    .join("");
  return `
    <div class="drawer-estimator-result" data-confidence="${confCls}">
      <div class="drawer-estimator-main">
        <span class="drawer-estimator-label">Estimation indicative</span>
        <strong class="drawer-estimator-total" data-count-to="${Number(result.estimate_total) || 0}">${fmtEuro(result.estimate_total)}</strong>
        <span class="drawer-estimator-range">${fmtEuro(result.range_low)} – ${fmtEuro(result.range_high)}</span>
      </div>
      <p class="drawer-estimator-meta">
        <span class="drawer-estimator-conf conf-${confCls}">Confiance ${escapeHtml(result.confidence_label || "")}</span>
        · ${result.sample_count || 0} ventes DVF · ${escapeHtml(result.reference_period || "")}
        · ${escapeHtml(result.commune || result.sector || "")}
      </p>
      <p class="drawer-estimator-m2">
        Base DVF <strong>${(result.median_m2 || 0).toLocaleString("fr-FR")} €/m²</strong>
        · retenu <strong>${fmtEuro(result.price_per_m2)}/m²</strong>
        · surface ${result.surface} m²
      </p>
      ${delta}
      ${adj}
      ${method ? `<ol class="drawer-estimator-method">${method}</ol>` : ""}
      <p class="drawer-estimator-disclaimer">${escapeHtml(result.disclaimer || "")}</p>
      <div class="drawer-estimator-actions">
        <button type="button" class="btn btn-primary btn-sm est-pdf-btn">📄 Dossier d'estimation (PDF)</button>
      </div>
    </div>`;
}

function resolveEstimatorContextLead() {
  if (state.currentView === "estimateur") {
    const l = resolveEstimatorLead();
    if (l) return l;
  }
  if (state.selectedLead) return state.selectedLead;
  return null;
}

function renderEstimatorFormHtml(lead, prefix = "tab-est") {
  const propType = guessLeadPropertyType(lead);
  const rooms = parseLeadRooms(lead);
  const addr = lead.address && lead.address !== "—" ? lead.address : "";
  const typeOpts = ESTIMATOR_PROPERTY_TYPES.map(
    ([v, l]) => `<option value="${v}"${v === propType ? " selected" : ""}>${l}</option>`,
  ).join("");
  const condOpts = ESTIMATOR_CONDITIONS.map(
    ([v, l]) => `<option value="${v}"${v === "standard" ? " selected" : ""}>${l}</option>`,
  ).join("");
  const featHtml = ESTIMATOR_FEATURES.map(
    ([key, label]) =>
      `<label class="drawer-estimator-check"><input type="checkbox" id="${prefix}-${key}" name="${key}"> ${escapeHtml(label)}</label>`,
  ).join("");
  const cached = drawerEstimates.get(lead.id) || lead.price_estimate || null;
  return `
    <form id="${prefix}-form" class="drawer-estimator-form" onsubmit="return false">
      <div class="drawer-estimator-grid">
        <label class="drawer-estimator-field">
          <span>Surface (m²)</span>
          <input type="number" id="${prefix}-surface" min="1" step="0.1" required value="${lead.surface != null ? lead.surface : ""}">
        </label>
        <label class="drawer-estimator-field">
          <span>Pièces</span>
          <input type="number" id="${prefix}-rooms" min="1" max="15" placeholder="—" value="${rooms}">
        </label>
        <label class="drawer-estimator-field">
          <span>Type de bien</span>
          <select id="${prefix}-property-type">${typeOpts}</select>
        </label>
        <label class="drawer-estimator-field">
          <span>État</span>
          <select id="${prefix}-condition">${condOpts}</select>
        </label>
        <label class="drawer-estimator-field drawer-estimator-field--wide">
          <span>Adresse</span>
          <input type="text" id="${prefix}-address" value="${escapeAttr(addr)}" placeholder="Rue, n°">
        </label>
        <label class="drawer-estimator-field">
          <span>Ville</span>
          <input type="text" id="${prefix}-city" value="${escapeAttr(lead.city || "")}">
        </label>
        <label class="drawer-estimator-field">
          <span>CP</span>
          <input type="text" id="${prefix}-postcode" value="${escapeAttr(lead.postcode || "")}" maxlength="5">
        </label>
      </div>
      <div class="drawer-estimator-features">${featHtml}</div>
      <button type="button" class="btn btn-primary" id="${prefix}-calc-btn">Calculer l'estimation</button>
    </form>
    <div id="${prefix}-result" class="drawer-estimator-result-wrap">${cached ? renderPriceEstimateResultHtml(cached) : ""}</div>`;
}

function renderDrawerEstimatorCta(lead) {
  if ((lead.transaction_type || "vente").toLowerCase() === "location") {
    return `<div class="drawer-section drawer-estimator-cta">
      <div class="drawer-section-title">Estimateur de prix</div>
      <p class="form-hint">Réservé aux biens en <strong>vente</strong>.</p>
    </div>`;
  }
  return `<div class="drawer-section drawer-estimator-cta">
    <div class="drawer-section-title">Estimateur de prix</div>
    <p class="form-hint">Fourchette DVF + critères du bien.</p>
    <button type="button" class="btn btn-primary btn-sm" id="drawer-open-estimator-btn">Ouvrir l'estimateur</button>
  </div>`;
}

function getVenteLeadsForEstimator() {
  return LEADS.filter((l) => (l.transaction_type || "vente").toLowerCase() !== "location");
}

function resolveEstimatorLead() {
  const vente = getVenteLeadsForEstimator();
  if (!vente.length) return null;
  const id = state.estimatorLeadId;
  if (id) {
    const found = vente.find((l) => l.id === id);
    if (found) return found;
  }
  if (state.selectedLead && (state.selectedLead.transaction_type || "vente") !== "location") {
    const fromDrawer = vente.find((l) => l.id === state.selectedLead.id);
    if (fromDrawer) return fromDrawer;
  }
  return vente[0];
}

function renderEstimateurView() {
  const root = document.getElementById("estimateur-root");
  if (!root) return;

  const vente = getVenteLeadsForEstimator();
  if (!vente.length) {
    root.innerHTML = `
      <div class="estimateur-empty">
        <h3>Aucun prospect en vente</h3>
        <p class="form-hint">L'estimateur s'applique aux annonces <strong>vente</strong>. Crawlez des sources ou importez une fiche vente.</p>
        <button type="button" class="btn btn-secondary" onclick="switchView('crawler')">Alimenter le radar</button>
      </div>`;
    return;
  }

  const lead = resolveEstimatorLead();
  if (!lead) return;
  state.estimatorLeadId = lead.id;

  const options = vente
    .map((l) => {
      const label = [
        l.property_title || l.listing_title || l.address || `Prospect #${l.id}`,
        l.city,
        l.price ? `${Number(l.price).toLocaleString("fr-FR")} €` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      return `<option value="${l.id}"${l.id === lead.id ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");

  const priceLine = lead.price ? formatPrice(lead) : "—";
  const ms = lead.mandate_score || 0;

  root.innerHTML = `
    <div class="estimateur-layout">
      <aside class="estimateur-sidebar">
        <p class="estimateur-intro">
          Estimation indicative à partir des <strong>ventes DVF réelles</strong> (Etalab) sur le secteur,
          ajustée selon le bien — première passe type Meilleurs Agents.
        </p>
        <label class="estimateur-lead-picker">
          <span class="estimateur-lead-picker-label">Prospect à estimer</span>
          <select id="tab-est-lead-select" class="estimateur-lead-select">${options}</select>
        </label>
        <div class="estimateur-lead-card">
          <strong>${escapeHtml(lead.property_title || lead.listing_title || "Bien")}</strong>
          <p>${escapeHtml(lead.address || "—")}${lead.city ? `, ${escapeHtml(lead.city)}` : ""}</p>
          <p class="estimateur-lead-meta">${escapeHtml(priceLine)} · ${signatureProbability(lead)} % de chance de signer</p>
          <button type="button" class="btn btn-ghost btn-sm" id="tab-est-open-drawer">Voir la fiche</button>
        </div>
      </aside>
      <div class="estimateur-main drawer-estimator">
        <h3 class="estimateur-form-title">Critères du bien</h3>
        ${renderEstimatorFormHtml(lead, "tab-est")}
      </div>
    </div>`;

  animateEstimatorTotal(root);
}

function openEstimateurTab(leadId) {
  if (leadId != null) state.estimatorLeadId = leadId;
  switchView("estimateur");
}

async function runPriceEstimate(lead, prefix = "tab-est") {
  const btn = document.getElementById(`${prefix}-calc-btn`);
  const wrap = document.getElementById(`${prefix}-result`);
  if (!wrap || !lead) return;
  const inputs = collectEstimatorInputs(lead, prefix);
  if (!inputs.surface || inputs.surface <= 0) {
    showToast("Indiquez une surface en m² pour estimer", "warning");
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Calcul en cours…";
  }
  wrap.innerHTML = `<p class="drawer-estimator-loading">Analyse DVF et ajustements…</p>`;
  try {
    // save:true -> l'estimation est persistée sur le lead (cohérence inter-onglets)
    const result = await api(`/leads/${lead.id}/estimate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ inputs, save: true }),
    });
    if (!result.ok) {
      wrap.innerHTML = renderPriceEstimateResultHtml(result);
      showToast(result.reason || "Estimation impossible", "warning", 6000);
      return;
    }
    drawerEstimates.set(lead.id, result);
    // Synchronise le lead en mémoire (fiche, liste, carte) avec l'estimation enregistrée.
    if (result.lead) {
      const idx = LEADS.findIndex((l) => Number(l.id) === Number(lead.id));
      if (idx >= 0) LEADS[idx] = result.lead;
      if (Number(state.selectedLead?.id) === Number(lead.id)) state.selectedLead = result.lead;
    }
    wrap.innerHTML = renderPriceEstimateResultHtml(result);
    animateEstimatorTotal(wrap);
    showToast(
      `Estimation ${fmtEuro(result.estimate_total)} (${result.confidence_label})`,
      "success",
      6000,
    );
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Calculer l'estimation";
    }
  }
}

// ─── Dossier d'estimation imprimable (PDF) ───
function estimatorDossierData(lead) {
  return drawerEstimates.get(lead?.id) || lead?.price_estimate || null;
}

function buildEstimationDossierHtml(lead, est, profile) {
  const ag = profile || {};
  const agencyName = escapeHtml(ag.brand_name || ag.legal_name || state.user?.agency_name || "Votre agence");
  const agencyLines = [
    ag.address,
    [ag.postal_code, ag.city].filter(Boolean).join(" "),
    ag.phone ? `Tél. ${ag.phone}` : "",
    ag.email || "",
    ag.rsac || ag.siren ? `SIREN ${escapeHtml(ag.siren || ag.rsac)}` : "",
  ].filter(Boolean).map((l) => escapeHtml(l)).join("<br>");
  const today = new Date().toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" });
  const owner = escapeHtml(lead.owner || "—");
  const addr = escapeHtml([lead.address, [lead.postcode, lead.city].filter(Boolean).join(" ")].filter((s) => s && s !== "—").join(", ") || "—");
  const adjRows = (est.adjustments || [])
    .map((a) => `<tr><td>${escapeHtml(a.label)}</td><td class="num">${a.pct > 0 ? "+" : ""}${a.pct} %</td></tr>`)
    .join("");
  const method = (est.methodology || []).map((m) => `<li>${escapeHtml(m)}</li>`).join("");
  const typeLabel = { appartement: "Appartement", maison: "Maison", studio: "Studio", terrain: "Terrain", autre: "Autre" }[est.property_type] || "Bien";
  return `
  <div class="est-doc">
    <header class="est-head">
      <div class="est-agency">${agencyName}<br><span class="est-agency-sub">${agencyLines}</span></div>
      <div class="est-doc-meta">Avis de valeur indicatif<br><span>${today}</span></div>
    </header>
    <h1>Dossier d'estimation</h1>
    <section class="est-bien">
      <h2>Le bien</h2>
      <table class="est-kv">
        <tr><td>Propriétaire</td><td>${owner}</td></tr>
        <tr><td>Adresse</td><td>${addr}</td></tr>
        <tr><td>Type</td><td>${escapeHtml(typeLabel)}${est.rooms ? ` · ${est.rooms} pièce(s)` : ""}</td></tr>
        <tr><td>Surface</td><td>${est.surface} m²</td></tr>
        <tr><td>État</td><td>${escapeHtml(est.condition || "standard")}</td></tr>
      </table>
    </section>
    <section class="est-value">
      <h2>Estimation de valeur vénale</h2>
      <div class="est-total">${fmtEuro(est.estimate_total)}</div>
      <div class="est-range">Fourchette : ${fmtEuro(est.range_low)} – ${fmtEuro(est.range_high)} · ${escapeHtml(est.price_per_m2 ? est.price_per_m2.toLocaleString("fr-FR") + " €/m²" : "")}</div>
      <p class="est-conf">Confiance ${escapeHtml(est.confidence_label || "")} · ${est.sample_count || 0} ventes DVF (${escapeHtml(est.reference_period || "")}) · ${escapeHtml(est.commune || est.sector || "")}</p>
    </section>
    ${adjRows ? `<section><h2>Ajustements appliqués</h2><table class="est-adj"><tbody>${adjRows}</tbody></table></section>` : ""}
    ${method ? `<section><h2>Méthodologie</h2><ol class="est-method">${method}</ol></section>` : ""}
    <p class="est-disclaimer">${escapeHtml(est.disclaimer || "")}</p>
    <div class="est-sig">
      <div><span>Le propriétaire</span></div>
      <div><span>${agencyName}</span></div>
    </div>
  </div>`;
}

async function printEstimationDossier(lead) {
  const est = estimatorDossierData(lead);
  if (!est || !est.ok) {
    showToast("Lancez d'abord une estimation", "warning");
    return;
  }
  let profile = null;
  try {
    const r = await api("/mandates/agency-profile", { headers: getAuthHeaders() });
    profile = r?.profile || null;
  } catch {
    /* en-tête optionnel */
  }
  const w = window.open("", "_blank");
  if (!w) {
    showToast("Autorisez les pop-ups pour générer le PDF", "warning");
    return;
  }
  const body = buildEstimationDossierHtml(lead, est, profile);
  w.document.write(`<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Dossier d'estimation</title>
    <style>
      body { font-family: Georgia, 'Times New Roman', serif; max-width: 820px; margin: 2rem auto; padding: 0 2rem; color: #1e3340; line-height: 1.55; }
      .est-head { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #9a7349; padding-bottom: 1rem; margin-bottom: 1.5rem; }
      .est-agency { font-size: 1.05rem; font-weight: 700; }
      .est-agency-sub { font-size: 0.78rem; font-weight: 400; color: #555; }
      .est-doc-meta { text-align: right; font-size: 0.9rem; color: #9a7349; font-weight: 600; }
      .est-doc-meta span { color: #555; font-weight: 400; }
      h1 { font-size: 1.6rem; text-align: center; margin: 0 0 1.5rem; letter-spacing: 0.02em; }
      h2 { font-size: 1rem; color: #9a7349; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; margin: 1.4rem 0 0.6rem; }
      table { width: 100%; border-collapse: collapse; }
      .est-kv td, .est-adj td { padding: 0.35rem 0; border-bottom: 1px solid #eee; font-size: 0.92rem; }
      .est-kv td:first-child { color: #555; width: 35%; }
      .est-adj td.num, .num { text-align: right; font-weight: 600; }
      .est-value { text-align: center; background: #f6f4f0; border: 1px solid #ddd6cb; border-radius: 10px; padding: 1.2rem; margin: 1rem 0; }
      .est-total { font-size: 2.4rem; font-weight: 700; color: #1e3340; }
      .est-range { font-size: 1rem; color: #333; margin-top: 0.3rem; }
      .est-conf { font-size: 0.82rem; color: #666; margin-top: 0.4rem; }
      .est-method { font-size: 0.85rem; color: #444; padding-left: 1.2rem; }
      .est-disclaimer { font-size: 0.72rem; color: #777; margin-top: 1.5rem; font-style: italic; }
      .est-sig { display: grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; margin-top: 2.5rem; }
      .est-sig div { border-top: 1px solid #999; padding-top: 0.4rem; font-size: 0.85rem; min-height: 4rem; }
      @media print { body { margin: 0; max-width: none; } @page { margin: 1.5cm; } }
    </style></head><body>${body}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 350);
}

function setupEstimateur() {
  const root = document.getElementById("view-estimateur");
  if (!root || root.dataset.wired === "1") return;
  root.dataset.wired = "1";

  root.addEventListener("change", (e) => {
    if (e.target.id !== "tab-est-lead-select") return;
    state.estimatorLeadId = parseInt(e.target.value, 10);
    renderEstimateurView();
  });

  root.addEventListener("click", (e) => {
    if (e.target.closest("#tab-est-calc-btn")) {
      e.preventDefault();
      const lead = resolveEstimatorLead();
      if (lead) runPriceEstimate(lead, "tab-est").catch((err) => showToast(err.message, "error"));
      return;
    }
    if (e.target.closest("#tab-est-open-drawer")) {
      e.preventDefault();
      const lead = resolveEstimatorLead();
      if (lead) openDrawer(lead.id);
    }
  });

  // Bouton « Dossier d'estimation (PDF) » — présent dans l'estimateur ET la fiche.
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".est-pdf-btn")) return;
    e.preventDefault();
    const lead = resolveEstimatorContextLead();
    if (lead) printEstimationDossier(lead).catch((err) => showToast(err.message, "error"));
    else showToast("Sélectionnez un prospect", "warning");
  });
}

function buildDrawerDvfHtml(lead) {
  let dvfHtml = `<div class="drawer-dvf-block">
    <strong>Comparatif DVF</strong> — ventes réelles (DGFiP / <a href="${DVF_APP_URL}" target="_blank" rel="noopener">Etalab</a>)<br>
    Cliquez sur « Comparatif DVF » pour analyser ce bien.`;
  if (lead.dvf_verdict) {
    const sign = lead.dvf_delta_pct > 0 ? "+" : "";
    const locParts = [
      lead.dvf_sector || lead.sector || lead.city,
      lead.postcode ? `(${lead.postcode})` : "",
    ].filter(Boolean).join(" ");
    const pubDate = lead.published_at ? formatPublishedDate(lead) : null;
    const dvfPeriod = lead.dvf_reference_period
      ? `ventes ${lead.dvf_reference_period}`
      : "24 derniers mois";
    dvfHtml = `<div class="drawer-dvf-block">
      <strong>${escapeHtml(lead.dvf_verdict_label || "")}</strong><br>
      <span class="dvf-context">${escapeHtml(locParts || "—")}${pubDate ? ` · Annonce publiée ${pubDate}` : ""}</span><br>
      Annonce : <strong>${lead.price && lead.surface ? Math.round(lead.price / lead.surface).toLocaleString("fr-FR") : "—"} €/m²</strong>
      · Médiane DVF : <strong>${(lead.dvf_median_m2 || 0).toLocaleString("fr-FR")} €/m²</strong>
      (${lead.dvf_sample_count || 0} ventes ${escapeHtml(dvfPeriod)}, ${escapeHtml(lead.dvf_commune || "")})<br>
      Écart : <strong>${sign}${lead.dvf_delta_pct}%</strong>
      · <a href="${DVF_APP_URL}" target="_blank" rel="noopener">Explorer sur DVF</a>`;
  }
  return `${dvfHtml}</div>`;
}

function buildDrawerLeadImageHtml(lead) {
  const url = leadImageUrl(lead);
  const imgBlock = url
    ? `<img class="drawer-lead-image-img" id="drawer-lead-img" src="${escapeHtml(url)}" alt="Photo du bien" decoding="async" fetchpriority="high">`
    : `<div class="drawer-lead-image-placeholder" id="drawer-lead-img-placeholder">Aucune photo — recrawlez l’annonce ou importez une image</div>`;
  const revertDisabled = !lead.image_custom && !lead.has_image ? " disabled" : "";
  return `
    <div class="drawer-lead-image" id="drawer-lead-image-block">
      ${imgBlock}
      <div class="drawer-lead-image-actions">
        <label class="btn btn-secondary btn-sm drawer-image-upload-label">
          Changer la photo
          <input type="file" id="drawer-image-upload" accept="image/jpeg,image/png,image/webp,image/gif" hidden>
        </label>
        <button type="button" class="btn btn-ghost btn-sm" id="drawer-image-revert"${revertDisabled}>Photo du crawl</button>
        <button type="button" class="btn btn-ghost btn-sm" id="drawer-image-sync" title="Re-télécharger depuis le portail">↻ Portail</button>
      </div>
      <p class="form-hint drawer-lead-image-hint">${lead.image_custom ? "Image personnalisée active" : "Image issue du crawl (WebP)"}</p>
    </div>`;
}

async function uploadDrawerLeadImage(lead, file) {
  const fd = new FormData();
  fd.append("image", file);
  const res = await fetch(`/api/leads/${lead.id}/image`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: fd,
    credentials: "same-origin",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "Échec envoi image");
  if (body.lead) {
    mergeLeadInCache(body.lead);
    invalidateDrawerCache(body.lead.id);
    if (state.currentView === "leads") renderLeads();
    refreshDrawerBodyContent(body.lead);
    updateDrawerChrome(body.lead);
  }
  showToast("Photo enregistrée (WebP)", "success");
}

async function revertDrawerLeadImage(lead) {
  const res = await fetch(`/api/leads/${lead.id}/image`, {
    method: "POST",
    headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ action: "revert" }),
    credentials: "same-origin",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "Image crawl indisponible");
  if (body.lead) {
    mergeLeadInCache(body.lead);
    invalidateDrawerCache(body.lead.id);
    if (state.currentView === "leads") renderLeads();
    refreshDrawerBodyContent(body.lead);
    updateDrawerChrome(body.lead);
  }
  showToast("Photo du crawl restaurée", "success");
}

async function syncDrawerLeadImage(lead) {
  const body = await api(`/leads/${lead.id}/image/sync`, { method: "POST" });
  if (body.lead) {
    mergeLeadInCache(body.lead);
    invalidateDrawerCache(body.lead.id);
    if (state.currentView === "leads") renderLeads();
    refreshDrawerBodyContent(body.lead);
    updateDrawerChrome(body.lead);
    showToast("Photo portail mise à jour", "success");
  }
}

function bindDrawerImageHandlers(_lead) {
  /* Délégation globale dans setupDrawer(). */
}

function buildDrawerBodyHtml(lead) {
  const daysTxt =
    lead.days_on_market != null
      ? `${lead.days_on_market} j en ligne`
      : formatPublishedDate(lead)
        ? formatPublishedLine(lead)
        : "Publication inconnue";
  const ms = lead.mandate_score || 0;
  return `
    ${buildDrawerLeadImageHtml(lead)}
    ${renderLeadJourneyHtml(lead)}
    <div class="drawer-mandate-hero">
      <span class="drawer-mandate-kicker">Score Mandat™</span>
      <div class="drawer-mandate-score-row">
        ${renderMandatePill(lead, { large: true })}
        <span class="drawer-mandate-reco">${escapeHtml(mandateCallRecommendation(ms))}</span>
      </div>
      <p class="drawer-mandate-reason">${escapeHtml(lead.mandate_score_reason || "—")}</p>
      <p class="drawer-mandate-hint">Opportunité du marché · ${escapeHtml(lead.city || lead.sector || "secteur")}</p>
      <div class="drawer-mandate-demand" id="drawer-mandate-demand" hidden></div>
    </div>
    ${renderFactsVerificationHtml(lead)}
    ${buildDrawerDvfHtml(lead)}
    <div class="drawer-section drawer-matches" id="drawer-matches" data-lead-id="${lead.id}">
      <div class="drawer-section-title">Acquéreurs &amp; locataires compatibles</div>
      <p class="drawer-matches-loading">Recherche de correspondances…</p>
    </div>
    ${renderDrawerEstimatorCta(lead)}
    ${renderDrawerEditSection(lead)}
    <div class="drawer-section drawer-readonly-summary">
      <div class="drawer-section-title">Résumé</div>
      ${lead.property_title ? `<div class="detail-row"><span class="label">Bien</span><span class="value">${escapeHtml(lead.property_title)}</span></div>` : ""}
      <div class="detail-row" data-drawer-field="owner"><span class="label">Contact</span><span class="value drawer-live-value">${escapeHtml(lead.owner)}</span></div>
      <div class="detail-row" data-drawer-field="price"><span class="label">Prix</span><span class="value drawer-live-value">${formatPrice(lead)} ${getTransactionBadge(lead)}</span></div>
      <div class="detail-row" data-drawer-field="surface"><span class="label">Surface</span><span class="value drawer-live-value">${lead.surface ? lead.surface + " m²" : "—"}</span></div>
      <div class="detail-row"><span class="label">Adresse</span><span class="value">${escapeHtml(lead.address || "—")}</span></div>
      <div class="detail-row"><span class="label">Ville</span><span class="value">${escapeHtml([lead.postcode, lead.city].filter(Boolean).join(" ") || "—")}</span></div>
      <div class="detail-row"><span class="label">Source URL</span><span class="value drawer-link-inline">${lead.source_url ? `<a href="${escapeAttr(lead.source_url)}" target="_blank" rel="noopener noreferrer">Ouvrir</a>` : "—"}</span></div>
      <div class="detail-row"><span class="label">En ligne</span><span class="value">${daysTxt}</span></div>
      <div class="detail-row"><span class="label">Portail</span><span class="value">${escapeHtml(lead.source)}</span></div>
      <div class="detail-row"><span class="label">Détecté le</span><span class="value">${escapeHtml(formatDate(lead.created_at) || "—")}</span></div>
      <div class="detail-row"><span class="label">Màj crawl</span><span class="value">${escapeHtml(formatDate(lead.updated_at) || "—")}</span></div>
      <div class="detail-row" data-drawer-field="score"><span class="label">Complétude données</span><span class="value drawer-live-value"><span class="score-pill ${getScoreClass(lead.score || 0)}">${lead.score || 0}/100</span></span></div>
      <div class="detail-row" data-drawer-field="pipeline"><span class="label">Pipeline</span><span class="value drawer-live-value">${getStatusBadge(lead.pipeline || lead.status)}</span></div>
    </div>`;
}

function updateDrawerPipelineButtons(lead) {
  const pipelineBtns = document.getElementById("drawer-pipeline-btns");
  if (!pipelineBtns) return;
  const current = leadPipelineKey(lead);
  pipelineBtns.innerHTML = PIPELINE_STAGES.map((s) => {
    const active = current === s.key || (!lead.pipeline && s.key === "nouveau");
    return `<button type="button" class="btn btn-sm ${active ? "btn-primary" : "btn-secondary"}" data-pipeline="${s.key}">${s.label}</button>`;
  }).join("");
}

function updateDrawerChrome(lead) {
  document.getElementById("drawer-title").textContent =
    lead.property_title || lead.listing_title || lead.address || "Prospect";

  const listingUrl = (lead.source_url || "").trim();
  const viewListingBtn = document.getElementById("drawer-view-listing-btn");
  if (viewListingBtn) {
    if (listingUrl) {
      viewListingBtn.href = listingUrl;
      viewListingBtn.style.display = "";
    } else {
      viewListingBtn.href = "#";
      viewListingBtn.style.display = "none";
    }
  }

  updateDrawerPipelineButtons(lead);

  const phoneBtn = document.getElementById("drawer-phone-btn");
  if (phoneBtn) {
    const tel = (lead.phone || "").replace(/\s/g, "");
    if (tel && tel !== "—") {
      phoneBtn.href = `tel:${tel}`;
      phoneBtn.style.display = "";
    } else {
      phoneBtn.href = "#";
      phoneBtn.style.display = "none";
    }
  }

  const refreshFooterBtn = document.getElementById("drawer-refresh-lead-btn");
  if (refreshFooterBtn) {
    refreshFooterBtn.dataset.id = String(lead.id);
    // Ne pas désactiver sur la seule copie cache (peut être partielle) : le handler
    // refreshLeadDeep revérifie côté serveur et affiche un message clair si vraiment
    // aucun lien d'annonce. Évite un bouton grisé à tort.
    refreshFooterBtn.disabled = false;
  }

  const mandateType = lead.transaction_type === "location" ? "location" : "vente";
  document.getElementById("drawer-mandate-vente")?.classList.toggle("btn-ghost-dim", mandateType !== "vente");
  document.getElementById("drawer-mandate-location")?.classList.toggle("btn-ghost-dim", mandateType !== "location");
}

function restoreDrawerEstimatePanel(lead) {
  const wrap = document.getElementById("drawer-estimator-result");
  const est = drawerEstimates.get(lead.id) || lead.price_estimate || null;
  if (wrap && est) {
    wrap.innerHTML = renderPriceEstimateResultHtml(est);
    animateEstimatorTotal(wrap);
  }
}

function setDrawerBodyHtml(lead, html) {
  const body = document.getElementById("drawer-body");
  if (!body) return;
  body.innerHTML = html;
  restoreDrawerEstimatePanel(lead);
}

function refreshDrawerBodyContent(lead, { skipCache = false } = {}) {
  const key = drawerFingerprint(lead);
  if (!skipCache && drawerHtmlCache.has(key)) {
    setDrawerBodyHtml(lead, drawerHtmlCache.get(key));
    return;
  }
  const html = buildDrawerBodyHtml(lead);
  drawerHtmlCacheSet(key, html);
  setDrawerBodyHtml(lead, html);
}

function wireDrawerJourneyButtons(_lead) {
  /* Délégation globale dans setupDrawer(). */
}

function openMandateFromLead(lead, type) {
  if (window.VelioraMandates?.createMandateFromLead) {
    window.VelioraMandates.createMandateFromLead(lead.id, type);
  } else {
    showToast("Module mandats indisponible — relancez le serveur", "error");
  }
}

async function openMandateLivretFromLead(lead) {
  const type = lead.transaction_type === "location" ? "location" : "vente";
  openMandateFromLead(lead, type);
  showToast(
    "Mandat ouvert — complétez le dossier puis « Imprimer / PDF » pour le livret projet",
    "info",
    8000,
  );
}

function openDrawer(id) {
  const lead = LEADS.find((l) => l.id === id);
  if (!lead) return;

  const sameLead = state.selectedLead?.id === id;
  if (!sameLead) {
    state.drawerShowAllFields = false;
    state.drawerEditExpanded = false;
  }
  state.selectedLead = lead;

  const overlay = document.getElementById("drawer-overlay");
  const drawerEl = document.getElementById("lead-drawer");
  overlay.classList.add("open");
  drawerEl.classList.add("open");

  const cacheKey = drawerFingerprint(lead);
  const cached = drawerHtmlCache.get(cacheKey);
  if (cached) {
    setDrawerBodyHtml(lead, cached);
  } else {
    refreshDrawerBodyContent(lead);
    prefetchDrawerHtml(lead);
  }

  updateDrawerPipelineButtons(lead);
  requestAnimationFrame(() => {
    updateDrawerChrome(lead);
    const body = document.getElementById("drawer-body");
    if (body) body.scrollTop = 0;
  });
  loadDrawerMatches(lead);
}

const leadMatchCache = new Map();

function renderLeadMatchesHtml(data) {
  if (!data?.ok) return `<p class="drawer-matches-empty">Correspondances indisponibles.</p>`;
  const c = data.counts || {};
  const reco = data.recommended_transaction || "vente";
  const recoLabel = reco === "location" ? "Location" : "Vente";
  const alignedCls = data.aligned ? "match-reco--aligned" : "match-reco--switch";
  const recoHtml = `
    <div class="match-reco ${alignedCls}">
      <span class="match-reco-badge">${escapeHtml(recoLabel)}</span>
      <span class="match-reco-text">${escapeHtml(data.recommendation_reason || "")}</span>
    </div>`;
  const list = (data.top_matches || []).slice(0, 6);
  if (!list.length) {
    return `${recoHtml}<p class="drawer-matches-empty">Aucun acquéreur/locataire compatible dans votre base. Importez ou créez des profils acheteurs/locataires.</p>`;
  }
  const rows = list
    .map((m) => {
      const seg = m.segment === "locataire" ? "Locataire" : "Acquéreur";
      const budget =
        m.budget_min || m.budget_max
          ? `${m.budget_min ? fmtEuro(m.budget_min) : "—"} – ${m.budget_max ? fmtEuro(m.budget_max) : "—"}`
          : "Budget non précisé";
      const reasons = (m.reasons || []).map((r) => `<span class="match-tag">${escapeHtml(r)}</span>`).join("");
      const tel = (m.phone || "").replace(/\s/g, "");
      const call = tel ? `<a class="btn btn-ghost btn-sm match-call" href="tel:${escapeAttr(tel)}">Appeler</a>` : "";
      return `
      <div class="match-row">
        <div class="match-row-head">
          <strong>${escapeHtml(m.name)}</strong>
          <span class="match-score score-pill ${getScoreClass(m.score)}">${m.score}</span>
        </div>
        <div class="match-row-meta">${escapeHtml(seg)} · ${escapeHtml(budget)}</div>
        <div class="match-row-tags">${reasons}</div>
        ${call}
      </div>`;
    })
    .join("");
  const counts = `<p class="match-counts">${c.vente || 0} acquéreur(s) · ${c.location || 0} locataire(s) compatibles</p>`;
  return `${recoHtml}${counts}<div class="match-list">${rows}</div>`;
}

function updateMandateDemandBadge(lead, data) {
  const el = document.getElementById("drawer-mandate-demand");
  if (!el || !data?.ok || Number(state.selectedLead?.id) !== Number(lead.id)) return;
  const c = data.counts || {};
  const total = c.total || 0;
  if (!total) {
    el.hidden = true;
    return;
  }
  const parts = [];
  if (c.vente) parts.push(`${c.vente} acquéreur${c.vente > 1 ? "s" : ""}`);
  if (c.location) parts.push(`${c.location} locataire${c.location > 1 ? "s" : ""}`);
  el.innerHTML = `<span class="demand-badge">🎯 ${parts.join(" · ")} compatible${total > 1 ? "s" : ""} en base</span>`;
  el.hidden = false;
}

async function loadDrawerMatches(lead) {
  const fill = (html) => {
    const box = document.getElementById("drawer-matches");
    if (box && Number(box.dataset.leadId) === Number(lead.id)) {
      const title = box.querySelector(".drawer-section-title")?.outerHTML || "";
      box.innerHTML = title + html;
    }
  };
  if (leadMatchCache.has(lead.id)) {
    const data = leadMatchCache.get(lead.id);
    fill(renderLeadMatchesHtml(data));
    updateMandateDemandBadge(lead, data);
    return;
  }
  try {
    const data = await api(`/leads/${lead.id}/matches`, { headers: getAuthHeaders() });
    leadMatchCache.set(lead.id, data);
    fill(renderLeadMatchesHtml(data));
    updateMandateDemandBadge(lead, data);
  } catch (err) {
    fill(`<p class="drawer-matches-empty">${escapeHtml(err.message || "Erreur")}</p>`);
  }
}

function closeDrawer() {
  document.getElementById("drawer-overlay").classList.remove("open");
  document.getElementById("lead-drawer").classList.remove("open");
  state.selectedLead = null;
}

function showWrongServerBanner(staleServer = false) {
  let el = document.getElementById("server-warning-banner");
  if (!el) {
    el = document.createElement("div");
    el.id = "server-warning-banner";
    el.className = "server-warning-banner";
    document.body.prepend(el);
  }
  const url = `http://localhost:${PROPSCOUT_PORT}`;
  el.innerHTML = staleServer
    ? `<strong>Serveur à redémarrer</strong> — Un ancien Veliora tourne encore sur le port 8000. Terminal : <code>Ctrl+C</code> puis <code>python app.py</code> ou double-clic <code>demarrer.bat</code>. <a href="${url}">Ouvrir ${url}</a>`
    : `<strong>Veliora non démarré</strong> — Lancez <code>python app.py</code> ou <code>demarrer.bat</code>, puis ouvrez <a href="${url}">${url}</a> (pas Live Server).`;
  el.hidden = false;
}

function hideWrongServerBanner() {
  const el = document.getElementById("server-warning-banner");
  if (el) el.hidden = true;
}

function showToast(message, type = "info", duration = 4000) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;

  const icons = {
    success: '<path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>',
    error: '<path d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>',
    warning: '<path d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>',
    info: '<path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>',
  };

  const lines = String(message).split("\n").map((l) => `<span>${l}</span>`).join("");
  toast.innerHTML = `
    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">${icons[type] || icons.info}</svg>
    <div class="toast-body">${lines}</div>`;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-8px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

function leadsDataFingerprint(leads) {
  if (!Array.isArray(leads)) return "";
  return leads
    .map(
      (l) =>
        `${l.id}|${l.updated_at || ""}|${l.image_updated_at || ""}|${l.has_image ? 1 : 0}|${l.price || 0}|${l.mandate_score || 0}|${l.previous_price || ""}|${l.pipeline || ""}|${l.status || ""}`,
    )
    .join(";");
}

function scheduleBackgroundPoll(delayMs) {
  if (backgroundPollTimer) clearTimeout(backgroundPollTimer);
  const ms =
    delayMs ??
    (crawlState.active || state.crawlerRunning ? POLL_CRAWL_MS : POLL_IDLE_MS);
  backgroundPollTimer = setTimeout(runBackgroundPoll, ms);
}

async function runBackgroundPoll() {
  backgroundPollTimer = null;
  if (document.visibilityState === "hidden") {
    scheduleBackgroundPoll(POLL_IDLE_MS);
    return;
  }
  try {
    if (crawlState.active && !crawlState.pagePollPaused) {
      // Suivi détaillé déjà fait par startCrawlPolling — pas de /leads en parallèle.
      scheduleBackgroundPoll(45000);
      return;
    }

    const status = await api("/crawler/status");
    state.crawlerRunning = status.running;
    syncCrawlerUI();

    if (crawlState.active) {
      scheduleBackgroundPoll(45000);
      return;
    }

    const leads = await api("/leads");
    const fp = leadsDataFingerprint(leads);
    const prevFp = leadsDataFingerprint(LEADS);
    const changed = fp !== prevFp;
    LEADS = leads;
    syncRadarFromLeads(leads);
    if (changed) {
      await refreshStats();
      renderViewLight(state.currentView);
      void loadRadarAndPlaybook().then(() => {
        if (state.currentView === "dashboard" || state.currentView === "playbook") {
          renderRadarBriefing();
          if (state.currentView === "playbook") renderPlaybook();
        }
      });
    } else {
      if (state.currentView === "dashboard") renderRadarBriefing();
      updateSidebarCount();
    }
  } catch {
    /* silent */
  }
  scheduleBackgroundPoll();
}

function startPolling() {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      scheduleBackgroundPoll(1200);
    }
  });
  scheduleBackgroundPoll(4000);
}

async function submitInvite(e) {
  e.preventDefault();
  const email = document.getElementById("invite-email")?.value?.trim();
  const password = document.getElementById("invite-password")?.value;
  const first = document.getElementById("invite-first")?.value?.trim();
  const last = document.getElementById("invite-last")?.value?.trim();
  if (!email || !password) {
    showToast("Email et mot de passe requis", "error");
    return;
  }
  try {
    await api("/auth/invite", {
      method: "POST",
      body: JSON.stringify({ email, password, first_name: first, last_name: last }),
    });
    document.getElementById("invite-modal")?.classList.remove("open");
    showToast("Collaborateur invité", "success");
  } catch (err) {
    showToast(err.message, "error");
  }
}

let onboardingCache = null;
let onboardingBarHidden = false;
let onboardingDidAutoNav = false;

const ONBOARDING_STEPS = [
  {
    step: 1,
    view: "crawler",
    spotId: "onboarding-spot-1",
    barTitle: "Ajoutez votre première source de veille",
    barGoto: "Ajouter une source",
  },
  {
    step: 2,
    view: "crawler",
    spotId: "onboarding-spot-2",
    barTitle: "Lancez un crawl pour importer des prospects",
    barGoto: "Lancer un crawl",
  },
  {
    step: 3,
    view: "dashboard",
    spotId: "onboarding-spot-3",
    barTitle: "Consultez le briefing — qui appeler en premier",
    barGoto: "Voir le briefing",
  },
];

function onboardingProgress(data) {
  const p = data?.progress || {};
  return {
    hasSource: !!p.has_source,
    hasLeads: !!p.has_leads,
    step3Seen: (data?.settings?.onboarding_step || 0) >= 3,
    step1Done: !!p.has_source,
    step2Done: !!p.has_leads,
    step3Done: !!p.has_leads && (data?.settings?.onboarding_step || 0) >= 3,
    allDone: !!p.has_source && !!p.has_leads && (data?.settings?.onboarding_step || 0) >= 3,
  };
}

function currentOnboardingStep(prog) {
  if (!prog.step1Done) return 1;
  if (!prog.step2Done) return 2;
  if (!prog.step3Done) return 3;
  return 0;
}

function applyOnboardingSpot(stepNum, state, visible) {
  const spot = document.getElementById(`onboarding-spot-${stepNum}`);
  if (!spot) return;
  spot.hidden = !visible;
  spot.classList.remove("onboarding-spot--done", "onboarding-spot--pending", "onboarding-spot--active");
  const titleEl = spot.querySelector(".onboarding-spot-title");
  const doneTitles = {
    1: "Source configurée",
    2: "Prospects détectés",
    3: "Briefing consulté",
  };
  const activeTitles = {
    1: "Ajoutez votre première source",
    2: "Lancez votre premier crawl",
    3: "Consultez vos priorités du jour",
  };
  if (!visible) return;
  if (state === "done") {
    spot.classList.add("onboarding-spot--done");
    if (titleEl) titleEl.textContent = doneTitles[stepNum] || titleEl.textContent;
  } else if (state === "pending") {
    spot.classList.add("onboarding-spot--pending");
    if (titleEl) titleEl.textContent = activeTitles[stepNum] || titleEl.textContent;
  } else {
    spot.classList.add("onboarding-spot--active");
    if (titleEl) titleEl.textContent = activeTitles[stepNum] || titleEl.textContent;
  }
}

async function refreshOnboardingUi() {
  try {
    const data = await api("/onboarding");
    onboardingCache = data;
    if (data.settings?.onboarding_completed) {
      document.getElementById("onboarding-bar")?.setAttribute("hidden", "");
      [1, 2, 3].forEach((n) => applyOnboardingSpot(n, "done", false));
      return;
    }

    const prog = onboardingProgress(data);
    const current = currentOnboardingStep(prog);
    const view = state.currentView;

    if (prog.allDone) {
      await api("/onboarding", { method: "PATCH", body: JSON.stringify({ complete: true }) });
      document.getElementById("onboarding-bar")?.setAttribute("hidden", "");
      [1, 2, 3].forEach((n) => applyOnboardingSpot(n, "done", false));
      showToast("Configuration terminée — bonne chasse aux mandats !", "success");
      return;
    }

    // Encarts contextuels par vue
    applyOnboardingSpot(1, prog.step1Done ? "done" : current === 1 ? "active" : "pending", view === "crawler");
    applyOnboardingSpot(
      2,
      prog.step2Done ? "done" : !prog.step1Done ? "pending" : current === 2 ? "active" : "pending",
      view === "crawler",
    );
    applyOnboardingSpot(
      3,
      prog.step3Done ? "done" : !prog.step2Done ? "pending" : current === 3 ? "active" : "pending",
      view === "dashboard",
    );

    // Barre de progression globale
    const bar = document.getElementById("onboarding-bar");
    if (bar) {
      const showBar = !onboardingBarHidden && current > 0;
      bar.hidden = !showBar;
      if (showBar) {
        const doneCount = [prog.step1Done, prog.step2Done, prog.step3Done].filter(Boolean).length;
        const pct = Math.round((doneCount / 3) * 100);
        const fill = document.getElementById("onboarding-bar-fill");
        if (fill) {
          if (window.matchMedia("(max-width: 640px)").matches) {
            fill.style.width = `${pct}%`;
            fill.style.height = "100%";
          } else {
            fill.style.height = `${Math.max(pct, 8)}%`;
            fill.style.width = "100%";
          }
        }
        const meta = ONBOARDING_STEPS.find((s) => s.step === current);
        const titleEl = document.getElementById("onboarding-bar-title");
        const stepEl = document.getElementById("onboarding-bar-step");
        const gotoBtn = document.getElementById("onboarding-bar-goto");
        if (titleEl && meta) titleEl.textContent = meta.barTitle;
        if (stepEl) stepEl.textContent = `Étape ${current} / 3 · ${doneCount} terminée${doneCount > 1 ? "s" : ""}`;
        if (gotoBtn && meta) {
          gotoBtn.textContent = meta.barGoto;
          gotoBtn.dataset.onboardingView = meta.view;
        }
      }
    }
  } catch {
    /* ignore */
  }
}

async function markOnboardingStep3Seen() {
  if (!onboardingCache || onboardingCache.settings?.onboarding_completed) return;
  const prog = onboardingProgress(onboardingCache);
  if (!prog.hasLeads || prog.step3Seen) return;
  try {
    await api("/onboarding", { method: "PATCH", body: JSON.stringify({ step: 3 }) });
    await refreshOnboardingUi();
  } catch {
    /* ignore */
  }
}

function setupOnboarding() {
  document.getElementById("onboarding-cta-source")?.addEventListener("click", () => {
    openAddSourceModal();
  });
  document.getElementById("onboarding-cta-crawl-all")?.addEventListener("click", () => {
    document.getElementById("crawler-all-btn")?.click();
  });
  document.getElementById("onboarding-cta-crawl-one")?.addEventListener("click", () => {
    const firstSource = document.querySelector(
      "#sources-grid-reliable .source-crawl-btn, #sources-grid-antibot .source-crawl-btn",
    );
    if (firstSource) firstSource.click();
    else showToast("Ajoutez d’abord une source, puis relancez le crawl", "info");
  });
  document.getElementById("onboarding-bar-goto")?.addEventListener("click", (e) => {
    const view = e.currentTarget.dataset.onboardingView || "crawler";
    switchView(view);
  });
  document.getElementById("onboarding-bar-dismiss")?.addEventListener("click", () => {
    onboardingBarHidden = true;
    document.getElementById("onboarding-bar")?.setAttribute("hidden", "");
  });
}

function setupAccountMenu() {
  document.getElementById("btn-account-menu")?.addEventListener("click", () => {
    document.getElementById("account-modal")?.classList.add("open");
  });
  document.getElementById("account-modal-close")?.addEventListener("click", () => {
    document.getElementById("account-modal")?.classList.remove("open");
  });
  document.getElementById("btn-export-leads")?.addEventListener("click", async () => {
    try {
      const token = localStorage.getItem(AUTH_TOKEN_KEY);
      const res = await fetch(`${API}/leads/export`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) throw new Error("Export impossible");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "veliora-prospects.csv";
      a.click();
      URL.revokeObjectURL(url);
      showToast("Export CSV téléchargé", "success");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
  document.getElementById("btn-billing-portal")?.addEventListener("click", async () => {
    try {
      const { portal_url } = await api("/billing/create-portal-session", { method: "POST" });
      if (portal_url) window.location.href = portal_url;
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}

async function syncAccountBillingButton() {
  try {
    const me = await api("/auth/me");
    const btn = document.getElementById("btn-billing-portal");
    if (btn) btn.hidden = !me.billing?.has_stripe_customer;
  } catch {
    /* ignore */
  }
}

document.addEventListener("DOMContentLoaded", init);
