"""Rotation de proxy — par job, par portail, et dès qu'un site bloque (anti-bot)."""

from __future__ import annotations

import logging
import random
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rr_index = 0
_session_proxy: str | None = None
_block_rotation_count = 0


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
    from crawler.config import CRAWL_PROXIES

    return bool(CRAWL_PROXIES)


def max_rotations_on_block() -> int:
    """Nombre max de changements d'IP après un blocage sur une même URL."""
    from crawler.config import CRAWL_PROXIES, CRAWL_PROXY_ROTATE_ON_BLOCK

    if not CRAWL_PROXY_ROTATE_ON_BLOCK or not CRAWL_PROXIES:
        return 0
    n = len(CRAWL_PROXIES)
    # Passerelle rotative (1 URL) : plusieurs resets session ; liste : 1 essai par proxy
    return max(3, min(n * 2, 12)) if n == 1 else max(1, min(n, 10))


def reset_block_rotation_counter() -> None:
    global _block_rotation_count
    _block_rotation_count = 0


def _advance_proxy() -> str | None:
    global _session_proxy, _rr_index
    from crawler.config import CRAWL_PROXIES

    if not CRAWL_PROXIES:
        _session_proxy = None
        return None
    _rr_index += 1
    _session_proxy = CRAWL_PROXIES[_rr_index % len(CRAWL_PROXIES)]
    return _session_proxy


def begin_crawl_session(*, force_new: bool = True) -> str | None:
    """Nouveau proxy en début de job / portail."""
    global _block_rotation_count
    if not proxies_enabled():
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

    from crawler.browser import close_browser_session
    from crawler.antibot import clear_antibot_state

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
    from crawler.config import CRAWL_PROXIES

    if not CRAWL_PROXIES:
        return None
    if _session_proxy:
        return _session_proxy
    return random.choice(CRAWL_PROXIES)
