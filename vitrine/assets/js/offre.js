/* Veliora — Page offre : prix HT/TTC dynamiques */

(function () {
  fetch("/api/public/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (!cfg) return;
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
      set("price-ttc", ttc);
      set("vat-rate", vat);
      set("max-sources", maxSrc);
      set("sla-h", sla);
      document.querySelectorAll(".max-sources-dup").forEach((el) => {
        el.textContent = String(maxSrc);
      });

      const demo = document.getElementById("demo-cta");
      if (demo && cfg.demo_url) demo.href = cfg.demo_url;
    })
    .catch(() => {});
})();
