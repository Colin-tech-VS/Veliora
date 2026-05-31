/* Veliora vitrine — navigation, révélations, config publique */

(function () {
  const toggle = document.getElementById("nav-toggle");
  const links = document.getElementById("nav-links");
  const nav = document.querySelector(".v-nav");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (toggle && links) {
    toggle.addEventListener("click", () => {
      const open = links.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      document.body.classList.toggle("v-nav-open", open);
    });

    links.querySelectorAll('a[href^="#"]').forEach((a) => {
      a.addEventListener("click", () => {
        links.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
        document.body.classList.remove("v-nav-open");
      });
    });
  }

  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener("click", (e) => {
      const id = anchor.getAttribute("href");
      if (!id || id === "#") return;
      const el = document.querySelector(id);
      if (!el) return;
      e.preventDefault();
      el.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
    });
  });

  if (nav) {
    const onScroll = () => nav.classList.toggle("v-nav-scrolled", window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  const revealEls = document.querySelectorAll(".v-reveal");
  const revealGroups = document.querySelectorAll(".v-reveal-group");

  const markVisible = (el) => {
    el.classList.add("is-visible");
    if (el.classList.contains("v-reveal-group")) {
      el.querySelectorAll("[data-reveal-delay]").forEach((c) => c.classList.add("is-visible"));
    }
  };

  if (revealEls.length || revealGroups.length) {
    if (reducedMotion || !("IntersectionObserver" in window)) {
      revealEls.forEach(markVisible);
      revealGroups.forEach(markVisible);
    } else {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            markVisible(entry.target);
            observer.unobserve(entry.target);
          });
        },
        { threshold: 0.1, rootMargin: "0px 0px -48px 0px" },
      );
      revealEls.forEach((el) => observer.observe(el));
      revealGroups.forEach((el) => observer.observe(el));
    }
  }

  fetch("/api/public/config")
    .then((r) => (r.ok ? r.json() : null))
    .then((cfg) => {
      if (!cfg) return;
      const sla = document.getElementById("sla-hours");
      if (sla && cfg.support_sla_hours) sla.textContent = String(cfg.support_sla_hours);
      const mail = document.getElementById("support-email-link");
      if (mail && cfg.support_email) {
        mail.href = `mailto:${cfg.support_email}`;
        mail.textContent = cfg.support_email;
      }
      ["demo-cta", "demo-cta-pilot"].forEach((id) => {
        const el = document.getElementById(id);
        if (el && cfg.demo_url) el.href = cfg.demo_url;
      });

      const ht = cfg.subscription_amount_ht || cfg.subscription_amount_eur || 500;
      const ttc = cfg.subscription_amount_ttc || Math.round(ht * 1.2);
      const setPrice = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(val);
      };
      setPrice("home-price-ht", ht);
      setPrice("home-hero-price-ht", ht);
      setPrice("home-price-ttc", ttc);
      setPrice("trial-price-ht", ht);
      const statHt = document.getElementById("stat-ht");
      if (statHt) statHt.textContent = `${ht} €`;
      const maxSrc = document.getElementById("max-sources");
      if (maxSrc && cfg.max_sources_per_agency) maxSrc.textContent = String(cfg.max_sources_per_agency);
      const trial = document.getElementById("trial-line");
      if (trial && cfg.stripe?.require_payment) {
        const days = cfg.trial_days || cfg.stripe?.trial_days;
        if (days > 0) trial.textContent = ` Essai ${days} jours à l'inscription (carte requise).`;
      }
    })
    .catch(() => {});
})();
