"""Paramètres globaux du crawler."""

import os
import sys

# Poste de bureau (Windows/Mac) = écran disponible → Chrome visible possible.
# Serveur Linux sans écran → on reste en headless.
_IS_DESKTOP = sys.platform in ("win32", "darwin")

# Profil : quality | balanced | fast (défaut) | turbo
# La vérification des données (cohérence + verify_lead) est appliquée quel que soit
# le profil : le profil ne change que les délais de politesse, jamais la validité.
# « quality » = comportement humain max (délais longs, scroll/pauses réalistes) →
# réduit fortement les bannissements anti-bot, au prix de la vitesse.
# fast = rapide + fiable (défaut). balanced = prudent, quality = lent, turbo = agressif.
CRAWL_SPEED_PROFILE = os.getenv("CRAWL_SPEED_PROFILE", "fast").strip().lower()

# Crawl local : arrêt découverte dès N liens (évite les pages vides)
CITY_DISCOVERY_STOP_LINKS = int(os.getenv("CITY_DISCOVERY_STOP_LINKS", "35"))

# Plafond annonces traitées par source quand une ville est ciblée (0 = illimité)
CITY_CRAWL_MAX_LISTINGS = int(os.getenv("CITY_CRAWL_MAX_LISTINGS", "75"))

# Scalingo / PaaS : le navigateur Chromium de Playwright voyage désormais avec le
# slug (cf. bin/post_compile + libs système dans Aptfile), donc on l'active par
# défaut — sans quoi les portails anti-bot (LBC, PAP, SeLoger, Logic-Immo,
# Bien'ici) resteraient classés « Bientôt disponible » et hors crawl. Forçable via
# CRAWL_PLAYWRIGHT_ENABLED / CRAWL_ANTIBOT_PORTALS_ENABLED si besoin.
IS_SCALINGO = bool(os.getenv("SCALINGO_APP", "").strip())
if IS_SCALINGO:
    os.environ.setdefault("CRAWL_PLAYWRIGHT_ENABLED", "true")
    os.environ.setdefault("AUTO_WARMUP_ANTIBOT", "false")
    os.environ.setdefault("DOMAIN_WARMUP", "false")

