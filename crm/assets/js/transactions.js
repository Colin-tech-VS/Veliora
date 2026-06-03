/* Transactions — cockpit du cycle de vie complet d'une affaire.
   Pensé pour un agent NON technique : une carte par affaire, une étape claire,
   UN bouton d'action principal. Tout est piloté par /api/transactions (l'étape
   est calculée côté serveur à partir des faits réels). */

(function () {
  const fmtEuro = (n) =>
    n == null || n === "" || Number.isNaN(Number(n))
      ? "—"
      : `${Math.round(Number(n)).toLocaleString("fr-FR")} €`;

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function escAttr(s) { return esc(s).replace(/'/g, "&#39;"); }

  let CACHE = { deals: [], stages: [], agents: [] };
  let SCOPE = "all"; // "all" (Transactions) | "mine" (Pipeline agent connecté)

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

  function stepperHtml(deal) {
    const pct = Math.round(((deal.stage_index + 1) / deal.stage_total) * 100);
    return `
      <div class="tx-stepper" title="Étape ${deal.stage_index + 1} / ${deal.stage_total}">
        <div class="tx-stepper-bar"><span style="width:${pct}%"></span></div>
        <div class="tx-stepper-label">${esc(deal.stage_label)} · ${deal.stage_index + 1}/${deal.stage_total}</div>
      </div>`;
  }

  function primaryActionHtml(deal) {
    const id = deal.lead_id;
    if (deal.stage === "prospect") {
      return `<div class="tx-assign">
        <select class="tx-agent-select" data-tx-agent="${id}" aria-label="Agent">
          <option value="">— choisir un agent —</option>${agentOptions()}
        </select>
        <button class="btn btn-primary btn-sm" data-tx-action="assign" data-id="${id}">Prendre en charge</button>
      </div>`;
    }
    if (deal.stage === "mandat_cree") {
      return `<div class="tx-validate">
        <button class="btn btn-secondary btn-sm" data-tx-action="validate-owner" data-id="${id}" data-mid="${escAttr(deal.mandate_id)}">✓ Vendeur</button>
        <button class="btn btn-secondary btn-sm" data-tx-action="validate-agent" data-id="${id}" data-mid="${escAttr(deal.mandate_id)}">✓ Agent</button>
      </div>`;
    }
    const map = {
      pris_en_charge: ["call", "Appeler & estimer"],
      contacte: ["mandate", "Créer le mandat"],
      mandat_valide: ["publish", "Publier l'annonce"],
      publie: ["buyer", "Rapprocher un acquéreur"],
      acquereur: ["visit", "Planifier la visite"],
      visite: ["buyer_dossier", "Préparer le dossier acquéreur"],
      dossier_acquereur: ["compromis", "Compromis (notaire)"],
      compromis: ["finalize", "Finaliser la vente"],
      vendu: ["done", "Vendu ✓"],
    };
    const a = map[deal.stage];
    if (!a) return "";
    if (a[0] === "done")
      return `<span class="tx-done">Affaire conclue ✓</span>`;
    return `<button class="btn btn-primary btn-sm" data-tx-action="${a[0]}" data-id="${id}">${esc(a[1])}</button>`;
  }

  function dealCardHtml(deal) {
    const tx = deal.transaction_type === "location" ? "Location" : "Vente";
    return `
      <article class="tx-card tx-stage-${esc(deal.stage)}" data-id="${deal.lead_id}">
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
        <div class="tx-card-meta">
          <span class="tx-agent-badge">${deal.agent_name ? "👤 " + esc(deal.agent_name) : "Non assigné"}</span>
          ${deal.mandate_validated ? '<span class="tx-badge tx-badge-ok">Mandat validé</span>' : ""}
        </div>
        <div class="tx-card-actions">
          ${primaryActionHtml(deal)}
          <button class="btn btn-ghost btn-sm" data-tx-action="dossier" data-id="${deal.lead_id}">📂 Dossier</button>
        </div>
      </article>`;
  }

  function renderInto(rootId, opts = {}) {
    const root = document.getElementById(rootId);
    if (!root) return;
    const deals = opts.scope === "mine"
      ? CACHE.deals
      : CACHE.deals;
    const header = opts.scope === "mine"
      ? `<p class="tx-intro">Vos affaires en cours — de la prise en charge à la vente. Seules <strong>vos</strong> annonces apparaissent ici.</p>`
      : `<p class="tx-intro">Pilotez chaque affaire pas à pas : prise en charge → mandat → publication → acquéreur → vente. L'étape se met à jour automatiquement.</p>`;
    if (!deals.length) {
      root.innerHTML = `${header}<div class="tx-empty">Aucune affaire en cours. Depuis <strong>Prospects</strong>, prenez une annonce en charge pour démarrer.</div>`;
      return;
    }
    const legend = CACHE.stages
      .map((s) => `<span class="tx-legend-chip"><b>${s.count}</b> ${esc(s.label)}</span>`)
      .join("");
    root.innerHTML = `
      ${header}
      <div class="tx-legend">${legend}</div>
      <div class="tx-grid">${deals.map(dealCardHtml).join("")}</div>`;
    bindActions(root);
  }

  function bindActions(root) {
    root.querySelectorAll("[data-tx-action]").forEach((btn) => {
      btn.addEventListener("click", () => handleAction(btn));
    });
  }

  async function handleAction(btn) {
    const action = btn.dataset.txAction;
    const id = Number(btn.dataset.id);
    try {
      if (action === "assign") {
        const sel = btn.closest(".tx-assign")?.querySelector(".tx-agent-select");
        const agentId = sel?.value;
        if (!agentId) return showToast("Choisissez un agent", "error");
        await api(`/leads/${id}/assign`, { method: "POST", body: JSON.stringify({ agent_id: agentId }) });
        showToast("Annonce prise en charge", "success");
      } else if (action === "call") {
        await api(`/leads/${id}/outcome`, { method: "POST", body: JSON.stringify({ outcome_type: "call" }) });
        showToast("Appel enregistré — créez le mandat quand le vendeur est convaincu", "success");
      } else if (action === "mandate") {
        await createMandateForLead(id);
      } else if (action === "validate-owner") {
        await api(`/mandates/${btn.dataset.mid}/validate`, { method: "POST", body: JSON.stringify({ party: "owner" }) });
        showToast("Validation propriétaire enregistrée", "success");
      } else if (action === "validate-agent") {
        const r = await api(`/mandates/${btn.dataset.mid}/validate`, { method: "POST", body: JSON.stringify({ party: "agent" }) });
        showToast(r.fully_validated ? "Mandat validé — dossier créé, publication possible" : "Validation agent enregistrée", "success");
      } else if (action === "publish") {
        const r = await api(`/portal/listings/from-lead`, { method: "POST", body: JSON.stringify({ lead_id: id }) });
        showToast(r.ok ? "Annonce publiée sur le portail" : (r.error || "Publication impossible"), r.ok ? "success" : "error");
      } else if (action === "buyer") {
        await pickBuyer(id);
      } else if (action === "visit") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "visit" }) });
        showToast("Visite planifiée", "success");
      } else if (action === "buyer_dossier") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "buyer_dossier" }) });
        showToast("Dossier acquéreur préparé", "success");
      } else if (action === "compromis") {
        await api(`/transactions/${id}/milestone`, { method: "POST", body: JSON.stringify({ kind: "compromis" }) });
        showToast("Compromis enregistré", "success");
      } else if (action === "finalize") {
        await finalizeDeal(id);
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
    showToast(`Mandat de ${type} créé — validez-le (vendeur + agent)`, "success");
  }

  async function pickBuyer(leadId) {
    let clients = [];
    try {
      const r = await api(`/clients`);
      clients = (r.clients || r || []).filter((c) => (c.segment || "") !== "");
    } catch { /* ignore */ }
    const opts = clients.length
      ? clients.map((c) => `<option value="${escAttr(c.id)}">${esc(c.full_name || c.email || "Client")} · ${esc(c.segment)}</option>`).join("")
      : "";
    const body = clients.length
      ? `<label class="form-field"><span>Acquéreur / locataire intéressé</span>
          <select id="tx-buyer-select">${opts}</select></label>`
      : `<p>Aucun acheteur/locataire enregistré. Ajoutez-en un dans l'onglet <strong>Acheteurs / Locataires</strong>.</p>`;
    const ok = await modal("Rapprocher un acquéreur", body, clients.length);
    if (!ok) return;
    const clientId = document.getElementById("tx-buyer-select")?.value;
    await api(`/transactions/${leadId}/buyer`, { method: "POST", body: JSON.stringify({ client_id: clientId }) });
    showToast("Acquéreur rapproché", "success");
  }

  async function finalizeDeal(leadId) {
    const body = `
      <label class="form-field"><span>Commission totale encaissée (€)</span>
        <input type="number" id="tx-commission" min="1" step="100" placeholder="9000"></label>
      <label class="form-field"><span>Part de l'agent (%)</span>
        <input type="number" id="tx-agent-pct" min="0" max="100" step="1" value="30"></label>
      <p class="form-hint">La commission est répartie entre l'agence et l'agent qui a suivi l'affaire.</p>`;
    const ok = await modal("Finaliser la vente", body, true, "Enregistrer la vente");
    if (!ok) return;
    const total = parseFloat(document.getElementById("tx-commission")?.value || "0");
    const pct = parseFloat(document.getElementById("tx-agent-pct")?.value || "30");
    if (!total || total <= 0) return showToast("Montant de commission requis", "error");
    const r = await api(`/transactions/${leadId}/finalize`, {
      method: "POST",
      body: JSON.stringify({ total_amount: total, agent_pct: pct }),
    });
    const c = r.commission || {};
    showToast(`Vente conclue · agence ${fmtEuro(c.agency_amount)} · agent ${fmtEuro(c.agent_amount)}`, "success");
  }

  // ── Dossier dynamique ─────────────────────────────────────────────────
  async function openDossier(leadId) {
    let d;
    try {
      d = await api(`/transactions/${leadId}/dossier`);
    } catch (err) {
      return showToast(err.message || "Dossier indisponible", "error");
    }
    const p = d.property || {}, s = d.seller || {}, b = d.buyer || {}, ag = d.agent || {}, agency = d.agency || {}, m = d.mandate || {};
    const body = `
      <div class="tx-dossier">
        <div class="tx-dossier-stage">${esc(d.transaction?.stage_label || "")} — étape ${(d.transaction?.stage_index ?? 0) + 1}/${d.transaction?.stage_total ?? 11}</div>
        <fieldset class="tx-dossier-block"><legend>🏠 Le bien <small>(modifiable)</small></legend>
          <div class="portal-form-grid">
            <label class="form-field"><span>Titre</span><input id="dz-title" value="${escAttr(p.title)}"></label>
            <label class="form-field"><span>Type</span><input id="dz-type" value="${escAttr(p.type)}"></label>
            <label class="form-field"><span>Surface m²</span><input id="dz-surface" type="number" value="${escAttr(p.surface ?? "")}"></label>
            <label class="form-field"><span>Prix €</span><input id="dz-price" type="number" value="${escAttr(p.price ?? "")}"></label>
            <label class="form-field form-field-wide"><span>Adresse</span><input id="dz-address" value="${escAttr(p.address)}"></label>
            <label class="form-field"><span>Code postal</span><input id="dz-postcode" value="${escAttr(p.postcode)}"></label>
            <label class="form-field"><span>Ville</span><input id="dz-city" value="${escAttr(p.city)}"></label>
          </div>
        </fieldset>
        <div class="tx-dossier-cols">
          <fieldset class="tx-dossier-block"><legend>👤 Vendeur</legend>
            <p>${esc([s.first_name, s.last_name].filter(Boolean).join(" ") || "—")}</p>
            <p>${esc(s.phone || "—")} · ${esc(s.email || "—")}</p>
            ${s.email ? `<button class="btn btn-secondary btn-sm" data-dz-email="seller" data-id="${leadId}">✉ Envoyer au vendeur</button>` : ""}
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>🔑 Acquéreur / locataire</legend>
            <p>${b && b.full_name ? esc(b.full_name) : "Aucun rapproché"}</p>
            <p>${esc((b && b.email) || "")}</p>
            ${b && b.email ? `<button class="btn btn-secondary btn-sm" data-dz-email="buyer" data-id="${leadId}">✉ Envoyer à l'acquéreur</button>` : ""}
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>🧑‍💼 Agent & agence</legend>
            <p>${esc(ag.agent_name || "Non assigné")}</p>
            <p>${esc(agency.name || "")}</p>
          </fieldset>
          <fieldset class="tx-dossier-block"><legend>📄 Mandat</legend>
            <p>${m.id ? `${esc(m.type || "")} · ${esc(m.status || "")}` : "Pas encore créé"}</p>
            <p>Vendeur ${m.owner_validated_at ? "✓" : "—"} · Agent ${m.agent_validated_at ? "✓" : "—"}</p>
          </fieldset>
        </div>
      </div>`;
    const ok = await modal("Dossier de l'affaire", body, true, "Enregistrer les modifications", (overlay) => {
      overlay.querySelectorAll("[data-dz-email]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try {
            const r = await api(`/transactions/${leadId}/email`, {
              method: "POST",
              body: JSON.stringify({ to_role: btn.dataset.dzEmail }),
            });
            showToast(r.email_configured ? `Email envoyé à ${r.to}` : "Email non configuré (SMTP) — voir réglages", r.email_configured ? "success" : "error");
          } catch (e) { showToast(e.message, "error"); }
        });
      });
    });
    if (!ok) return;
    // Sauvegarde des champs éditables sur le lead (cohérence partout).
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
    } catch (e) { showToast(e.message, "error"); }
  }

  // ── Modal utilitaire ──────────────────────────────────────────────────
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
        ? [{ label: "Annuler", v: false }, { label: confirmLabel, v: true, primary: true }]
        : [{ label: "Fermer", v: false, primary: true }];
      buttons.forEach((b) => {
        const el = document.createElement("button");
        el.type = "button";
        el.className = `btn ${b.primary ? "btn-primary" : "btn-secondary"}`;
        el.textContent = b.label;
        el.addEventListener("click", () => { overlay.classList.remove("open"); resolve(b.v); });
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
    const root = document.getElementById("pipeline-board");
    if (root) root.innerHTML = `<p class="tx-loading">Chargement de votre pipeline…</p>`;
    try {
      await fetchTransactions("mine");
      renderInto("pipeline-board", { scope: "mine" });
    } catch (err) {
      if (root) root.innerHTML = `<p class="tx-error">${esc(err.message)}</p>`;
    }
  }

  async function renderCommissionsView() {
    const root = document.getElementById("commissions-root");
    if (!root) return;
    root.innerHTML = `<p class="tx-loading">Chargement du suivi…</p>`;
    try {
      const r = await api(`/commissions`);
      const byAgent = (r.by_agent || [])
        .map((a) => `<tr><td>${esc(a.agent_name)}</td><td class="num">${a.deals}</td><td class="num">${fmtEuro(a.agent_amount)}</td></tr>`)
        .join("");
      const rows = (r.commissions || [])
        .map((c) => `<tr><td>${esc(c.agent_name || "—")}</td><td class="num">${fmtEuro(c.total_amount)}</td><td class="num">${fmtEuro(c.agency_amount)}</td><td class="num">${fmtEuro(c.agent_amount)}</td><td>${esc((c.created_at || "").slice(0, 10))}</td></tr>`)
        .join("");
      root.innerHTML = `
        <div class="tx-comm-summary">
          <div class="tx-comm-kpi"><span>Total encaissé</span><strong>${fmtEuro(r.total_amount)}</strong></div>
          <div class="tx-comm-kpi"><span>Part agence</span><strong>${fmtEuro(r.agency_amount)}</strong></div>
          <div class="tx-comm-kpi"><span>Part agents</span><strong>${fmtEuro(r.agent_amount)}</strong></div>
          <div class="tx-comm-kpi"><span>Affaires conclues</span><strong>${r.deals_count || 0}</strong></div>
        </div>
        <h3 class="tx-comm-h">Par agent</h3>
        <table class="portal-table"><thead><tr><th>Agent</th><th>Ventes</th><th>Commission agent</th></tr></thead>
          <tbody>${byAgent || '<tr><td colspan="3">Aucune vente conclue.</td></tr>'}</tbody></table>
        <h3 class="tx-comm-h">Détail des ventes</h3>
        <table class="portal-table"><thead><tr><th>Agent</th><th>Total</th><th>Agence</th><th>Agent</th><th>Date</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5">Aucune commission enregistrée.</td></tr>'}</tbody></table>`;
    } catch (err) {
      root.innerHTML = `<p class="tx-error">${esc(err.message)}</p>`;
    }
  }

  window.renderTransactionsView = renderTransactionsView;
  window.renderPipelineView = renderPipelineView;
  window.renderCommissionsView = renderCommissionsView;
})();
