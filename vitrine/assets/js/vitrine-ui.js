/* Toasts & modales Veliora — pas de alert/confirm navigateur */

window.VelioraUi = (function () {
  let modalOpen = false;

  function ensureToastRoot() {
    let el = document.getElementById("vitrine-toast-root");
    if (!el) {
      el = document.createElement("div");
      el.id = "vitrine-toast-root";
      el.className = "vitrine-toast-root";
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    return el;
  }

  function ensureModalRoot() {
    let el = document.getElementById("vitrine-modal-root");
    if (!el) {
      el = document.createElement("div");
      el.id = "vitrine-modal-root";
      el.className = "vitrine-modal-root";
      el.hidden = true;
      el.innerHTML = `
        <div class="vitrine-modal-backdrop" data-modal-close></div>
        <div class="vitrine-modal-card" role="dialog" aria-modal="true" aria-labelledby="vitrine-modal-title">
          <button type="button" class="vitrine-modal-close" data-modal-close aria-label="Fermer">×</button>
          <h3 id="vitrine-modal-title" class="vitrine-modal-title"></h3>
          <div id="vitrine-modal-body" class="vitrine-modal-body"></div>
          <div id="vitrine-modal-actions" class="vitrine-modal-actions"></div>
        </div>`;
      document.body.appendChild(el);
    }
    return el;
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toast(message, type = "info", duration = 4200) {
    const root = ensureToastRoot();
    const t = document.createElement("div");
    t.className = `vitrine-toast vitrine-toast--${type}`;
    t.innerHTML = `<span class="vitrine-toast-msg">${escapeHtml(message)}</span>`;
    root.appendChild(t);
    requestAnimationFrame(() => t.classList.add("is-visible"));
    setTimeout(() => {
      t.classList.remove("is-visible");
      setTimeout(() => t.remove(), 320);
    }, duration);
    return t;
  }

  function closeModal() {
    const root = document.getElementById("vitrine-modal-root");
    if (!root) return;
    root.hidden = true;
    root.classList.remove("is-open");
    document.body.classList.remove("vitrine-modal-open");
    modalOpen = false;
  }

  function openModal({ title, bodyHtml, buttons }) {
    return new Promise((resolve) => {
      if (modalOpen) resolve(null);
      const root = ensureModalRoot();
      modalOpen = true;
      root.querySelector("#vitrine-modal-title").textContent = title || "Veliora";
      root.querySelector("#vitrine-modal-body").innerHTML = bodyHtml || "";
      const actions = root.querySelector("#vitrine-modal-actions");
      actions.innerHTML = "";
      (buttons || []).forEach((b) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `v-btn ${b.primary ? "v-btn-primary" : "v-btn-ghost"}`;
        btn.textContent = b.label;
        btn.addEventListener("click", () => {
          closeModal();
          resolve(b.value);
        });
        actions.appendChild(btn);
      });
      root.hidden = false;
      requestAnimationFrame(() => {
        root.classList.add("is-open");
        document.body.classList.add("vitrine-modal-open");
        actions.querySelector("button")?.focus();
      });
      const onKey = (e) => {
        if (e.key === "Escape") {
          document.removeEventListener("keydown", onKey);
          closeModal();
          resolve(null);
        }
      };
      document.addEventListener("keydown", onKey);
      root.querySelectorAll("[data-modal-close]").forEach((el) => {
        el.onclick = () => {
          document.removeEventListener("keydown", onKey);
          closeModal();
          resolve(null);
        };
      });
    });
  }

  function alert(message, { title = "Information" } = {}) {
    return openModal({
      title,
      bodyHtml: `<p>${escapeHtml(message)}</p>`,
      buttons: [{ label: "OK", value: true, primary: true }],
    });
  }

  function confirm(message, { title = "Confirmer", confirmLabel = "Confirmer", cancelLabel = "Annuler" } = {}) {
    return openModal({
      title,
      bodyHtml: `<p>${escapeHtml(message)}</p>`,
      buttons: [
        { label: cancelLabel, value: false, primary: false },
        { label: confirmLabel, value: true, primary: true },
      ],
    }).then((v) => v === true);
  }

  return { toast, alert, confirm, closeModal };
})();
