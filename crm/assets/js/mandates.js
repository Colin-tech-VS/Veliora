/* Veliora — Mandats de vente et de location */

let mandateDeps = null;
let MANDATES = [];
let mandateTypeFilter = "all";
let mandateStatusFilter = "all";
let editingMandate = null;
let mandateTemplateCache = {};
let previewDebounce = null;
let autoSaveDebounce = null;
let editingAgencyProfile = null;
let mandateEditorDirty = false;
let activeMandateTab = "contrat";
let mandateDossiers = [];
let activeDossierId = null;
let propertyClientsCache = null;

const AGENCY_PROFILE_FIELDS = [
  { key: "legal_name", label: "Raison sociale", required: true },
  { key: "brand_name", label: "Nom commercial" },
  { key: "address", label: "Adresse" },
  { key: "postal_code", label: "Code postal" },
  { key: "city", label: "Ville" },
  { key: "siret", label: "SIRET" },
  { key: "rcs", label: "RCS" },
  { key: "capital", label: "Capital social" },
  { key: "tva_intra", label: "N° TVA intracommunautaire" },
  { key: "professional_card", label: "Carte professionnelle (CCI)" },
  { key: "insurance_company", label: "Assurance RCP — compagnie" },
  { key: "insurance_policy", label: "Assurance RCP — police n°" },
  { key: "representative_name", label: "Représentant légal" },
  { key: "representative_title", label: "Qualité du représentant", default: "Gérant" },
  { key: "phone", label: "Téléphone agence", type: "tel" },
  { key: "email", label: "Email agence", type: "email" },
  { key: "website", label: "Site web" },
];

const MANDATE_STATUS_LABELS = {
  draft: "Brouillon",
  sent: "Envoyé",
  signed: "Signé",
};

function initVelioraMandates(deps) {
  mandateDeps = deps;
  setupMandatesUi();
}

async function loadMandates() {
  if (!mandateDeps) return;
  const params = new URLSearchParams();
  if (mandateTypeFilter !== "all") params.set("type", mandateTypeFilter);
  if (mandateStatusFilter !== "all") params.set("status", mandateStatusFilter);
  const q = params.toString() ? `?${params}` : "";
  try {
    const data = await mandateDeps.api(`/mandates${q}`);
    MANDATES = data.mandates || [];
  } catch (err) {
    if ((err.message || "").includes("Route API introuvable")) {
      throw new Error(
        "Module Mandats absent — relancez demarrer.bat puis vérifiez http://localhost:8000/api/health (mandates: true).",
      );
    }
    throw err;
  }
}

function renderMandatesModule() {
  const view = document.getElementById("view-mandates");
  if (!view?.classList.contains("active")) return;
  renderMandatesList();
}

function setupMandatesUi() {
  document.querySelectorAll("[data-mandate-filter]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document
        .querySelectorAll("[data-mandate-filter]")
        .forEach((b) => b.classList.toggle("active", b === btn));
      mandateTypeFilter = btn.dataset.mandateFilter || "all";
      await loadMandates();
      renderMandatesList();
    });
  });

  document.querySelectorAll("[data-mandate-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      document
        .querySelectorAll("[data-mandate-status]")
        .forEach((b) => b.classList.toggle("active", b === btn));
      mandateStatusFilter = btn.dataset.mandateStatus || "all";
      await loadMandates();
      renderMandatesList();
    });
  });

  document.getElementById("btn-new-mandate-vente")?.addEventListener("click", () => {
    createAndOpenMandate("vente");
  });
  document.getElementById("btn-new-mandate-location")?.addEventListener("click", () => {
    createAndOpenMandate("location");
  });

  const openProfile = () => openAgencyProfileModal();
  document.getElementById("btn-agency-legal-profile")?.addEventListener("click", openProfile);
  document.getElementById("btn-agency-legal-from-account")?.addEventListener("click", openProfile);

  document.getElementById("agency-profile-close")?.addEventListener("click", () => {
    document.getElementById("agency-profile-modal")?.classList.remove("open");
  });
  document.getElementById("agency-profile-form")?.addEventListener("submit", submitAgencyProfile);

  document.getElementById("mandate-editor-close")?.addEventListener("click", closeMandateEditor);
  document.getElementById("mandate-btn-save")?.addEventListener("click", () => saveMandateEditor(false));
  document.getElementById("mandate-btn-signed")?.addEventListener("click", () => markMandateSigned());
  document.getElementById("mandate-btn-print")?.addEventListener("click", printMandatePreview);
  document.getElementById("mandate-btn-send")?.addEventListener("click", sendMandateToSeller);
  document.getElementById("mandate-btn-delete")?.addEventListener("click", deleteMandateEditor);

  document.querySelectorAll("[data-mandate-tab]").forEach((btn) => {
    btn.addEventListener("click", () => switchMandateTab(btn.dataset.mandateTab));
  });
  document.getElementById("mandate-dossier-new")?.addEventListener("click", () => createMandateDossier(false));
  document.getElementById("mandate-dossier-from-mandate")?.addEventListener("click", () => createMandateDossier(true));

  document.getElementById("mandate-editor-exclusivity")?.addEventListener("change", () => {
    refreshMandatePreview();
    scheduleMandateAutoSave();
  });

  document.getElementById("mandate-editor-type")?.addEventListener("change", () => {
    onMandateTypeChange();
  });

  document.getElementById("mandate-send-email")?.addEventListener("input", () => {
    syncEmailToMandateFields();
    refreshMandatePreview();
    scheduleMandateAutoSave();
  });
}

