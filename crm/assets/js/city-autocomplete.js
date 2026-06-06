/**
 * Sélecteur de ville stylé (type Google Places) — communes françaises (34k+)
 * via /api/geo/communes.
 *
 * Au lieu du <datalist> natif (non stylé, limité), on affiche un menu déroulant
 * personnalisé : pin de localisation, nom de commune + département / code postal,
 * navigation clavier (↑ ↓ Entrée Échap) et sélection souris.
 *
 * Branchement automatique : tout <input data-city-autocomplete> (même inséré
 * dynamiquement) est équipé via un MutationObserver — « partout, quoi qu'il
 * arrive ». Ajouter data-city-multi="1" pour les champs multi-villes (séparées
 * par des virgules).
 */
(function (global) {
  const ATTR = "data-city-autocomplete";
  const STYLE_ID = "city-ac-styles";

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .city-ac-dropdown {
        position: absolute;
        z-index: 100000;
        background: #fff;
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 14px;
        box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18), 0 2px 8px rgba(15, 23, 42, 0.08);
        padding: 6px;
        max-height: 320px;
        overflow-y: auto;
        font-family: inherit;
        -webkit-overflow-scrolling: touch;
      }
      .city-ac-option {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 9px 10px;
        border-radius: 10px;
        cursor: pointer;
        color: #0f172a;
        line-height: 1.25;
      }
      .city-ac-option:hover,
      .city-ac-option.is-active {
        background: #f1f5f9;
      }
      .city-ac-pin {
        flex: 0 0 auto;
        width: 18px;
        height: 18px;
        color: #6366f1;
      }
      .city-ac-text { min-width: 0; flex: 1 1 auto; }
      .city-ac-name {
        font-weight: 600;
        font-size: 0.92rem;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .city-ac-name mark {
        background: transparent;
        color: #4f46e5;
        font-weight: 700;
      }
      .city-ac-meta {
        font-size: 0.76rem;
        color: #64748b;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .city-ac-empty {
        padding: 12px 14px;
        color: #94a3b8;
        font-size: 0.85rem;
        text-align: center;
      }
      @media (prefers-color-scheme: dark) {
        .city-ac-dropdown {
          background: #1e293b;
          border-color: rgba(148, 163, 184, 0.18);
          box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        }
        .city-ac-option { color: #e2e8f0; }
        .city-ac-option:hover,
        .city-ac-option.is-active { background: #334155; }
        .city-ac-meta { color: #94a3b8; }
        .city-ac-name mark { color: #a5b4fc; }
      }
    `;
    document.head.appendChild(style);
  }

  const PIN_SVG =
    '<svg class="city-ac-pin" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M12 21s-6.5-5.5-6.5-10.5a6.5 6.5 0 1113 0C18.5 15.5 12 21 12 21z" ' +
    'fill="currentColor" opacity="0.18"/>' +
    '<path d="M12 21s-6.5-5.5-6.5-10.5a6.5 6.5 0 1113 0C18.5 15.5 12 21 12 21z" ' +
    'stroke="currentColor" stroke-width="1.6"/>' +
    '<circle cx="12" cy="10.5" r="2.4" fill="currentColor"/></svg>';

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function highlight(name, query) {
    const safe = escHtml(name);
    const q = (query || "").trim();
    if (!q) return safe;
    const fold = (t) =>
      t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    const fn = fold(name);
    const fq = fold(q);
    const idx = fn.indexOf(fq);
    if (idx < 0) return safe;
    // Mappe les positions « foldées » sur le texte original (longueurs identiques
    // pour les accents latins : 1 caractère ↔ 1 caractère).
    const before = escHtml(name.slice(0, idx));
    const match = escHtml(name.slice(idx, idx + q.length));
    const after = escHtml(name.slice(idx + q.length));
    return `${before}<mark>${match}</mark>${after}`;
  }

  function apiBase() {
    if (global.getApiBase) return global.getApiBase();
    const { protocol, hostname, port } = window.location;
    if (protocol === "file:") return "http://127.0.0.1:8000/api";
    const devPorts = new Set(["5500", "5501", "5173", "3000", "8080", "4173"]);
    const isLocal = hostname === "localhost" || hostname === "127.0.0.1";
    if (isLocal && devPorts.has(port)) return `http://${hostname}:8000/api`;
    if (isLocal && port && port !== "8000") return `http://${hostname}:8000/api`;
    return "/api";
  }

  // ---- Menu déroulant unique, réutilisé pour tous les champs ----
  let dd = null;
  let activeInput = null;
  let activeOptions = [];
  let activeIndex = -1;
  let activeMulti = false;
  let activeQuery = "";
  let repositionBound = null;

  function ensureDropdown() {
    if (dd) return dd;
    injectStyles();
    dd = document.createElement("div");
    dd.className = "city-ac-dropdown";
    dd.style.display = "none";
    dd.setAttribute("role", "listbox");
    dd.addEventListener("mousedown", (e) => {
      const el = e.target.closest("[data-ac-index]");
      if (!el) return;
      e.preventDefault(); // évite le blur avant la sélection
      applySelection(activeOptions[Number(el.dataset.acIndex)]);
    });
    document.body.appendChild(dd);
    return dd;
  }

  function positionDropdown() {
    if (!dd || !activeInput) return;
    const r = activeInput.getBoundingClientRect();
    dd.style.left = `${r.left + window.scrollX}px`;
    dd.style.top = `${r.bottom + window.scrollY + 4}px`;
    dd.style.minWidth = `${r.width}px`;
  }

  function closeDropdown() {
    if (dd) dd.style.display = "none";
    activeInput = null;
    activeOptions = [];
    activeIndex = -1;
    if (repositionBound) {
      window.removeEventListener("scroll", repositionBound, true);
      window.removeEventListener("resize", repositionBound);
      repositionBound = null;
    }
  }

  function renderOptions() {
    ensureDropdown();
    if (!activeOptions.length) {
      dd.innerHTML = '<div class="city-ac-empty">Aucune commune trouvée</div>';
    } else {
      dd.innerHTML = activeOptions
        .map((c, i) => {
          const meta = [c.dept ? `Dépt ${c.dept}` : "", c.postcode]
            .filter(Boolean)
            .join(" · ");
          return (
            `<div class="city-ac-option${i === activeIndex ? " is-active" : ""}" ` +
            `data-ac-index="${i}" role="option">${PIN_SVG}` +
            `<span class="city-ac-text">` +
            `<span class="city-ac-name">${highlight(c.name, activeQuery)}</span>` +
            (meta ? `<span class="city-ac-meta">${escHtml(meta)}</span>` : "") +
            `</span></div>`
          );
        })
        .join("");
    }
    dd.style.display = "block";
    positionDropdown();
  }

  function openDropdown(input, communes, multi, query) {
    activeInput = input;
    activeOptions = communes;
    activeMulti = multi;
    activeQuery = query || "";
    activeIndex = communes.length ? 0 : -1;
    ensureDropdown();
    renderOptions();
    if (!repositionBound) {
      repositionBound = () => positionDropdown();
      window.addEventListener("scroll", repositionBound, true);
      window.addEventListener("resize", repositionBound);
    }
  }

  function moveActive(delta) {
    if (!activeOptions.length) return;
    activeIndex = (activeIndex + delta + activeOptions.length) % activeOptions.length;
    renderOptions();
    const el = dd.querySelector(`[data-ac-index="${activeIndex}"]`);
    if (el) el.scrollIntoView({ block: "nearest" });
  }

  function applySelection(item) {
    if (!activeInput || !item) return;
    const input = activeInput;
    const name = item.name;
    if (activeMulti) {
      const segs = input.value.split(",").map((s) => s.trim());
      segs[segs.length - 1] = name;
      input.value = segs.filter(Boolean).join(", ") + ", ";
    } else {
      input.value = name;
    }
    // « change » seulement (pas « input ») : on évite de relancer une recherche
    // qui rouvrirait le menu juste après la sélection.
    input.dispatchEvent(new Event("change", { bubbles: true }));
    closeDropdown();
    input.focus();
  }

  function setupFrenchCityAutocomplete(input, options = {}) {
    if (!input || input.dataset.cityAc === "1") return;
    input.dataset.cityAc = "1";
    const multi =
      Boolean(options.multi) ||
      input.getAttribute("data-city-multi") === "1" ||
      input.getAttribute("data-city-multi") === "true";
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("spellcheck", "false");
    let timer = null;

    const queryFromInput = () => {
      const raw = input.value || "";
      return multi ? raw.split(",").pop().trim() : raw.trim();
    };

    const runSearch = () => {
      clearTimeout(timer);
      const query = queryFromInput();
      if (query.length < 2) {
        if (activeInput === input) closeDropdown();
        return;
      }
      timer = setTimeout(async () => {
        try {
          const res = await fetch(
            `${apiBase()}/geo/communes?q=${encodeURIComponent(query)}&limit=8`,
          );
          if (!res.ok) {
            if (activeInput === input) closeDropdown();
            return;
          }
          const data = await res.json();
          const communes = (data && data.communes) || [];
          if (document.activeElement !== input || queryFromInput() !== query) {
            return;
          }
          openDropdown(input, communes, multi, query);
        } catch {
          if (activeInput === input) closeDropdown();
        }
      }, 160);
    };

    input.addEventListener("input", runSearch);
    input.addEventListener("focus", () => {
      if (queryFromInput().length >= 2) runSearch();
    });
    input.addEventListener("keydown", (e) => {
      if (!dd || dd.style.display === "none" || activeInput !== input) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveActive(1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        moveActive(-1);
      } else if (e.key === "Enter") {
        if (activeIndex >= 0 && activeOptions[activeIndex]) {
          e.preventDefault();
          applySelection(activeOptions[activeIndex]);
        }
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });
    input.addEventListener("blur", () => {
      // Délai : laisse le mousedown sur une option se déclencher avant de fermer.
      setTimeout(() => {
        if (activeInput === input) closeDropdown();
      }, 150);
    });
  }

  // ---- Branchement automatique de tous les champs ville ----
  function autoWire(root) {
    const scope = root && root.querySelectorAll ? root : document;
    let nodes;
    try {
      nodes = scope.querySelectorAll(`input[${ATTR}]`);
    } catch {
      return;
    }
    nodes.forEach((inp) => setupFrenchCityAutocomplete(inp));
    // Le root lui-même peut être un input ciblé.
    if (
      root &&
      root.matches &&
      root.matches(`input[${ATTR}]`)
    ) {
      setupFrenchCityAutocomplete(root);
    }
  }

  function startObserver() {
    autoWire(document);
    if (!("MutationObserver" in window)) return;
    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach((node) => {
          if (node.nodeType === 1) autoWire(node);
        });
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startObserver);
  } else {
    startObserver();
  }

  global.setupFrenchCityAutocomplete = setupFrenchCityAutocomplete;
  global.autoWireCityInputs = autoWire;
})(window);
