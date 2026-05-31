"""Paramètres globaux du crawler."""

import os
import sys

# Poste de bureau (Windows/Mac) = écran disponible → Chrome visible possible.
# Serveur Linux sans écran → on reste en headless.
_IS_DESKTOP = sys.platform in ("win32", "darwin")

# Profil : quality (défaut) | balanced | fast | turbo
# La vérification des données (cohérence + verify_lead) est appliquée quel que soit
# le profil : le profil ne change que les délais de politesse, jamais la validité.
# « quality » = comportement humain max (délais longs, scroll/pauses réalistes) →
# réduit fortement les bannissements anti-bot, au prix de la vitesse.
# balanced = rapide + fiable (défaut). quality = lent, turbo = agressif.
CRAWL_SPEED_PROFILE = os.getenv("CRAWL_SPEED_PROFILE", "balanced").strip().lower()

# Crawl local : arrêt découverte dès N liens (évite 35 pages vides)
CITY_DISCOVERY_STOP_LINKS = int(os.getenv("CITY_DISCOVERY_STOP_LINKS", "28"))

# Plafond annonces traitées par source quand une ville est ciblée (0 = illimité)
CITY_CRAWL_MAX_LISTINGS = int(os.getenv("CITY_CRAWL_MAX_LISTINGS", "90"))

# Scalingo / PaaS : pas de Chrome embarqué par défaut
IS_SCALINGO = bool(os.getenv("SCALINGO_APP", "").strip())
if IS_SCALINGO:
    os.environ.setdefault("CRAWL_PLAYWRIGHT_ENABLED", "false")
    os.environ.setdefault("AUTO_WARMUP_ANTIBOT", "false")
    os.environ.setdefault("DOMAIN_WARMUP", "false")

