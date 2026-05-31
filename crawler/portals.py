"""Résolution portail (IDs scoped agence) et hôtes anti-bot."""

from __future__ import annotations

from urllib.parse import urlparse

PORTAL_IDS = (
    "leboncoin",
    "pap",
    "seloger",
    "logicimmo",
    "bienici",
    "paruvendu",
    "lefigaro",
    "superimmo",
    "avendrealouer",
    "etreproprio",
    "maisonappart",
    "ouestfranceimmo",
    "lesiteimmo",
    "notaires",
)

# Portails payants / anti-bot fort — Playwright souvent requis.
PROTECTED_HOSTS = (
    "leboncoin.fr",
    "seloger.com",
    "logic-immo.com",
    "bienici.com",
)


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
