"""Page HTML SSR d'une annonce — SEO acquéreurs / locataires."""

from __future__ import annotations

import html
import json

from flask import Response, abort

from crm.config import SITE_URL
from crm.portal.storage import get_listing_by_slug


def _fmt_price(item: dict) -> str:
    p = item.get("price")
    if p is None:
        return ""
    try:
        n = int(p)
        suffix = " / mois" if (item.get("transaction_type") or "").lower() == "location" else ""
        return f"{n:,}".replace(",", " ") + f" €{suffix}"
    except (TypeError, ValueError):
        return ""


def _seo_audience(item: dict) -> tuple[str, str]:
    tx = (item.get("transaction_type") or "vente").lower()
    if tx == "location":
        return (
            "à louer",
            "Locataires : contactez l'agence pour visiter ce bien en location.",
        )
    return (
        "à vendre",
        "Acquéreurs : contactez l'agence pour organiser une visite ou recevoir le dossier.",
    )


def _meta_description(item: dict) -> str:
    tx_label, _ = _seo_audience(item)
    parts = [
        (item.get("property_type") or "bien").capitalize(),
        tx_label,
        f"à {item.get('city') or ''}".strip(),
    ]
    if item.get("surface"):
        parts.append(f"{item['surface']} m²")
    if item.get("rooms"):
        parts.append(f"{item['rooms']} pièces")
    price = _fmt_price(item)
    if price:
        parts.append(price)
    agency = item.get("agency_name") or "agence immobilière"
    desc = (item.get("description") or "").strip()[:120]
    base = " — ".join(p for p in parts if p)
    return f"{base}. {agency}. {desc}".strip()[:300]


def _json_ld(item: dict, canonical: str) -> str:
    tx = (item.get("transaction_type") or "vente").lower()
    offer = "RentAction" if tx == "location" else "SellAction"
    data = {
        "@context": "https://schema.org",
        "@type": "RealEstateListing",
        "name": item.get("title"),
        "description": (item.get("description") or "")[:500],
        "url": canonical,
        "datePosted": item.get("published_at") or item.get("created_at"),
        "offers": {
            "@type": "Offer",
            "price": item.get("price"),
            "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock",
            "businessFunction": offer,
        },
        "address": {
            "@type": "PostalAddress",
            "addressLocality": item.get("city"),
            "postalCode": item.get("postcode"),
            "streetAddress": item.get("address"),
        },
    }
    if item.get("_seo_image"):
        data["image"] = item["_seo_image"]
    elif item.get("image_url"):
        data["image"] = item["image_url"]
    if item.get("agency_name"):
        data["seller"] = {"@type": "RealEstateAgent", "name": item["agency_name"]}
    return json.dumps(data, ensure_ascii=False)


