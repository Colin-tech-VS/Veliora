/* Rendu résultat estimation — aligné sur renderPriceEstimateResultHtml (CRM) */

window.VelioraPublicEstimator = (function () {
  const fmtEuro = (n) =>
    n == null || Number.isNaN(Number(n))
      ? "—"
      : `${Math.round(Number(n)).toLocaleString("fr-FR")} €`;

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderComparablesHtml(result) {
    const list = result.comparables;
    if (!list?.length) return "";
    const total = result.comparables_total || list.length;
    const rows = list
      .map((c) => {
        const dist =
          c.distance_m != null ? `${Number(c.distance_m).toLocaleString("fr-FR")} m` : "—";
        const d = c.date
          ? new Date(c.date).toLocaleDateString("fr-FR", { month: "short", year: "numeric" })
          : "—";
        return `<tr>
          <td>${escapeHtml(d)}</td>
          <td>${escapeHtml(c.address || "—")}${c.postcode ? ` <small>${escapeHtml(c.postcode)}</small>` : ""}</td>
          <td class="num">${c.surface != null ? `${c.surface} m²` : "—"}</td>
          <td class="num">${c.price != null ? fmtEuro(c.price) : "—"}</td>
          <td class="num">${c.price_m2 != null ? `${Number(c.price_m2).toLocaleString("fr-FR")} €` : "—"}</td>
          <td class="num">${escapeHtml(dist)}</td>
        </tr>`;
      })
      .join("");
    return `
      <details class="v-est-result-comparables" open>
        <summary>Ventes comparables DVF (${list.length}${total > list.length ? ` sur ${total}` : ""})</summary>
        <p class="v-est-result-comparables-hint">Actes récents retenus pour la médiane (type, surface proche, hors valeurs aberrantes).</p>
        <div class="v-est-result-comparables-scroll">
          <table>
            <thead><tr><th>Date</th><th>Adresse</th><th>Surf.</th><th>Prix</th><th>€/m²</th><th>Dist.</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </details>`;
  }

  function renderResultHtml(result) {
    if (!result?.ok) {
      return `<div class="v-est-result-error-wrap">
        <p class="v-est-result-error">${escapeHtml(result?.reason || result?.error || "Estimation indisponible")}</p>
        <button type="button" class="v-btn v-btn-secondary" data-est-retry>Corriger et réessayer</button>
      </div>`;
    }
    const confCls = result.confidence || "low";
    const comparables = renderComparablesHtml(result);
    const capNote =
      result.adjustments_capped && result.adjustments_raw_total_pct != null
        ? `<p class="v-est-result-cap">Ajustements plafonnés (brut ${result.adjustments_raw_total_pct > 0 ? "+" : ""}${result.adjustments_raw_total_pct} % → retenu ${result.adjustments_total_pct > 0 ? "+" : ""}${result.adjustments_total_pct} %).</p>`
        : "";
    const adj =
      result.adjustments?.length > 0
        ? `<ul class="v-est-result-adj">${result.adjustments
            .map(
              (a) =>
                `<li>${escapeHtml(a.label)} <span>${a.pct > 0 ? "+" : ""}${a.pct} %</span></li>`,
            )
            .join("")}</ul>${capNote}`
        : "";
    const method = (result.methodology || [])
      .map((line) => `<li>${escapeHtml(line)}</li>`)
      .join("");
    const commissionPct = result.commission_pct != null ? result.commission_pct : null;
    const faiBlock =
      result.estimate_fai != null
        ? `
        <div class="v-est-result-fai">
          <span class="v-est-result-fai-label">Prix de présentation FAI<small>honoraires ${commissionPct != null ? commissionPct : "—"} % inclus</small></span>
          <strong class="v-est-result-fai-total">${fmtEuro(result.estimate_fai)}</strong>
          <span class="v-est-result-fai-range">${fmtEuro(result.range_low_fai)} – ${fmtEuro(result.range_high_fai)}${result.commission_amount ? ` · dont ${fmtEuro(result.commission_amount)} d'honoraires` : ""}</span>
        </div>`
        : "";
    return `
      <div class="v-est-result-panel" data-confidence="${escapeHtml(confCls)}">
        <div class="v-est-result-hero">
          <span class="v-est-result-kicker">Estimation net vendeur</span>
          <strong class="v-est-result-total">${fmtEuro(result.estimate_total)}</strong>
          <span class="v-est-result-range">${fmtEuro(result.range_low)} – ${fmtEuro(result.range_high)}</span>
          <span class="v-est-result-hint">Valeur actée du bien (hors honoraires)</span>
        </div>
        ${faiBlock}
        <p class="v-est-result-meta">
          <span class="v-est-result-conf conf-${escapeHtml(confCls)}">Confiance ${escapeHtml(result.confidence_label || "")}</span>
          · ${result.sample_count || 0} ventes DVF · ${escapeHtml(result.reference_period || "")}
          · ${escapeHtml(result.commune || result.sector || "")}
        </p>
        <p class="v-est-result-m2">
          Base DVF <strong>${(result.median_m2 || 0).toLocaleString("fr-FR")} €/m²</strong>
          · retenu <strong>${fmtEuro(result.price_per_m2)}/m²</strong>
          · surface ${result.surface} m²
          ${result.dvf_surface_band ? ` · tranche ${escapeHtml(result.dvf_surface_band)}` : ""}
        </p>
        ${result.dvf_filter_detail ? `<p class="v-est-result-filter">${escapeHtml(result.dvf_filter_detail)}</p>` : ""}
        ${comparables}
        ${adj}
        ${method ? `<ol class="v-est-result-method">${method}</ol>` : ""}
        <p class="v-est-result-disclaimer">${escapeHtml(result.disclaimer || "Estimation indicative — non contractuelle.")}</p>
        <div class="v-est-result-actions">
          <button type="button" class="v-btn v-btn-primary" data-est-restart>Nouvelle estimation</button>
          <a href="/" class="v-btn v-btn-ghost">Retour à l'accueil</a>
        </div>
      </div>`;
  }

  function renderSellIntentHtml({ hasOwnerContact = false } = {}) {
    const ownerAttr = hasOwnerContact ? ' data-owner-known="1"' : "";
    const leadExtra = hasOwnerContact
      ? " Vos coordonnées sont déjà enregistrées — un clic suffit pour être contacté."
      : "";
    return `
      <section class="v-est-sell-intent"${ownerAttr} aria-labelledby="lp-sell-title">
        <h3 id="lp-sell-title">Souhaitez-vous vendre ce bien&nbsp;?</h3>
        <p class="v-est-sell-lead">Si oui, nous transmettons votre demande aux <strong>agences immobilières de votre secteur</strong> qui utilisent Veliora.${leadExtra}</p>
        <div class="v-est-sell-choices">
          <button type="button" class="v-btn v-btn-primary" data-sell-choice="yes">Oui, je souhaite être contacté</button>
          <button type="button" class="v-btn v-btn-secondary" data-sell-choice="no">Non, estimation seule</button>
        </div>
        <form id="lp-sell-contact-form" class="v-est-sell-contact" hidden novalidate>
          <p class="v-est-sell-contact-intro">Vos coordonnées pour qu’une agence vous rappelle.</p>
          <div class="v-est-row-2">
            <label class="v-est-input">
              <span>Prénom <abbr title="obligatoire">*</abbr></span>
              <input type="text" id="lp-sell-first" required minlength="2" autocomplete="given-name">
            </label>
            <label class="v-est-input">
              <span>Nom <abbr title="obligatoire">*</abbr></span>
              <input type="text" id="lp-sell-last" required minlength="2" autocomplete="family-name">
            </label>
          </div>
          <div class="v-est-row-2">
            <label class="v-est-input">
              <span>Téléphone</span>
              <input type="tel" id="lp-sell-phone" autocomplete="tel" placeholder="06 12 34 56 78">
            </label>
            <label class="v-est-input">
              <span>Email</span>
              <input type="email" id="lp-sell-email" autocomplete="email" placeholder="vous@email.fr">
            </label>
          </div>
          <p class="v-est-hint">Téléphone ou email requis.</p>
          <label class="v-est-consent">
            <input type="checkbox" id="lp-sell-consent">
            <span>J’accepte d’être contacté par une agence de mon secteur pour mon projet de vente. <a href="/confidentialite" target="_blank" rel="noopener">Confidentialité</a>.</span>
          </label>
          <button type="button" class="v-btn v-btn-primary v-btn-lg" id="lp-sell-confirm">Envoyer ma demande</button>
        </form>
        <p class="v-est-sell-status" id="lp-sell-status" aria-live="polite"></p>
      </section>`;
  }

  return { fmtEuro, escapeHtml, renderResultHtml, renderSellIntentHtml };
})();

