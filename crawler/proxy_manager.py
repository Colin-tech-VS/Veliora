"""Rotation de proxy — par job, par portail, et dès qu'un site bloque (anti-bot).

Deux NIVEAUX d'IP, choisis automatiquement selon le site crawlé :

  • PREMIUM (CRAWL_PROXIES, ex. Decodo résidentiel) → réservé aux GROS portails
    anti-bot : leboncoin, seloger, pap, logic-immo, bienici. La bande passante
    résidentielle est payante : on ne la gaspille pas sur les petits sites.

  • GRATUIT (pool public auto, CRAWL_AUTO_FREE_PROXIES) → petits sites / agences.
    Rotation à chaque portail + sur blocage. Si le pool gratuit est vide, on crawle
    en IP serveur directe (les petits sites ne bannissent quasiment jamais).

Le niveau est fixé au début de chaque session de crawl (begin_crawl_session) à partir
de l'URL ou de l'ID de portail. La rotation sur blocage reste DANS le même niveau
(un petit site bloqué ne consomme jamais de crédits Decodo).
"""

from __future__ import annotations

import logging
import random
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rr_index = 0
_session_proxy: str | None = None
_session_tier: str = "free"  # "premium" | "free"
_block_rotation_count = 0
_auto_pool: list[str] = []
_pool_warm_started = False


# ─────────────────────────── Sources d'IP ───────────────────────────


def _premium_proxies() -> list[str]:
    """Proxies payants dédiés (CRAWL_PROXIES) — ex. Decodo résidentiel."""
    from crawler.config import CRAWL_PROXIES

    return CRAWL_PROXIES


def _free_proxies() -> list[str]:
    """Pool public gratuit déjà chargé (CRAWL_AUTO_FREE_PROXIES)."""
    return _auto_pool


def _pool_for_tier(tier: str) -> list[str]:
    return _premium_proxies() if tier == "premium" else _free_proxies()


def _env_proxies() -> list[str]:
    return _premium_proxies()


def reload_proxies_from_env() -> list[str]:
    """Recharge la liste depuis l'environnement (après mise à jour .env)."""
    import os

    from crawler import config as cfg

    proxies = [
        p.strip()
        for p in os.getenv("CRAWL_PROXIES", os.getenv("CRAWL_PROXY", "")).split(",")
        if p.strip()
    ]
    cfg.CRAWL_PROXIES = proxies
    return proxies


def proxies_enabled() -> bool:
    """True si une rotation est possible (pool premium OU gratuit disponible)."""
    return bool(_premium_proxies() or _free_proxies())


# ──────────────────── Choix premium (gros sites) vs gratuit ────────────────────


def target_needs_premium(*, url: str | None = None, source_id: str | None = None) -> bool:
    """True pour les gros portails anti-bot (Decodo conseillé), False sinon."""
    try:
        if url:
            from crawler.portals import url_needs_browser

            if url_needs_browser(url):
                return True
        if source_id:
            from crawler.portals import COMING_SOON_PORTAL_IDS, resolve_base_portal_id

            base = resolve_base_portal_id(source_id)
            if base and base in COMING_SOON_PORTAL_IDS:
                return True
    except Exception:
        logger.debug("target_needs_premium : détection échouée", exc_info=True)
    return False


def ensure_proxy_pool() -> int:
    """Charge le pool GRATUIT si activé (pour les petits sites).

    Le pool premium (CRAWL_PROXIES) ne nécessite aucun chargement : il est fourni
    directement par l'environnement.
    """
    global _auto_pool
    from crawler.config import CRAWL_AUTO_FREE_PROXIES

    if CRAWL_AUTO_FREE_PROXIES and not _auto_pool:
        from crawler.free_proxies import get_free_proxies

        pool = get_free_proxies()
        with _lock:
            _auto_pool = pool
        if pool:
            logger.info(
                "Pool IP gratuit prêt : %d proxy(s) testés (petits sites).", len(pool)
            )
        else:
            logger.warning(
                "Aucun proxy gratuit fonctionnel — petits sites crawlés en IP serveur."
            )
    return len(_premium_proxies()) + len(_auto_pool)


def warm_proxy_pool_async() -> None:
    """Précharge le pool gratuit au boot serveur (non bloquant)."""
    global _pool_warm_started
    from crawler.config import CRAWL_AUTO_FREE_PROXIES

    if _pool_warm_started or not CRAWL_AUTO_FREE_PROXIES:
        return
    _pool_warm_started = True

    def _run() -> None:
        try:
            ensure_proxy_pool()
        except Exception:
            logger.exception("Préchargement pool proxies")

    threading.Thread(target=_run, name="veliora-proxy-warm", daemon=True).start()


