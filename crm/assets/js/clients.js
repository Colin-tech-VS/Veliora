/* Veliora — Acheteurs / Locataires (ajout manuel + import CSV / Excel) */

/** Modèle CSV (miroir de crm/mandates/client_import.py) — téléchargement local si l’API échoue */
const CLIENT_CSV_TEMPLATE =
  "segment;prenom;nom;email;telephone;budget_min;budget_max;" +
  "type_bien;pieces_min;surface_min;villes;notes\r\n" +
  "acheteur;Marie;Dupont;marie@exemple.fr;0612345678;200000;350000;" +
  "Appartement;3;65;Lyon,Villeurbanne;Recherche T3\r\n" +
  "locataire;Paul;Martin;paul@exemple.fr;0698765432;800;1200;" +
  "Appartement;2;45;Lyon;Disponible mars\r\n";

let clientDeps = null;
let CLIENTS = [];
let clientSegmentFilter = "all";
let editingClientId = null;

function initVelioraClients(deps) {
  clientDeps = deps;
  setupClientsUi();
}

async function loadClients() {
  if (!clientDeps) return;
  const q =
    clientSegmentFilter === "all"
      ? ""
      : `?segment=${encodeURIComponent(clientSegmentFilter)}`;
  try {
    const data = await clientDeps.api(`/clients${q}`);
    CLIENTS = data.clients || [];
  } catch (err) {
    if ((err.message || "").includes("Route API introuvable")) {
      throw new Error(
        "Module Acheteurs/Locataires absent sur le serveur. Fermez tous les terminaux Veliora (Ctrl+C), puis relancez demarrer.bat — vérifiez http://localhost:8000/api/health (api_version 6, clients true).",
      );
    }
    throw err;
  }
}

function renderClientsModule() {
  const view = document.getElementById("view-clients");
  if (!view?.classList.contains("active")) return;
  renderClientsGrid();
}

function setupClientsUi() {
  document.querySelectorAll("[data-client-segment]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document
        .querySelectorAll("[data-client-segment]")
        .forEach((b) => b.classList.toggle("active", b === btn));
      clientSegmentFilter = btn.dataset.clientSegment || "all";
      await loadClients();
      renderClientsGrid();
    });
  });

  document.getElementById("btn-new-client-acheteur")?.addEventListener("click", () => {
    openClientEditor(null, "acheteur");
  });
  document.getElementById("btn-new-client-locataire")?.addEventListener("click", () => {
    openClientEditor(null, "locataire");
  });

  document.getElementById("btn-client-import")?.addEventListener("click", () => {
    document.getElementById("client-import-modal")?.classList.add("open");
    const res = document.getElementById("client-import-result");
    if (res) {
      res.hidden = true;
      res.textContent = "";
    }
    const fin = document.getElementById("client-import-modal-file");
    if (fin) fin.value = "";
  });

  document.getElementById("client-import-close")?.addEventListener("click", () => {
    document.getElementById("client-import-modal")?.classList.remove("open");
  });

  document.getElementById("client-import-submit")?.addEventListener("click", () => {
    submitClientImport();
  });

  bindClientTemplateButtons();

  document.getElementById("client-editor-close")?.addEventListener("click", closeClientEditor);
  document.getElementById("client-editor-form")?.addEventListener("submit", submitClientEditor);
  document.getElementById("client-btn-delete")?.addEventListener("click", deleteClientEditor);
}

function triggerCsvDownload(blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "veliora-acheteurs-locataires-modele.csv";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function downloadClientTemplateLocal() {
  const blob = new Blob(["\ufeff", CLIENT_CSV_TEMPLATE], {
    type: "text/csv;charset=utf-8",
  });
  triggerCsvDownload(blob);
}

function clientApiBase() {
  if (clientDeps?.API) return clientDeps.API;
  const { hostname, port, protocol } = window.location;
  if (protocol === "file:") return "http://127.0.0.1:8000/api";
  const devPorts = new Set(["5500", "5501", "5173", "3000", "8080"]);
  if (
    (hostname === "localhost" || hostname === "127.0.0.1") &&
    port &&
    (devPorts.has(port) || port !== "8000")
  ) {
    return `http://${hostname}:8000/api`;
  }
  return "/api";
}

function bindClientTemplateButtons() {
  ["btn-client-template", "btn-client-template-modal"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el || el.dataset.templateBound === "1") return;
    el.dataset.templateBound = "1";
    el.addEventListener("click", () => downloadClientTemplate());
  });
}

