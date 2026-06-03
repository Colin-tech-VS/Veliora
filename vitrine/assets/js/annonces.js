/* Catalogue annonces public — consultation uniquement (publication via CRM agence) */

(function () {
  const grid = document.getElementById("listings-grid");

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

  function listingHref(l) {
    if (l.url) return l.url;
    if (l.slug) return `/annonces/${l.slug}`;
    return `/annonces/${l.id}`;
  }

  function cardHtml(l) {
    const href = escapeHtml(listingHref(l));
    const img = l.image_url
      ? `<img src="${escapeHtml(l.image_url)}" alt="" loading="lazy" decoding="async">`
      : `<div class="v-ann-card-placeholder" aria-hidden="true"></div>`;
    const who = escapeHtml(l.agency_name || "Agence immobilière");
    return `
      <article class="v-ann-card">
        <a href="${href}" class="v-ann-card-link">
          <div class="v-ann-card-img">${img}</div>
          <div class="v-ann-card-body">
            <span class="v-ann-card-badge">${who}</span>
            <h3>${escapeHtml(l.title)}</h3>
            <p class="v-ann-card-loc">${escapeHtml(l.city || "")}${l.postcode ? ` (${escapeHtml(l.postcode)})` : ""}</p>
            <p class="v-ann-card-price">${fmtEuro(l.price)} · ${l.surface ? `${l.surface} m²` : ""}</p>
            <p class="v-ann-card-meta">${escapeHtml(l.transaction_type || "")} · ${escapeHtml(l.property_type || "")}</p>
          </div>
        </a>
      </article>`;
  }

  async function loadListings() {
    if (!grid) return;
    const city = document.getElementById("filter-city")?.value?.trim() || "";
    const tx = document.getElementById("filter-tx")?.value || "";
    const params = new URLSearchParams();
    if (city) params.set("city", city);
    if (tx) params.set("transaction_type", tx);
    grid.innerHTML = `<p class="v-annonces-loading">Chargement…</p>`;
    try {
      const res = await fetch(`/api/public/portal/listings?${params}`);
      const data = await res.json().catch(() => ({}));
      const list = (data.listings || []).filter(
        (l) => l.publisher_type === "agency" || l.agency_id || l.agency_name
      );
      if (!list.length) {
        grid.innerHTML = `<p class="v-annonces-empty">Aucune annonce agence pour ces critères. <a href="/estimation" class="v-btn v-btn-primary">Estimer mon bien</a></p>`;
        return;
      }
      grid.innerHTML = `<div class="v-ann-grid">${list.map(cardHtml).join("")}</div>`;
    } catch {
      grid.innerHTML = `<p class="v-annonces-error">Connexion impossible — lancez Veliora avec demarrer.bat.</p>`;
    }
  }

  document.getElementById("btn-filter")?.addEventListener("click", loadListings);

  if (location.hash === "#publier") {
    VelioraUi?.toast(
      "La publication est réservée aux agences (CRM → Portail annonces).",
      "info"
    );
    history.replaceState(null, "", location.pathname + location.search);
  }

  loadListings();
})();
