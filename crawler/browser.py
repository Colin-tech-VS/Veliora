"""Fetch pages — curl_cffi (TLS Chrome) + Playwright furtif + repli Chrome visible."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from crawler.human import micro_pause, thinking_pause
from crawler.antibot import (
    BROWSER_HEADERS,
    STEALTH_INIT_SCRIPT,
    apply_playwright_stealth,
    challenge_wait_seconds,
    clear_antibot_state,
    curl_cffi_available,
    curl_fetch,
    is_blocked_html,
    sync_cookies_from_playwright,
)
from crawler.config import (
    PLAYWRIGHT_HEADED_FALLBACK,
    PLAYWRIGHT_PROFILE_DIR,
    PLAYWRIGHT_RETRIES,
    PLAYWRIGHT_TIMEOUT_MS,
)
from crawler.errors import CrawlError

logger = logging.getLogger(__name__)

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
]

CONTACT_SELECTORS = [
    'button:has-text("Voir le numéro")',
    'button:has-text("Afficher le numéro")',
    'button:has-text("Voir le téléphone")',
    'button:has-text("Afficher le téléphone")',
    'button:has-text("Contacter")',
    'a:has-text("Voir le numéro")',
    'a:has-text("Afficher le téléphone")',
    '[data-testid*="phone"]',
    '[data-qa-id*="phone"]',
    '[data-qa-id="adview_phone_number"]',
    'button[data-test-id="phone-button"]',
    'button[data-qa-id*="phone"]',
    '[aria-label*="numéro"]',
    '[aria-label*="téléphone"]',
    ".btn-phone",
    ".PhoneNumberButton",
    "#voir-numero",
    'a[href^="tel:"]',
]

COOKIE_SELECTORS = [
    'button:has-text("Tout accepter")',
    'button:has-text("Accepter et fermer")',
    'button:has-text("Accepter")',
    'button:has-text("Accept")',
    'button:has-text("J\'accepte")',
    'button:has-text("Continuer sans accepter")',
    "#didomi-notice-agree-button",
    ".didomi-continue-without-agreeing",
    "[id*=accept-cookies]",
    "[class*=accept-cookie]",
    '[aria-label*="Accepter"]',
]

_playwright_ready: bool | None = None
_thread_local = threading.local()


@dataclass
class FetchResult:
    html: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    method: str = "none"

    @property
    def ok(self) -> bool:
        return bool(self.html)


def _profile_path() -> Path:
    base = Path(__file__).resolve().parent.parent / PLAYWRIGHT_PROFILE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _playwright_proxy() -> dict | None:
    """Proxy au format Playwright depuis CRAWL_PROXIES (rotation), ou None."""
    from urllib.parse import unquote, urlparse

    from crawler.config import pick_proxy

    raw = pick_proxy()
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname:
        return None
    server = f"{u.scheme or 'http'}://{u.hostname}"
    if u.port:
        server += f":{u.port}"
    proxy: dict[str, str] = {"server": server}
    if u.username:
        proxy["username"] = unquote(u.username)
    if u.password:
        proxy["password"] = unquote(u.password)
    return proxy


# ─── Capture live (heatmap réel : ce que voit le crawler) ───
LIVE_FRAME_PATH = Path(__file__).resolve().parent.parent / "data" / "crawl_live.jpg"
_live_last_capture = 0.0
_LIVE_MIN_INTERVAL = 1.1  # throttle : 1 capture / ~1.1 s max (coûteux)


def capture_live_frame(page) -> None:
    """Écrit un JPEG de la fenêtre courante du crawler (throttlé, écriture atomique)."""
    global _live_last_capture
    now = time.time()
    if now - _live_last_capture < _LIVE_MIN_INTERVAL:
        return
    _live_last_capture = now
    try:
        LIVE_FRAME_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIVE_FRAME_PATH.with_suffix(".tmp.jpg")
        page.screenshot(path=str(tmp), type="jpeg", quality=50, full_page=False, timeout=4000)
        import os as _os

        _os.replace(tmp, LIVE_FRAME_PATH)
    except Exception:
        pass


class _PlaywrightSession:
    """Navigateur persistant — profil, stealth, challenges, mode visible en secours."""

    def __init__(self, *, headless: bool = True) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._user_agent = random.choice(USER_AGENTS)
        self._headless = headless
        self._headed_tried = False

    def _launch_args(self) -> list[str]:
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-infobars",
            "--window-size=1366,900",
            "--lang=fr-FR",
            "--disable-features=IsolateOrigins,site-per-process",
        ]

    def _create_context(self, p):
        profile = str(_profile_path())
        args = self._launch_args()
        headless = self._headless
        proxy = _playwright_proxy()
        proxy_kw = {"proxy": proxy} if proxy else {}

        try:
            ctx = p.chromium.launch_persistent_context(
                profile,
                channel="chrome",
                headless=headless,
                user_agent=self._user_agent,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1366, "height": 900},
                extra_http_headers=BROWSER_HEADERS,
                args=args,
                ignore_default_args=["--enable-automation"],
                **proxy_kw,
            )
            return ctx, None
        except Exception:
            browser = p.chromium.launch(
                channel="chrome",
                headless=headless,
                args=args + ([] if headless else []),
                **proxy_kw,
            )
            ctx = browser.new_context(
                user_agent=self._user_agent,
                locale="fr-FR",
                timezone_id="Europe/Paris",
                viewport={"width": 1366, "height": 900},
                extra_http_headers=BROWSER_HEADERS,
            )
            return ctx, browser

    def _session_alive(self) -> bool:
        """Le contexte/page Playwright est-il encore utilisable ?"""
        try:
            if self._context is None or self._page is None:
                return False
            if self._page.is_closed():
                return False
            if self._browser is not None and not self._browser.is_connected():
                return False
            return True
        except Exception:
            return False

    def _ensure_context(self):
        if self._context is not None:
            if self._session_alive():
                return self._page
            # Contexte/navigateur mort (crash, fermeture) → on reconstruit proprement
            logger.info("Session Playwright fermée — reconstruction du contexte")
            self.close()

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._context, self._browser = self._create_context(self._playwright)
        self._context.add_init_script(STEALTH_INIT_SCRIPT)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        from crawler.config import active_speed_preset

        p = active_speed_preset()
        self._page_timeout_ms = int(p.get("playwright_timeout_ms", PLAYWRIGHT_TIMEOUT_MS))
        self._page_retries = int(p.get("playwright_retries", PLAYWRIGHT_RETRIES))
        self._page.set_default_timeout(self._page_timeout_ms)
        apply_playwright_stealth(self._page)
        return self._page

    def _switch_to_headed(self) -> bool:
        if self._headed_tried or not PLAYWRIGHT_HEADED_FALLBACK:
            return False
        self._headed_tried = True
        logger.info("Anti-bot : passage en Chrome visible (headless bloqué)")
        self.close()
        self._headless = False
        self._ensure_context()
        return True

    def _wait_rendered_content(self, page, *, min_chars: int = 400, timeout_ms: int | None = None) -> None:
        """SPA / anti-bot : attendre du texte visible (évite captures et HTML vides)."""
        from crawler.config import active_speed_preset

        if timeout_ms is None:
            timeout_ms = int(active_speed_preset().get("content_wait_ms", 12_000))
        try:
            page.wait_for_function(
                "() => document.body && (document.body.innerText || '').trim().length >= "
                + str(min_chars),
                timeout=timeout_ms,
            )
        except Exception:
            page.wait_for_timeout(600 if min_chars <= 300 else 1200)

    def _page_looks_empty(self, html: str) -> bool:
        if not html:
            return True
        n = len(html)
        if n < 1800:
            return True
        low = html.lower()
        signals = ("annonce", "listing", "property", "recherche", "vente", "appartement", "maison")
        if any(s in low for s in signals):
            return n < 3500
        return n < 6000

    def _dismiss_cookies(self, page) -> None:
        for selector in COOKIE_SELECTORS:
            try:
                loc = page.locator(selector).first
                if loc.is_visible(timeout=600):
                    loc.click(timeout=2000)
                    page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    def _wait_out_challenge(self, page) -> bool:
        deadline = time.time() + challenge_wait_seconds()
        while time.time() < deadline:
            html = page.content()
            if not is_blocked_html(html, min_len=1200):
                return True
            page.wait_for_timeout(1200)
            try:
                page.mouse.move(random.randint(80, 900), random.randint(80, 600))
                page.mouse.wheel(0, random.randint(100, 400))
            except Exception:
                pass
            for sel in ('iframe[src*="challenge"]', "#challenge-stage", ".cf-turnstile"):
                try:
                    frame = page.frame_locator(sel).first
                    frame.locator("body").click(timeout=500)
                except Exception:
                    pass
        return not is_blocked_html(page.content(), min_len=1200)

    def _human_scroll(self, page) -> None:
        try:
            from crawler.config import active_speed_preset

            p = active_speed_preset()
            smin = int(p.get("scroll_min", 5))
            smax = int(p.get("scroll_max", 11))
            scrolls = random.randint(smin, max(smin, smax))
            pause_lo, pause_hi = (120, 400) if scrolls <= 4 else (200, 650)
            for _ in range(scrolls):
                delta = random.randint(280, 920)
                page.evaluate(f"window.scrollBy(0, {delta})")
                page.wait_for_timeout(random.randint(pause_lo, pause_hi))
                capture_live_frame(page)
                if random.random() < 0.12 and scrolls > 4:
                    page.wait_for_timeout(random.randint(500, 1400))
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(250 if scrolls <= 4 else 400)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(120 if scrolls <= 4 else 200)
        except Exception:
            pass

    def _click_contact_buttons(self, page) -> None:
        clicked = 0
        for selector in CONTACT_SELECTORS:
            try:
                locators = page.locator(selector)
                count = min(locators.count(), 3)
                for i in range(count):
                    loc = locators.nth(i)
                    if loc.is_visible(timeout=600):
                        loc.click(timeout=2500)
                        page.wait_for_timeout(900 + clicked * 200)
                        clicked += 1
                        if clicked >= 2:
                            return
            except Exception:
                continue
        if clicked == 0:
            try:
                page.locator('a[href^="tel:"]').first.click(timeout=1500)
                page.wait_for_timeout(500)
            except Exception:
                pass

    def fetch(
        self,
        url: str,
        *,
        scroll_lazy: bool = False,
        click_contacts: bool = False,
        referer: str | None = None,
        fast_mode: bool = False,
    ) -> FetchResult:
        if not _playwright_available():
            return FetchResult(error_code=CrawlError.PLAYWRIGHT_MISSING, method="playwright")

        from playwright.sync_api import TimeoutError as PWTimeout

        last_result = FetchResult(error_code=CrawlError.FETCH_FAILED, method="playwright")
        referers = [
            referer,
            "https://www.google.fr/",
            "https://www.google.com/",
            "https://duckduckgo.com/",
        ]
        referers = [r for r in referers if r]

        from crawler.config import active_speed_preset

        preset = active_speed_preset()
        max_retries = getattr(self, "_page_retries", PLAYWRIGHT_RETRIES)
        if fast_mode:
            max_retries = min(max_retries, 3)
        page_timeout = getattr(self, "_page_timeout_ms", PLAYWRIGHT_TIMEOUT_MS)
        networkidle_ms = int(preset.get("networkidle_ms", 12_000))
        content_min = 280 if fast_mode else 400
        for attempt in range(max_retries):
            try:
                page = self._ensure_context()
                if attempt > 0:
                    time.sleep(0.8 * attempt + random.uniform(0.4, 1.0))

                micro_pause()
                ref = referers[attempt % len(referers)] if referers else None
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    referer=ref,
                    timeout=page_timeout,
                )

                if response and response.status == 403:
                    if not self._wait_out_challenge(page):
                        if self._switch_to_headed():
                            continue
                        last_result = FetchResult(
                            error_code=CrawlError.SITE_BLOCKED,
                            error_detail="HTTP 403",
                            method="playwright",
                        )
                        continue

                elif response and response.status >= 400:
                    last_result = FetchResult(
                        error_code=CrawlError.FETCH_FAILED,
                        error_detail=f"HTTP {response.status}",
                        method="playwright",
                    )
                    continue

                self._dismiss_cookies(page)

                try:
                    page.wait_for_load_state("networkidle", timeout=networkidle_ms)
                except Exception:
                    page.wait_for_timeout(500 + attempt * 200 if fast_mode else 800 + attempt * 300)

                self._wait_rendered_content(page, min_chars=content_min)

                if is_blocked_html(page.content(), min_len=1200):
                    if self._wait_out_challenge(page):
                        pass
                    elif self._switch_to_headed():
                        continue
                    else:
                        last_result = FetchResult(
                            error_code=CrawlError.SITE_BLOCKED,
                            error_detail=f"challenge {attempt + 1}/{PLAYWRIGHT_RETRIES}",
                            method="playwright",
                        )
                        continue

                capture_live_frame(page)

                if scroll_lazy and not fast_mode:
                    self._human_scroll(page)
                    capture_live_frame(page)

                if click_contacts:
                    self._click_contact_buttons(page)
                    page.wait_for_timeout(400)
                    capture_live_frame(page)

                html = page.content()
                if is_blocked_html(html, min_len=800):
                    if self._switch_to_headed():
                        continue
                    last_result = FetchResult(
                        error_code=CrawlError.SITE_BLOCKED,
                        error_detail=f"page courte/bloquée ({len(html)} o)",
                        method="playwright",
                    )
                    continue

                if self._page_looks_empty(html):
                    if attempt + 1 < max_retries:
                        page.wait_for_timeout(800 + attempt * 400)
                        self._wait_rendered_content(page, min_chars=500)
                        html = page.content()
                    if self._page_looks_empty(html) and not fast_mode:
                        if self._switch_to_headed():
                            continue
                    if self._page_looks_empty(html):
                        last_result = FetchResult(
                            error_code=CrawlError.FETCH_FAILED,
                            error_detail=f"page vide ({len(html)} o)",
                            method="playwright",
                        )
                        continue

                sync_cookies_from_playwright(self._context)
                return FetchResult(html=html, method="playwright")

            except PWTimeout:
                last_result = FetchResult(error_code=CrawlError.TIMEOUT, method="playwright")
            except Exception as exc:
                logger.warning("Playwright %s attempt %s: %s", url[:70], attempt + 1, exc)
                last_result = FetchResult(
                    error_code=CrawlError.FETCH_FAILED,
                    error_detail=str(exc)[:200],
                    method="playwright",
                )
                # Contexte/navigateur fermé → on force la reconstruction au prochain essai
                if not self._session_alive():
                    self.close()

        return last_result

    def close(self) -> None:
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


def _get_session() -> _PlaywrightSession:
    session = getattr(_thread_local, "pw_session", None)
    if session is None:
        from crawler.config import PLAYWRIGHT_FORCE_HEADED

        session = _PlaywrightSession(headless=not PLAYWRIGHT_FORCE_HEADED)
        _thread_local.pw_session = session
    return session


def close_browser_session() -> None:
    session = getattr(_thread_local, "pw_session", None)
    if session is not None:
        try:
            session.close()
        except Exception:
            pass
        _thread_local.pw_session = None
    clear_antibot_state()


def _playwright_available() -> bool:
    from crawler.config import CRAWL_PLAYWRIGHT_ENABLED

    if not CRAWL_PLAYWRIGHT_ENABLED:
        return False
    global _playwright_ready
    if _playwright_ready is not None:
        return _playwright_ready
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        _playwright_ready = True
    except ImportError:
        _playwright_ready = False
    return _playwright_ready


def _fetch_requests(url: str, timeout: int = 25) -> FetchResult:
    ua = random.choice(USER_AGENTS)
    from crawler.config import pick_proxy

    proxy = pick_proxy()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={**BROWSER_HEADERS, "User-Agent": ua},
            allow_redirects=True,
            proxies=proxies,
        )
        if resp.status_code == 403:
            return FetchResult(
                error_code=CrawlError.SITE_BLOCKED,
                error_detail="HTTP 403",
                method="requests",
            )
        resp.raise_for_status()
        html = resp.text
        if is_blocked_html(html):
            return FetchResult(
                error_code=CrawlError.SITE_BLOCKED,
                error_detail=f"HTTP {resp.status_code}",
                method="requests",
            )
        return FetchResult(html=html, method="requests")
    except requests.Timeout:
        return FetchResult(error_code=CrawlError.TIMEOUT, method="requests")
    except requests.RequestException as exc:
        return FetchResult(
            error_code=CrawlError.FETCH_FAILED,
            error_detail=str(exc)[:200],
            method="requests",
        )


def prime_protected_sites(targets, on_status=None) -> dict[str, bool]:
    """
    Préchauffe les portails à anti-bot fort dans la session Chrome (visible si bureau).
    Ouvre chaque page de résultats, laisse passer le challenge / pose les cookies de
    session, et retourne {nom: ok}. Réutilisé ensuite par le crawl sans re-challenge.
    """
    results: dict[str, bool] = {}
    if not _playwright_available():
        return results
    session = _get_session()
    for name, url in targets:
        if on_status:
            on_status(name, url)
        try:
            res = session.fetch(url, scroll_lazy=True)
            results[name] = bool(res.ok)
        except Exception as exc:
            logger.warning("Préchauffage %s: %s", name, exc)
            results[name] = False
    return results


def warmup_domain(base_url: str, search_url: str | None = None) -> None:
    """Visite l’accueil du site pour poser cookies / session avant la liste.

    Réservé aux hôtes à anti-bot (DataDome…) : inutile et coûteux sur les sites
    accessibles, on saute pour gagner du temps.
    """
    if not base_url:
        return
    from crawler.portals import url_needs_browser

    if not url_needs_browser(base_url):
        return
    referer = random.choice(
        ["https://www.google.fr/", "https://www.google.com/"]
    )
    html, err = curl_fetch(base_url, referer=referer)
    if html:
        logger.debug("Warmup curl OK — %s", base_url[:50])
        return
    session = _get_session()
    session.fetch(base_url, referer=referer)
    if search_url and search_url != base_url:
        time.sleep(0.5)
        session.fetch(search_url, referer=base_url, scroll_lazy=True)


def _html_has_contact_hints(html: str) -> bool:
    low = (html or "").lower()
    return (
        "tel:" in low
        or "mailto:" in low
        or "data-phone" in low
        or "phone" in low and "@" in low
    )


def _fetch_page_once(
    url: str,
    *,
    scroll_lazy: bool = False,
    click_contacts: bool = False,
    referer: str | None = None,
    prefer_browser: bool = False,
    fast_mode: bool = False,
) -> FetchResult:
    """Un passage fetch (sans rotation proxy)."""
    if not prefer_browser and curl_cffi_available():
        html, detail = curl_fetch(url, referer=referer)
        if html:
            return FetchResult(html=html, method="curl_cffi")

    if _playwright_available():
        session = _get_session()
        result = session.fetch(
            url,
            scroll_lazy=scroll_lazy,
            click_contacts=click_contacts,
            referer=referer,
            fast_mode=fast_mode,
        )
        if result.ok:
            return result

        if not prefer_browser and curl_cffi_available():
            html, _ = curl_fetch(url, referer=referer)
            if html:
                return FetchResult(html=html, method="curl_cffi")

        if result.error_code != CrawlError.PLAYWRIGHT_MISSING:
            fallback = _fetch_requests(url)
            if fallback.ok:
                return fallback
            return result

    if curl_cffi_available():
        html, detail = curl_fetch(url, referer=referer)
        if html:
            return FetchResult(html=html, method="curl_cffi")
        if detail:
            return FetchResult(
                error_code=CrawlError.SITE_BLOCKED,
                error_detail=detail,
                method="curl_cffi",
            )

    return _fetch_requests(url)


def _is_block_result(result: FetchResult) -> bool:
    if result.error_code == CrawlError.SITE_BLOCKED:
        return True
    detail = (result.error_detail or "").lower()
    return any(
        x in detail
        for x in ("403", "bloqu", "challenge", "cloudflare", "captcha", "restreint")
    )


def fetch_page(
    url: str,
    *,
    scroll_lazy: bool = False,
    click_contacts: bool = False,
    referer: str | None = None,
    prefer_browser: bool = False,
    fast_mode: bool = False,
    _block_retry: int = 0,
) -> FetchResult:
    """
    Stratégie anti-bot :
    1. curl_cffi (empreinte TLS Chrome + cookies session)
    2. Playwright furtif (scroll, cookies, challenges)
    3. requests en dernier recours
    4. Si blocage et CRAWL_PROXIES : rotation IP + nouvel essai
    """
    result = _fetch_page_once(
        url,
        scroll_lazy=scroll_lazy,
        click_contacts=click_contacts,
        referer=referer,
        prefer_browser=prefer_browser,
        fast_mode=fast_mode,
    )
    if result.ok:
        return result
    if not _is_block_result(result):
        return result

    from crawler.proxy_manager import max_rotations_on_block, rotate_proxy_on_block

    if _block_retry >= max_rotations_on_block():
        return result
    if rotate_proxy_on_block(result.error_detail or "blocked"):
        return fetch_page(
            url,
            scroll_lazy=scroll_lazy,
            click_contacts=click_contacts,
            referer=referer,
            prefer_browser=prefer_browser,
            fast_mode=fast_mode,
            _block_retry=_block_retry + 1,
        )
    return result