def listing_detail_response(slug: str) -> Response:
    item = get_listing_by_slug(slug, public=True)
    if not item:
        abort(404)

    slug = item.get("public_slug") or slug
    canonical = f"{SITE_URL}/annonces/{html.escape(slug, quote=True)}"
    title = html.escape(item.get("title") or "Annonce")
    city = html.escape(item.get("city") or "")
    agency = html.escape(item.get("agency_name") or "Agence immobilière")
    desc_html = html.escape((item.get("description") or "").strip()).replace("\n", "<br>")
    tx_label, audience_hint = _seo_audience(item)
    meta_desc = html.escape(_meta_description(item))
    price = html.escape(_fmt_price(item))
    surface = item.get("surface")
    rooms = item.get("rooms")
    ptype = html.escape((item.get("property_type") or "").capitalize())
    tx = html.escape((item.get("transaction_type") or "vente").capitalize())
    listing_id = html.escape(item["id"])

    # Galerie servie par l'app (WebP, marquages retirés). Repli sur l'URL externe.
    source_images = item.get("source_images") or (
        [item["image_url"]] if item.get("image_url") else []
    )
    gallery = [
        f"/api/public/portal/listings/{item['id']}/image/{i}"
        for i in range(len(source_images))
    ]
    if not gallery and item.get("image_url"):
        gallery = [item["image_url"]]
    # Image absolue pour og:image / JSON-LD.
    if gallery:
        first = gallery[0]
        item["_seo_image"] = first if first.startswith("http") else f"{SITE_URL}{first}"

    if gallery:
        slide_parts = []
        for i, u in enumerate(gallery):
            loading = 'loading="eager" fetchpriority="high"' if i == 0 else 'loading="lazy"'
            slide_parts.append(
                f'<div class="v-ann-slide" role="group" aria-label="Photo {i + 1} sur {len(gallery)}">'
                f'<img class="v-ann-slide-img" src="{html.escape(u, quote=True)}" '
                f'alt="{title} — {city} ({i + 1})" width="1200" height="675" '
                f'{loading} decoding="async"></div>'
            )
        slides = "".join(slide_parts)
        controls = ""
        dots = ""
        if len(gallery) > 1:
            dots_inner = "".join(
                f'<button type="button" class="v-ann-dot{" is-active" if i == 0 else ""}" '
                f'data-slide-to="{i}" aria-label="Aller à la photo {i + 1}"></button>'
                for i in range(len(gallery))
            )
            dots = f'<div class="v-ann-dots" role="tablist">{dots_inner}</div>'
            controls = (
                '<button type="button" class="v-ann-nav v-ann-prev" aria-label="Photo précédente">‹</button>'
                '<button type="button" class="v-ann-nav v-ann-next" aria-label="Photo suivante">›</button>'
                f'<span class="v-ann-counter"><span class="v-ann-counter-cur">1</span>/{len(gallery)}</span>'
            )
        img_block = (
            f'<div class="v-ann-slider" data-slider data-count="{len(gallery)}">'
            f'<div class="v-ann-slides">{slides}</div>'
            f"{controls}{dots}</div>"
        )
    else:
        img_block = '<div class="v-ann-detail-hero-placeholder" aria-hidden="true"></div>'

    specs = []
    if surface:
        specs.append(f"<li><strong>Surface</strong> {html.escape(str(surface))} m²</li>")
    if rooms:
        specs.append(f"<li><strong>Pièces</strong> {html.escape(str(rooms))}</li>")
    if item.get("postcode"):
        specs.append(f"<li><strong>Code postal</strong> {html.escape(str(item['postcode']))}</li>")
    specs_html = "".join(specs)

    page_title = f"{title} {tx_label} {city} — {agency} | Veliora"
    h1 = f'{title} <span class="v-ann-detail-tx">{html.escape(tx_label)}</span>'

    body = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <meta name="description" content="{meta_desc}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{page_title}">
  <meta property="og:description" content="{meta_desc}">
  <meta property="og:url" content="{canonical}">
  <meta name="theme-color" content="#152a36">
  <link rel="icon" href="/favicon.ico" type="image/svg+xml">
  <title>{html.escape(page_title)}</title>
  <script type="application/ld+json">{_json_ld(item, canonical.replace("&amp;", "&"))}</script>
  <link rel="stylesheet" href="/crm/assets/css/veliora-brand.css?v=2">
  <link rel="stylesheet" href="/vitrine/assets/css/vitrine.css?v=45">
  <link rel="stylesheet" href="/vitrine/assets/css/annonces.css?v=4">
