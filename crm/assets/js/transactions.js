/* Transactions — cockpit affaires : une carte, une action, parcours lisible. */

(function () {
  const fmtEuro = (n) =>
    n == null || n === "" || Number.isNaN(Number(n))
      ? "—"
      : `${Math.round(Number(n)).toLocaleString("fr-FR")} €`;

  const PIPELINE = [
    { key: "prospect", label: "Nouveau" },
    { key: "pris_en_charge", label: "En charge" },
    { key: "contacte", label: "Contacté" },
    { key: "mandat_cree", label: "Mandat" },
    { key: "mandat_valide", label: "Mandat OK" },
    { key: "publie", label: "En ligne" },
    { key: "acquereur", label: "Client" },
    { key: "visite", label: "Visite" },
    { key: "dossier_acquereur", label: "Dossier" },
    { key: "compromis", label: "Compromis" },
    { key: "vendu", label: "Terminé" },
  ];

  const STAGE_HINTS = {
    prospect: "Assignez un agent : l'affaire démarre dans votre pipeline.",
    pris_en_charge: "Appelez le vendeur pour estimer le bien et le convaincre.",
    contacte: "Le vendeur est chaud : créez le mandat vente ou location.",
    mandat_cree: "Cochez vendeur puis agent — obligatoire avant publication.",
    mandat_valide: "Publiez l'annonce sur votre catalogue Veliora.",
    publie: "Rapprochez un acquéreur ou locataire compatible (liste filtrée).",
    acquereur: "Planifiez la visite avec le client rapproché.",
    visite: "Préparez le dossier acquéreur pour la banque / notaire.",
    dossier_acquereur: "Enregistrez le compromis de vente.",
    compromis: "Saisissez la commission : le bien sort des Prospects.",
    vendu: "Consultez le détail dans Commissions.",
  };

  const ACTION_LABELS = {
    call: "J'ai contacté le vendeur",
    mandate: "Créer le mandat",
    publish: "Publier sur le catalogue",
    buyer: "Rapprocher un client",
    visit: "Visite planifiée",
    buyer_dossier: "Dossier acquéreur prêt",
    compromis: "Compromis signé",
    finalize: "Clôturer la vente",
  };

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function escAttr(s) {
    return esc(s).replace(/'/g, "&#39;");
  }

  let CACHE = { deals: [], stages: [], agents: [] };
  let SCOPE = "all";
  let FILTER = "active";

  async function fetchTransactions(scope) {
    const q = scope === "mine" ? "?scope=mine" : "";
    const res = await api(`/transactions${q}`);
    CACHE = {
      deals: res.deals || [],
      stages: res.stages || [],
      agents: res.agents || [],
    };
    return CACHE;
  }

  function agentOptions(selectedId) {
    return CACHE.agents
      .map(
        (a) =>
          `<option value="${escAttr(a.id)}" ${a.id === selectedId ? "selected" : ""}>${esc(a.name)}</option>`,
      )
      .join("");
  }

  function filterDeals(deals) {
    if (FILTER === "done") return deals.filter((d) => d.stage === "vendu");
    if (FILTER === "active") return deals.filter((d) => d.stage !== "vendu");
    return deals;
  }

  function sortDeals(deals) {
    return [...deals].sort((a, b) => {
      const av = a.stage === "vendu" ? 1 : 0;
      const bv = b.stage === "vendu" ? 1 : 0;
      if (av !== bv) return av - bv;
      return (a.stage_index ?? 0) - (b.stage_index ?? 0);
    });
  }

  function pipelineRoadmapHtml(currentStage) {
    const idx = Math.max(0, PIPELINE.findIndex((s) => s.key === currentStage));
    const steps = PIPELINE.map((s, i) => {
      let cls = "tx-roadmap-step";
      if (i < idx) cls += " is-done";
      else if (i === idx) cls += " is-current";
      return `<span class="${cls}" title="${esc(s.label)}"><i aria-hidden="true"></i><em>${esc(s.label)}</em></span>`;
    }).join("");
    return `<div class="tx-roadmap" aria-label="Parcours des 11 étapes">${steps}</div>`;
  }

  function stepperHtml(deal) {
    const pct = Math.round(((deal.stage_index + 1) / deal.stage_total) * 100);
    return `
      ${pipelineRoadmapHtml(deal.stage)}
      <div class="tx-stepper" title="Étape ${deal.stage_index + 1} sur ${deal.stage_total}">
        <div class="tx-stepper-bar"><span style="width:${pct}%"></span></div>
        <div class="tx-stepper-label">Étape ${deal.stage_index + 1}/${deal.stage_total} · ${esc(deal.stage_label)}</div>
      </div>`;
  }

  function nextStepHtml(deal) {
    if (deal.stage === "vendu") {
      return `<div class="tx-next tx-next--done"><span class="tx-next-kicker">Statut</span><strong>Affaire terminée</strong><p class="tx-next-hint">Le bien n'apparaît plus dans Prospects. Détail dans Commissions.</p></div>`;
    }
    const hint = STAGE_HINTS[deal.stage] || "";
    const action = deal.next_action || "Continuer";
    return `<div class="tx-next">
      <span class="tx-next-kicker">Prochaine étape</span>
      <strong>${esc(action)}</strong>
      ${hint ? `<p class="tx-next-hint">${esc(hint)}</p>` : ""}
    </div>`;
  }

  function primaryActionHtml(deal) {
    const id = deal.lead_id;
    if (deal.stage === "prospect") {
      return `<div class="tx-assign">
        <label class="tx-assign-label">Agent en charge</label>
        <select class="tx-agent-select" data-tx-agent="${id}" aria-label="Agent">
          <option value="">— Choisir —</option>${agentOptions()}
        </select>
        <button class="btn btn-primary btn-sm" data-tx-action="assign" data-id="${id}">Prendre en charge</button>
      </div>`;
    }
    if (deal.stage === "mandat_cree") {
      return `<div class="tx-validate">
        <p class="tx-validate-hint">Validez les deux parties pour débloquer la publication.</p>
        <div class="tx-validate-btns">
          <button class="btn btn-secondary btn-sm" data-tx-action="validate-owner" data-id="${id}" data-mid="${escAttr(deal.mandate_id)}">✓ Vendeur</button>
          <button class="btn btn-secondary btn-sm" data-tx-action="validate-agent" data-id="${id}" data-mid="${escAttr(deal.mandate_id)}">✓ Agent</button>
        </div>
      </div>`;
    }
    const map = {
      pris_en_charge: ["call", ACTION_LABELS.call],
      contacte: ["mandate", ACTION_LABELS.mandate],
      mandat_valide: ["publish", ACTION_LABELS.publish],
      publie: ["buyer", ACTION_LABELS.buyer],
      acquereur: ["visit", ACTION_LABELS.visit],
      visite: ["buyer_dossier", ACTION_LABELS.buyer_dossier],
      dossier_acquereur: ["compromis", ACTION_LABELS.compromis],
      compromis: ["finalize", ACTION_LABELS.finalize],
      vendu: ["done", "Terminé"],
    };
    const a = map[deal.stage];
    if (!a) return "";
    if (a[0] === "done") return `<span class="tx-done">✓ Vente conclue</span>`;
    return `<button class="btn btn-primary" data-tx-action="${a[0]}" data-id="${id}">${esc(a[1])}</button>`;
  }

  function dealCardHtml(deal) {
    const tx = deal.transaction_type === "location" ? "Location" : "Vente";
    return `
      <article class="tx-card tx-stage-${esc(deal.stage)}" data-id="${deal.lead_id}" data-stage="${esc(deal.stage)}">
        <div class="tx-card-head">
          <div>
            <h4>${esc(deal.property_title || deal.owner || "Bien")}</h4>
            <p class="tx-card-sub">${esc(deal.property_type || "")} · ${tx} · ${esc(deal.city || "—")}</p>
          </div>
          <div class="tx-card-figures">
            <span>${fmtEuro(deal.price)}</span>
            <small>${deal.surface ? `${deal.surface} m²` : ""}</small>
          </div>
        </div>
        ${stepperHtml(deal)}
        ${nextStepHtml(deal)}
        <div class="tx-card-meta">
          <span class="tx-agent-badge">${deal.agent_name ? "👤 " + esc(deal.agent_name) : "Non assigné"}</span>
          ${deal.mandate_validated ? '<span class="tx-badge tx-badge-ok">Mandat validé</span>' : ""}
        </div>
        <div class="tx-card-actions">
          ${primaryActionHtml(deal)}
          <button type="button" class="btn btn-ghost btn-sm" data-tx-action="lead" data-id="${deal.lead_id}">Fiche prospect</button>
          <button type="button" class="btn btn-ghost btn-sm" data-tx-action="docs" data-id="${deal.lead_id}">📁 Pièces &amp; documents</button>
          <button type="button" class="btn btn-ghost btn-sm" data-tx-action="dossier" data-id="${deal.lead_id}">Dossier complet</button>
        </div>
      </article>`;
  }

  function processGuideHtml(scope) {
    const who =
      scope === "mine"
        ? "Votre pipeline : uniquement les biens que vous avez pris en charge."
        : "Toutes les affaires de l'agence — du premier contact à la vente.";
    const steps = PIPELINE.map(
      (s, i) => `<li><strong>${i + 1}. ${esc(s.label)}</strong> — ${esc(STAGE_HINTS[s.key] || "")}</li>`,
    ).join("");
    return `
      <details class="tx-guide">
        <summary>Comment fonctionnent les Affaires ?</summary>
        <p class="tx-guide-lead">${who} Chaque carte affiche <strong>une seule action</strong> à faire : une fois faite, l'étape avance automatiquement.</p>
        <ol class="tx-guide-steps">${steps}</ol>
        <p class="tx-guide-foot">Départ : onglet <strong>Prospects</strong> → « Prendre en charge ». Fin : « Clôturer la vente » → le bien disparaît des Prospects.</p>
      </details>`;
  }

  function filterBarHtml(deals) {
    const active = deals.filter((d) => d.stage !== "vendu").length;
    const done = deals.filter((d) => d.stage === "vendu").length;
    const btn = (key, label, count) =>
      `<button type="button" class="tx-filter-btn${FILTER === key ? " active" : ""}" data-tx-filter="${key}">${label} <span class="tx-filter-count">${count}</span></button>`;
    return `<div class="tx-filters" role="group" aria-label="Filtrer les affaires">
      ${btn("active", "En cours", active)}
      ${btn("done", "Terminées", done)}
      ${btn("all", "Toutes", deals.length)}
    </div>`;
  }

  function headerHtml(scope, deals) {
    const intro =
      scope === "mine"
        ? "Suivez vos biens du premier appel jusqu'à la vente. Une carte = une action claire."
        : "Vue agence : qui fait quoi, à quelle étape. Cliquez le bouton bleu sur chaque carte.";
    return `<div class="tx-header">
      <p class="tx-intro">${intro}</p>
      <div class="tx-toolbar">
        <button type="button" class="btn btn-primary btn-sm" data-tx-action="go-prospects">+ Récupérer une annonce</button>
        <span class="tx-toolbar-hint">Ouvrez une annonce dans Prospects et cliquez « Prendre en charge » : elle arrive ici.</span>
      </div>
      ${processGuideHtml(scope)}
      ${filterBarHtml(deals)}
    </div>`;
  }

  function renderInto(rootId, opts = {}) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const allDeals = CACHE.deals;
    const deals = sortDeals(filterDeals(allDeals));
    if (!allDeals.length) {
      root.innerHTML = `${headerHtml(opts.scope, [])}<div class="tx-empty">
        <strong>Aucune affaire pour l'instant</strong>
        <p>Allez dans <strong>Prospects</strong>, ouvrez une annonce et cliquez <strong>Prendre en charge</strong> pour démarrer le parcours ici.</p>
      </div>`;
      bindActions(root);
      return;
    }
    if (!deals.length) {
      root.innerHTML = `${headerHtml(opts.scope, allDeals)}<div class="tx-empty">Aucune affaire dans ce filtre. Essayez « Toutes » ou « Terminées ».</div>`;
      bindActions(root);
      return;
    }
    const legend = CACHE.stages
      .filter((s) => s.count > 0)
      .map((s) => `<span class="tx-legend-chip"><b>${s.count}</b> ${esc(s.label)}</span>`)
      .join("");
    root.innerHTML = `
      ${headerHtml(opts.scope, allDeals)}
      ${legend ? `<div class="tx-legend" aria-label="Répartition par étape">${legend}</div>` : ""}
      <div class="tx-grid">${deals.map(dealCardHtml).join("")}</div>`;
    bindActions(root);
  }

  function bindActions(root) {
    root.querySelectorAll("[data-tx-filter]").forEach((btn) => {
      btn.addEventListener("click", () => {
        FILTER = btn.dataset.txFilter || "active";
        renderInto(root.id, { scope: SCOPE });
      });
    });
    root.querySelectorAll("[data-tx-action]").forEach((btn) => {
      btn.addEventListener("click", () => handleAction(btn));
    });
  }

  async function handleAction(btn) {
    const action = btn.dataset.txAction;
    const id = Number(btn.dataset.id);
    try {
      if (action === "go-prospects") {
        if (typeof switchView === "function") switchView("leads");
        return;
      }
      if (action === "lead") {
        if (typeof openDrawer === "function") openDrawer(id);
        return;
      }
      if (action === "assign") {
        const sel = btn.closest(".tx-assign")?.querySelector(".tx-agent-select");
        const agentId = sel?.value;
        if (!agentId) return showToast("Choisissez un agent", "error");
        await api(`/leads/${id}/assign`, { method: "POST", body: JSON.stringify({ agent_id: agentId }) });
        showToast("Prise en charge enregistrée · dossier de pièces créé — appelez le vendeur", "success");
      } else if (action === "call") {
        await api(`/leads/${id}/outcome`, { method: "POST", body: JSON.stringify({ outcome_type: "call" }) });
        showToast("Contact enregistré — vous pouvez créer le mandat", "success");
      } else if (action === "mandate") {
        await createMandateForLead(id);
      } else if (action === "validate-owner") {
        await api(`/mandates/${btn.dataset.mid}/validate`, { method: "POST", body: JSON.stringify({ party: "owner" }) });
        showToast("Validation vendeur OK", "success");
      } else if (action === "validate-agent") {
        const r = await api(`/mandates/${btn.dataset.mid}/validate`, { method: "POST", body: JSON.stringify({ party: "agent" }) });
        showToast(
          r.fully_validated ? "Mandat complet — vous pouvez publier l'annonce" : "Validation agent OK — il manque l'autre partie",
          "success",
        );
      } else if (action === "publish") {
        const r = await api(`/portal/listings/from-lead`, { method: "POST", body: JSON.stringify({ lead_id: id }) });
        showToast(r.ok ? "Annonce en ligne — rapprochez un client" : (r.error || "Publication impossible"), r.ok ? "success" : "error");
      } else if (action === "buyer") {
        await pickBuyer(id);
      } else if (action === "visit") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "visit" }) });
        showToast("Visite enregistrée", "success");
      } else if (action === "buyer_dossier") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "buyer_dossier" }) });
        showToast("Dossier acquéreur enregistré", "success");
      } else if (action === "compromis") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "compromis" }) });
        showToast("Compromis enregistré — saisissez la commission", "success");
      } else if (action === "finalize") {
        await finalizeDeal(id);
      } else if (action === "docs") {
        await openLeadDocuments(id);
        return;
      } else if (action === "dossier") {
        await openDossier(id);
        return;
      }
      await reloadCurrent();
    } catch (err) {
      showToast(err.message || "Action impossible", "error");
    }
  }

  async function createMandateForLead(leadId) {
    const deal = CACHE.deals.find((d) => d.lead_id === leadId);
    const type = deal?.transaction_type === "location" ? "location" : "vente";
    await api(`/mandates`, { method: "POST", body: JSON.stringify({ mandate_type: type, lead_id: leadId }) });
    showToast(`Mandat ${type} créé — validez vendeur + agent`, "success");
  }

  async function pickBuyer(leadId) {
    const deal = CACHE.deals.find((d) => d.lead_id === leadId);
    const tx = deal?.transaction_type === "location" ? "location" : "vente";
    const roleLabel = tx === "location" ? "locataire" : "acquéreur";
    let eligible = [];
    let hints = [];
    try {
      const m = await api(`/leads/${leadId}/matches`);
      eligible = tx === "location" ? m.location_matches || [] : m.vente_matches || [];
      hints = (m.diagnostics && m.diagnostics.hints) || [];
    } catch {
      /* ignore */
    }
    const opts = eligible.length
      ? eligible
          .map((c) => {
            const reasons = (c.reasons || []).slice(0, 2).join(" · ");
            const budget =
              c.in_budget === true ? "dans le budget" : c.in_budget === false ? "hors budget" : "";
            const extra = [reasons, budget].filter(Boolean).join(" · ");
            return `<option value="${escAttr(c.client_id)}">${esc(c.name || c.full_name || "Client")} — ${c.score}%${extra ? ` · ${esc(extra)}` : ""}</option>`;
          })
          .join("")
      : "";
    const hintHtml = hints.length
      ? `<p class="form-hint">${hints.map((h) => esc(h)).join("<br>")}</p>`
      : "";
    const body = eligible.length
      ? `<p class="form-hint">Profils compatibles uniquement (secteur, budget, type de bien).</p>
          <label class="form-field"><span>Choisir un ${roleLabel}</span>
          <select id="tx-buyer-select" required>${opts}</select></label>${hintHtml}`
      : `<p>Aucun ${roleLabel} compatible.</p>
          <p class="form-hint">Complétez les fiches dans <strong>Clients</strong> (villes, budget, critères).</p>${hintHtml}`;
    const ok = await modal(`Rapprocher un ${roleLabel}`, body, eligible.length);
    if (!ok) return;
    const clientId = document.getElementById("tx-buyer-select")?.value;
    await api(`/transactions/${leadId}/buyer`, { method: "POST", body: JSON.stringify({ client_id: clientId }) });
    showToast(`${roleLabel.charAt(0).toUpperCase() + roleLabel.slice(1)} rapproché — planifiez la visite`, "success");
  }

  async function finalizeDeal(leadId) {
    const body = `
      <p class="form-hint">La vente sera clôturée et le bien retiré de la liste Prospects.</p>
      <label class="form-field"><span>Commission totale encaissée (€)</span>
        <input type="number" id="tx-commission" min="1" step="100" placeholder="9000"></label>
      <label class="form-field"><span>Part de l'agent (%)</span>
        <input type="number" id="tx-agent-pct" min="0" max="100" step="1" value="30"></label>`;
    const ok = await modal("Clôturer la vente", body, true, "Enregistrer et retirer des Prospects");
    if (!ok) return;
    const total = parseFloat(document.getElementById("tx-commission")?.value || "0");
    const pct = parseFloat(document.getElementById("tx-agent-pct")?.value || "30");
    if (!total || total <= 0) return showToast("Montant de commission requis", "error");
    const r = await api(`/transactions/${leadId}/finalize`, {
      method: "POST",
      body: JSON.stringify({ total_amount: total, agent_pct: pct }),
    });
    const c = r.commission || {};
    showToast(
      `Vente conclue · retirée des Prospects · agence ${fmtEuro(c.agency_amount)} · agent ${fmtEuro(c.agent_amount)}`,
      "success",
    );
    if (typeof window.velioraReloadLeads === "function") {
      try {
        await window.velioraReloadLeads();
      } catch {
        /* ignore */
      }
    }
  }

  // ── Pièces & documents du prospect (dossier auto-créé à la prise en charge) ──

  function txFileIcon(ext) {
    const e = (ext || "").toLowerCase();
    if (e === "pdf") return "📕";
    if (["jpg", "jpeg", "png", "webp", "gif", "heic", "heif"].includes(e)) return "🖼️";
    if (["doc", "docx", "odt", "rtf", "txt"].includes(e)) return "📄";
    if (["xls", "xlsx", "ods", "csv"].includes(e)) return "📊";
    if (e === "zip") return "🗜️";
    return "📎";
  }

  function txFmtBytes(n) {
    if (!n) return "";
    if (n < 1024) return `${n} o`;
    if (n < 1024 * 1024) return `${Math.round(n / 1024)} Ko`;
    return `${(n / (1024 * 1024)).toFixed(1)} Mo`;
  }

  async function txUploadFiles(dossierId, folderKey, folderName, fileList) {
    if (!fileList?.length) return;
    const authHeaders = typeof getAuthHeaders === "function" ? getAuthHeaders() : {};
    let ok = 0;
    for (const file of fileList) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("folder_key", folderKey);
      fd.append("folder_name", folderName || "");
      try {
        const res = await fetch(`${API}/mandates/dossiers/${dossierId}/documents`, {
          method: "POST",
          headers: { ...authHeaders },
          body: fd,
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.error || `Erreur ${res.status}`);
        ok += 1;
      } catch (err) {
        showToast(`${file.name} : ${err.message}`, "error");
      }
    }
    if (ok) {
      showToast(`${ok} document(s) importé(s)`, "success");
      await reloadLeadDocuments(dossierId);
    }
  }

  async function reloadLeadDocuments(dossierId) {
    try {
      const d = await api(`/mandates/dossiers/${dossierId}/documents`);
      renderLeadDocuments(dossierId, d.documents || {});
    } catch (err) {
      showToast(err.message || "Pièces indisponibles", "error");
    }
  }

  function renderLeadDocuments(dossierId, docs) {
    const root = document.getElementById("tx-docs-root");
    if (!root) return;
    const folders = docs.folders || [];
    const total = docs.required_total || 0;
    const done = docs.required_done || 0;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const profile = docs.profile?.label || "";

    const foldersHtml = folders
      .map((f) => {
        const files = (f.files || [])
          .map(
            (file) => `<li class="tx-doc-file">
              <a href="${escAttr(file.url)}" target="_blank" rel="noopener" class="tx-doc-file-link">
                <span>${txFileIcon(file.ext)}</span>
                <span class="tx-doc-file-name">${esc(file.original_name || "Document")}</span>
                <span class="tx-doc-file-size">${esc(txFmtBytes(file.size))}</span>
              </a>
              <button type="button" class="tx-doc-del" data-doc-del data-folder="${escAttr(f.key)}" data-file="${escAttr(file.id)}" title="Supprimer">×</button>
            </li>`,
          )
          .join("");
        const badge = f.required
          ? f.complete
            ? `<span class="tx-doc-badge ok">✓ Fournie</span>`
            : `<span class="tx-doc-badge req">Obligatoire</span>`
          : f.custom
            ? `<span class="tx-doc-badge custom">Perso</span>`
            : `<span class="tx-doc-badge opt">Facultative</span>`;
        const delFolder = f.custom
          ? `<button type="button" class="tx-doc-folder-del" data-folder-del data-folder="${escAttr(f.key)}" title="Supprimer le dossier">🗑</button>`
          : "";
        return `<article class="tx-doc-folder${f.complete ? " complete" : ""}">
          <header class="tx-doc-folder-head">
            <div class="tx-doc-folder-title"><strong>📁 ${esc(f.name)}</strong>${f.description ? `<p class="tx-doc-folder-desc">${esc(f.description)}</p>` : ""}</div>
            <div class="tx-doc-folder-actions">${badge}${delFolder}</div>
          </header>
          <ul class="tx-doc-files">${files || '<li class="tx-doc-empty">Aucune pièce</li>'}</ul>
          <label class="tx-doc-drop" data-folder-name="${escAttr(f.name)}">
            <input type="file" hidden multiple data-folder-input="${escAttr(f.key)}">
            <span>+ Importer un document</span>
          </label>
        </article>`;
      })
      .join("");

    root.innerHTML = `
      <p class="tx-docs-intro">Espace ouvert automatiquement à la prise en charge. Rassemblez ici les pièces du vendeur (identité, diagnostics, titre de propriété…) — glissez-déposez ou cliquez « Importer ».${profile ? ` <strong>Profil détecté : ${esc(profile)}</strong>.` : ""}</p>
      ${total ? `<div class="tx-docs-progress"><div class="tx-docs-progress-bar"><span style="width:${pct}%"></span></div><span class="tx-docs-progress-label">${done}/${total} pièces obligatoires fournies</span></div>` : ""}
      <div class="tx-docs-toolbar"><button type="button" class="btn btn-secondary btn-sm" id="tx-docs-new-folder">+ Nouveau dossier</button></div>
      <div class="tx-docs-grid">${foldersHtml}</div>`;

    root.querySelectorAll("[data-folder-input]").forEach((input) => {
      input.addEventListener("change", () =>
        txUploadFiles(
          dossierId,
          input.dataset.folderInput,
          input.closest(".tx-doc-drop")?.dataset.folderName || "",
          input.files,
        ),
      );
    });
    root.querySelectorAll(".tx-doc-drop").forEach((drop) => {
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
        txUploadFiles(dossierId, input?.dataset.folderInput, drop.dataset.folderName, e.dataTransfer?.files);
      });
    });
    root.querySelectorAll("[data-doc-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api(
            `/mandates/dossiers/${dossierId}/documents/${encodeURIComponent(btn.dataset.folder)}/${btn.dataset.file}`,
            { method: "DELETE" },
          );
          await reloadLeadDocuments(dossierId);
        } catch (err) {
          showToast(err.message, "error");
        }
      });
    });
    root.querySelectorAll("[data-folder-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Supprimer ce dossier et les pièces qu'il contient ?")) return;
        try {
          await api(`/mandates/dossiers/${dossierId}/folders/${encodeURIComponent(btn.dataset.folder)}`, {
            method: "DELETE",
          });
          await reloadLeadDocuments(dossierId);
        } catch (err) {
          showToast(err.message, "error");
        }
      });
    });
    root.querySelector("#tx-docs-new-folder")?.addEventListener("click", async () => {
      const name = prompt("Nom du nouveau dossier (ex. : Servitudes, Travaux…)");
      if (!name?.trim()) return;
      try {
        await api(`/mandates/dossiers/${dossierId}/folders`, {
          method: "POST",
          body: JSON.stringify({ name: name.trim() }),
        });
        await reloadLeadDocuments(dossierId);
      } catch (err) {
        showToast(err.message, "error");
      }
    });
  }

  async function openLeadDocuments(leadId) {
    let data;
    try {
      data = await api(`/leads/${leadId}/document-folder`);
    } catch (err) {
      return showToast(err.message || "Dossier indisponible", "error");
    }
    const title = data.title ? ` — ${data.title}` : "";
    modal(`Pièces & documents${title}`, `<div id="tx-docs-root" class="tx-docs"></div>`, false);
    renderLeadDocuments(data.dossier_id, data.documents || {});
  }

  async function openDossier(leadId) {
    let d;
    try {
      d = await api(`/transactions/${leadId}/dossier`);
    } catch (err) {
      return showToast(err.message || "Dossier indisponible", "error");
    }
    const p = d.property || {},
      s = d.seller || {},
      b = d.buyer || {},
      ag = d.agent || {},
      agency = d.agency || {},
      m = d.mandate || {};
    const body = `
      <div class="tx-dossier">
        <div class="tx-dossier-stage">${esc(d.transaction?.stage_label || "")} — étape ${(d.transaction?.stage_index ?? 0) + 1}/${d.transaction?.stage_total ?? 11}</div>
        <p class="tx-dossier-next">${esc(d.transaction?.next_action || "")}</p>
        <fieldset class="tx-dossier-block"><legend>Le bien</legend>
          <div class="portal-form-grid">
            <label class="form-field"><span>Titre</span><input id="dz-title" value="${escAttr(p.title)}"></label>
            <label class="form-field"><span>Type</span><input id="dz-type" value="${escAttr(p.type)}"></label>
            <label class="form-field"><span>Surface m²</span><input id="dz-surface" type="number" value="${escAttr(p.surface ?? "")}"></label>
            <label class="form-field"><span>Prix €</span><input id="dz-price" type="number" value="${escAttr(p.price ?? "")}"></label>
            <label class="form-field form-field-wide"><span>Adresse</span><input id="dz-address" value="${escAttr(p.address)}"></label>
            <label class="form-field"><span>Code postal</span><input id="dz-postcode" value="${escAttr(p.postcode)}"></label>
            <label class="form-field"><span>Ville</span><input id="dz-city" value="${escAttr(p.city)}" data-city-autocomplete></label>
          </div>
        </fieldset>
        <div class="tx-dossier-cols">
          <fieldset class="tx-dossier-block"><legend>Vendeur</legend>
            <p>${esc([s.first_name, s.last_name].filter(Boolean).join(" ") || "—")}</p>
            <p>${esc(s.phone || "—")} · ${esc(s.email || "—")}</p>
            ${s.email ? `<button type="button" class="btn btn-secondary btn-sm" data-dz-email="seller" data-id="${leadId}">Email vendeur</button>` : ""}
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>Acquéreur / locataire</legend>
            <p>${b && b.full_name ? esc(b.full_name) : "Aucun rapproché"}</p>
            <p>${esc((b && b.email) || "")}</p>
            ${b && b.email ? `<button type="button" class="btn btn-secondary btn-sm" data-dz-email="buyer" data-id="${leadId}">Email acquéreur</button>` : ""}
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>Agent</legend>
            <p>${esc(ag.agent_name || "Non assigné")}</p>
            <p>${esc(agency.name || "")}</p>
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>Mandat</legend>
            <p>${m.id ? `${esc(m.type || "")} · ${esc(m.status || "")}` : "Pas encore créé"}</p>
            <p>Vendeur ${m.owner_validated_at ? "✓" : "—"} · Agent ${m.agent_validated_at ? "✓" : "—"}</p>
          </fieldset>
        </div>
      </div>`;
    const ok = await modal("Dossier de l'affaire", body, true, "Enregistrer", (overlay) => {
      overlay.querySelectorAll("[data-dz-email]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            const r = await api(`/transactions/${leadId}/email`, {
              method: "POST",
              body: JSON.stringify({ to_role: btn.dataset.dzEmail }),
            });
            showToast(
              r.email_configured ? `Email envoyé à ${r.to}` : "SMTP non configuré",
              r.email_configured ? "success" : "error",
            );
          } catch (e) {
            showToast(e.message, "error");
          }
        });
      });
    });
    if (!ok) return;
    const patch = {
      surface: parseFloat(document.getElementById("dz-surface")?.value) || undefined,
      price: parseInt(document.getElementById("dz-price")?.value, 10) || undefined,
      address: document.getElementById("dz-address")?.value?.trim(),
      postcode: document.getElementById("dz-postcode")?.value?.trim(),
      city: document.getElementById("dz-city")?.value?.trim(),
    };
    try {
      await api(`/leads/${leadId}`, { method: "PATCH", body: JSON.stringify(patch) });
      showToast("Dossier mis à jour", "success");
      await reloadCurrent();
    } catch (e) {
      showToast(e.message, "error");
    }
  }

  function modal(title, bodyHtml, showConfirm, confirmLabel = "Valider", onReady) {
    let overlay = document.getElementById("tx-modal-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "tx-modal-overlay";
      overlay.className = "modal-overlay";
      overlay.innerHTML = `<div class="modal-card modal-card-wide" role="dialog" aria-modal="true">
        <button type="button" class="modal-close" data-tx-close aria-label="Fermer">×</button>
        <h2 id="tx-modal-title"></h2><div id="tx-modal-body"></div>
        <div id="tx-modal-actions" class="modal-actions"></div></div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay || e.target.closest("[data-tx-close]")) overlay.classList.remove("open");
      });
    }
    overlay.querySelector("#tx-modal-title").textContent = title;
    overlay.querySelector("#tx-modal-body").innerHTML = bodyHtml;
    const actions = overlay.querySelector("#tx-modal-actions");
    actions.innerHTML = "";
    if (typeof onReady === "function") onReady(overlay);
    return new Promise((resolve) => {
      const buttons = showConfirm
        ? [
            { label: "Annuler", v: false },
            { label: confirmLabel, v: true, primary: true },
          ]
        : [{ label: "Fermer", v: false, primary: true }];
      buttons.forEach((b) => {
        const el = document.createElement("button");
        el.type = "button";
        el.className = `btn ${b.primary ? "btn-primary" : "btn-secondary"}`;
        el.textContent = b.label;
        el.addEventListener("click", () => {
          overlay.classList.remove("open");
          resolve(b.v);
        });
        actions.appendChild(el);
      });
      overlay.classList.add("open");
    });
  }

  async function reloadCurrent() {
    await fetchTransactions(SCOPE === "mine" ? "mine" : "all");
    if (SCOPE === "mine") renderInto("pipeline-board", { scope: "mine" });
    else renderInto("transactions-root", { scope: "all" });
  }

  async function renderTransactionsView() {
    SCOPE = "all";
    FILTER = "active";
    const root = document.getElementById("transactions-root");
    if (root) root.innerHTML = `<p class="tx-loading">Chargement des affaires…</p>`;
    try {
      await fetchTransactions("all");
      renderInto("transactions-root", { scope: "all" });
    } catch (err) {
      if (root) root.innerHTML = `<p class="tx-error">${esc(err.message)}</p>`;
    }
  }

  async function renderPipelineView() {
    SCOPE = "mine";
    FILTER = "active";
    const root = document.getElementById("pipeline-board");
    if (root) root.innerHTML = `<p class="tx-loading">Chargement de votre pipeline…</p>`;
    try {
      await fetchTransactions("mine");
      renderInto("pipeline-board", { scope: "mine" });
    } catch (err) {
      if (root) root.innerHTML = `<p class="tx-error">${esc(err.message)}</p>`;
    }
  }

  function commInitials(name) {
    return String(name || "—")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((p) => p[0].toUpperCase())
      .join("") || "—";
  }

  function commDate(iso) {
    const s = (iso || "").slice(0, 10);
    if (!s) return "—";
    const [y, m, d] = s.split("-");
    return d && m && y ? `${d}/${m}/${y}` : s;
  }

  async function renderCommissionsView() {
    const root = document.getElementById("commissions-root");
    if (!root) return;
    root.innerHTML = `<p class="tx-loading">Chargement des commissions…</p>`;
    let r;
    try {
      r = await api(`/commissions`);
    } catch (err) {
      root.innerHTML = `<p class="tx-error">${esc(err.message)}</p>`;
      return;
    }

    const header = `<header class="tx-comm-header">
      <h2 class="tx-comm-title">Commissions</h2>
      <p class="tx-comm-sub">Honoraires des affaires <strong>clôturées</strong> depuis l'onglet Affaires — répartition agence / agents.</p>
    </header>`;

    if (!r.deals_count) {
      root.innerHTML = `${header}<div class="tx-comm-empty">
        <div class="tx-comm-empty-icon">💶</div>
        <strong>Aucune commission pour l'instant</strong>
        <p>Clôturez une vente dans <strong>Affaires</strong> (« Clôturer la vente ») pour enregistrer la première commission ici.</p>
      </div>`;
      return;
    }

    const kpi = (label, value, accent) =>
      `<div class="tx-comm-kpi${accent ? ` tx-comm-kpi--${accent}` : ""}">
        <span class="tx-comm-kpi-label">${label}</span>
        <strong class="tx-comm-kpi-value">${value}</strong>
      </div>`;
    const kpis = `<div class="tx-comm-kpis">
      ${kpi("Total encaissé", fmtEuro(r.total_amount), "total")}
      ${kpi("Part agence", fmtEuro(r.agency_amount), "agency")}
      ${kpi("Part agents", fmtEuro(r.agent_amount), "agent")}
      ${kpi("Ventes clôturées", r.deals_count || 0)}
    </div>`;

    const agents = [...(r.by_agent || [])].sort((a, b) => (b.agent_amount || 0) - (a.agent_amount || 0));
    const topAmount = agents.reduce((m, a) => Math.max(m, a.agent_amount || 0), 0) || 1;
    const leaderboard = agents
      .map((a, i) => {
        const width = Math.max(4, Math.round(((a.agent_amount || 0) / topAmount) * 100));
        return `<li class="tx-comm-agent">
          <span class="tx-comm-agent-rank">${i + 1}</span>
          <span class="tx-comm-agent-avatar">${esc(commInitials(a.agent_name))}</span>
          <div class="tx-comm-agent-main">
            <div class="tx-comm-agent-line">
              <strong class="tx-comm-agent-name">${esc(a.agent_name || "—")}</strong>
              <span class="tx-comm-agent-amount">${fmtEuro(a.agent_amount)}</span>
            </div>
            <div class="tx-comm-agent-bar"><span style="width:${width}%"></span></div>
            <span class="tx-comm-agent-meta">${a.deals} vente${a.deals > 1 ? "s" : ""} conclue${a.deals > 1 ? "s" : ""}</span>
          </div>
        </li>`;
      })
      .join("");

    const rows = (r.commissions || [])
      .map(
        (c) => `<tr>
          <td>${commDate(c.created_at)}</td>
          <td><span class="tx-comm-cell-agent"><span class="tx-comm-agent-avatar tx-comm-agent-avatar--sm">${esc(commInitials(c.agent_name))}</span>${esc(c.agent_name || "—")}</span></td>
          <td class="num">${fmtEuro(c.total_amount)}</td>
          <td class="num">${fmtEuro(c.agency_amount)}</td>
          <td class="num"><strong>${fmtEuro(c.agent_amount)}</strong></td>
          <td class="num"><span class="tx-comm-pct">${Math.round(c.agent_pct || 0)} %</span></td>
        </tr>`,
      )
      .join("");

    root.innerHTML = `
      ${header}
      ${kpis}
      <section class="tx-comm-section">
        <h3 class="tx-comm-h">Classement des agents</h3>
        <ul class="tx-comm-leaderboard">${leaderboard}</ul>
      </section>
      <section class="tx-comm-section">
        <h3 class="tx-comm-h">Historique des commissions</h3>
        <div class="tx-comm-table-wrap">
          <table class="portal-table tx-comm-table">
            <thead><tr><th>Date</th><th>Agent</th><th class="num">Commission</th><th class="num">Part agence</th><th class="num">Part agent</th><th class="num">%</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>`;
  }

  window.renderTransactionsView = renderTransactionsView;
  window.renderPipelineView = renderPipelineView;
  window.renderCommissionsView = renderCommissionsView;
})();
