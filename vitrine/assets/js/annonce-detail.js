/* Fiche annonce publique — CTA contact / demande d'info */

(function () {
  const listingId = document.body?.dataset?.listingId;
  const modal = document.getElementById("inquiry-modal");
  const form = document.getElementById("inquiry-form");
  const kindInput = document.getElementById("inquiry-kind");
  const titleEl = document.getElementById("inquiry-modal-title");
  const leadEl = document.getElementById("inquiry-modal-lead");
  const msgWrap = document.getElementById("inquiry-message-wrap");
  const msgLabel = document.getElementById("inquiry-message-label");
  const msgField = form?.querySelector('[name="message"]');

  if (!listingId || !modal || !form) return;

  function openModal(kind) {
    const isInfo = kind === "info_request";
    if (kindInput) kindInput.value = kind;
    if (titleEl) {
      titleEl.textContent = isInfo ? "Demande d'information" : "Contacter l'agence";
    }
    if (leadEl) {
      leadEl.textContent = isInfo
        ? "Décrivez les informations souhaitées (dossier, charges, disponibilité visite…)."
        : "L'agence vous recontacte pour organiser un échange ou une visite.";
    }
    if (msgLabel) msgLabel.textContent = isInfo ? "Votre demande *" : "Message (optionnel)";
    if (msgField) {
      msgField.required = isInfo;
      msgField.placeholder = isInfo
        ? "Ex. : disponibilités pour une visite, honoraires, état du bien…"
        : "Précisez votre projet (visite, financement, délais…)";
    }
    if (msgWrap) msgWrap.classList.toggle("v-inquiry-field-required", isInfo);
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("v-inquiry-open");
    form.querySelector('[name="name"]')?.focus();
  }

  function closeModal() {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("v-inquiry-open");
  }

  document.getElementById("cta-contact")?.addEventListener("click", () => openModal("contact_agency"));
  document.getElementById("cta-info")?.addEventListener("click", () => openModal("info_request"));
  modal.querySelectorAll("[data-inquiry-close]").forEach((el) => {
    el.addEventListener("click", closeModal);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = {
      kind: fd.get("kind") || "contact_agency",
      name: String(fd.get("name") || "").trim(),
      email: String(fd.get("email") || "").trim(),
      phone: String(fd.get("phone") || "").trim(),
      message: String(fd.get("message") || "").trim(),
    };
    const btn = form.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      const res = await fetch(`/api/public/portal/listings/${listingId}/inquiry`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        VelioraUi?.toast(data.error || "Envoi impossible", "error");
        return;
      }
      VelioraUi?.toast(data.message || "Demande envoyée", "success");
      form.reset();
      closeModal();
    } catch {
      VelioraUi?.toast("Connexion impossible — réessayez plus tard.", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  });
})();