def max_rotations_on_block() -> int:
    """Nombre max de changements d'IP après un blocage — dans le niveau courant."""
    from crawler.config import CRAWL_PROXY_ROTATE_ON_BLOCK

    proxies = _pool_for_tier(_session_tier)
    if not CRAWL_PROXY_ROTATE_ON_BLOCK or not proxies:
        return 0
    n = len(proxies)
    return max(3, min(n * 2, 12)) if n == 1 else max(1, min(n, 10))


def reset_block_rotation_counter() -> None:
    global _block_rotation_count
    _block_rotation_count = 0


def _advance_proxy() -> str | None:
    """Avance dans le pool du NIVEAU courant (jamais de mélange premium/gratuit)."""
    global _session_proxy, _rr_index
    proxies = _pool_for_tier(_session_tier)

    if not proxies:
        _session_proxy = None
        return None
    _rr_index += 1
    _session_proxy = proxies[_rr_index % len(proxies)]
    return _session_proxy


def begin_crawl_session(
    *,
    force_new: bool = True,
    url: str | None = None,
    source_id: str | None = None,
) -> str | None:
    """Démarre une session : choisit le niveau (premium/gratuit) selon le site.

    - Gros portail anti-bot (et CRAWL_PROXIES défini) → IP Decodo premium.
    - Petit site → IP gratuite si pool dispo, sinon IP serveur directe.
    """
    global _block_rotation_count, _session_tier

    ensure_proxy_pool()

    want_premium = target_needs_premium(url=url, source_id=source_id) and bool(
        _premium_proxies()
    )
    tier = "premium" if want_premium else "free"

    from crawler.browser import close_browser_session

    if force_new:
        close_browser_session()
        from crawler.antibot import clear_antibot_state

        clear_antibot_state()

    with _lock:
        _session_tier = tier
        if not _pool_for_tier(tier):
            # Petit site sans pool gratuit → IP serveur directe (pas de Decodo gaspillé).
            _session_proxy = None
            return None
        if force_new or _session_proxy is None:
            px = _advance_proxy()
            if px:
                host = px.split("@")[-1] if "@" in px else px
                label = "premium/Decodo" if tier == "premium" else "gratuit"
                logger.info("Proxy crawl (%s) → %s", label, host)
            return px
    return _session_proxy


def rotate_proxy_on_block(reason: str = "anti-bot") -> str | None:
    """Change d'IP dès qu'un portail bloque — dans le même niveau que la session."""
    global _block_rotation_count
    from crawler.config import CRAWL_PROXY_ROTATE_ON_BLOCK

    ensure_proxy_pool()
    if not CRAWL_PROXY_ROTATE_ON_BLOCK or not _pool_for_tier(_session_tier):
        return None
    if _block_rotation_count >= max_rotations_on_block():
        logger.warning("Rotation proxy max atteinte (%s)", reason)
        return None

    from crawler.antibot import clear_antibot_state
    from crawler.browser import close_browser_session

    close_browser_session()
    clear_antibot_state()

    with _lock:
        _block_rotation_count += 1
        px = _advance_proxy()
        if px:
            host = px.split("@")[-1] if "@" in px else px
            label = "premium/Decodo" if _session_tier == "premium" else "gratuit"
            logger.warning(
                "Blocage crawl — nouvelle IP %s (%s/%s) → %s",
                label,
                _block_rotation_count,
                max_rotations_on_block(),
                host,
            )
        return px


def end_crawl_session() -> None:
    global _session_proxy, _block_rotation_count, _session_tier
    _session_proxy = None
    _block_rotation_count = 0
    _session_tier = "free"


def pick_proxy() -> str | None:
    """Proxy courant pour curl_cffi / Playwright.

    En session, renvoie le proxy choisi pour le niveau (premium/gratuit). Hors
    session, ne force pas Decodo (IP gratuite ou serveur) pour préserver les crédits.
    """
    from crawler.config import CRAWL_AUTO_FREE_PROXIES

    if CRAWL_AUTO_FREE_PROXIES and not _auto_pool:
        ensure_proxy_pool()
    if _session_proxy:
        return _session_proxy
    pool = _free_proxies()
    return random.choice(pool) if pool else None