function syncEmailToMandateFields() {
  const email = document.getElementById("mandate-send-email")?.value.trim() || "";
  const form = document.getElementById("mandate-fields-form");
  if (!form || !editingMandate) return;
  const type = editingMandate.mandate_type === "location" ? "owner_email" : "seller_email";
  const el = form.querySelector(`[name="${type}"]`);
  if (el && email) el.value = email;
}

function mapFieldsForTypeChange(fromType, toType, fields) {
  const out = { ...fields };
  const common = [
    "property_type", "property_address", "postal_code", "city", "neighborhood",
    "rooms", "floor", "has_elevator", "dpe_energy", "heating_type", "general_condition",
    "mandate_duration_months", "mandate_start_date", "mandate_end_date", "clauses",
    "has_photos", "portal_listings", "first_listed_date", "transport_proximity",
  ];
  const mapped = {};
  common.forEach((k) => {
    if (out[k] !== undefined && out[k] !== "") mapped[k] = out[k];
  });
  if (fromType === "vente" && toType === "location") {
    if (out.seller_civility) mapped.owner_civility = out.seller_civility;
    if (out.seller_first_name) mapped.owner_first_name = out.seller_first_name;
    if (out.seller_last_name) mapped.owner_last_name = out.seller_last_name;
    if (out.seller_address) mapped.owner_address = out.seller_address;
    if (out.seller_phone) mapped.owner_phone = out.seller_phone;
    if (out.seller_email) mapped.owner_email = out.seller_email;
    if (out.surface_carrez) mapped.surface = out.surface_carrez;
  } else if (fromType === "location" && toType === "vente") {
    if (out.owner_civility) mapped.seller_civility = out.owner_civility;
    if (out.owner_first_name) mapped.seller_first_name = out.owner_first_name;
    if (out.owner_last_name) mapped.seller_last_name = out.owner_last_name;
    if (out.owner_address) mapped.seller_address = out.owner_address;
    if (out.owner_phone) mapped.seller_phone = out.owner_phone;
    if (out.owner_email) mapped.seller_email = out.owner_email;
    if (out.surface) mapped.surface_carrez = out.surface;
  }
  return mapped;
}

async function onMandateTypeChange() {
  if (!editingMandate) return;
  const select = document.getElementById("mandate-editor-type");
  const newType = select?.value || "vente";
  if (newType === editingMandate.mandate_type) return;

  const currentFields = collectMandateFieldsFromForm();
  const mapped = mapFieldsForTypeChange(editingMandate.mandate_type, newType, currentFields);
  editingMandate.mandate_type = newType;

  const typeLabel = newType === "location" ? "Mandat de location" : "Mandat de vente";
  document.getElementById("mandate-editor-title").textContent = typeLabel;

  const template = await getTemplateFields(newType);
  renderMandateFieldsForm(template.fields, mapped);
  refreshMandatePreview();
  scheduleMandateAutoSave(true);
}

function renderMandatesList() {
  const list = document.getElementById("mandates-list");
  const hint = document.getElementById("mandates-empty-hint");
  if (!list) return;
  const esc = mandateDeps.escapeHtml;

  if (!MANDATES.length) {
    list.innerHTML = "";
    if (hint) hint.hidden = false;
    return;
  }
  if (hint) hint.hidden = true;

  list.innerHTML = MANDATES.map((m) => {
    const typeLabel = m.mandate_type === "location" ? "Location" : "Vente";
    const typeClass = m.mandate_type === "location" ? "mandate-pill-loc" : "mandate-pill-vente";
    const status = MANDATE_STATUS_LABELS[m.status] || m.status;
    const exclMap = { simple: "Simple", exclusif: "Exclusif", "semi-exclusif": "Semi-exclusif" };
    const excl = exclMap[m.exclusivity] || "Exclusif";
    const addr =
      (m.fields && m.fields.property_address) || m.title || "Sans adresse";
    const updated = m.updated_at
      ? new Date(m.updated_at).toLocaleDateString("fr-FR", {
          day: "numeric",
          month: "short",
          year: "numeric",
        })
      : "";
    return `<article class="mandate-card" data-mandate-id="${esc(m.id)}">
      <div class="mandate-card-head">
        <span class="mandate-pill ${typeClass}">${typeLabel}</span>
        <span class="mandate-pill mandate-pill-excl">${excl}</span>
        <span class="mandate-status-badge mandate-status-${esc(m.status)}">${esc(status)}</span>
        <button type="button" class="mandate-card-delete" data-delete-mandate="${esc(m.id)}" title="Supprimer" aria-label="Supprimer le mandat">×</button>
      </div>
      <h3 class="mandate-card-title">${esc(addr)}</h3>
      <p class="mandate-card-meta">${esc(m.title || "")}</p>
      <p class="mandate-card-date">Modifié le ${esc(updated)}</p>
    </article>`;
  }).join("");

  list.querySelectorAll(".mandate-card").forEach((card) => {
    card.addEventListener("click", () => {
      const m = MANDATES.find((x) => x.id === card.dataset.mandateId);
      if (m) openMandateEditor(m);
    });
    card.querySelector("[data-delete-mandate]")?.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = e.currentTarget.dataset.deleteMandate;
      const m = MANDATES.find((x) => x.id === id);
      if (m) confirmDeleteMandate(m);
    });
  });
}

async function getTemplateFields(mandateType) {
  if (mandateTemplateCache[mandateType]) return mandateTemplateCache[mandateType];
  const data = await mandateDeps.api(`/mandates/templates?type=${mandateType}`);
  mandateTemplateCache[mandateType] = data.template;
  return data.template;
}