CRAWL_PLAYWRIGHT_ENABLED = os.getenv("CRAWL_PLAYWRIGHT_ENABLED", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Enregistrer si contact + bien exploitables (tél. ou email + adresse + prix/surface)
SAVE_ACTIONABLE_LEADS = os.getenv("SAVE_ACTIONABLE_LEADS", "true").lower() in ("1", "true", "yes")
SAVE_MINIMAL_LEADS = os.getenv("SAVE_MINIMAL_LEADS", "true").lower() in ("1", "true", "yes")
# Dernier recours crawl : fiche visible dans Prospects (URL + prix ou surface ou adresse)
SAVE_CRAWL_SNAPSHOT = os.getenv("SAVE_CRAWL_SNAPSHOT", "true").lower() in ("1", "true", "yes")

# DVF en parallèle pendant le crawl (ne ralentit pas Playwright)
DVF_PARALLEL_DURING_CRAWL = True
_default_dvf_workers = "1" if IS_SCALINGO else "3"
DVF_PARALLEL_WORKERS = int(os.getenv("DVF_PARALLEL_WORKERS", _default_dvf_workers))
DVF_QUEUE_DRAIN_TIMEOUT_SEC = int(os.getenv("DVF_QUEUE_DRAIN_TIMEOUT_SEC", "120"))
DVF_RECOMPARE_HOURS = 48

# Rapprochement d'adresse (DPE/BAN/DVF/cadastre) en parallèle pendant le crawl.
# Standardisé pour TOUTES les sources : post-processing après scraping.
ADDRESS_MATCH_DURING_CRAWL = os.getenv("ADDRESS_MATCH_DURING_CRAWL", "true").lower() in (
    "1",
    "true",
    "yes",
)
_default_addr_workers = "1" if IS_SCALINGO else "3"
ADDRESS_MATCH_WORKERS = int(os.getenv("ADDRESS_MATCH_WORKERS", _default_addr_workers))
ADDRESS_MATCH_DRAIN_TIMEOUT_SEC = int(
    os.getenv("ADDRESS_MATCH_DRAIN_TIMEOUT_SEC", "150")
)

# Annonces traitées par crawl (0 = pas de plafond, jusqu'à MAX_LISTING_LINKS)
MAX_LISTINGS_PER_SCAN = 0

# Liens extraits des pages résultats — réduit pour des crawls plus rapides
MAX_LISTING_LINKS = int(os.getenv("MAX_LISTING_LINKS", "180"))

# Découverte : seuils adaptatif / IA (diviseurs de MAX_LISTING_LINKS)
DISCOVERY_ADAPTIVE_MIN_LINKS_DIV = int(os.getenv("DISCOVERY_ADAPTIVE_MIN_LINKS_DIV", "40"))
DISCOVERY_AI_MIN_LINKS_DIV = int(os.getenv("DISCOVERY_AI_MIN_LINKS_DIV", "20"))
AI_DISCOVERY_MAX_ATTEMPTS = int(os.getenv("AI_DISCOVERY_MAX_ATTEMPTS", "8"))

# ─── Analyse approfondie (API agrégée — ne remplace PAS les crawlers HTML) ───
STREAMESTATE_DISPLAY_NAME = os.getenv("STREAMESTATE_DISPLAY_NAME", "Analyse approfondie").strip() or "Analyse approfondie"
# 1 requête API ≈ 1 page (jusqu'à 30 annonces) ≈ 1 crédit.
# itemsPerPage reste maxé à 30 : meilleur ratio annonces/crédit.
# Crawl manuel : plus que 30 annonces, mais plafonné pour préserver les crédits.
STREAMESTATE_ITEMS_PER_PAGE = min(30, max(1, int(os.getenv("STREAMESTATE_ITEMS_PER_PAGE", "30"))))
STREAMESTATE_MAX_PAGES = max(1, int(os.getenv("STREAMESTATE_MAX_PAGES", "3")))
STREAMESTATE_MAX_LISTINGS = max(1, int(os.getenv("STREAMESTATE_MAX_LISTINGS", "90")))
STREAMESTATE_VEILLE_MAX_PAGES = max(1, int(os.getenv("STREAMESTATE_VEILLE_MAX_PAGES", "1")))
STREAMESTATE_VEILLE_MAX_LISTINGS = max(1, int(os.getenv("STREAMESTATE_VEILLE_MAX_LISTINGS", "15")))
# Vérification des annonces déjà en base : budget de crédits (pages) par run.
# Une page ville vérifie d'un coup TOUTES les annonces existantes de la ville
# par correspondance d'URL → coût mutualisé, très économe.
STREAMESTATE_VERIFY_MAX_PAGES = max(1, int(os.getenv("STREAMESTATE_VERIFY_MAX_PAGES", "5")))
STREAMESTATE_VERIFY_MAX_PAGES_PER_CITY = max(
    1, int(os.getenv("STREAMESTATE_VERIFY_MAX_PAGES_PER_CITY", "2"))
)
STREAMESTATE_INCLUDE_IN_VEILLE = os.getenv("STREAMESTATE_INCLUDE_IN_VEILLE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
# Mettre de côté StreamEstate (code conservé) — crawl 100 % HTML + Decodo/Playwright.
CRAWL_SKIP_STREAMESTATE = os.getenv("CRAWL_SKIP_STREAMESTATE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
STREAMESTATE_PARTICULIER_ONLY = os.getenv("STREAMESTATE_PARTICULIER_ONLY", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
STREAMESTATE_TRANSACTION_SALE = os.getenv("STREAMESTATE_TRANSACTION_SALE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)
STREAMESTATE_WITH_COHERENT_PRICE = os.getenv("STREAMESTATE_WITH_COHERENT_PRICE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Ne pas enchaîner le crawl des blocs « annonces similaires » (évite confusion + surcharge)
CRAWL_SIMILAR_LISTINGS = False

# Pages de résultats (pagination) à parcourir
MAX_SEARCH_PAGES = int(os.getenv("MAX_SEARCH_PAGES", "18"))

# Exploration site entier : pages index / catégories / pagination (BFS)
MAX_SITE_DISCOVERY_PAGES = int(os.getenv("MAX_SITE_DISCOVERY_PAGES", "28"))

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
        "listing_min": 0.35,
        "listing_max": 1.2,
        "search_min": 0.28,
        "search_max": 1.0,
        "networkidle_ms": 3_500,
        "content_wait_ms": 6_000,
        "extra_pause_chance": 0.01,
        "extra_pause_min": 1.0,
        "extra_pause_max": 3.0,
        "recrawl_delay_factor": 0.1,
        "warmup_sec": 0.6,
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

# Anti-bot : attente max pour laisser passer Cloudflare / challenge.
# 30s gelait le crawl sur chaque portail bloqué ; 15s suffit pour un challenge
# légitime — au-delà, la rotation d'IP / le passage au portail suivant est plus utile.
ANTIBOT_CHALLENGE_WAIT_MS = int(os.getenv("ANTIBOT_CHALLENGE_WAIT_MS", "15000"))

# Si bloqué en headless, réessayer avec Chrome visible — OFF par défaut (mode automatique).
PLAYWRIGHT_HEADED_FALLBACK = os.getenv("CRAWL_HEADED_FALLBACK", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

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

# Mode headless du navigateur sur serveur :
#   "new" → Chrome Headless « nouvelle génération » (--headless=new) : empreinte
#           quasi identique au navigateur visible → bien meilleur face à DataDome,
#           sans écran. C'est le défaut sur Scalingo (pas de display).
#   "old" → ancien headless (plus détectable) — repli si "new" pose souci.
CRAWL_HEADLESS_MODE = (os.getenv("CRAWL_HEADLESS_MODE", "new") or "new").strip().lower()

# Écran virtuel Xvfb (Linux) : lance Chrome en mode VISIBLE (headful) sous un display
# virtuel — l'évasion anti-bot la plus robuste sur serveur sans écran. OFF par défaut
# (nécessite le paquet apt `xvfb` + `pyvirtualdisplay`). À activer si "new headless"
# se fait encore bloquer par DataDome : CRAWL_XVFB=1.
CRAWL_XVFB = os.getenv("CRAWL_XVFB", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Profil navigateur persistant (cookies entre pages)
PLAYWRIGHT_PROFILE_DIR = "data/playwright_profile"

# curl_cffi — requêtes TLS type Chrome (souvent suffisant sans navigateur)
USE_CURL_CFFI = True
CURL_CFFI_IMPERSONATE = "chrome"

# Timeout HTTP par requête (curl_cffi / requests). 35s était trop généreux :
# une page légitime répond en <10s ; un proxy lent/mort ne doit pas geler le crawl.
# Réduit fortement le temps total, surtout avec des proxys gratuits (lents).
CRAWL_HTTP_TIMEOUT_SEC = int(os.getenv("CRAWL_HTTP_TIMEOUT_SEC", "14"))

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

# Nouvelle IP à chaque job / portail crawlé (désactiver : CRAWL_PROXY_ROTATE_EACH_CRAWL=false).
CRAWL_PROXY_ROTATE_EACH_CRAWL = os.getenv("CRAWL_PROXY_ROTATE_EACH_CRAWL", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Nouvelle IP dès qu'un portail renvoie anti-bot / Cloudflare (recommandé avec CRAWL_PROXIES).
CRAWL_PROXY_ROTATE_ON_BLOCK = os.getenv("CRAWL_PROXY_ROTATE_ON_BLOCK", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Si CRAWL_PROXIES est vide : pool de proxies HTTP publics testés + rotation auto.
# Activé par défaut (veille, crawl manuel, import URL, refresh fiches). Désactiver :
# CRAWL_AUTO_FREE_PROXIES=false. Les CRAWL_PROXIES payants restent prioritaires.
CRAWL_AUTO_FREE_PROXIES = os.getenv("CRAWL_AUTO_FREE_PROXIES", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Ne pas ouvrir Chrome pour « tester » les URLs ville avant le crawl.
CRAWL_SKIP_CITY_PROBE = os.getenv("CRAWL_SKIP_CITY_PROBE", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)


def pick_proxy() -> str | None:
    from crawler.proxy_manager import pick_proxy as _pick

    return _pick()


def proxies_enabled() -> bool:
    from crawler.proxy_manager import proxies_enabled as _enabled

    return _enabled()

# Délais entre pages de résultats (pagination)
SEARCH_PAGE_DELAY_MIN_SEC = 3.0
SEARCH_PAGE_DELAY_MAX_SEC = 8.0

# Échauffement : visite page d’accueil avant la liste
DOMAIN_WARMUP_ENABLED = os.getenv("DOMAIN_WARMUP", "false").strip().lower() in (
    "1",
    "true",
    "yes",
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

# Fourchette surface habitable (m²) par type, pour éviter de mélanger terrains/parkings
# avec des logements. Plafonds généreux (grandes maisons/lofts) mais réglables par env.
SURFACE_MIN_SALE_M2 = int(os.getenv("SURFACE_MIN_SALE_M2", "5"))
SURFACE_MAX_SALE_M2 = int(os.getenv("SURFACE_MAX_SALE_M2", "500"))
SURFACE_MIN_RENT_M2 = int(os.getenv("SURFACE_MIN_RENT_M2", "8"))
SURFACE_MAX_RENT_M2 = int(os.getenv("SURFACE_MAX_RENT_M2", "300"))

# ─── Veille automatique (portails + prospects) ───

def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes")


# Démarre la veille auto au boot (wsgi / python app.py). Désactiver : CRAWL_AUTO_START=false
CRAWL_AUTO_START = _env_bool("CRAWL_AUTO_START", "true")

# Intervalle entre deux passages « tous portails » (secondes). Défaut 300 = 5 min.
CRAWL_BACKGROUND_INTERVAL_SEC = max(
    60,
    int(os.getenv("CRAWL_BACKGROUND_INTERVAL_SEC", "300") or "300"),
)

# Inclure les portails personnalisés (URL ajoutée) dans la veille auto all_sources
CRAWL_INCLUDE_CUSTOM_IN_AUTO = _env_bool("CRAWL_INCLUDE_CUSTOM_IN_AUTO", "true")

# Réseaux agences + petites annonces (La Forêt, ORPI, Entre Particuliers, etc.)
CRAWL_INCLUDE_CATALOG_IN_AUTO = _env_bool("CRAWL_INCLUDE_CATALOG_IN_AUTO", "true")

# Recrawl périodique des fiches prospects (prix / contacts à jour)
CRAWL_LEAD_REFRESH_ENABLED = _env_bool("CRAWL_LEAD_REFRESH_ENABLED", "true")
CRAWL_LEAD_REFRESH_INTERVAL_SEC = max(
    300,
    int(os.getenv("CRAWL_LEAD_REFRESH_INTERVAL_SEC", "3600") or "3600"),
)
CRAWL_LEAD_REFRESH_STALE_HOURS = max(
    1,
    int(os.getenv("CRAWL_LEAD_REFRESH_STALE_HOURS", "24") or "24"),
)
CRAWL_LEAD_REFRESH_MAX_PER_RUN = max(
    1,
    int(os.getenv("CRAWL_LEAD_REFRESH_MAX_PER_RUN", "15") or "15"),
)

# Veille auto : durée max par portail (évite blocage anti-bot — on passe au suivant)
CRAWL_VEILLE_SOURCE_MAX_SEC = max(
    120,
    int(os.getenv("CRAWL_VEILLE_SOURCE_MAX_SEC", "540") or "540"),
)
# Budget temps veille : +N secondes par fiche déjà en base (recrawl obligatoire)
CRAWL_VEILLE_SEC_PER_EXISTING = max(
    2,
    int(os.getenv("CRAWL_VEILLE_SEC_PER_EXISTING", "4") or "4"),
)
CRAWL_VEILLE_SOURCE_MAX_CAP_SEC = max(
    CRAWL_VEILLE_SOURCE_MAX_SEC,
    int(os.getenv("CRAWL_VEILLE_SOURCE_MAX_CAP_SEC", "3600") or "3600"),
)
# Plafond découverte (nouvelles URLs) par portail en veille — les fiches existantes ne comptent pas dedans
CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS = int(
    os.getenv("CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS", str(CITY_CRAWL_MAX_LISTINGS))
    or str(CITY_CRAWL_MAX_LISTINGS)
)


def veille_source_budget_sec(existing_lead_count: int) -> int:
    """Durée max par portail en veille : base + temps pour recrawler toutes les fiches en base."""
    n = max(0, int(existing_lead_count))
    budget = CRAWL_VEILLE_SOURCE_MAX_SEC + n * CRAWL_VEILLE_SEC_PER_EXISTING
    return min(CRAWL_VEILLE_SOURCE_MAX_CAP_SEC, budget)


def antibot_portals_crawl_enabled() -> bool:
    """Leboncoin, PAP, SeLoger, Logic-Immo, BienIci (DataDome / Cloudflare).

    Réalité technique : ces portails exigent un VRAI navigateur (Playwright) — le
    fetch HTTP simple échoue même avec rotation IP. On ne les active donc QUE si un
    navigateur est disponible, sinon c'est « 0 annonce » silencieux et du budget de
    crawl gaspillé. Pour de la fiabilité, ajouter en plus des proxies résidentiels
    (CRAWL_PROXIES) : les proxies publics gratuits ne passent pas DataDome.
    Forçable explicitement via CRAWL_ANTIBOT_PORTALS_ENABLED=1/0.
    """
    raw = (os.getenv("CRAWL_ANTIBOT_PORTALS_ENABLED") or "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    # Navigateur indispensable ; la rotation IP (proxy/pool) reste fortement conseillée.
    return CRAWL_PLAYWRIGHT_ENABLED


def antibot_portals_readiness() -> dict:
    """État de préparation au crawl des portails anti-bot (pour diagnostic / UI)."""
    has_browser = CRAWL_PLAYWRIGHT_ENABLED
    # Résidentiel = STRICTEMENT CRAWL_PROXIES (Decodo). Les proxies publics gratuits
    # ne passent PAS DataDome : on ne les compte pas comme « prêt » pour l'anti-bot,
    # sinon on lancerait Chrome pour rien sur SeLoger & co.
    has_residential = bool(CRAWL_PROXIES)
    has_any_rotation = has_residential or CRAWL_AUTO_FREE_PROXIES
    if has_browser and has_residential:
        level = "ready"  # navigateur + proxies dédiés : configuration fiable
    elif has_browser and has_any_rotation:
        level = "partial"  # navigateur + pool gratuit : fonctionne par intermittence
    elif has_browser:
        level = "browser_only"  # navigateur sans rotation : blocages fréquents
    else:
        level = "blocked"  # pas de navigateur : portails anti-bot inopérants
    return {
        "level": level,
        "enabled": antibot_portals_crawl_enabled(),
        "has_browser": has_browser,
        "has_residential_proxies": has_residential,
        "has_ip_rotation": has_any_rotation,
    }


def antibot_setup_hint(portal_name: str = "Ce portail", readiness: dict | None = None) -> str:
    """Message clair sur ce qu'il manque pour crawler un portail anti-bot.

    Évite le « 0 annonce » silencieux : on dit explicitement ce qu'il reste à
    configurer (navigateur, proxies résidentiels, activation).
    """
    r = readiness or antibot_portals_readiness()
    missing: list[str] = []
    if not r.get("has_browser"):
        missing.append(
            "un navigateur (CRAWL_PLAYWRIGHT_ENABLED=true + playwright install chromium)"
        )
    if not r.get("has_residential_proxies"):
        missing.append("des proxies résidentiels (CRAWL_PROXIES=…Decodo…)")
    if not r.get("enabled"):
        missing.append("l'activation (CRAWL_ANTIBOT_PORTALS_ENABLED=1)")
    if not missing:
        return f"{portal_name} : portail anti-bot prêt (navigateur + proxies résidentiels)."
    return (
        f"{portal_name} est un portail anti-bot (DataDome) : 0 annonce tant qu'il manque "
        + " ; ".join(missing)
        + ". Voir DECODO.md."
    )


def background_crawl_config() -> dict:
    """Exposé API / health pour le CRM."""
    return {
        "auto_start": CRAWL_AUTO_START,
        "interval_sec": CRAWL_BACKGROUND_INTERVAL_SEC,
        "include_custom_portals": CRAWL_INCLUDE_CUSTOM_IN_AUTO,
        "include_catalog_sites": CRAWL_INCLUDE_CATALOG_IN_AUTO,
        "lead_refresh_enabled": CRAWL_LEAD_REFRESH_ENABLED,
        "lead_refresh_interval_sec": CRAWL_LEAD_REFRESH_INTERVAL_SEC,
        "lead_refresh_stale_hours": CRAWL_LEAD_REFRESH_STALE_HOURS,
        "lead_refresh_max_per_run": CRAWL_LEAD_REFRESH_MAX_PER_RUN,
        "veille_recheck_all_existing": True,
        "veille_discovery_max_listings": CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS,
        "veille_source_max_cap_sec": CRAWL_VEILLE_SOURCE_MAX_CAP_SEC,
        "antibot_portals_enabled": antibot_portals_crawl_enabled(),
        "antibot_readiness": antibot_portals_readiness(),
        "proxies_configured": proxies_enabled(),
        "auto_free_proxies": CRAWL_AUTO_FREE_PROXIES,
        "ai_discovery": (os.getenv("CRAWL_AI_DISCOVERY") or "auto").strip(),
        "crawl_skip_streamestate": CRAWL_SKIP_STREAMESTATE,
        "streamestate": __import__(
            "crawler.streamestate", fromlist=["streamestate_health"]
        ).streamestate_health(),
    }
