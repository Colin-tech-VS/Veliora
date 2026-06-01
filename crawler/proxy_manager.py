"""Rotation de proxy à chaque session de crawl (nouvelle IP / session navigateur)."""

from __future__ import annotations

import logging
import random
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_rr_index = 0
_session_proxy: str | None = None


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


def begin_crawl_session(*, force_new: bool = True) -> str | None:
    """Choisit un proxy (round-robin), ferme le navigateur pour repartir sur une nouvelle IP."""
    global _session_proxy, _rr_index
    from crawler.config import CRAWL_PROXIES

    if not CRAWL_PROXIES:
        _session_proxy = None
        return None

    from crawler.browser import close_browser_session

    if force_new:
        close_browser_session()

    with _lock:
        if force_new or _session_proxy is None:
            _rr_index += 1
            _session_proxy = CRAWL_PROXIES[_rr_index % len(CRAWL_PROXIES)]
            logger.info(
                "Proxy crawl #%s/%s",
                (_rr_index - 1) % len(CRAWL_PROXIES) + 1,
                len(CRAWL_PROXIES),
            )
    return _session_proxy


def end_crawl_session() -> None:
    global _session_proxy
    _session_proxy = None


def pick_proxy() -> str | None:
    """Proxy actif pour cette session de crawl, sinon tirage aléatoire."""
    from crawler.config import CRAWL_PROXIES

    if not CRAWL_PROXIES:
        return None
    if _session_proxy:
        return _session_proxy
    return random.choice(CRAWL_PROXIES)


def proxies_enabled() -> bool:
    from crawler.config import CRAWL_PROXIES

    return bool(CRAWL_PROXIES)