async function createAndOpenMandate(mandateType, leadId = null) {
  try {
    const profile = await mandateDeps.api("/mandates/agency-profile");
    const p = profile.profile || {};
    if (!p.legal_name && !p.brand_name) {
      mandateDeps.showToast(
        "Complétez la fiche agence avant de créer un mandat",
        "warning",
        6000,
      );
      openAgencyProfileModal(p);
    }
    const data = await mandateDeps.api("/mandates", {
      method: "POST",
      body: JSON.stringify({
        mandate_type: mandateType,
        lead_id: leadId,
        exclusivity: "exclusif",
      }),
    });
    await loadMandates();
    renderMandatesList();
    openMandateEditor(data.mandate);
    if (leadId) {
      mandateDeps.showToast("Mandat prérempli depuis le prospect", "success");
    }
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function createMandateFromLead(leadId, mandateType) {
  if (!mandateDeps) return;
  await createAndOpenMandate(mandateType, leadId);
  if (typeof switchView === "function") switchView("mandates");
}

async function openMandateEditor(mandate) {
  editingMandate = mandate;
  const modal = document.getElementById("mandate-editor-modal");
  const typeLabel =
    mandate.mandate_type === "location" ? "Mandat de location" : "Mandat de vente";
  document.getElementById("mandate-editor-title").textContent = typeLabel;
  document.getElementById("mandate-editor-type").value = mandate.mandate_type || "vente";
  document.getElementById("mandate-editor-exclusivity").value =
    mandate.exclusivity || "exclusif";
  updateMandateStatusPill(mandate.status);
  document.getElementById("mandate-live-badge")?.removeAttribute("hidden");

  try {
    const profileData = await mandateDeps.api("/mandates/agency-profile");
    editingAgencyProfile = profileData.profile || window.MandateRender?.defaultAgency?.() || {};
  } catch {
    editingAgencyProfile = window.MandateRender?.defaultAgency?.() || {};
  }

  const email =
    mandate.recipient_email ||
    mandate.fields?.seller_email ||
    mandate.fields?.owner_email ||
    "";
  document.getElementById("mandate-send-email").value =
    email && email !== "—" ? email : "";

  const template = await getTemplateFields(mandate.mandate_type);
  renderMandateFieldsForm(template.fields, mandate.fields || {});
  refreshMandatePreview();
  setAutosaveHint("Sauvegarde automatique");
  switchMandateTab("contrat");
  await loadMandateDossiers();
  modal?.classList.add("open");
}

function updateMandateStatusPill(status) {
  const el = document.getElementById("mandate-editor-status");
  if (!el) return;
  el.textContent = MANDATE_STATUS_LABELS[status] || status || "Brouillon";
  el.className = `mandate-status-pill mandate-status-${status || "draft"}`;
}

function closeMandateEditor() {
  document.getElementById("mandate-editor-modal")?.classList.remove("open");
  document.getElementById("mandate-live-badge")?.setAttribute("hidden", "");
  editingMandate = null;
  editingAgencyProfile = null;
  mandateEditorDirty = false;
  mandateDossiers = [];
  activeDossierId = null;
  clearTimeout(previewDebounce);
  clearTimeout(autoSaveDebounce);
}

function renderMandateFieldInput(f, val, esc) {
  const req = f.required ? " required" : "";
  const id = `mf-${f.key}`;
  const reqMark = f.required ? '<span class="mandate-field-req">*</span>' : "";
  if (f.type === "textarea") {
    return `<label class="form-field" for="${id}"><span>${esc(f.label)}${reqMark}</span><textarea id="${id}" name="${esc(f.key)}" rows="2"${req}>${esc(String(val))}</textarea></label>`;
  }
  if (f.type === "select" && f.options) {
    const opts = f.options
      .map(
        (o) =>
          `<option value="${esc(o)}"${o === val ? " selected" : ""}>${esc(o)}</option>`,
      )
      .join("");
    return `<label class="form-field" for="${id}"><span>${esc(f.label)}${reqMark}</span><select id="${id}" name="${esc(f.key)}"${req}>${opts}</select></label>`;
  }
  const type = f.type === "number" ? "number" : f.type || "text";
  return `<label class="form-field" for="${id}"><span>${esc(f.label)}${reqMark}</span><input type="${type}" id="${id}" name="${esc(f.key)}" value="${esc(String(val))}"${req}></label>`;
}

function renderMandateFieldsForm(fieldDefs, values) {
  const form = document.getElementById("mandate-fields-form");
  if (!form) return;
  const esc = mandateDeps.escapeHtml;
  // Regroupe les champs par section -> blocs repliables (<details>), pour réduire
  // la densité : seule la 1re section est ouverte, les autres se déplient au clic.
  const groups = [];
  let current = null;
  fieldDefs.forEach((f) => {
    const section = f.section || "";
    if (!current || section !== current.section) {
      current = { section, fields: [] };
      groups.push(current);
    }
    current.fields.push(f);
  });
  let html = "";
  let sectionIndex = 0;
  groups.forEach((g) => {
    const inner = g.fields
      .map((f) => renderMandateFieldInput(f, values[f.key] ?? f.default ?? "", esc))
      .join("");
    if (g.section) {
      const open = sectionIndex === 0 ? " open" : "";
      sectionIndex += 1;
      html +=
        `<details class="mandate-form-section"${open}>` +
        `<summary class="mandate-form-section-title">${esc(g.section)}</summary>` +
        `<div class="mandate-form-section-fields">${inner}</div>` +
        `</details>`;
    } else {
      // Champs sans section : toujours visibles, en tête de formulaire.
      html += inner;
    }
  });
  form.innerHTML = html;

  form.querySelectorAll("input, textarea, select").forEach((el) => {
    el.addEventListener("input", () => {
      scheduleMandatePreview(false);
      scheduleMandateAutoSave();
    });
    el.addEventListener("change", () => {
      refreshMandatePreview();
      scheduleMandateAutoSave();
    });
  });
}

function collectMandateFieldsFromForm() {
  const form = document.getElementById("mandate-fields-form");
  const fields = {};
  form?.querySelectorAll("[name]").forEach((el) => {
    fields[el.name] = el.value;
  });
  return fields;
}

function scheduleMandatePreview(immediate) {
  clearTimeout(previewDebounce);
  if (immediate) {
    refreshMandatePreview();
    return;
  }
  previewDebounce = setTimeout(refreshMandatePreview, 80);
}

function refreshMandatePreview() {
  if (!editingMandate || !window.MandateRender) return;
  syncEmailToMandateFields();
  const fields = collectMandateFieldsFromForm();
  const exclusivity = document.getElementById("mandate-editor-exclusivity")?.value || "exclusif";
  const mandateType = document.getElementById("mandate-editor-type")?.value || editingMandate.mandate_type;
  const html = window.MandateRender.renderMandateHtml(
    mandateType,
    exclusivity,
    fields,
    editingAgencyProfile,
  );
  setMandatePreviewHtml(html);
}

function setAutosaveHint(text, saving) {
  const el = document.getElementById("mandate-autosave-hint");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("is-saving", Boolean(saving));
}

function scheduleMandateAutoSave(immediate) {
  mandateEditorDirty = true;
  clearTimeout(autoSaveDebounce);
  setAutosaveHint("Modifications en cours…", true);
  autoSaveDebounce = setTimeout(
    () => autoSaveMandateEditor(),
    immediate ? 0 : 700,
  );
}

async function autoSaveMandateEditor() {
  if (!editingMandate || !mandateEditorDirty) return;
  const fields = collectMandateFieldsFromForm();
  const exclusivity = document.getElementById("mandate-editor-exclusivity")?.value || "exclusif";
  const mandate_type = document.getElementById("mandate-editor-type")?.value || editingMandate.mandate_type;
  const recipient_email = document.getElementById("mandate-send-email")?.value.trim();
  try {
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        fields,
        exclusivity,
        mandate_type,
        recipient_email,
      }),
    });
    editingMandate = data.mandate;
    mandateEditorDirty = false;
    setAutosaveHint("Enregistré automatiquement");
    await loadMandates();
    renderMandatesList();
  } catch {
    setAutosaveHint("Erreur de sauvegarde — cliquez Enregistrer");
  }
}

