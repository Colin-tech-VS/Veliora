"""Rotation de proxy — par job, par portail, et dès qu'un site bloque (anti-bot).

Deux sources d'IP :
  1. CRAWL_PROXIES (env) — proxies fournis (idéalement résidentiels rotatifs).
  2. Pool auto-gratuit — chargé à la volée au 1ᵉʳ blocage si CRAWL_AUTO_FREE_PROXIES
     est actif et qu'aucun proxy n'est configuré. Consulté UNIQUEMENT en rotation
     de blocage : le début de crawl reste en direct (rapide) tant qu'aucun portail
     ne bloque.
"""

from __future__ import annotations

import logging
import random
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rr_index = 0
_session_proxy: str | None = None
_block_rotation_count = 0
# Pool de secours (proxies publics testés), rempli paresseusement au blocage.
_auto_pool: list[str] = []


def _env_proxies() -> list[str]:
    from crawler.config import CRAWL_PROXIES

    return CRAWL_PROXIES


def _effective_proxies() -> list[str]:
    """IP fournies si présentes, sinon le pool auto-gratuit déjà chargé."""
    env = _env_proxies()
    return env if env else _auto_pool


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
    return bool(_effective_proxies())


def ensure_proxy_pool() -> int:
    """Garantit qu'un pool d'IP est disponible pour la rotation.

    - CRAWL_PROXIES défini → on l'utilise tel quel.
    - Sinon, si CRAWL_AUTO_FREE_PROXIES actif → on récupère (paresseusement, en
      cache) un pool de proxies publics testés.

    Retourne le nombre d'IP disponibles.
    """
    global _auto_pool
    from crawler.config import CRAWL_AUTO_FREE_PROXIES

    env = _env_proxies()
    if env:
        return len(env)
    if not CRAWL_AUTO_FREE_PROXIES:
        return 0
    if _auto_pool:
        return len(_auto_pool)

    from crawler.free_proxies import get_free_proxies

    pool = get_free_proxies()
    with _lock:
        _auto_pool = pool
    if pool:
        logger.warning("Pool de proxies gratuits chargé : %d IP testées (rotation active).", len(pool))
    else:
        logger.warning("Aucun proxy gratuit fonctionnel — rotation impossible pour ce blocage.")
    return len(pool)


def max_rotations_on_block() -> int:
    """Nombre max de changements d'IP après un blocage sur une même URL."""
    from crawler.config import CRAWL_PROXY_ROTATE_ON_BLOCK

    proxies = _effective_proxies()
    if not CRAWL_PROXY_ROTATE_ON_BLOCK or not proxies:
        return 0
    n = len(proxies)
    # Passerelle rotative (1 URL) : plusieurs resets session ; liste : 1 essai par proxy
    return max(3, min(n * 2, 12)) if n == 1 else max(1, min(n, 10))


def reset_block_rotation_counter() -> None:
    global _block_rotation_count
    _block_rotation_count = 0


def _advance_proxy() -> str | None:
    global _session_proxy, _rr_index
    proxies = _effective_proxies()

    if not proxies:
        _session_proxy = None
        return None
    _rr_index += 1
    _session_proxy = proxies[_rr_index % len(proxies)]
    return _session_proxy


def begin_crawl_session(*, force_new: bool = True) -> str | None:
    """Nouveau proxy en début de job / portail.

    N'agit que si des proxies sont *fournis* (CRAWL_PROXIES) : le pool auto-gratuit
    n'est pas utilisé ici pour garder un démarrage direct/rapide — il n'entre en jeu
    qu'à la rotation sur blocage.
    """
    global _block_rotation_count
    if not _env_proxies():
        return None

    from crawler.browser import close_browser_session

    if force_new:
        close_browser_session()
        from crawler.antibot import clear_antibot_state

        clear_antibot_state()

    with _lock:
        if force_new or _session_proxy is None:
            px = _advance_proxy()
            if px:
                host = px.split("@")[-1] if "@" in px else px
                logger.info("Proxy crawl (session) → %s", host)
            return px
    return _session_proxy


def rotate_proxy_on_block(reason: str = "anti-bot") -> str | None:
    """Change d'IP dès qu'un portail bloque — reset navigateur + curl."""
    global _block_rotation_count
    from crawler.config import CRAWL_PROXY_ROTATE_ON_BLOCK

    if not CRAWL_PROXY_ROTATE_ON_BLOCK or not proxies_enabled():
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
            logger.warning(
                "Blocage crawl — nouvelle IP (%s/%s) → %s",
                _block_rotation_count,
                max_rotations_on_block(),
                host,
            )
        return px


def end_crawl_session() -> None:
    global _session_proxy, _block_rotation_count
    _session_proxy = None
    _block_rotation_count = 0


def pick_proxy() -> str | None:
    proxies = _effective_proxies()
    if not proxies:
        return None
    if _session_proxy:
        return _session_proxy
    return random.choice(proxies)
