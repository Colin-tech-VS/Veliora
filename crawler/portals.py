"""Résolution portail (IDs scoped agence) et hôtes anti-bot."""

from __future__ import annotations

from urllib.parse import urlparse

PORTAL_IDS = (
    # ─── Premium / anti-bot fort (offre payante à venir — pas crawlés par défaut) ───
    "leboncoin",
    "seloger",
    "logicimmo",
    "bienici",
    # ─── Recommandés (HTML serveur, sans anti-bot fort — crawlés par « Crawler tout ») ───
    "pap",
    "paruvendu",
    "lefigaro",
    "superimmo",
    "avendrealouer",
    "etreproprio",
    "maisonappart",
    "ouestfranceimmo",
    "lesiteimmo",
    "notaires",
    "entreparticuliers",
    "immonot",
    "acheterlouer",
    "century21",
    "orpi",
)

# ─── Source unique de vérité : portails « premium » (anti-bot fort, DataDome /
# Cloudflare). Réservés à une offre payante ultérieure : on les garde visibles
# mais on ne les crawl PAS par défaut (ni dans « Crawler tout », ni en unitaire). ───
PREMIUM_PORTAL_IDS = (
    "leboncoin",
    "seloger",
    "logicimmo",
    "bienici",
)

# Portails payants / anti-bot fort — Playwright souvent requis.
PROTECTED_HOSTS = (
    "leboncoin.fr",
    "seloger.com",
    "logic-immo.com",
    "bienici.com",
)


def is_premium_portal_id(source_id: str | None) -> bool:
    """True si la source est un portail premium / anti-bot (offre à venir)."""
    base = resolve_base_portal_id(source_id)
    return bool(base and base in PREMIUM_PORTAL_IDS)


def resolve_base_portal_id(source_id: str | None) -> str | None:
    """`abc123_leboncoin` → `leboncoin`, ou `leboncoin` → `leboncoin`."""
    sid = (source_id or "").lower().strip()
    if not sid:
        return None
    for pid in PORTAL_IDS:
        if sid == pid or sid.endswith(f"_{pid}"):
            return pid
    return None


def host_from_url(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def url_needs_browser(url: str) -> bool:
    host = host_from_url(url)
    if any(h in host for h in PROTECTED_HOSTS):
        return True
    from crawler.host_discovery import host_needs_browser

    return host_needs_browser(url)


def portal_from_url(url: str) -> str | None:
    host = host_from_url(url)
    for pid in PORTAL_IDS:
        key = pid.replace("logicimmo", "logic-immo")
        if key in host or pid in host:
            return pid
    return None