function setMandatePreviewHtml(html) {
  const el = document.getElementById("mandate-preview");
  if (el) el.innerHTML = html || "<p class=\"mandate-preview-empty\">Remplissez les champs pour voir l’aperçu.</p>";
}

async function markMandateSigned() {
  if (!editingMandate) return;
  await saveMandateEditor(false);
  try {
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}`, {
      method: "PATCH",
      body: JSON.stringify({ mark_signed: true }),
    });
    editingMandate = data.mandate;
    updateMandateStatusPill(data.mandate.status);
    await loadMandates();
    renderMandatesList();
    mandateDeps.showToast("Mandat marqué comme signé", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function saveMandateEditor(closeAfter) {
  if (!editingMandate) return;
  clearTimeout(autoSaveDebounce);
  const fields = collectMandateFieldsFromForm();
  const exclusivity = document.getElementById("mandate-editor-exclusivity")?.value || "exclusif";
  const mandate_type = document.getElementById("mandate-editor-type")?.value || editingMandate.mandate_type;
  const recipient_email = document.getElementById("mandate-send-email")?.value.trim();
  try {
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        fields,
        exclusivity,
        mandate_type,
        recipient_email,
      }),
    });
    editingMandate = data.mandate;
    refreshMandatePreview();
    updateMandateStatusPill(data.mandate.status);
    mandateEditorDirty = false;
    setAutosaveHint("Enregistré");
    await loadMandates();
    renderMandatesList();
    mandateDeps.showToast("Mandat enregistré", "success");
    if (closeAfter) closeMandateEditor();
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function sendMandateToSeller() {
  if (!editingMandate) return;
  clearTimeout(autoSaveDebounce);
  await saveMandateEditor(false);
  const email = document.getElementById("mandate-send-email")?.value.trim();
  if (!email) {
    mandateDeps.showToast("Indiquez l’email du vendeur ou bailleur", "warning");
    return;
  }
  try {
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}/send`, {
      method: "POST",
      body: JSON.stringify({ email }),
    });
    editingMandate = data.mandate;
    updateMandateStatusPill(data.mandate.status);
    await loadMandates();
    renderMandatesList();
    if (data.sent_smtp) {
      mandateDeps.showToast("Mandat envoyé par email", "success");
    } else if (data.mailto) {
      window.location.href = data.mailto;
      mandateDeps.showToast(
        "SMTP non configuré — ouvrez votre client mail ou configurez SMTP dans .env",
        "info",
        8000,
      );
    } else {
      mandateDeps.showToast("Mandat marqué comme envoyé", "success");
    }
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

