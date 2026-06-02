/** Logos sources — favicon web (Google / Clearbit) + repli SVG */

const SOURCE_LOGO_SVG = {
  leboncoin: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#FF6E14"/><path d="M18 42V22l14-8 14 8v20H36V32H26v10H18z" fill="#fff"/></svg>`,
  pap: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#0066CC"/><text x="32" y="41" text-anchor="middle" font-weight="700" font-size="20" fill="#fff">PAP</text></svg>`,
  seloger: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#E30613"/><text x="32" y="41" text-anchor="middle" font-weight="700" font-size="17" fill="#fff">SeLoger</text></svg>`,
  logicimmo: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#E11D48"/><text x="32" y="41" text-anchor="middle" font-weight="700" font-size="14" fill="#fff">LI</text></svg>`,
  bienici: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#7C3AED"/><path d="M32 16l16 28H16L32 16z" fill="#fff"/></svg>`,
  paruvendu: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#059669"/><text x="32" y="41" text-anchor="middle" font-weight="700" font-size="14" fill="#fff">PV</text></svg>`,
  lefigaro: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#2563eb"/><text x="32" y="42" text-anchor="middle" font-family="Georgia,serif" font-weight="700" font-size="24" fill="#fff">F</text></svg>`,
  custom: `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><rect width="64" height="64" rx="12" fill="#64748B"/><text x="32" y="42" text-anchor="middle" font-weight="700" font-size="18" fill="#fff">?</text></svg>`,
};

function resolveLogoId(source) {
  if (SOURCE_LOGO_SVG[source.id]) return source.id;
  const url = `${source.domain || ""} ${source.base_url || ""} ${source.search_url || ""}`.toLowerCase();
  if (url.includes("lefigaro") || url.includes("figaro")) return "lefigaro";
  if (url.includes("paruvendu")) return "paruvendu";
  if (url.includes("leboncoin")) return "leboncoin";
  if (url.includes("pap.fr")) return "pap";
  if (url.includes("seloger")) return "seloger";
  if (url.includes("logic-immo")) return "logicimmo";
  if (url.includes("bienici")) return "bienici";
  return "custom";
}

function onSourceLogoError(img) {
  if (img.dataset.fallback && !img.dataset.triedFb) {
    img.dataset.triedFb = "1";
    img.src = img.dataset.fallback;
    return;
  }
  img.style.display = "none";
  const letter = img.parentElement?.querySelector(".source-logo-letter");
  if (letter) letter.hidden = false;
}

function getSourceLogoHtml(source) {
  const name = (source.name || "Source").replace(/"/g, "");
  const initial = (name.charAt(0) || "?").toUpperCase();
  const inline = SOURCE_LOGO_SVG[resolveLogoId(source)] || SOURCE_LOGO_SVG.custom;

  if (source.logo_url) {
    const fb = source.logo_fallback || "";
    return `<div class="source-logo-img source-logo-web" aria-label="${name}">
      <img src="${source.logo_url}" alt="${name}" class="source-logo-favicon" loading="eager" decoding="async" referrerpolicy="no-referrer"
        data-fallback="${fb}" onerror="onSourceLogoError(this)">
      <span class="source-logo-letter" hidden>${initial}</span>
    </div>`;
  }

  return `<div class="source-logo-img" aria-label="${name}">${inline}</div>`;
}