</head>
<body class="vitrine vitrine-annonces vitrine-annonce-detail" data-listing-id="{listing_id}">
  <header class="v-nav" id="top">
    <div class="v-nav-inner">
      <a href="/" class="v-brand" aria-label="Veliora — accueil">
        <span class="v-logo-mark" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.75" stroke="currentColor"><path d="M3 9.5L12 3l9 6.5V20a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1V9.5z"/></svg>
        </span>
        Veliora
      </a>
      <button type="button" class="v-nav-toggle" id="nav-toggle" aria-label="Menu" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
      <nav class="v-nav-links" id="nav-links" aria-label="Navigation">
        <a href="/annonces">← Catalogue</a>
        <a href="/estimation" class="v-nav-estimation">Estimation gratuite</a>
        <a href="/crm/auth" class="v-btn v-btn-ghost">Connexion pro</a>
      </nav>
    </div>
  </header>

  <main class="v-ann-detail-main">
    <nav class="v-ann-detail-breadcrumb" aria-label="Fil d'Ariane">
      <a href="/">Accueil</a> › <a href="/annonces">Annonces</a> › <span>{city}</span>
    </nav>

    <article class="v-ann-detail" itemscope itemtype="https://schema.org/RealEstateListing">
      <div class="v-ann-detail-hero">{img_block}</div>
      <div class="v-ann-detail-grid">
        <div class="v-ann-detail-content">
          <p class="v-ann-detail-agency">{agency}</p>
          <h1 itemprop="name">{h1}</h1>
          <p class="v-ann-detail-price" itemprop="offers" itemscope itemtype="https://schema.org/Offer">
            <span itemprop="price">{price}</span>
            <meta itemprop="priceCurrency" content="EUR">
          </p>
          <p class="v-ann-detail-loc" itemprop="address" itemscope itemtype="https://schema.org/PostalAddress">
            <span itemprop="addressLocality">{city}</span>
            {f' · <span itemprop="postalCode">{html.escape(str(item["postcode"]))}</span>' if item.get("postcode") else ""}
          </p>
          <p class="v-ann-detail-meta">{ptype} · {tx}</p>
          <p class="v-ann-detail-hint">{html.escape(audience_hint)}</p>
          <ul class="v-ann-detail-specs">{specs_html}</ul>
          <div class="v-ann-detail-desc" itemprop="description">
            {desc_html if desc_html else "<p>Description à venir — contactez l'agence pour plus de détails.</p>"}
          </div>
        </div>

        <aside class="v-ann-detail-aside" aria-label="Contacter l'agence">
          <div class="v-ann-detail-cta-card">
            <h2>Intéressé par ce bien ?</h2>
            <p>Transmettez votre demande à <strong>{agency}</strong>. Réponse sous 48 h ouvrées.</p>
            <button type="button" class="v-btn v-btn-primary v-btn-block" id="cta-contact" data-kind="contact_agency">
              Contacter l'agence
            </button>
            <button type="button" class="v-btn v-btn-secondary v-btn-block" id="cta-info" data-kind="info_request">
              Demande d'information
            </button>
          </div>
          <p class="v-ann-detail-legal">Veliora met en relation acquéreurs et locataires avec des agences professionnelles. Aucun engagement sans réponse de l'agence.</p>
        </aside>
      </div>
    </article>
  </main>

  <div id="inquiry-modal" class="v-inquiry-modal" hidden aria-hidden="true">
    <div class="v-inquiry-modal-backdrop" data-inquiry-close></div>
    <div class="v-inquiry-modal-card" role="dialog" aria-labelledby="inquiry-modal-title">
      <button type="button" class="v-inquiry-modal-close" data-inquiry-close aria-label="Fermer">×</button>
      <h2 id="inquiry-modal-title">Contacter l'agence</h2>
      <p id="inquiry-modal-lead" class="v-inquiry-modal-lead"></p>
      <form id="inquiry-form" class="v-inquiry-form" novalidate>
        <input type="hidden" name="kind" id="inquiry-kind" value="contact_agency">
        <label class="v-inquiry-field"><span>Votre nom *</span>
          <input type="text" name="name" required minlength="2" autocomplete="name"></label>
        <label class="v-inquiry-field"><span>Email</span>
          <input type="email" name="email" autocomplete="email"></label>
        <label class="v-inquiry-field"><span>Téléphone</span>
          <input type="tel" name="phone" autocomplete="tel"></label>
        <label class="v-inquiry-field v-inquiry-field-wide" id="inquiry-message-wrap">
          <span id="inquiry-message-label">Message</span>
          <textarea name="message" rows="4" placeholder="Précisez votre projet (visite, financement, délais…)"></textarea>
        </label>
        <p class="v-inquiry-hint">Email ou téléphone requis. Données transmises uniquement à l'agence mandataire.</p>
        <button type="submit" class="v-btn v-btn-primary v-btn-block">Envoyer ma demande</button>
      </form>
    </div>
  </div>

  <footer class="v-footer">
    <p class="v-footer-copy">© 2026 Veliora · <a href="/confidentialite">Confidentialité</a></p>
  </footer>

  <script src="/vitrine/assets/js/vitrine-ui.js?v=1" defer></script>
  <script src="/vitrine/assets/js/vitrine-nav.js?v=1" defer></script>
  <script src="/vitrine/assets/js/annonce-detail.js?v=1" defer></script>
</body>
</html>"""

    return Response(body, mimetype="text/html; charset=utf-8")