function printMandatePreview() {
  const html = document.getElementById("mandate-preview")?.innerHTML;
  if (!html) return;
  const w = window.open("", "_blank");
  if (!w) {
    mandateDeps.showToast("Autorisez les pop-ups pour imprimer", "warning");
    return;
  }
  w.document.write(`<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>Mandat</title>
    <style>
      body { font-family: Georgia, serif; max-width: 800px; margin: 2rem auto; padding: 0 1.5rem; color: #111; line-height: 1.5; }
      .mandate-doc h1 { font-size: 1.35rem; text-align: center; margin-bottom: 0.5rem; }
      .mandate-doc h2 { font-size: 1rem; margin-top: 1.25rem; border-bottom: 1px solid #ccc; padding-bottom: 0.25rem; }
      .mandate-meta { font-size: 0.85rem; color: #666; text-align: center; }
      .sig-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin-top: 2rem; }
      @media print { body { margin: 0; } }
    </style></head><body>${html}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => w.print(), 400);
}

async function openAgencyProfileModal(prefill) {
  const modal = document.getElementById("agency-profile-modal");
  const form = document.getElementById("agency-profile-form");
  if (!form) return;
  let profile = prefill;
  if (!profile) {
    try {
      const data = await mandateDeps.api("/mandates/agency-profile");
      profile = data.profile || {};
    } catch (err) {
      mandateDeps.showToast(err.message, "error");
      return;
    }
  }
  const esc = mandateDeps.escapeHtml;
  form.innerHTML = AGENCY_PROFILE_FIELDS.map((f) => {
    const val = profile[f.key] ?? f.default ?? "";
    const type = f.type || "text";
    const req = f.required ? " required" : "";
    return `<label class="form-field"><span>${esc(f.label)}</span><input type="${type}" name="${esc(f.key)}" value="${esc(String(val))}"${req}></label>`;
  }).join("");
  modal?.classList.add("open");
}

async function submitAgencyProfile(e) {
  e.preventDefault();
  const form = document.getElementById("agency-profile-form");
  const profile = {};
  form?.querySelectorAll("input[name]").forEach((el) => {
    profile[el.name] = el.value.trim();
  });
  try {
    await mandateDeps.api("/mandates/agency-profile", {
      method: "PATCH",
      body: JSON.stringify({ profile }),
    });
    if (typeof mandateDeps.refreshAgencySettings === "function") {
      await mandateDeps.refreshAgencySettings();
    }
    if (typeof mandateDeps.scheduleSourceUrlsForCity === "function") {
      mandateDeps.scheduleSourceUrlsForCity();
    } else if (typeof window.VelioraScheduleSourceUrlsForCity === "function") {
      window.VelioraScheduleSourceUrlsForCity();
    }
    document.getElementById("agency-profile-modal")?.classList.remove("open");
    mandateDeps.showToast(
      profile.city
        ? `Fiche agence enregistrée — crawl : ${profile.city}`
        : "Fiche agence enregistrée",
      "success",
    );
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

function switchMandateTab(tab) {
  activeMandateTab = tab || "contrat";
  document.querySelectorAll("[data-mandate-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mandateTab === activeMandateTab);
  });
  document.getElementById("mandate-tab-contrat")?.toggleAttribute("hidden", activeMandateTab !== "contrat");
  document.getElementById("mandate-tab-dossiers")?.toggleAttribute("hidden", activeMandateTab !== "dossiers");
  if (activeMandateTab === "dossiers") {
    loadMandateDossiers();
  }
}

async function apiUpload(path, formData) {
  const res = await fetch(`${mandateDeps.API}${path}`, {
    method: "POST",
    headers: { ...mandateDeps.getAuthHeaders() },
    body: formData,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Erreur ${res.status}`);
  return body;
}

async function loadPropertyClients() {
  if (propertyClientsCache) return propertyClientsCache;
  const data = await mandateDeps.api("/clients");
  propertyClientsCache = data.clients || [];
  return propertyClientsCache;
}

