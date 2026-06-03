"""Pool de proxies HTTP publics — secours automatique quand CRAWL_PROXIES est vide.

Récupère des listes de proxies gratuits depuis plusieurs sources publiques, les
teste réellement (connexion HTTPS via ipify) et ne garde que ceux qui répondent.

⚠️ Best-effort : utile contre le rate-limit / bannissement par IP (la rotation
laisse repartir le crawl), mais des proxies gratuits ne suffisent pas seuls
contre DataDome / Cloudflare avancés. Dès que de vrais proxies résidentiels sont
fournis via CRAWL_PROXIES, ceux-ci prennent le dessus (ce module n'est plus utilisé).
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# Sources publiques (texte brut : une entrée ip:port par ligne)
FREE_PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=8000&country=all&ssl=yes&anonymity=all",
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
]

# Cible : on s'arrête dès qu'on a assez de proxies fonctionnels
TARGET_WORKING = 20
MAX_CANDIDATES_TESTED = 140
TEST_TIMEOUT_SEC = 6
TEST_WORKERS = 40
CACHE_TTL_SEC = 600  # 10 min

_IPPORT_RE = re.compile(r"(?:https?://)?(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})")

_lock = threading.Lock()
_cache: list[str] = []
_cache_at: float = 0.0


def _fetch_source(url: str) -> list[str]:
    try:
        import requests

        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        if not resp.ok:
            return []
        out: list[str] = []
        for m in _IPPORT_RE.finditer(resp.text):
            out.append(f"http://{m.group(1)}:{m.group(2)}")
        return out
    except Exception as exc:
        logger.debug("source proxies %s: %s", url[:50], exc)
        return []


def _gather_candidates() -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(FREE_PROXY_SOURCES)) as ex:
        for proxies in ex.map(_fetch_source, FREE_PROXY_SOURCES):
            for p in proxies:
                if p not in seen:
                    seen.add(p)
                    candidates.append(p)
    import random

    random.shuffle(candidates)
    return candidates


def _test_proxy(url: str) -> str | None:
    try:
        import requests

        resp = requests.get(
            "https://api.ipify.org?format=text",
            proxies={"http": url, "https": url},
            timeout=TEST_TIMEOUT_SEC,
        )
        body = (resp.text or "").strip()
        if resp.ok and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", body):
            return url
    except Exception:
        pass
    return None


def _build_pool() -> list[str]:
    candidates = _gather_candidates()
    if not candidates:
        logger.warning("Aucune source de proxies gratuits n'a répondu.")
        return []

    candidates = candidates[:MAX_CANDIDATES_TESTED]
    working: list[str] = []
    started = time.time()
    logger.info("Test de %d proxies gratuits (cible %d)…", len(candidates), TARGET_WORKING)

    with concurrent.futures.ThreadPoolExecutor(max_workers=TEST_WORKERS) as ex:
        futures = {ex.submit(_test_proxy, p): p for p in candidates}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if res:
                working.append(res)
                if len(working) >= TARGET_WORKING:
                    break
        for fut in futures:
            fut.cancel()

    logger.info(
        "Proxies gratuits fonctionnels : %d/%d en %.1fs",
        len(working),
        len(candidates),
        time.time() - started,
    )
    return working


def get_free_proxies(force_refresh: bool = False) -> list[str]:
    """Retourne une liste de proxies gratuits testés (cache 10 min)."""
    global _cache, _cache_at
    with _lock:
        fresh = (time.time() - _cache_at) < CACHE_TTL_SEC
        if _cache and fresh and not force_refresh:
            return list(_cache)
        pool = _build_pool()
        if pool:
            _cache = pool
            _cache_at = time.time()
        elif _cache and fresh:
            # Échec de rafraîchissement : on garde l'ancien pool encore valide
            return list(_cache)
        return list(pool)
