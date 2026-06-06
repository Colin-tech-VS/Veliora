/* LP estimation vendeur — 3 étapes + choix contact agence après résultat */

(function () {
  const form = document.getElementById("lp-est-form");
  const shell = document.getElementById("lp-est-form-shell");
  if (!form || !window.VelioraPublicEstimator) return;

  const { escapeHtml, renderResultHtml, renderSellIntentHtml } = window.VelioraPublicEstimator;

  const STEP_MAX = 2;
  const FALLBACK = {
    property_types: [
      { value: "appartement", label: "Appartement" },
      { value: "maison", label: "Maison" },
      { value: "studio", label: "Studio" },
      { value: "terrain", label: "Terrain" },
      { value: "autre", label: "Autre" },
    ],
    conditions: [
      { value: "neuf", label: "Neuf / récent" },
      { value: "bon", label: "Bon état" },
      { value: "standard", label: "Standard" },
      { value: "rafraichir", label: "À rafraîchir" },
      { value: "renover", label: "À rénover" },
    ],
    dpe_grades: [
      { value: "A", label: "DPE A" },
      { value: "B", label: "DPE B" },
      { value: "C", label: "DPE C" },
      { value: "D", label: "DPE D" },
      { value: "E", label: "DPE E" },
      { value: "F", label: "DPE F (passoire)" },
      { value: "G", label: "DPE G (passoire)" },
    ],
    exposures: [
      { value: "sud", label: "Plein sud" },
      { value: "sud_ouest", label: "Sud / Ouest" },
      { value: "traversant", label: "Traversant" },
      { value: "est_ouest", label: "Est / Ouest" },
      { value: "nord", label: "Nord" },
    ],
    construction_periods: [
      { value: "avant_1949", label: "Avant 1949 (ancien)" },
      { value: "1949_1974", label: "1949–1974" },
      { value: "1975_2000", label: "1975–2000" },
      { value: "apres_2000", label: "Après 2000 (récent)" },
    ],
    features: [
      { key: "has_elevator", label: "Ascenseur" },
      { key: "has_parking", label: "Parking / box" },
      { key: "has_outdoor", label: "Balcon / terrasse / jardin" },
      { key: "has_cellar", label: "Cave / cellier" },
      { key: "has_view", label: "Belle vue" },
      { key: "bright", label: "Très lumineux" },
      { key: "recent_renovation", label: "Rénovation récente" },
      { key: "noise_nuisance", label: "Nuisances (bruit, vis-à-vis…)" },
      { key: "prime_sector", label: "Quartier très recherché" },
    ],
    default_commission_pct: 5,
  };

  let defaultCommission = FALLBACK.default_commission_pct;
  let step = 0;
  let session = { leadId: null, contactToken: null };
  const featureState = {};

  const panes = [...form.querySelectorAll(".v-est-pane")];
  const progressFill = document.getElementById("lp-progress-fill");
  const progressBar = document.getElementById("lp-progress");
  const stepLabels = [...document.querySelectorAll("#lp-steps-label li")];
  const btnPrev = document.getElementById("lp-prev");
  const btnNext = document.getElementById("lp-next");
  const resultEl = document.getElementById("lp-est-result");
  const panelTitle = document.getElementById("lp-form-title");

  function showMsg(msg, focusId) {
    if (window.VelioraUi?.toast) VelioraUi.toast(msg, "warning");
    else console.warn(msg);
    if (focusId) document.getElementById(focusId)?.focus();
  }

  function normalizeOption(item) {
    if (Array.isArray(item)) return { value: item[0], label: item[1] };
    if (item && typeof item === "object") {
      return {
        value: item.value ?? item[0] ?? "",
        label: item.label ?? item[1] ?? item.value ?? "",
      };
    }
    return { value: String(item), label: String(item) };
  }

  function normalizeList(list) {
    return (list || []).map(normalizeOption).filter((o) => o.value !== "" || o.label);
  }

  function fillSelect(id, options, placeholder) {
    const sel = document.getElementById(id);
    if (!sel) return;
    const opts = normalizeList(options);
    sel.innerHTML =
      (placeholder != null
        ? `<option value="">${escapeHtml(placeholder)}</option>`
        : "") +
      opts
        .map(
          (o) =>
            `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`,
        )
        .join("");
  }

  function selectRadioGroup(container, hidden, val) {
    if (!container || !hidden) return;
    hidden.value = val;
    container.querySelectorAll(".v-est-chip[data-value]").forEach((btn) => {
      const on = btn.dataset.value === val;
      btn.classList.toggle("is-selected", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function initRadioChips(containerId, hiddenId, options, { allowEmpty } = {}) {
    const container = document.getElementById(containerId);
    const hidden = document.getElementById(hiddenId);
    if (!container || !hidden) return;

    container.dataset.hiddenFor = hiddenId;
    const opts = normalizeList(options);
    const current = hidden.value;
    container.innerHTML = opts
      .map(
        (o) =>
          `<button type="button" class="v-est-chip" data-value="${escapeHtml(o.value)}" role="radio" aria-checked="false">${escapeHtml(o.label)}</button>`,
      )
      .join("");

    if (allowEmpty) {
      const none = document.createElement("button");
      none.type = "button";
      none.className = "v-est-chip";
      none.dataset.value = "";
      none.setAttribute("role", "radio");
      none.textContent = "—";
      container.prepend(none);
    }

    if (allowEmpty && current === "") selectRadioGroup(container, hidden, "");
    else selectRadioGroup(container, hidden, current || opts[0]?.value || "");
  }

  function syncFeatureChipUI() {
    const container = document.getElementById("lp-features-chips");
    if (!container) return;
    container.querySelectorAll(".v-est-chip[data-key]").forEach((btn) => {
      const on = !!featureState[btn.dataset.key];
      btn.classList.toggle("is-selected", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function initFeatureChips(features) {
    const container = document.getElementById("lp-features-chips");
    if (!container) return;
    const prev = { ...featureState };
    Object.keys(featureState).forEach((k) => delete featureState[k]);
    (features || []).forEach((f) => {
      const key = f.key || f[0];
      featureState[key] = !!prev[key];
    });
    container.innerHTML = (features || [])
      .map((f) => {
        const key = f.key || f[0];
        const label = f.label || f[1];
        return `<button type="button" class="v-est-chip" data-key="${escapeHtml(key)}" aria-pressed="false">${escapeHtml(label)}</button>`;
      })
      .join("");
    syncFeatureChipUI();
  }

  function bindFormChipClicksOnce() {
    if (form.dataset.chipsBound === "1") return;
    form.dataset.chipsBound = "1";
    form.addEventListener("click", (e) => {
      const btn = e.target.closest(".v-est-chip");
      if (!btn || !form.contains(btn)) return;

      const featureKey = btn.dataset.key;
      if (featureKey) {
        e.preventDefault();
        featureState[featureKey] = !featureState[featureKey];
        btn.classList.toggle("is-selected", featureState[featureKey]);
        btn.setAttribute("aria-pressed", featureState[featureKey] ? "true" : "false");
        return;
      }

      if (btn.dataset.value === undefined) return;
      const container = btn.closest(".v-est-chips[role='radiogroup']");
      if (!container?.dataset.hiddenFor) return;
      const hidden = document.getElementById(container.dataset.hiddenFor);
      if (!hidden) return;
      e.preventDefault();
      selectRadioGroup(container, hidden, btn.dataset.value ?? "");
    });
  }

  function bindRange(inputId, outId, format) {
    const input = document.getElementById(inputId);
    const out = document.getElementById(outId);
    if (!input) return;
    const update = () => {
      const min = Number(input.min);
      const max = Number(input.max);
      const val = Number(input.value);
      const pct = max > min ? ((val - min) / (max - min)) * 100 : 0;
      input.style.setProperty("--lp-range-pct", `${pct}%`);
      if (out) out.textContent = format(val);
    };
    input.addEventListener("input", update);
    update();
  }

  function setStep(n) {
    step = Math.max(0, Math.min(STEP_MAX, n));
    panes.forEach((p) => {
      const active = Number(p.dataset.pane) === step;
      p.classList.toggle("is-active", active);
      p.hidden = !active;
    });
    stepLabels.forEach((li) => {
      const s = Number(li.dataset.step);
      li.classList.toggle("is-active", s === step);
      li.classList.toggle("is-done", s < step);
    });
    const pct = ((step + 1) / (STEP_MAX + 1)) * 100;
    if (progressFill) progressFill.style.width = `${pct}%`;
    if (progressBar) progressBar.setAttribute("aria-valuenow", String(step + 1));

    if (btnPrev) {
      const hidePrev = step === 0;
      btnPrev.classList.toggle("is-hidden", hidePrev);
      btnPrev.disabled = hidePrev;
    }
    if (btnNext) {
      btnNext.textContent =
        step === STEP_MAX ? "Obtenir mon estimation" : "Continuer";
    }
  }

  function validateStep(n) {
    if (n === 0) {
      const city = document.getElementById("lp-city")?.value?.trim();
      const surface = Number(document.getElementById("lp-surface")?.value);
      if (!surface || surface < 1) {
        showMsg("Indiquez une surface habitable valide.", "lp-surface");
        return false;
      }
      if (!city) {
        showMsg("La ville est requise.", "lp-city");
        return false;
      }
      return true;
    }
    if (n === 2) {
      const first = document.getElementById("lp-first")?.value?.trim() || "";
      const last = document.getElementById("lp-last")?.value?.trim() || "";
      if (first.length < 2) {
        showMsg("Prénom requis (2 caractères minimum).", "lp-first");
        return false;
      }
      if (last.length < 2) {
        showMsg("Nom requis (2 caractères minimum).", "lp-last");
        return false;
      }
      const phone = document.getElementById("lp-phone")?.value?.trim();
      const email = document.getElementById("lp-email")?.value?.trim();
      if (!phone && !email) {
        showMsg("Indiquez un téléphone ou un email.", "lp-phone");
        return false;
      }
      if (!document.getElementById("lp-consent")?.checked) {
        showMsg("Veuillez accepter la politique de confidentialité.", "lp-consent");
        return false;
      }
    }
    return true;
  }

  function collectPayload() {
    const val = (id) => document.getElementById(id)?.value?.trim() ?? "";
    const commRaw = document.getElementById("lp-commission")?.value;
    const payload = {
      first_name: val("lp-first"),
      last_name: val("lp-last"),
      phone: val("lp-phone"),
      email: val("lp-email"),
      address: val("lp-address"),
      city: val("lp-city"),
      postcode: val("lp-postcode"),
      surface: parseFloat(document.getElementById("lp-surface")?.value) || null,
      property_type: val("lp-property-type") || "appartement",
      rooms: val("lp-rooms") || null,
      floor: val("lp-floor") ?? "",
      condition: val("lp-condition") || "standard",
      dpe: val("lp-dpe") || "",
      exposure: val("lp-exposure") || "",
      construction_period: val("lp-construction") || "",
      commission_pct:
        commRaw !== "" && commRaw != null ? commRaw : defaultCommission,
      consent: !!document.getElementById("lp-consent")?.checked,
      website: val("lp-est-website"),
    };
    // Widget marque blanche : agence émettrice transmise via ?agency=<slug>.
    const embedAgency =
      window.VELIORA_EMBED_AGENCY ||
      new URLSearchParams(location.search).get("agency");
    if (embedAgency) payload.agency = embedAgency;
    Object.assign(payload, featureState);
    return payload;
  }

  function hasOwnerContact(payload) {
    return (
      (payload.first_name || "").length >= 2 &&
      (payload.last_name || "").length >= 2 &&
      (!!(payload.phone || "").trim() || !!(payload.email || "").trim())
    );
  }

  function ownerContactFromWizard() {
    return {
      first_name: document.getElementById("lp-first")?.value?.trim() || "",
      last_name: document.getElementById("lp-last")?.value?.trim() || "",
      phone: document.getElementById("lp-phone")?.value?.trim() || "",
      email: document.getElementById("lp-email")?.value?.trim() || "",
      consent: true,
    };
  }

  function resetWizard() {
    session = { leadId: null, contactToken: null };
    shell?.classList.remove("is-result-mode");
    if (resultEl) {
      resultEl.hidden = true;
      resultEl.innerHTML = "";
    }
    form.reset();
    document.getElementById("lp-property-type").value = "appartement";
    document.getElementById("lp-condition").value = "standard";
    document.getElementById("lp-dpe").value = "";
    const comm = document.getElementById("lp-commission");
    if (comm) comm.value = String(defaultCommission);
    applySchema(FALLBACK);
    if (panelTitle) panelTitle.textContent = "Votre estimation";
    setStep(0);
  }

  function showLoading() {
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.innerHTML = '<p class="v-est-result-loading">Calcul en cours…</p>';
    }
  }

  function showResultView(html, { final = false } = {}) {
    if (final) shell?.classList.add("is-result-mode");
    if (final && panelTitle) panelTitle.textContent = "Votre résultat";
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.innerHTML = html;
      resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function applySchema(schema) {
    const s = schema || FALLBACK;
    if (s.default_commission_pct != null) {
      defaultCommission = s.default_commission_pct;
      const comm = document.getElementById("lp-commission");
      if (comm && !comm.matches(":focus")) comm.value = String(defaultCommission);
    }
    initRadioChips("lp-type-chips", "lp-property-type", s.property_types);
    initRadioChips("lp-condition-chips", "lp-condition", s.conditions);
    initRadioChips("lp-dpe-chips", "lp-dpe", s.dpe_grades, { allowEmpty: true });
    fillSelect("lp-exposure", s.exposures, "—");
    fillSelect("lp-construction", s.construction_periods, "—");
    initFeatureChips(s.features);
  }

  async function runEstimate() {
    const payload = collectPayload();
    if (btnNext) {
      btnNext.disabled = true;
      btnNext.textContent = "Analyse en cours…";
    }
    showLoading();

    try {
      const res = await fetch("/api/public/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (data?.ok && data.estimate) {
        session.leadId = data.lead_id;
        session.contactToken = data.contact_token;
        const ownerOk = hasOwnerContact(payload);
        const html =
          renderResultHtml({ ok: true, ...data.estimate }) +
          renderSellIntentHtml({ hasOwnerContact: ownerOk });
        showResultView(html, { final: true });
        bindSellIntentHandlers();
      } else {
        showResultView(
          renderResultHtml({
            ok: false,
            error: data?.error || "Estimation indisponible pour ce bien.",
          }),
          { final: false },
        );
      }
    } catch {
      showResultView(
        renderResultHtml({
          ok: false,
          error: "Connexion impossible. Vérifiez que Veliora tourne (demarrer.bat) puis réessayez.",
        }),
        { final: false },
      );
    } finally {
      if (btnNext) {
        btnNext.disabled = false;
        setStep(step);
      }
    }
  }

  function bindSellIntentHandlers() {
    const root = resultEl;
    if (!root) return;

    const choiceBtns = root.querySelectorAll("[data-sell-choice]");
    const contactForm = root.querySelector("#lp-sell-contact-form");
    const confirmBtn = root.querySelector("#lp-sell-confirm");

    const ownerKnown = root.querySelector(".v-est-sell-intent")?.dataset?.ownerKnown === "1";

    choiceBtns.forEach((btn) => {
      btn.addEventListener("click", async () => {
        const wants = btn.dataset.sellChoice === "yes";
        if (!wants) {
          await submitContactChoice(false, {});
          return;
        }
        if (ownerKnown) {
          await submitContactChoice(true, ownerContactFromWizard());
          return;
        }
        if (contactForm) {
          contactForm.hidden = false;
          contactForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }
      });
    });

    confirmBtn?.addEventListener("click", async () => {
      const first = root.querySelector("#lp-sell-first")?.value?.trim() || "";
      const last = root.querySelector("#lp-sell-last")?.value?.trim() || "";
      const phone = root.querySelector("#lp-sell-phone")?.value?.trim() || "";
      const email = root.querySelector("#lp-sell-email")?.value?.trim() || "";
      const consent = !!root.querySelector("#lp-sell-consent")?.checked;

      if (first.length < 2) {
        showMsg("Prénom requis.", "lp-sell-first");
        return;
      }
      if (last.length < 2) {
        showMsg("Nom requis.", "lp-sell-last");
        return;
      }
      if (!phone && !email) {
        showMsg("Téléphone ou email requis.", "lp-sell-phone");
        return;
      }
      if (!consent) {
        showMsg("Acceptez d’être contacté par une agence.", "lp-sell-consent");
        return;
      }

      await submitContactChoice(true, {
        first_name: first,
        last_name: last,
        phone,
        email,
        consent,
      });
    });
  }

  async function submitContactChoice(wantsAgency, contact) {
    if (!session.leadId || !session.contactToken) {
      showMsg("Session expirée — relancez une estimation.");
      return;
    }

    const statusEl = resultEl?.querySelector("#lp-sell-status");
    if (statusEl) statusEl.textContent = "Enregistrement…";

    try {
      const res = await fetch("/api/public/estimate/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lead_id: session.leadId,
          contact_token: session.contactToken,
          wants_agency_contact: wantsAgency,
          ...contact,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) {
        showMsg(data.error || "Enregistrement impossible.");
        if (statusEl) statusEl.textContent = "";
        return;
      }

      const done = resultEl?.querySelector(".v-est-sell-intent");
      if (done) {
        if (wantsAgency) {
          const n = data.agencies_notified || 0;
          done.innerHTML = `<p class="v-est-sell-done v-est-sell-done--yes">
            Merci ! Votre demande a été transmise${n ? ` à <strong>${n} agence${n > 1 ? "s" : ""}</strong> de votre secteur` : ""}.
            Un professionnel pourra vous recontacter prochainement.
          </p>`;
          VelioraUi?.toast("Demande envoyée aux agences du secteur", "success");
        } else {
          done.innerHTML = `<p class="v-est-sell-done">
            Votre estimation reste enregistrée. Aucune prise de contact ne sera initiée.
          </p>`;
          VelioraUi?.toast("Estimation enregistrée", "success");
        }
      }
    } catch {
      showMsg("Erreur réseau — réessayez.");
      if (statusEl) statusEl.textContent = "";
    }
  }

  bindRange("lp-surface", "lp-surface-out", (v) => `${v} m²`);
  bindRange("lp-rooms", "lp-rooms-out", (v) => String(v));
  bindRange("lp-floor", "lp-floor-out", (v) => (v === 0 ? "RDC" : String(v));
  bindFormChipClicksOnce();

  applySchema(FALLBACK);
  fetch("/api/public/estimate/schema")
    .then((r) => (r.ok ? r.json() : null))
    .then((res) => {
      if (res?.schema) applySchema(res.schema);
    })
    .catch(() => {});

  document.getElementById("lp-est-form-shell")?.addEventListener("click", (e) => {
    if (e.target.closest("#lp-prev")) {
      e.preventDefault();
      if (step > 0) setStep(step - 1);
      return;
    }
    if (e.target.closest("#lp-next")) {
      e.preventDefault();
      if (!validateStep(step)) return;
      if (step < STEP_MAX) {
        setStep(step + 1);
      } else {
        runEstimate();
      }
    }
  });

  document.querySelectorAll("[data-goto-step]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const target = Number(btn.dataset.gotoStep);
      if (Number.isNaN(target)) return;
      if (target > step) {
        for (let i = step; i < target; i++) {
          if (!validateStep(i)) return;
        }
      }
      if (target === STEP_MAX && step === STEP_MAX) {
        runEstimate();
        return;
      }
      setStep(target);
    });
  });

  shell?.addEventListener("click", (e) => {
    if (e.target.closest("[data-est-restart]") || e.target.closest("[data-est-retry]")) {
      e.preventDefault();
      resetWizard();
    }
  });

  form.addEventListener("submit", (e) => e.preventDefault());

  setStep(0);
})();