async function loadMandateDossiers() {
  if (!editingMandate) return;
  try {
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}/dossiers`);
    mandateDossiers = data.dossiers || [];
    if (activeDossierId && !mandateDossiers.find((d) => d.id === activeDossierId)) {
      activeDossierId = mandateDossiers[0]?.id || null;
    } else if (!activeDossierId && mandateDossiers.length) {
      activeDossierId = mandateDossiers[0].id;
    }
    renderDossiersList();
    renderDossierDetail();
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

function renderDossiersList() {
  const ul = document.getElementById("mandate-dossiers-list");
  if (!ul) return;
  const esc = mandateDeps.escapeHtml;
  if (!mandateDossiers.length) {
    ul.innerHTML = `<li class="mandate-dossiers-empty">Aucun dossier</li>`;
    return;
  }
  ul.innerHTML = mandateDossiers
    .map((d) => {
      const photos = (d.photos || []).length;
      const clients = (d.linked_clients || []).length;
      return `<li><button type="button" class="mandate-dossier-item${d.id === activeDossierId ? " active" : ""}" data-dossier-id="${esc(d.id)}">
        <span class="mandate-dossier-item-title">${esc(d.title || "Dossier")}</span>
        <span class="mandate-dossier-item-meta">${photos} photo(s) · ${clients} client(s)</span>
      </button></li>`;
    })
    .join("");
  ul.querySelectorAll("[data-dossier-id]").forEach((btn) => {
    btn.addEventListener("click", () => {
      activeDossierId = btn.dataset.dossierId;
      renderDossiersList();
      renderDossierDetail();
    });
  });
}

async function renderDossierDetail() {
  const panel = document.getElementById("mandate-dossier-detail");
  if (!panel) return;
  const esc = mandateDeps.escapeHtml;
  const d = mandateDossiers.find((x) => x.id === activeDossierId);
  if (!d) {
    panel.innerHTML = `<p class="mandate-dossier-empty">Sélectionnez ou créez un dossier pour y ajouter photos et clients.</p>`;
    return;
  }

  const clients = await loadPropertyClients();
  const linkedIds = new Set((d.linked_clients || []).map((x) => x.client_id));
  const linkedHtml = (d.linked_clients || [])
    .map((link) => {
      const c = clients.find((x) => x.id === link.client_id);
      const name = c ? c.full_name || `${c.first_name} ${c.last_name}`.trim() : link.client_id;
      const seg = c?.segment === "locataire" ? "Locataire" : "Acheteur";
      return `<li class="mandate-linked-client">
        <span><strong>${esc(name)}</strong> <em>${esc(seg)}</em></span>
        <button type="button" class="btn btn-sm btn-ghost" data-unlink-client="${esc(link.client_id)}">Retirer</button>
      </li>`;
    })
    .join("");

  const photosHtml = (d.photos || [])
    .map(
      (p) => `<figure class="mandate-photo-card">
        <img src="${esc(p.url)}" alt="${esc(p.caption || "Photo du bien")}" loading="lazy">
        <button type="button" class="mandate-photo-remove" data-remove-photo="${esc(p.id)}" title="Supprimer">×</button>
        ${p.caption ? `<figcaption>${esc(p.caption)}</figcaption>` : ""}
      </figure>`,
    )
    .join("");

  panel.innerHTML = `
    <div class="mandate-dossier-detail-head">
      <input type="text" class="mandate-dossier-title-input" id="dossier-title" value="${esc(d.title || "")}" placeholder="Titre du dossier">
      <button type="button" class="btn btn-danger-outline btn-sm" id="dossier-delete-btn">Supprimer le dossier</button>
    </div>
    <div class="mandate-dossier-fields">
      <label class="form-field"><span>Adresse</span><input type="text" id="dossier-address" value="${esc(d.property_address || "")}"></label>
      <label class="form-field"><span>Ville</span><input type="text" id="dossier-city" value="${esc(d.city || "")}"></label>
      <label class="form-field"><span>Surface (m²)</span><input type="number" id="dossier-surface" value="${d.surface ?? ""}"></label>
      <label class="form-field"><span>Prix (€)</span><input type="number" id="dossier-price" value="${d.price ?? ""}"></label>
    </div>
    <label class="form-field"><span>Description / points forts</span><textarea id="dossier-description" rows="3">${esc(d.description || "")}</textarea></label>
    <div class="mandate-dossier-section">
      <h3>Photos du bien</h3>
      <label class="mandate-photo-dropzone" id="mandate-photo-dropzone">
        <input type="file" id="mandate-photo-input" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden>
        <span>Glissez vos photos ici ou cliquez pour ajouter (JPG, PNG — max 8 Mo)</span>
      </label>
      <div class="mandate-photo-grid">${photosHtml || '<p class="form-hint">Aucune photo</p>'}</div>
    </div>
    <div class="mandate-dossier-section">
      <div class="dossier-docs-head">
        <h3>Pièces &amp; documents du dossier</h3>
        <button type="button" class="btn btn-secondary btn-sm" id="dossier-folder-new">+ Nouveau dossier</button>
      </div>
      <p class="form-hint">Espace type Drive : les pièces obligatoires sont créées automatiquement selon le profil du vendeur. Importez chaque document par glisser-déposer.</p>
      <div id="dossier-documents" class="dossier-docs"><p class="form-hint">Chargement des pièces…</p></div>
    </div>
    <div class="mandate-dossier-section">
      <h3>Clients à qui présenter le bien</h3>
      <div class="mandate-client-link-row">
        <select id="dossier-client-pick" class="mandate-select">
          <option value="">— Choisir un client —</option>
          ${clients
            .filter((c) => !linkedIds.has(c.id))
            .map(
              (c) =>
                `<option value="${esc(c.id)}">${esc(c.full_name || c.first_name || "Client")} (${c.segment === "locataire" ? "Loc." : "Ach."})</option>`,
            )
            .join("")}
        </select>
        <button type="button" class="btn btn-secondary btn-sm" id="dossier-link-client-btn">Ajouter</button>
      </div>
      <ul class="mandate-linked-clients">${linkedHtml || '<li class="form-hint">Aucun client lié</li>'}</ul>
    </div>
    <button type="button" class="btn btn-primary" id="dossier-save-btn">Enregistrer le dossier</button>`;

  panel.querySelector("#dossier-save-btn")?.addEventListener("click", () => saveDossierFields(d.id));
  panel.querySelector("#dossier-delete-btn")?.addEventListener("click", () => deleteDossier(d.id));
  panel.querySelector("#dossier-link-client-btn")?.addEventListener("click", () => {
    const cid = panel.querySelector("#dossier-client-pick")?.value;
    if (cid) linkClientToDossier(d.id, cid);
  });
  panel.querySelectorAll("[data-unlink-client]").forEach((btn) => {
    btn.addEventListener("click", () => unlinkClientFromDossier(d.id, btn.dataset.unlinkClient));
  });
  panel.querySelectorAll("[data-remove-photo]").forEach((btn) => {
    btn.addEventListener("click", () => removeDossierPhoto(d.id, btn.dataset.removePhoto));
  });

  const fileInput = panel.querySelector("#mandate-photo-input");
  const dropzone = panel.querySelector("#mandate-photo-dropzone");
  dropzone?.addEventListener("click", () => fileInput?.click());
  fileInput?.addEventListener("change", () => uploadDossierPhotos(d.id, fileInput.files));
  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone?.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone?.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    uploadDossierPhotos(d.id, e.dataTransfer?.files);
  });

  panel.querySelector("#dossier-folder-new")?.addEventListener("click", () => createDossierFolder(d.id));
  loadDossierDocuments(d.id);
}

// ── Pièces & documents (espace Drive automatisé) ────────────────────────────

async function loadDossierDocuments(dossierId) {
  const root = document.getElementById("dossier-documents");
  if (!root) return;
  try {
    const data = await mandateDeps.api(`/mandates/dossiers/${dossierId}/documents`);
    renderDossierDocuments(dossierId, data.documents || {});
  } catch (err) {
    root.innerHTML = `<p class="form-hint">${mandateDeps.escapeHtml(err.message)}</p>`;
  }
}

function fileIcon(ext) {
  const e = (ext || "").toLowerCase();
  if (["pdf"].includes(e)) return "📕";
  if (["jpg", "jpeg", "png", "webp", "gif", "heic", "heif"].includes(e)) return "🖼️";
  if (["doc", "docx", "odt", "rtf", "txt"].includes(e)) return "📄";
  if (["xls", "xlsx", "ods", "csv"].includes(e)) return "📊";
  if (["zip"].includes(e)) return "🗜️";
  return "📎";
}

function fmtBytes(n) {
  if (!n) return "";
  if (n < 1024) return `${n} o`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} Ko`;
  return `${(n / (1024 * 1024)).toFixed(1)} Mo`;
}

