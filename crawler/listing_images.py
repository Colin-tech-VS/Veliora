"""Extraction de l'image principale d'une annonce immobilière."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
_SKIP_IMG_RE = re.compile(
    r"logo|icon|avatar|sprite|pixel|tracking|badge|favicon|placeholder|"
    r"1x1|blank|spacer|banner-ad",
    re.I,
)

_GALLERY_SELECTORS = (
    "[data-testid*='gallery'] img",
    "[class*='Gallery'] img",
    "[class*='gallery'] img",
    "[class*='carousel'] img",
    "[class*='slider'] img",
    ".swiper-slide img",
    "picture source",
    "picture img",
    "main img",
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
    if not _IMAGE_EXT_RE.search(parsed.path) and "image" not in (parsed.path or "").lower():
        if "leboncoin" in parsed.netloc or "seloger" in parsed.netloc:
            pass
        elif not re.search(r"/(photo|image|media|asset|picture)", parsed.path, re.I):
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


def extract_primary_listing_image(soup: BeautifulSoup, page_url: str) -> str | None:
    """Retourne l'URL de la meilleure image annonce trouvée."""
    candidates: list[str] = []

    for prop in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        meta = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop.replace("og:", "")}
        )
        if meta and meta.get("content"):
            u = _normalize_image_url(meta["content"], page_url)
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
        for el in soup.select(sel)[:12]:
            srcset = el.get("srcset") or el.get("data-srcset")
            if srcset:
                u = _pick_largest_from_srcset(srcset, page_url)
                if u:
                    candidates.append(u)
            for attr in ("data-src", "data-lazy-src", "data-original", "src"):
                u = _normalize_image_url(el.get(attr), page_url)
                if u:
                    candidates.append(u)

    seen: set[str] = set()
    for u in candidates:
        if u in seen:
            continue
        seen.add(u)
        return u
    return None