CRAWL_PLAYWRIGHT_ENABLED = os.getenv("CRAWL_PLAYWRIGHT_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Enregistrer si les 4 champs minimum sont présents : adresse, tél., email, m²
SAVE_ACTIONABLE_LEADS = os.getenv("SAVE_ACTIONABLE_LEADS", "true").lower() in ("1", "true", "yes")
SAVE_MINIMAL_LEADS = os.getenv("SAVE_MINIMAL_LEADS", "true").lower() in ("1", "true", "yes")

# DVF en parallèle pendant le crawl (ne ralentit pas Playwright)
DVF_PARALLEL_DURING_CRAWL = True
DVF_PARALLEL_WORKERS = 5
DVF_QUEUE_DRAIN_TIMEOUT_SEC = 600
DVF_RECOMPARE_HOURS = 48

# Annonces traitées par crawl (0 = pas de plafond, jusqu'à MAX_LISTING_LINKS)
MAX_LISTINGS_PER_SCAN = 0

# Liens extraits des pages résultats + annonces similaires sur chaque fiche
MAX_LISTING_LINKS = 500

# Ne pas enchaîner le crawl des blocs « annonces similaires » (évite confusion + surcharge)
CRAWL_SIMILAR_LISTINGS = False

# Pages de résultats (pagination) à parcourir
MAX_SEARCH_PAGES = int(os.getenv("MAX_SEARCH_PAGES", "30"))

# Exploration site entier : pages index / catégories / pagination (BFS)
MAX_SITE_DISCOVERY_PAGES = int(os.getenv("MAX_SITE_DISCOVERY_PAGES", "35"))

# Découverte adaptative (heuristiques + multi-seeds)
SITE_WIDE_CRAWL_ENABLED = os.getenv("SITE_WIDE_CRAWL_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Délais humains entre annonces — surchargés par CRAWL_SPEED_PROFILE (voir human.py)
LISTING_DELAY_MIN_SEC = 2.5
LISTING_DELAY_MAX_SEC = 6.5
HUMAN_EXTRA_PAUSE_CHANCE = 0.18
HUMAN_EXTRA_PAUSE_MIN_SEC = 8.0
HUMAN_EXTRA_PAUSE_MAX_SEC = 22.0

CRAWL_SPEED_PRESETS: dict[str, dict[str, float]] = {
    "quality": {
        "listing_min": 2.5,
        "listing_max": 6.5,
        "search_min": 3.0,
        "search_max": 8.0,
        "networkidle_ms": 12_000,
        "content_wait_ms": 16_000,
        "extra_pause_chance": 0.18,
        "extra_pause_min": 8.0,
        "extra_pause_max": 22.0,
        "recrawl_delay_factor": 1.0,
        "warmup_sec": 3.5,
        "source_gap_min": 0.5,
        "source_gap_max": 1.2,
        "scroll_min": 5,
        "scroll_max": 11,
        "playwright_timeout_ms": 45_000,
        "playwright_retries": 6,
    },
    "balanced": {
        "listing_min": 0.65,
        "listing_max": 2.0,
        "search_min": 0.5,
        "search_max": 1.8,
        "extra_pause_chance": 0.03,
        "extra_pause_min": 2.0,
        "extra_pause_max": 5.0,
        "recrawl_delay_factor": 0.28,
        "warmup_sec": 0.8,
        "source_gap_min": 0.15,
        "source_gap_max": 0.45,
        "scroll_min": 2,
        "scroll_max": 5,
        "playwright_timeout_ms": 32_000,
        "playwright_retries": 4,
        "networkidle_ms": 6_000,
        "content_wait_ms": 10_000,
    },
    "fast": {
        "listing_min": 0.45,
        "listing_max": 1.5,
        "search_min": 0.35,
        "search_max": 1.2,
        "networkidle_ms": 4_000,
        "content_wait_ms": 7_000,
        "extra_pause_chance": 0.02,
        "extra_pause_min": 1.5,
        "extra_pause_max": 4.0,
        "recrawl_delay_factor": 0.15,
        "warmup_sec": 1.0,
        "source_gap_min": 0.1,
        "source_gap_max": 0.35,
        "scroll_min": 2,
        "scroll_max": 5,
        "playwright_timeout_ms": 28_000,
        "playwright_retries": 4,
    },
    "turbo": {
        "listing_min": 0.2,
        "listing_max": 0.9,
        "search_min": 0.2,
        "search_max": 0.8,
        "networkidle_ms": 2_500,
        "content_wait_ms": 5_000,
        "extra_pause_chance": 0.0,
        "extra_pause_min": 0.0,
        "extra_pause_max": 0.0,
        "recrawl_delay_factor": 0.08,
        "warmup_sec": 0.5,
        "source_gap_min": 0.05,
        "source_gap_max": 0.2,
        "scroll_min": 1,
        "scroll_max": 3,
        "playwright_timeout_ms": 22_000,
        "playwright_retries": 3,
    },
}


def active_speed_preset() -> dict[str, float]:
    return CRAWL_SPEED_PRESETS.get(
        CRAWL_SPEED_PROFILE,
        CRAWL_SPEED_PRESETS["balanced"],
    )

# Playwright
PLAYWRIGHT_TIMEOUT_MS = 45_000
PLAYWRIGHT_RETRIES = 6

# Anti-bot : attente max pour laisser passer Cloudflare / challenge
ANTIBOT_CHALLENGE_WAIT_MS = 30_000

# Si bloqué en headless, réessayer avec Chrome visible (souvent débloque en local)
PLAYWRIGHT_HEADED_FALLBACK = True

# Chrome visible dès le départ — OFF par défaut pour la vitesse (headless = rapide).
# Le navigateur ne passe en visible QUE si un site bloque (fallback _switch_to_headed),
# ce qui garde les sites accessibles rapides et n'ouvre Chrome que pour DataDome.
# Forçable via CRAWL_HEADFUL=1.
PLAYWRIGHT_FORCE_HEADED = os.getenv("CRAWL_HEADFUL", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Préchauffage des portails DataDome AVANT le crawl — OFF par défaut (front-load lent).
# À activer (AUTO_WARMUP_ANTIBOT=1) seulement si DataDome bloque de façon persistante.
AUTO_WARMUP_ANTIBOT = os.getenv("AUTO_WARMUP_ANTIBOT", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Profil navigateur persistant (cookies entre pages)
PLAYWRIGHT_PROFILE_DIR = "data/playwright_profile"

# curl_cffi — requêtes TLS type Chrome (souvent suffisant sans navigateur)
USE_CURL_CFFI = True
CURL_CFFI_IMPERSONATE = "chrome"

# ─── Proxies (rotation d'IP — pour passer DataDome/Cloudflare comme un service pro) ───
# Un crawl depuis une seule IP maison se fait bannir par les portails protégés.
# Branche un ou plusieurs proxies (de préférence RÉSIDENTIELS rotatifs) ici :
#   CRAWL_PROXIES="http://user:pass@gw1.proxy.com:8000,http://user:pass@gw2.proxy.com:8000"
# ou un seul :  CRAWL_PROXY="http://user:pass@host:port"
import random as _random

CRAWL_PROXIES = [
    p.strip()
    for p in os.getenv("CRAWL_PROXIES", os.getenv("CRAWL_PROXY", "")).split(",")
    if p.strip()
]


def pick_proxy() -> str | None:
    """Renvoie un proxy (rotation aléatoire) ou None si aucun configuré."""
    return _random.choice(CRAWL_PROXIES) if CRAWL_PROXIES else None


def proxies_enabled() -> bool:
    return bool(CRAWL_PROXIES)

# Délais entre pages de résultats (pagination)
SEARCH_PAGE_DELAY_MIN_SEC = 3.0
SEARCH_PAGE_DELAY_MAX_SEC = 8.0

# Échauffement : visite page d’accueil avant la liste
DOMAIN_WARMUP_ENABLED = os.getenv("DOMAIN_WARMUP", "auto").strip().lower() not in (
    "0",
    "false",
    "no",
)
DOMAIN_WARMUP_SEC = 3.5

# Playwright : préférer navigateur complet (moins headless agressif si bloqué)
PLAYWRIGHT_PREFER_HEADED = False

# Fourchette prix vente (€)
PRICE_MIN_SALE_EUR = 10_000
PRICE_MAX_SALE_EUR = 25_000_000

# Fourchette loyer (€ / mois en général)
PRICE_MIN_RENT_EUR = 150
PRICE_MAX_RENT_EUR = 50_000

# Rétrocompat validation
PRICE_MIN_EUR = PRICE_MIN_SALE_EUR
PRICE_MAX_EUR = PRICE_MAX_SALE_EUR
PRICE_TYPICAL_MAX_EUR = 3_000_000