function renderDossierDocuments(dossierId, docs) {
  const root = document.getElementById("dossier-documents");
  if (!root) return;
  const esc = mandateDeps.escapeHtml;
  const folders = docs.folders || [];
  const total = docs.required_total || 0;
  const done = docs.required_done || 0;
  const pct = total ? Math.round((done / total) * 100) : 0;

  const profileHtml = docs.profile
    ? `<div class="dossier-docs-profile">
         <span class="dossier-docs-profile-label">Profil détecté</span>
         <strong>${esc(docs.profile.label || "")}</strong>
       </div>`
    : "";

  const progressHtml = total
    ? `<div class="dossier-docs-progress">
         <div class="dossier-docs-progress-bar"><span style="width:${pct}%"></span></div>
         <span class="dossier-docs-progress-label">${done}/${total} pièces obligatoires fournies</span>
       </div>`
    : "";

  const foldersHtml = folders
    .map((f) => {
      const filesHtml = (f.files || [])
        .map(
          (file) => `<li class="dossier-doc-file">
            <a href="${esc(file.url)}" target="_blank" rel="noopener" class="dossier-doc-file-link">
              <span class="dossier-doc-file-icon">${fileIcon(file.ext)}</span>
              <span class="dossier-doc-file-name">${esc(file.original_name || "Document")}</span>
              <span class="dossier-doc-file-size">${esc(fmtBytes(file.size))}</span>
            </a>
            <button type="button" class="dossier-doc-file-del" title="Supprimer"
              data-doc-del data-folder="${esc(f.key)}" data-file="${esc(file.id)}">×</button>
          </li>`,
        )
        .join("");
      const badge = f.required
        ? (f.complete
            ? `<span class="dossier-doc-badge ok">✓ Fournie</span>`
            : `<span class="dossier-doc-badge req">Obligatoire</span>`)
        : (f.custom
            ? `<span class="dossier-doc-badge custom">Perso</span>`
            : `<span class="dossier-doc-badge opt">Facultative</span>`);
      const delFolder = f.custom
        ? `<button type="button" class="dossier-doc-folder-del" title="Supprimer le dossier" data-folder-del data-folder="${esc(f.key)}">🗑</button>`
        : "";
      return `<article class="dossier-doc-folder${f.complete ? " complete" : ""}" data-folder-card="${esc(f.key)}">
        <header class="dossier-doc-folder-head">
          <div class="dossier-doc-folder-title">
            <span class="dossier-doc-folder-icon">📁</span>
            <div>
              <strong>${esc(f.name)}</strong>
              ${f.description ? `<p class="dossier-doc-folder-desc">${esc(f.description)}</p>` : ""}
            </div>
          </div>
          <div class="dossier-doc-folder-actions">${badge}${delFolder}</div>
        </header>
        <ul class="dossier-doc-files">${filesHtml || '<li class="dossier-doc-empty">Aucune pièce</li>'}</ul>
        <label class="dossier-doc-drop" data-folder-drop="${esc(f.key)}" data-folder-name="${esc(f.name)}">
          <input type="file" hidden multiple data-folder-input="${esc(f.key)}">
          <span>+ Importer un document</span>
        </label>
      </article>`;
    })
    .join("");

  root.innerHTML = `
    <div class="dossier-docs-bar">${profileHtml}${progressHtml}</div>
    <div class="dossier-docs-grid">${foldersHtml}</div>
    <details class="dossier-docs-mentions">
      <summary>Mentions obligatoires du mandat écrit</summary>
      <ul>${(docs.mandate_mentions || []).map((m) => `<li>${esc(m)}</li>`).join("")}</ul>
    </details>`;

  root.querySelectorAll("[data-folder-input]").forEach((input) => {
    input.addEventListener("change", () =>
      uploadDossierDocuments(dossierId, input.dataset.folderInput,
        input.closest("[data-folder-drop]")?.dataset.folderName || "", input.files),
    );
  });
  root.querySelectorAll("[data-folder-drop]").forEach((drop) => {
    const input = drop.querySelector("input[type=file]");
    drop.addEventListener("click", (e) => {
      if (e.target.tagName !== "INPUT") input?.click();
    });
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("dragover");
      uploadDossierDocuments(dossierId, drop.dataset.folderDrop, drop.dataset.folderName, e.dataTransfer?.files);
    });
  });
  root.querySelectorAll("[data-doc-del]").forEach((btn) => {
    btn.addEventListener("click", () =>
      removeDossierDocument(dossierId, btn.dataset.folder, btn.dataset.file),
    );
  });
  root.querySelectorAll("[data-folder-del]").forEach((btn) => {
    btn.addEventListener("click", () => deleteDossierFolder(dossierId, btn.dataset.folder));
  });
}

