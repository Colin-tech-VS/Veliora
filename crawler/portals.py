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

# Anti-bot / Cloudflare / DataDome — PAS encore crawlés (classés « Bientôt disponible »).
# Ce n'est pas une offre payante : le crawl de ces portails n'est juste pas activé
# pour le moment (ils exigent des proxys résidentiels). On les garde hors crawl.
COMING_SOON_PORTAL_IDS = frozenset({
    "leboncoin",
    "pap",
    "seloger",
    "logicimmo",
    "bienici",
})

# Hôtes qui nécessitent Playwright ou sont bloqués en HTTP simple.
PROTECTED_HOSTS = (
    "leboncoin.fr",
    "pap.fr",
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


def is_coming_soon_portal(portal_id: str | None) -> bool:
    """Portail anti-bot non encore crawlé (classé « Bientôt disponible »)."""
    base = resolve_base_portal_id(portal_id or "")
    return bool(base and base in COMING_SOON_PORTAL_IDS)


def is_coming_soon_url(url: str) -> bool:
    pid = portal_from_url(url)
    return bool(pid and is_coming_soon_portal(pid))


# Rétrocompat : anciens noms (offre payante) → nouvelle sémantique « bientôt ».
PAID_CRAWL_PORTAL_IDS = COMING_SOON_PORTAL_IDS
is_paid_crawl_portal = is_coming_soon_portal
is_paid_crawl_url = is_coming_soon_url


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
