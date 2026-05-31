/* Veliora — Page offre : prix dynamiques + calculateur ROI */

(function () {
  const COMMISSION_PER_MANDATE = 3000;

  function initRoi(subscriptionHt) {
    const slider = document.getElementById("roi-mandats-slider");
    const valEl = document.getElementById("roi-mandats-val");
    const commissionEl = document.getElementById("roi-commission");
    const netEl = document.getElementById("roi-net");
    const subEl = document.getElementById("roi-subscription");
    if (!slider || !valEl || !commissionEl || !netEl) return;

    const sub = subscriptionHt || 500;
    if (subEl) subEl.textContent = "− " + sub.toLocaleString("fr-FR") + " € HT";

    function updateRoi() {
      const n = parseInt(slider.value, 10);
      const gross = n * COMMISSION_PER_MANDATE;
      const net = gross - sub;
      valEl.textContent = String(n);
      commissionEl.textContent = gross.toLocaleString("fr-FR") + " €";
      netEl.textContent = "+ " + net.toLocaleString("fr-FR") + " €";
    }

    slider.addEventListener("input", updateRoi);
    updateRoi();
  }

  function applyPricing(cfg) {
    const ht = cfg.subscription_amount_ht || cfg.subscription_amount_eur || 500;
    const ttc = cfg.subscription_amount_ttc || Math.round(ht * 1.2);
    const vat = cfg.vat_rate || 20;
    const maxSrc = cfg.max_sources || 25;
    const sla = cfg.support_sla_hours || 24;

    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(val);
    };

    set("price-ht", ht);
    set("price-ht-hero", ht);
    set("price-ttc", ttc);
    set("vat-rate", vat);
    set("max-sources", maxSrc);
    set("sla-h", sla);
    set("footer-price-ht", ht);

    document.querySelectorAll(".max-sources-dup").forEach((el) => {
      el.textContent = String(maxSrc);
    });

    const demo = document.getElementById("demo-cta");
    if (demo && cfg.demo_url) demo.href = cfg.demo_url;

    initRoi(ht);
  }

  fetch("/api/public/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (cfg) applyPricing(cfg);
      else initRoi(500);
    })
    .catch(() => initRoi(500));
})();
