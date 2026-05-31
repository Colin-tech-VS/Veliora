"""Contournement anti-bot — curl_cffi (TLS Chrome) + cookies partagés avec Playwright."""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from typing import Any

from crawler.config import (
    ANTIBOT_CHALLENGE_WAIT_MS,
    CURL_CFFI_IMPERSONATE,
    USE_CURL_CFFI,
)
from crawler.errors import CrawlError

logger = logging.getLogger(__name__)

_thread = threading.local()

BLOCKED_PATTERNS = [
    r"cf-browser-verification",
    r"challenge-platform",
    r"Attention Required",
    r"Access denied",
    r"Just a moment",
    r"cf-challenge",
    r"turnstile",
    r"/cdn-cgi/challenge",
    r"unusual traffic",
    r"captcha",
    r"are you a robot",
    r"datadome",
    r"perimeterx",
    r"px-captcha",
    r"bot.?detect",
    r"blocked",
    r"ray id",
    # Blocages / rate-limit en français
    r"acc[èe]s\s+temporairement\s+restreint",
    r"acc[èe]s\s+restreint",
    r"temporairement\s+indisponible",
    r"trop\s+de\s+requ[êe]tes",
    r"too\s+many\s+requests",
    r"vous\s+avez\s+[ée]t[ée]\s+bloqu",
]

BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

REFERERS = [
    "https://www.google.fr/",
    "https://www.google.com/",
    "https://duckduckgo.com/",
]

STEALTH_INIT_SCRIPT = """
(() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'] });
  Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
  Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
  window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
  };
})();
"""


STRONG_BLOCK_PATTERNS = [
    r"just a moment",
    r"cf-browser-verification",
    r"attention required",
    r"access denied",
    r"enable javascript",
    r"checking your browser",
    # Cloudflare / challenge en français
    r"v[ée]rification de s[ée]curit[ée]",
    r"v[ée]rifie que vous n.?[ée]tes pas un bot",
    r"challenges\.cloudflare\.com",
    r"un instant",
    # DataDome / rate-limit IP (LeBonCoin notamment)
    r"acc[èe]s\s+temporairement\s+restreint",
    r"acc[èe]s\s+restreint",
    r"trop\s+de\s+requ[êe]tes",
]


def is_blocked_html(html: str, min_len: int = 800) -> bool:
    if not html or len(html) < min_len:
        return True

    head = html[:14_000].lower()
    body_hint = html[:80_000].lower()

    for p in STRONG_BLOCK_PATTERNS:
        if re.search(p, head, re.I):
            return True

    # Grande page immo = souvent OK même si scripts mentionnent "captcha"
    if len(html) > 60_000 and any(
        k in body_hint for k in ("annonce", "immobilier", "vente", "appartement", "maison", "listing")
    ):
        strong_in_head = sum(1 for p in BLOCKED_PATTERNS if re.search(p, head, re.I))
        return strong_in_head >= 2

    return any(re.search(p, head, re.I) for p in BLOCKED_PATTERNS)


def apply_playwright_stealth(page) -> None:
    """Applique playwright-stealth si installé."""
    try:
        from playwright_stealth import stealth_sync

        stealth_sync(page)
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("playwright-stealth: %s", exc)


def challenge_wait_seconds() -> float:
    return ANTIBOT_CHALLENGE_WAIT_MS / 1000.0


_curl_available: bool | None = None


def curl_cffi_available() -> bool:
    global _curl_available
    if _curl_available is not None:
        return _curl_available
    if not USE_CURL_CFFI:
        _curl_available = False
        return False
    try:
        from curl_cffi import requests as _curl  # noqa: F401

        _curl_available = True
    except ImportError:
        _curl_available = False
    return _curl_available


def _get_curl_session():
    from curl_cffi.requests import Session

    from crawler.config import pick_proxy

    session = getattr(_thread, "curl_session", None)
    if session is None:
        kwargs = {"impersonate": CURL_CFFI_IMPERSONATE}
        proxy = pick_proxy()
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}
        session = Session(**kwargs)
        _thread.curl_session = session
    return session


def sync_cookies_from_playwright(context) -> None:
    """Réutilise les cookies Playwright dans curl_cffi (même thread)."""
    if not curl_cffi_available():
        return
    try:
        cookies = context.cookies()
    except Exception:
        return
    jar: dict[str, dict[str, str]] = getattr(_thread, "cookie_jar", None) or {}
    for c in cookies:
        domain = (c.get("domain") or "").lstrip(".")
        if not domain:
            continue
        jar.setdefault(domain, {})[c["name"]] = c["value"]
    _thread.cookie_jar = jar
    session = _get_curl_session()
    for c in cookies:
        try:
            session.cookies.set(
                c["name"],
                c["value"],
                domain=c.get("domain"),
                path=c.get("path", "/"),
            )
        except Exception:
            pass


def curl_fetch(url: str, *, referer: str | None = None) -> tuple[str | None, str | None]:
    """
    GET via curl_cffi (empreinte TLS Chrome).
    Retourne (html, error_detail) — html None si échec.
    """
    if not curl_cffi_available():
        return None, "curl_cffi non installé"

    headers = {**BROWSER_HEADERS}
    headers["Referer"] = referer or random.choice(REFERERS)

    try:
        session = _get_curl_session()
        resp = session.get(
            url,
            headers=headers,
            timeout=35,
            allow_redirects=True,
        )
        if resp.status_code == 403:
            return None, "HTTP 403"
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        html = resp.text or ""
        if is_blocked_html(html):
            return None, f"page bloquée ({len(html)} o)"
        return html, None
    except Exception as exc:
        logger.debug("curl_cffi %s: %s", url[:60], exc)
        return None, str(exc)[:200]


def clear_antibot_state() -> None:
    _thread.curl_session = None
    _thread.cookie_jar = None
