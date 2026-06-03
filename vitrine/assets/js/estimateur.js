/* Estimateur public — pool prospects partagé, filtré par ville côté agences */

(function () {
  const root = document.getElementById("v-estimateur");
  if (!root) return;

  const form = document.getElementById("v-est-form");
  const resultEl = document.getElementById("v-est-result");
  const submitBtn = document.getElementById("v-est-submit");
  const featuresEl = document.getElementById("v-est-features");

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

  function renderResult(payload) {
    if (!payload?.ok) {
      return `<p class="v-est-error">${escapeHtml(payload?.error || "Estimation indisponible")}</p>`;
    }
    const e = payload.estimate || {};
    const conf = e.confidence || "low";
    const adj =
      e.adjustments?.length > 0
        ? `<ul class="v-est-adj">${e.adjustments
            .map(
              (a) =>
                `<li>${escapeHtml(a.label)} <span>${a.pct > 0 ? "+" : ""}${a.pct} %</span></li>`,
            )
            .join("")}</ul>`
        : "";
    const method = (e.methodology || [])
      .map((line) => `<li>${escapeHtml(line)}</li>`)
      .join("");
    return `
      <div class="v-est-result-card" data-confidence="${escapeHtml(conf)}">
        <p class="v-est-success">Demande enregistrée. Les agences de votre ville verront votre fiche dans leur espace.</p>
        <div class="v-est-main">
          <span class="v-est-label">Estimation net vendeur</span>
          <strong class="v-est-total">${fmtEuro(e.estimate_total)}</strong>
          <span class="v-est-range">${fmtEuro(e.range_low)} – ${fmtEuro(e.range_high)}</span>
        </div>
        ${
          e.estimate_fai != null
            ? `<p class="v-est-fai">Prix FAI (honoraires inclus) : <strong>${fmtEuro(e.estimate_fai)}</strong> (${fmtEuro(e.range_low_fai)} – ${fmtEuro(e.range_high_fai)})</p>`
            : ""
        }
        <p class="v-est-meta">Confiance ${escapeHtml(e.confidence_label || "")} · ${e.sample_count || 0} ventes DVF · ${escapeHtml(e.commune || e.sector || "")}</p>
        <p class="v-est-m2">Base ${(e.median_m2 || 0).toLocaleString("fr-FR")} €/m² · retenu <strong>${fmtEuro(e.price_per_m2)}/m²</strong> · ${e.surface} m²</p>
        ${adj}
        ${method ? `<ol class="v-est-method">${method}</ol>` : ""}
        <p class="v-est-disclaimer">${escapeHtml(e.disclaimer || "Estimation indicative — non contractuelle.")}</p>
      </div>`;
  }

  function collectPayload() {
    const val = (id) => document.getElementById(id)?.value?.trim() || "";
    const payload = {
      first_name: val("v-est-first"),
      last_name: val("v-est-last"),
      phone: val("v-est-phone"),
      email: val("v-est-email"),
      address: val("v-est-address"),
      city: val("v-est-city"),
      postcode: val("v-est-postcode"),
      surface: parseFloat(val("v-est-surface")) || null,
      property_type: val("v-est-type"),
      rooms: val("v-est-rooms") || null,
      floor: val("v-est-floor") || null,
      condition: val("v-est-condition"),
      dpe: val("v-est-dpe"),
      exposure: val("v-est-exposure"),
      construction_period: val("v-est-construction"),
      commission_pct: parseFloat(val("v-est-commission")) || 5,
      consent: !!document.getElementById("v-est-consent")?.checked,
      website: val("v-est-website"),
    };
    if (featuresEl) {
      featuresEl.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        payload[cb.name] = cb.checked;
      });
    }
    return payload;
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

  function fillSelect(id, options, placeholder) {
    const sel = document.getElementById(id);
    if (!sel || !options) return;
    const opts = options.map(normalizeOption).filter((o) => o.value !== "");
    sel.innerHTML =
      (placeholder ? `<option value="">${escapeHtml(placeholder)}</option>` : "") +
      opts
        .map(
          (o) =>
            `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`,
        )
        .join("");
  }

  function fillFeatures(schema) {
    if (!featuresEl || !schema?.features) return;
    featuresEl.innerHTML = schema.features
      .map(
        (f) =>
          `<label class="v-est-check"><input type="checkbox" name="${escapeHtml(f.key)}"> ${escapeHtml(f.label)}</label>`,
      )
      .join("");
  }

  fetch("/api/public/estimate/schema")
    .then((r) => (r.ok ? r.json() : null))
    .then((schemaRes) => {
      const schema = schemaRes?.schema;
      if (!schema) return;
      fillSelect("v-est-type", schema.property_types);
      fillSelect("v-est-condition", schema.conditions, null);
      fillSelect("v-est-dpe", schema.dpe_grades, "—");
      fillSelect("v-est-exposure", schema.exposures, "—");
      fillSelect("v-est-construction", schema.construction_periods, "—");
      const comm = document.getElementById("v-est-commission");
      if (comm && schema.default_commission_pct != null) {
        comm.value = String(schema.default_commission_pct);
      }
      fillFeatures(schema);
    })
    .catch(() => {});

  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = collectPayload();
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Analyse en cours…";
    }
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.innerHTML = '<p class="v-est-loading">Enregistrement dans la base prospects…</p>';
    }
    try {
      const res = await fetch("/api/public/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (resultEl) {
        resultEl.innerHTML = renderResult(data);
        resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    } catch {
      if (resultEl) {
        resultEl.innerHTML =
          '<p class="v-est-error">Connexion impossible. Réessayez dans un instant.</p>';
      }
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = "Obtenir mon estimation";
      }
    }
  });
})();
