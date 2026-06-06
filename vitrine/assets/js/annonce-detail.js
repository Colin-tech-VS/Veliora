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

/* Slider galerie photos — navigation flèches / points / clavier / swipe */
(function () {
  function initSlider(root) {
    const track = root.querySelector(".v-ann-slides");
    const slides = Array.from(root.querySelectorAll(".v-ann-slide"));
    if (!track || slides.length <= 1) return;
    const dots = Array.from(root.querySelectorAll(".v-ann-dot"));
    const counter = root.querySelector(".v-ann-counter-cur");
    let index = 0;

    function go(to) {
      index = (to + slides.length) % slides.length;
      track.style.transform = `translateX(-${index * 100}%)`;
      dots.forEach((d, i) => d.classList.toggle("is-active", i === index));
      if (counter) counter.textContent = String(index + 1);
    }

    root.querySelector(".v-ann-prev")?.addEventListener("click", () => go(index - 1));
    root.querySelector(".v-ann-next")?.addEventListener("click", () => go(index + 1));
    dots.forEach((d) =>
      d.addEventListener("click", () => go(Number(d.dataset.slideTo) || 0)),
    );

    root.tabIndex = 0;
    root.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") go(index - 1);
      else if (e.key === "ArrowRight") go(index + 1);
    });

    let startX = null;
    root.addEventListener(
      "touchstart",
      (e) => {
        startX = e.touches[0].clientX;
      },
      { passive: true },
    );
    root.addEventListener(
      "touchend",
      (e) => {
        if (startX == null) return;
        const dx = e.changedTouches[0].clientX - startX;
        if (Math.abs(dx) > 40) go(dx < 0 ? index + 1 : index - 1);
        startX = null;
      },
      { passive: true },
    );

    go(0);
  }

  document.querySelectorAll("[data-slider]").forEach(initSlider);
})();
