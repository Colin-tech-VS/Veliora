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
        "Module Acheteurs/Locataires absent sur le serveur. Fermez tous les terminaux Veliora (Ctrl+C), puis relancez demarrer.bat — vérifiez http://localhost:8000/api/health (api_version 7, clients true).",
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
  document.getElementById("btn-client-seed-demo")?.addEventListener("click", seedDemoClients);

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
  const token =
    localStorage.getItem("veliora_token") ||
    localStorage.getItem("propscout_token") ||
    "";
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
  renderClientMatchesPanel(client);
}

async function renderClientMatchesPanel(client) {
  const section = document.getElementById("client-matches");
  const list = document.getElementById("client-matches-list");
  const countEl = document.getElementById("client-matches-count");
  if (!section || !list) return;
  // Fiche jamais sauvegardée : on attend l'enregistrement pour proposer le matching.
  if (!client?.id) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  list.innerHTML = `<p class="client-matches-empty">Recherche d'annonces compatibles…</p>`;
  if (countEl) countEl.textContent = "—";

  const esc = clientDeps?.escapeHtml || ((s) => String(s));
  try {
    const data = await clientDeps.api(`/clients/${client.id}/matches`);
    if (!data?.ok) {
      list.innerHTML = `<p class="client-matches-empty">${esc(data?.error || "Matching indisponible.")}</p>`;
      return;
    }
    const counts = data.counts || {};
    const seg = data.expected_transaction === "location" ? "location" : "vente";
    const segLabel = seg === "location" ? "à louer" : "à vendre";
    if (countEl) {
      const tot = counts.total || 0;
      countEl.textContent = tot
        ? `${tot} annonce${tot > 1 ? "s" : ""} ${segLabel}${counts.in_budget ? ` · ${counts.in_budget} dans le budget` : ""}`
        : `0 annonce ${segLabel}`;
    }
    const items = data.top_matches || [];
    if (!items.length) {
      const hints = (data.diagnostics?.hints || [])
        .map((h) => `<li>${esc(h)}</li>`)
        .join("");
      list.innerHTML = hints
        ? `<ul class="match-diagnostics">${hints}</ul>`
        : `<p class="client-matches-empty">Aucune annonce ${segLabel} compatible. Lancez la veille ou élargissez villes (nom, CP ou département), budget et critères.</p>`;
      return;
    }
    list.innerHTML = items
      .map((m) => {
        const price = m.price
          ? `${Number(m.price).toLocaleString("fr-FR")} €${m.transaction_type === "location" ? " /mois" : ""}`
          : "Prix non communiqué";
        const surf = m.surface ? ` · ${m.surface} m²` : "";
        const cityLine = [m.address || m.city, m.postcode].filter(Boolean).join(" ");
        const reasons = (m.reasons || [])
          .slice(0, 3)
          .map((r) => `<span class="match-tag">${esc(r)}</span>`)
          .join("");
        const url = m.source_url ? `<a class="btn btn-ghost btn-sm" href="${esc(m.source_url)}" target="_blank" rel="noopener noreferrer">Voir l'annonce</a>` : "";
        const scoreCls = m.score >= 75 ? "high" : m.score >= 55 ? "mid" : "low";
        return `<article class="client-match-row" data-lead-id="${esc(m.lead_id)}">
            <div class="client-match-head">
              <strong>${esc(m.title || "Annonce")}</strong>
              <span class="client-match-score ${scoreCls}">${m.score}%</span>
            </div>
            <div class="client-match-meta">${esc(cityLine || "—")} · ${esc(price)}${surf}</div>
            ${reasons ? `<div class="client-match-tags">${reasons}</div>` : ""}
            <div class="client-match-actions">
              <button type="button" class="btn btn-secondary btn-sm" data-action="open-lead" data-lead-id="${esc(m.lead_id)}">Ouvrir la fiche</button>
              ${url}
            </div>
          </article>`;
      })
      .join("");
    list.querySelectorAll('[data-action="open-lead"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = parseInt(btn.dataset.leadId, 10);
        if (!id) return;
        closeClientEditor();
        if (typeof window.openDrawer === "function") window.openDrawer(id);
      });
    });
  } catch (err) {
    list.innerHTML = `<p class="client-matches-empty">${esc(err.message || "Matching indisponible.")}</p>`;
  }
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

async function seedDemoClients() {
  if (
    !confirm(
      "Créer 50 profils de test (acheteurs + locataires) sur Chaville et Lorient ?",
    )
  ) {
    return;
  }
  try {
    const res = await clientDeps.api("/clients/seed-demo", {
      method: "POST",
      body: JSON.stringify({ count: 50, cities: ["Chaville", "Lorient"] }),
    });
    await loadClients();
    renderClientsGrid();
    clientDeps.showToast(
      `${res.created || 0} profil(s) test créés sur Chaville/Lorient`,
      "success",
      5500,
    );
  } catch (err) {
    clientDeps.showToast(err.message || "Génération de profils impossible", "error");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindClientTemplateButtons);
} else {
  bindClientTemplateButtons();
}