async function uploadDossierDocuments(dossierId, folderKey, folderName, fileList) {
  if (!fileList?.length) return;
  let ok = 0;
  for (const file of fileList) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("folder_key", folderKey);
    fd.append("folder_name", folderName || "");
    try {
      await apiUpload(`/mandates/dossiers/${dossierId}/documents`, fd);
      ok += 1;
    } catch (err) {
      mandateDeps.showToast(`${file.name} : ${err.message}`, "error");
    }
  }
  if (ok) {
    await loadDossierDocuments(dossierId);
    mandateDeps.showToast(`${ok} document(s) importé(s)`, "success");
  }
}

async function removeDossierDocument(dossierId, folderKey, fileId) {
  try {
    await mandateDeps.api(
      `/mandates/dossiers/${dossierId}/documents/${encodeURIComponent(folderKey)}/${fileId}`,
      { method: "DELETE" },
    );
    await loadDossierDocuments(dossierId);
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function createDossierFolder(dossierId) {
  const name = prompt("Nom du nouveau dossier (ex. : Servitudes, Travaux…)");
  if (!name?.trim()) return;
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}/folders`, {
      method: "POST",
      body: JSON.stringify({ name: name.trim() }),
    });
    await loadDossierDocuments(dossierId);
    mandateDeps.showToast("Dossier créé", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function deleteDossierFolder(dossierId, folderKey) {
  if (!confirm("Supprimer ce dossier et les pièces qu'il contient ?")) return;
  try {
    await mandateDeps.api(
      `/mandates/dossiers/${dossierId}/folders/${encodeURIComponent(folderKey)}`,
      { method: "DELETE" },
    );
    await loadDossierDocuments(dossierId);
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function createMandateDossier(fromMandate) {
  if (!editingMandate) return;
  try {
    const body = fromMandate ? { from_mandate: true } : { title: "Nouveau dossier" };
    const data = await mandateDeps.api(`/mandates/${editingMandate.id}/dossiers`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    activeDossierId = data.dossier.id;
    await loadMandateDossiers();
    switchMandateTab("dossiers");
    mandateDeps.showToast("Dossier créé", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function saveDossierFields(dossierId) {
  const panel = document.getElementById("mandate-dossier-detail");
  const payload = {
    title: panel?.querySelector("#dossier-title")?.value.trim(),
    property_address: panel?.querySelector("#dossier-address")?.value.trim(),
    city: panel?.querySelector("#dossier-city")?.value.trim(),
    surface: parseFloat(panel?.querySelector("#dossier-surface")?.value) || null,
    price: parseInt(panel?.querySelector("#dossier-price")?.value, 10) || null,
    description: panel?.querySelector("#dossier-description")?.value.trim(),
  };
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    propertyClientsCache = null;
    await loadMandateDossiers();
    mandateDeps.showToast("Dossier enregistré", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function uploadDossierPhotos(dossierId, fileList) {
  if (!fileList?.length) return;
  let ok = 0;
  for (const file of fileList) {
    const fd = new FormData();
    fd.append("file", file);
    try {
      await apiUpload(`/mandates/dossiers/${dossierId}/photos`, fd);
      ok += 1;
    } catch (err) {
      mandateDeps.showToast(`${file.name} : ${err.message}`, "error");
    }
  }
  if (ok) {
    await loadMandateDossiers();
    mandateDeps.showToast(`${ok} photo(s) ajoutée(s)`, "success");
  }
}

async function removeDossierPhoto(dossierId, photoId) {
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}/photos/${photoId}`, { method: "DELETE" });
    await loadMandateDossiers();
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function linkClientToDossier(dossierId, clientId) {
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}/clients`, {
      method: "POST",
      body: JSON.stringify({ client_id: clientId }),
    });
    propertyClientsCache = null;
    await loadMandateDossiers();
    mandateDeps.showToast("Client ajouté au dossier", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function unlinkClientFromDossier(dossierId, clientId) {
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}/clients`, {
      method: "DELETE",
      body: JSON.stringify({ client_id: clientId }),
    });
    await loadMandateDossiers();
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

async function deleteDossier(dossierId) {
  if (!confirm("Supprimer ce dossier et toutes ses photos ?")) return;
  try {
    await mandateDeps.api(`/mandates/dossiers/${dossierId}`, { method: "DELETE" });
    if (activeDossierId === dossierId) activeDossierId = null;
    await loadMandateDossiers();
    mandateDeps.showToast("Dossier supprimé", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

function confirmDeleteMandate(mandate) {
  const addr = mandate.fields?.property_address || mandate.title || "ce mandat";
  if (!confirm(`Supprimer le mandat « ${addr} » ? Cette action est irréversible.`)) return;
  deleteMandateById(mandate.id);
}

async function deleteMandateEditor() {
  if (!editingMandate) return;
  confirmDeleteMandate(editingMandate);
}

async function deleteMandateById(mandateId) {
  try {
    await mandateDeps.api(`/mandates/${mandateId}`, { method: "DELETE" });
    if (editingMandate?.id === mandateId) closeMandateEditor();
    await loadMandates();
    renderMandatesList();
    mandateDeps.showToast("Mandat supprimé", "success");
  } catch (err) {
    mandateDeps.showToast(err.message, "error");
  }
}

window.VelioraMandates = { createMandateFromLead };
