"""Extraction de l'image principale d'une annonce immobilière."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", re.I)
_BG_IMAGE_RE = re.compile(
    r"background(?:-image)?\s*:\s*[^;]*url\(\s*['\"]?([^)'\"]+)['\"]?\s*\)",
    re.I,
)
_EMBEDDED_IMG_URL_RE = re.compile(
    r"https?://[^\s\"'<>\\]+?(?:"
    r"\.(?:jpe?g|png|webp|gif|avif)(?:\?[^\s\"'<>]*)?"
    r"|[?&](?:format|fm|type|ext)=(?:jpe?g|png|webp|gif|avif|jpg)"
    r"|/(?:photo|image|media|asset|picture|annonce|listing|gallery|thumb)[^\s\"'<>]*)",
    re.I,
)
_SKIP_IMG_RE = re.compile(
    r"logo|icon|avatar|sprite|pixel|tracking|badge|favicon|placeholder|"
    r"1x1|blank|spacer|banner-ad",
    re.I,
)

_GALLERY_SELECTORS = (
    "[data-testid*='gallery'] img",
    "[data-testid*='photo'] img",
    "[data-qa-id*='photo'] img",
    "[class*='Gallery'] img",
    "[class*='gallery'] img",
    "[class*='carousel'] img",
    "[class*='slider'] img",
    "[class*='Photo'] img",
    "[class*='diaporama'] img",
    "[class*='slideshow'] img",
    ".swiper-slide img",
    "img[data-src], source[data-src]",
    "img[data-lazy], [data-lazy-src]",
    "picture source",
    "picture img",
    "main img",
    "article img",
)


def _normalize_image_url(raw: str | None, page_url: str) -> str | None:
    if not raw or not str(raw).strip():
        return None
    u = str(raw).strip().split()[0]
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("data:"):
        return None
    try:
        abs_u = urljoin(page_url, u)
    except Exception:
        return None
    parsed = urlparse(abs_u)
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    qs = (parsed.query or "").lower()
    if not _IMAGE_EXT_RE.search(path):
        if re.search(r"(?:^|[?&])(?:format|fm|type|ext)=(?:jpe?g|png|webp|gif|avif)", qs):
            pass
        elif re.search(r"(?:^|[?&])w=\d+", qs) and re.search(r"(?:^|[?&])h=\d+", qs):
            pass
        else:
            known = (
                "leboncoin",
                "seloger",
                "pap.fr",
                "bienici",
                "figaro",
                "logic-immo",
                "paruvendu",
                "mms.fr",
                "cloudinary",
                "akamaized",
                "imgix",
                "cdn",
                "images.",
                "static.",
                "media.",
            )
            if not any(k in host or k in path for k in known):
                if not re.search(
                    r"/(photo|image|media|asset|picture|annonce|listing|gallery|thumb)",
                    path,
                    re.I,
                ):
                    return None
    if _SKIP_IMG_RE.search(abs_u):
        return None
    return abs_u


def _pick_largest_from_srcset(srcset: str, page_url: str) -> str | None:
    best_url = None
    best_w = 0
    for part in srcset.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        bits = chunk.split()
        url = bits[0]
        w = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                w = int(bits[1][:-1])
            except ValueError:
                w = 0
        if w >= best_w:
            best_w = w
            best_url = url
    return _normalize_image_url(best_url, page_url)


def _json_ld_images(data: object, page_url: str) -> list[str]:
    found: list[str] = []
    if isinstance(data, dict):
        t = data.get("@type") or data.get("type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("Product", "Apartment", "House", "Residence", "Offer", "SingleFamilyResidence") for x in types if x):
            img = data.get("image")
            if isinstance(img, str):
                found.append(img)
            elif isinstance(img, list):
                for item in img:
                    if isinstance(item, str):
                        found.append(item)
                    elif isinstance(item, dict) and item.get("url"):
                        found.append(item["url"])
            elif isinstance(img, dict) and img.get("url"):
                found.append(img["url"])
        for v in data.values():
            found.extend(_json_ld_images(v, page_url))
    elif isinstance(data, list):
        for item in data:
            found.extend(_json_ld_images(item, page_url))
    return found


#: Plafond par défaut d'images conservées par annonce (galerie).
MAX_LISTING_IMAGES = 10


def _collect_image_candidates(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Toutes les URLs d'images annonce trouvées, dédupliquées dans l'ordre."""
    candidates: list[str] = []

    for prop in (
        "og:image",
        "og:image:url",
        "og:image:secure_url",
        "twitter:image",
        "twitter:image:src",
    ):
        meta = (
            soup.find("meta", attrs={"property": prop})
            or soup.find("meta", attrs={"property": re.compile(prop, re.I)})
            or soup.find("meta", attrs={"name": prop})
            or soup.find("meta", attrs={"name": re.compile(prop.replace(":", "_"), re.I)})
        )
        if meta and meta.get("content"):
            u = _normalize_image_url(meta["content"], page_url)
            if u:
                candidates.append(u)

    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "image_src" in rel or rel == "thumbnail":
            u = _normalize_image_url(link.get("href"), page_url)
            if u:
                candidates.append(u)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for raw in _json_ld_images(payload, page_url):
            u = _normalize_image_url(raw, page_url)
            if u:
                candidates.append(u)

    for sel in _GALLERY_SELECTORS:
        for el in soup.select(sel)[:16]:
            srcset = el.get("srcset") or el.get("data-srcset")
            if srcset:
                u = _pick_largest_from_srcset(srcset, page_url)
                if u:
                    candidates.append(u)
            for attr in (
                "data-src",
                "data-lazy-src",
                "data-original",
                "data-zoom-image",
                "data-full",
                "data-image",
                "data-url",
                "src",
            ):
                u = _normalize_image_url(el.get(attr), page_url)
                if u:
                    candidates.append(u)
            style = el.get("style") or ""
            for m in _BG_IMAGE_RE.finditer(style):
                u = _normalize_image_url(m.group(1), page_url)
                if u:
                    candidates.append(u)

    for el in soup.select("[style*='background']")[:20]:
        style = el.get("style") or ""
        for m in _BG_IMAGE_RE.finditer(style):
            u = _normalize_image_url(m.group(1), page_url)
            if u:
                candidates.append(u)

    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if len(text) < 40 or "http" not in text:
            continue
        for m in _EMBEDDED_IMG_URL_RE.finditer(text[:500_000]):
            u = _normalize_image_url(m.group(0), page_url)
            if u:
                candidates.append(u)

    seen: set[str] = set()
    ordered: list[str] = []
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        ordered.append(u)
    return ordered


def extract_primary_listing_image(soup: BeautifulSoup, page_url: str) -> str | None:
    """Retourne l'URL de la meilleure image annonce trouvée."""
    candidates = _collect_image_candidates(soup, page_url)
    return candidates[0] if candidates else None


def extract_listing_images(
    soup: BeautifulSoup, page_url: str, limit: int = MAX_LISTING_IMAGES
) -> list[str]:
    """Toutes les images de l'annonce (galerie), image principale en premier.

    Les logos/icônes/bannières sont déjà écartés par ``_SKIP_IMG_RE`` lors de la
    normalisation. La liste est plafonnée à ``limit`` pour limiter le stockage.
    """
    images = _collect_image_candidates(soup, page_url)
    if limit and limit > 0:
        images = images[:limit]
    return images
