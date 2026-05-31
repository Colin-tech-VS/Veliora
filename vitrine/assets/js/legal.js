/* Veliora — Injection des infos légales depuis /api/public/config */

(function () {
  function setText(sel, val, fallback) {
    document.querySelectorAll(sel).forEach((el) => {
      el.textContent = val || fallback || "—";
    });
  }

  function setHtml(sel, html) {
    document.querySelectorAll(sel).forEach((el) => {
      el.innerHTML = html;
    });
  }

  fetch("/api/public/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (!cfg) return;
      const le = cfg.legal_entity || {};
      const name = le.company_name || cfg.company_name || "Veliora";
      const siret = le.siret || "";
      const addr = le.address || "";
      const hosting = le.hosting || "Hébergeur à renseigner dans .env (LEGAL_HOSTING)";
      const email = le.email || cfg.support_email || "contact@veliora.fr";

      setText("[data-legal-company]", name, "Veliora");
      setText("[data-legal-siret]", siret ? `SIRET ${siret}` : "SIRET à renseigner");
      setText("[data-legal-address]", addr, "Adresse à renseigner dans .env");
      setText("[data-legal-hosting]", hosting);
      setHtml(
        "[data-legal-contact]",
        `<a href="mailto:${email}">${email}</a>`,
      );

      const editor = document.querySelector("[data-legal-editor]");
      if (editor) {
        const parts = [name];
        if (siret) parts.push(`SIRET ${siret}`);
        if (addr) parts.push(addr);
        editor.textContent = parts.join(" — ");
      }
    })
    .catch(() => {});
})();
