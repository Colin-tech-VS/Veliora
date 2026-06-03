/* Portail annonces — publication agence via CRM */

(function () {
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

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  async function portalApi(path, opts = {}) {
    return api(path, opts);
  }

  function statusBadge(status) {
    const labels = {
      published: "En ligne",
      draft: "Brouillon",
      pending: "En attente",
      archived: "Archivé",
    };
    return `<span class="portal-status portal-status--${escapeHtml(status)}">${escapeHtml(labels[status] || status)}</span>`;
  }

  function listingFormHtml(data = {}, idPrefix = "portal") {
    const d = data || {};
    return `
      <form id="${idPrefix}-form" class="portal-form" onsubmit="return false">
        <div class="portal-form-grid">
          <label class="form-field"><span>Titre *</span>
            <input type="text" id="${idPrefix}-title" required minlength="5" value="${escapeAttr(d.title || "")}"></label>
          <label class="form-field"><span>Type</span>
            <select id="${idPrefix}-property-type">
              <option value="appartement">Appartement</option>
              <option value="maison">Maison</option>
              <option value="studio">Studio</option>
              <option value="terrain">Terrain</option>
            </select></label>
          <label class="form-field"><span>Transaction</span>
            <select id="${idPrefix}-transaction">
              <option value="vente">Vente</option>
              <option value="location">Location</option>
            </select></label>
          <label class="form-field"><span>Prix *</span>
            <input type="number" id="${idPrefix}-price" required min="1" value="${d.price != null ? d.price : ""}"></label>
          <label class="form-field"><span>Surface m² *</span>
            <input type="number" id="${idPrefix}-surface" required min="1" step="0.1" value="${d.surface != null ? d.surface : ""}"></label>
          <label class="form-field"><span>Pièces</span>
            <input type="number" id="${idPrefix}-rooms" min="1" max="20" value="${d.rooms != null ? d.rooms : ""}"></label>
          <label class="form-field"><span>Ville *</span>
            <input type="text" id="${idPrefix}-city" required value="${escapeAttr(d.city || "")}"></label>
          <label class="form-field"><span>Code postal</span>
            <input type="text" id="${idPrefix}-postcode" maxlength="5" value="${escapeAttr(d.postcode || "")}"></label>
          <label class="form-field form-field-wide"><span>Adresse</span>
            <input type="text" id="${idPrefix}-address" value="${escapeAttr(d.address || "")}"></label>
          <label class="form-field form-field-wide"><span>Description</span>
            <textarea id="${idPrefix}-description" rows="4">${escapeHtml(d.description || "")}</textarea></label>
          <label class="form-field form-field-wide"><span>URL photo (optionnel)</span>
            <input type="url" id="${idPrefix}-image" value="${escapeAttr(d.image_url || "")}" placeholder="https://…"></label>
          <label class="form-field"><span>Statut</span>
            <select id="${idPrefix}-status">
              <option value="published">Publiée en ligne</option>
              <option value="draft">Brouillon</option>
              <option value="archived">Archivée</option>
            </select></label>
        </div>
      </form>`;
  }

  function collectForm(idPrefix) {
    const val = (id) => document.getElementById(`${idPrefix}-${id}`)?.value?.trim() ?? "";
    return {
      title: val("title"),
      property_type: val("property-type"),
      transaction_type: val("transaction"),
      price: parseInt(val("price"), 10) || null,
      surface: parseFloat(val("surface")) || null,
      rooms: val("rooms") || null,
      city: val("city"),
      postcode: val("postcode"),
      address: val("address"),
      description: document.getElementById(`${idPrefix}-description`)?.value?.trim() || "",
      image_url: val("image"),
      status: val("status") || "published",
    };
  }

  function openPortalModal(title, bodyHtml, buttons, onReady) {
    let overlay = document.getElementById("portal-modal-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.id = "portal-modal-overlay";
      overlay.className = "modal-overlay";
      overlay.innerHTML = `
        <div class="modal-card modal-card-wide" role="dialog" aria-modal="true">
          <button type="button" class="modal-close" data-portal-modal-close aria-label="Fermer">×</button>
          <h2 id="portal-modal-title"></h2>
          <div id="portal-modal-body"></div>
          <div id="portal-modal-actions" class="modal-actions"></div>
        </div>`;
      document.body.appendChild(overlay);
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay || e.target.closest("[data-portal-modal-close]")) {
          overlay.classList.remove("open");
        }
      });
    }
    overlay.querySelector("#portal-modal-title").textContent = title;
    overlay.querySelector("#portal-modal-body").innerHTML = bodyHtml;
    if (typeof onReady === "function") onReady();
    const actions = overlay.querySelector("#portal-modal-actions");
    actions.innerHTML = "";
    return new Promise((resolve) => {
      buttons.forEach((b) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `btn ${b.primary ? "btn-primary" : "btn-secondary"}`;
        btn.textContent = b.label;
        btn.addEventListener("click", () => {
          overlay.classList.remove("open");
          resolve(b.value);
        });
        actions.appendChild(btn);
      });
      overlay.classList.add("open");
    });
  }

  async function confirmPortal(message) {
    const ok = await openPortalModal("Confirmer", `<p>${escapeHtml(message)}</p>`, [
      { label: "Annuler", value: false },
      { label: "Confirmer", value: true, primary: true },
    ]);
    return ok === true;
  }

  async function showEditor(listing = null) {
    const isEdit = !!listing?.id;
    const prefix = "portal";
    const body = listingFormHtml(listing, prefix);
    const ok = await openPortalModal(
      isEdit ? "Modifier l'annonce" : "Publier une annonce",
      body,
      [
        { label: "Annuler", value: false },
        { label: isEdit ? "Enregistrer" : "Publier", value: true, primary: true },
      ],
      () => {
        if (!listing) return;
        const pt = document.getElementById(`${prefix}-property-type`);
        const tx = document.getElementById(`${prefix}-transaction`);
        const st = document.getElementById(`${prefix}-status`);
        if (pt) pt.value = listing.property_type || "appartement";
        if (tx) tx.value = listing.transaction_type || "vente";
        if (st) st.value = listing.status || "published";
      },
    );
    if (!ok) return;
    const payload = collectForm(prefix);
    try {
      if (isEdit) {
        await portalApi(`/portal/listings/${listing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        showToast("Annonce mise à jour", "success");
      } else {
        await portalApi("/portal/listings", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("Annonce publiée sur le portail", "success");
      }
      renderPortalView();
    } catch (err) {
      showToast(err.message, "error");
    }
  }

  async function showInquiries(listing) {
    const res = await portalApi(`/portal/listings/${listing.id}/inquiries`);
    const items = res.inquiries || [];
    const rows = items.length
      ? items
          .map((q) => {
            const kind =
              q.kind === "info_request" ? "Demande d'info" : "Contact agence";
            const when = (q.created_at || "").slice(0, 16).replace("T", " ");
            return `<li class="portal-inquiry-item">
              <strong>${escapeHtml(kind)}</strong> — ${escapeHtml(q.name || "")}
              <span class="portal-inquiry-meta">${escapeHtml(when)}</span>
              ${q.phone ? `<br>Tél. ${escapeHtml(q.phone)}` : ""}
              ${q.email ? `<br>${escapeHtml(q.email)}` : ""}
              ${q.message ? `<p>${escapeHtml(q.message)}</p>` : ""}
            </li>`;
          })
          .join("")
      : "<p>Aucune demande pour cette annonce en ligne.</p>";
    const pub =
      listing.public_url || (listing.public_slug ? `/annonces/${listing.public_slug}` : "");
    const pubLink = pub
      ? `<p><a href="${escapeAttr(pub)}" target="_blank" rel="noopener">Voir la fiche publique</a></p>`
      : "";
    await openPortalModal(
      `Demandes — ${listing.title || "Annonce"}`,
      `${pubLink}<ul class="portal-inquiry-list">${rows}</ul>`,
      [{ label: "Fermer", value: true, primary: true }],
    );
  }

  function renderListingsTable(listings) {
    if (!listings.length) {
      return `<p class="portal-empty">Aucune annonce. Publiez la première — elle apparaîtra sur <a href="/annonces" target="_blank" rel="noopener">veliora.fr/annonces</a>.</p>`;
    }
    const rows = listings
      .map((l) => {
        const unread = Number(l.inquiry_unread_count || 0);
        const badge = unread
          ? `<span class="portal-inquiry-badge" title="Nouvelles demandes">${unread}</span>`
          : "";
        const pub =
          l.public_url || (l.public_slug ? `/annonces/${l.public_slug}` : "");
        const pubBtn = pub
          ? `<a href="${escapeAttr(pub)}" class="btn btn-ghost btn-sm" target="_blank" rel="noopener">Fiche</a>`
          : "";
        return `<tr data-id="${escapeHtml(l.id)}">
          <td>${statusBadge(l.status)}</td>
          <td><strong>${escapeHtml(l.title)}</strong><br><small>${escapeHtml(l.city || "")}</small></td>
          <td>${escapeHtml(l.transaction_type || "")} · ${escapeHtml(l.property_type || "")}</td>
          <td class="num">${fmtEuro(l.price)}</td>
          <td class="num">${l.surface ? `${l.surface} m²` : "—"}</td>
          <td class="portal-actions">
            ${pubBtn}
            <button type="button" class="btn btn-ghost btn-sm" data-portal-inquiries="${escapeHtml(l.id)}">Demandes${badge}</button>
            <button type="button" class="btn btn-ghost btn-sm" data-portal-edit="${escapeHtml(l.id)}">Modifier</button>
            <button type="button" class="btn btn-ghost btn-sm" data-portal-archive="${escapeHtml(l.id)}">Archiver</button>
            <button type="button" class="btn btn-ghost btn-sm" data-portal-delete="${escapeHtml(l.id)}">Supprimer</button>
          </td>
        </tr>`;
      })
      .join("");
    return `
      <div class="portal-table-wrap">
        <table class="portal-table">
          <thead><tr><th>Statut</th><th>Annonce</th><th>Type</th><th>Prix</th><th>Surf.</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  async function renderPortalView() {
    const root = document.getElementById("portal-root");
    if (!root) return;
    root.innerHTML = `<p class="portal-loading">Chargement du portail…</p>`;
    try {
      const res = await portalApi("/portal/listings");
      const listings = await Promise.all(
        (res.listings || []).map(async (l) => {
          if (l.status !== "published") return l;
          try {
            const d = await portalApi(`/portal/listings/${l.id}`);
            return { ...l, ...(d.listing || {}) };
          } catch {
            return l;
          }
        }),
      );
      root.innerHTML = `
        <div class="portal-view">
          <div class="portal-head">
            <p class="portal-intro">Publiez vos mandats sur le <strong>catalogue public Veliora</strong> — visible sur <a href="/annonces" target="_blank" rel="noopener">/annonces</a>. Réservé aux agences connectées (les particuliers n’ont accès qu’à l’<a href="/estimation" target="_blank" rel="noopener">estimation gratuite</a>).</p>
            <button type="button" class="btn btn-primary" id="portal-new-btn">+ Nouvelle annonce</button>
          </div>
          ${renderListingsTable(listings)}
        </div>`;
      root.querySelector("#portal-new-btn")?.addEventListener("click", () => showEditor());
      root.querySelectorAll("[data-portal-inquiries]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.portalInquiries;
          const item = listings.find((l) => l.id === id);
          if (item) showInquiries(item);
        });
      });
      root.querySelectorAll("[data-portal-edit]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.dataset.portalEdit;
          const item = listings.find((l) => l.id === id);
          if (item) showEditor(item);
        });
      });
      root.querySelectorAll("[data-portal-archive]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.portalArchive;
          if (!(await confirmPortal("Archiver cette annonce ?"))) return;
          try {
            await portalApi(`/portal/listings/${id}`, {
              method: "PATCH",
              body: JSON.stringify({ status: "archived" }),
            });
            showToast("Annonce archivée", "success");
            renderPortalView();
          } catch (e) {
            showToast(e.message, "error");
          }
        });
      });
      root.querySelectorAll("[data-portal-delete]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.dataset.portalDelete;
          if (!(await confirmPortal("Supprimer définitivement cette annonce ?"))) return;
          try {
            await portalApi(`/portal/listings/${id}`, { method: "DELETE" });
            showToast("Annonce supprimée", "success");
            renderPortalView();
          } catch (e) {
            showToast(e.message, "error");
          }
        });
      });
    } catch (err) {
      root.innerHTML = `<p class="portal-error">${escapeHtml(err.message)}</p>`;
    }
  }

  window.renderPortalView = renderPortalView;
})();