async function downloadClientTemplate() {
  const showToastFn = clientDeps?.showToast || ((msg, type) => console.warn(type, msg));
  const apiBase = clientApiBase();
  const headers = clientDeps?.getAuthHeaders?.() || {};
  const token = localStorage.getItem("propscout_token");
  if (token && !headers.Authorization) {
    headers.Authorization = `Bearer ${token}`;
  }

  try {
    const res = await fetch(`${apiBase}/clients/import/template`, { headers });
    if (res.ok) {
      const blob = await res.blob();
      const ct = res.headers.get("content-type") || "";
      if (ct.includes("json")) {
        throw new Error("Réponse serveur invalide");
      }
      triggerCsvDownload(blob);
      showToastFn("Modèle CSV téléchargé", "success");
      return;
    }
    if (res.status === 401) {
      showToastFn("Session expirée — modèle local utilisé", "warning");
    }
  } catch {
    /* fallback local */
  }

  downloadClientTemplateLocal();
  showToastFn("Modèle CSV téléchargé", "success");
}

async function submitClientImport() {
  const { API, getAuthHeaders, showToast } = clientDeps;
  const input = document.getElementById("client-import-modal-file");
  const file = input?.files?.[0];
  if (!file) {
    showToast("Choisissez un fichier CSV ou Excel", "warning");
    return;
  }
  const segment = document.getElementById("client-import-default-segment")?.value || "";
  const fd = new FormData();
  fd.append("file", file);
  if (segment) fd.append("segment", segment);

  const resultEl = document.getElementById("client-import-result");
  try {
    const res = await fetch(`${API}/clients/import`, {
      method: "POST",
      headers: getAuthHeaders(),
      body: fd,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || "Import échoué");
    await loadClients();
    renderClientsGrid();
    const errCount = (body.errors || []).length;
    const msg = `${body.created} fiche(s) importée(s)${body.skipped ? `, ${body.skipped} ligne(s) ignorée(s)` : ""}${errCount ? `, ${errCount} erreur(s)` : ""}.`;
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.textContent = msg;
      if (errCount && body.errors) {
        resultEl.textContent +=
          "\n" +
          body.errors
            .slice(0, 5)
            .map((e) => `Ligne ${e.line} : ${e.error}`)
            .join("\n");
      }
    }
    showToast(msg, errCount ? "warning" : "success", 6000);
    if (!errCount) {
      document.getElementById("client-import-modal")?.classList.remove("open");
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

function renderClientsGrid() {
  const grid = document.getElementById("clients-grid");
  const hint = document.getElementById("clients-empty-hint");
  if (!grid) return;

  if (!CLIENTS.length) {
    grid.innerHTML = "";
    if (hint) hint.hidden = false;
    return;
  }
  if (hint) hint.hidden = true;

  const esc = clientDeps.escapeHtml;
  grid.innerHTML = CLIENTS.map((c) => {
    const segLabel = c.segment === "locataire" ? "Locataire" : "Acheteur";
    const segClass = c.segment === "locataire" ? "client-pill-loc" : "client-pill-ach";
    const budget =
      c.budget_min || c.budget_max
        ? `${formatClientMoney(c.budget_min)} – ${formatClientMoney(c.budget_max)}`
        : "—";
    const cities = (c.cities || []).join(", ") || "—";
    const name = c.full_name || "Sans nom";
    return `<article class="client-card" data-client-id="${esc(c.id)}">
      <div class="client-card-head">
        <span class="client-pill ${segClass}">${segLabel}</span>
        <span class="client-status">${esc(c.status || "actif")}</span>
      </div>
      <h3 class="client-card-name">${esc(name)}</h3>
      <p class="client-card-line">${esc(c.phone || "—")} · ${esc(c.email || "—")}</p>
      <p class="client-card-line"><strong>Budget</strong> ${esc(budget)}</p>
      <p class="client-card-line"><strong>Villes</strong> ${esc(cities)}</p>
      ${c.property_type ? `<p class="client-card-line">${esc(c.property_type)}${c.rooms_min ? ` · ${c.rooms_min} p.` : ""}${c.surface_min ? ` · ${c.surface_min} m²` : ""}</p>` : ""}
    </article>`;
  }).join("");

  grid.querySelectorAll(".client-card").forEach((card) => {
    card.addEventListener("click", () => {
      const id = card.dataset.clientId;
      const client = CLIENTS.find((x) => x.id === id);
      if (client) openClientEditor(client);
    });
  });
}

function formatClientMoney(n) {
  if (n == null || n === "") return "—";
  return `${Number(n).toLocaleString("fr-FR")} €`;
}

function openClientEditor(client, defaultSegment) {
  editingClientId = client?.id || null;
  const modal = document.getElementById("client-editor-modal");
  const title = document.getElementById("client-editor-title");
  const delBtn = document.getElementById("client-btn-delete");

  if (title) {
    if (client) title.textContent = "Modifier la fiche";
    else if (defaultSegment === "locataire") title.textContent = "Nouveau locataire";
    else title.textContent = "Nouvel acheteur";
  }
  if (delBtn) delBtn.hidden = !client;

  document.getElementById("client-segment").value =
    client?.segment || defaultSegment || "acheteur";
  document.getElementById("client-first-name").value = client?.first_name || "";
  document.getElementById("client-last-name").value = client?.last_name || "";
  document.getElementById("client-phone").value = client?.phone || "";
  document.getElementById("client-email").value = client?.email || "";
  document.getElementById("client-budget-min").value = client?.budget_min ?? "";
  document.getElementById("client-budget-max").value = client?.budget_max ?? "";
  document.getElementById("client-property-type").value = client?.property_type || "";
  document.getElementById("client-rooms-min").value = client?.rooms_min ?? "";
  document.getElementById("client-surface-min").value = client?.surface_min ?? "";
  document.getElementById("client-cities").value = (client?.cities || []).join(", ");
  document.getElementById("client-status").value = client?.status || "actif";
  document.getElementById("client-notes").value = client?.notes || "";

  modal?.classList.add("open");
}

function closeClientEditor() {
  document.getElementById("client-editor-modal")?.classList.remove("open");
  editingClientId = null;
}

function clientFormPayload() {
  const citiesRaw = document.getElementById("client-cities")?.value || "";
  const cities = citiesRaw
    .split(/[,;]/)
    .map((s) => s.trim())
    .filter(Boolean);
  return {
    segment: document.getElementById("client-segment")?.value || "acheteur",
    first_name: document.getElementById("client-first-name")?.value.trim(),
    last_name: document.getElementById("client-last-name")?.value.trim(),
    phone: document.getElementById("client-phone")?.value.trim(),
    email: document.getElementById("client-email")?.value.trim(),
    budget_min: parseNumField("client-budget-min"),
    budget_max: parseNumField("client-budget-max"),
    property_type: document.getElementById("client-property-type")?.value.trim(),
    rooms_min: parseNumField("client-rooms-min"),
    surface_min: parseFloatField("client-surface-min"),
    cities,
    status: document.getElementById("client-status")?.value || "actif",
    notes: document.getElementById("client-notes")?.value.trim(),
  };
}

function parseNumField(id) {
  const v = document.getElementById(id)?.value;
  if (v === "" || v == null) return null;
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? null : n;
}

function parseFloatField(id) {
  const v = document.getElementById(id)?.value;
  if (v === "" || v == null) return null;
  const n = parseFloat(v);
  return Number.isNaN(n) ? null : n;
}

async function submitClientEditor(e) {
  e.preventDefault();
  const payload = clientFormPayload();
  if (!payload.first_name && !payload.last_name && !payload.email && !payload.phone) {
    clientDeps.showToast("Indiquez au moins un nom, un email ou un téléphone", "warning");
    return;
  }
  try {
    if (editingClientId) {
      await clientDeps.api(`/clients/${editingClientId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      clientDeps.showToast("Fiche mise à jour", "success");
    } else {
      await clientDeps.api("/clients", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      clientDeps.showToast("Fiche créée", "success");
    }
    closeClientEditor();
    await loadClients();
    renderClientsGrid();
  } catch (err) {
    clientDeps.showToast(err.message, "error");
  }
}

async function deleteClientEditor() {
  if (!editingClientId || !confirm("Supprimer cette fiche ?")) return;
  try {
    await clientDeps.api(`/clients/${editingClientId}`, { method: "DELETE" });
    clientDeps.showToast("Fiche supprimée", "success");
    closeClientEditor();
    await loadClients();
    renderClientsGrid();
  } catch (err) {
    clientDeps.showToast(err.message, "error");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindClientTemplateButtons);
} else {
  bindClientTemplateButtons();
}
