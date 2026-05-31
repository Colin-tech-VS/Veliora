"""
Préchauffage de session anti-bot (DataDome) — Chrome visible.

Ouvre un vrai Chrome avec le profil persistant du crawler, va sur les portails
protégés (LeBonCoin, SeLoger, BienIci) et vous laisse passer le captcha / accepter
les cookies une seule fois. Les cookies validés sont enregistrés dans le profil
(data/playwright_profile) et réutilisés ensuite par le crawl.

Lancement :
    python -m crawler.warmup_session
puis crawlez avec Chrome visible :
    set CRAWL_HEADFUL=1   (Windows)   &&   python app.py
"""

from __future__ import annotations

import time

from crawler.antibot import BROWSER_HEADERS, STEALTH_INIT_SCRIPT, is_blocked_html
from crawler.browser import USER_AGENTS, _profile_path
from crawler.config import PLAYWRIGHT_PROFILE_DIR  # noqa: F401  (assure le dossier)

# Pages de résultats des portails à anti-bot fort
WARMUP_TARGETS = [
    ("LeBonCoin", "https://www.leboncoin.fr/recherche?category=9&real_estate_type=2"),
    ("SeLoger", "https://www.seloger.com/list.htm?types=1&projects=2"),
    ("BienIci", "https://www.bienici.com/recherche/achat/appartement"),
]

MAX_WAIT_SEC = 600  # 10 min pour résoudre les challenges
COOKIE_SELECTORS = [
    "#didomi-notice-agree-button",
    'button:has-text("Tout accepter")',
    'button:has-text("Accepter")',
    'button:has-text("J\'accepte")',
]


def _dismiss_cookies(page) -> None:
    for sel in COOKIE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=800):
                loc.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def main() -> int:
    import random

    from playwright.sync_api import sync_playwright

    profile = str(_profile_path())
    print(f"Profil de session : {profile}")
    print("Ouverture de Chrome visible — résolvez les captchas / cookies si demandé.\n")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile,
            channel="chrome",
            headless=False,
            user_agent=random.choice(USER_AGENTS),
            locale="fr-FR",
            timezone_id="Europe/Paris",
            viewport={"width": 1366, "height": 900},
            extra_http_headers=BROWSER_HEADERS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,900",
                "--lang=fr-FR",
            ],
            ignore_default_args=["--enable-automation"],
        )
        ctx.add_init_script(STEALTH_INIT_SCRIPT)

        pages = []
        for i, (name, url) in enumerate(WARMUP_TARGETS):
            page = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                _dismiss_cookies(page)
            except Exception as exc:
                print(f"  {name}: ouverture partielle ({str(exc)[:60]})")
            pages.append((name, page))
            print(f"  Onglet ouvert — {name}")

        print("\nRésolvez les éventuels captchas dans chaque onglet…")
        print("Le préchauffage se termine automatiquement quand les 3 portails passent.\n")

        deadline = time.time() + MAX_WAIT_SEC
        ok_rounds = 0
        while time.time() < deadline:
            statuses = []
            all_ok = True
            for name, page in pages:
                try:
                    blocked = is_blocked_html(page.content(), min_len=1200)
                except Exception:
                    blocked = True
                statuses.append(f"{name}={'OK' if not blocked else 'bloqué'}")
                if blocked:
                    all_ok = False
            print("  " + " | ".join(statuses))
            ok_rounds = ok_rounds + 1 if all_ok else 0
            if ok_rounds >= 2:
                print("\n✓ Session validée pour les 3 portails — cookies enregistrés.")
                break
            time.sleep(6)
        else:
            print("\n⏱ Délai écoulé — les cookies obtenus sont tout de même enregistrés.")

        try:
            ctx.close()
        except Exception:
            pass

    print("\nProfil prêt. Lancez le crawl en Chrome visible :")
    print("  Windows :  set CRAWL_HEADFUL=1 && python app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
