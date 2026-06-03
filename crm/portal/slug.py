"""Slugs URL publics pour les fiches annonces."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str, *, max_len: int = 60) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text))
    ascii_txt = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_txt).strip("-")
    return slug[:max_len] if slug else ""


def make_public_slug(item: dict) -> str:
    """Slug SEO : ville-titre-idcourt (unique via suffixe id)."""
    city = slugify(item.get("city") or "ville", max_len=30) or "ville"
    title = slugify(item.get("title") or "bien", max_len=50) or "bien"
    lid = (item.get("id") or "").replace("-", "")[:8]
    base = f"{city}-{title}"
    if lid:
        base = f"{base}-{lid}"
    return base[:120]
