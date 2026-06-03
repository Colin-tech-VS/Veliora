/* Navigation vitrine — page active (liens : partials/nav-links.html) */

(function () {
  const CURRENT_BY_PATH = [
    { match: (p) => p === "/offre" || p === "/tarifs", href: "/offre" },
    {
      match: (p) =>
        p === "/estimation" ||
        p.startsWith("/estimer") ||
        p === "/publier-annonce",
      href: "/estimation",
    },
    {
      match: (p) => p === "/annonces" || p === "/portail",
      href: "/annonces",
    },
  ];

  function init() {
    const nav = document.getElementById("nav-links");
    if (!nav) return;

    const path = ((location.pathname || "/").replace(/\/+$/, "") || "/").toLowerCase();
    const rule = CURRENT_BY_PATH.find((r) => r.match(path));
    nav.querySelectorAll("[aria-current]").forEach((el) => el.removeAttribute("aria-current"));
    if (!rule) return;
    const link = nav.querySelector(`a[href="${rule.href}"]`);
    if (link) link.setAttribute("aria-current", "page");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
