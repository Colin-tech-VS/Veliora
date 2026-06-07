#!/usr/bin/env python3
"""Veliora — Serveur Flask (API + frontend pige immobilière IA)."""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, g, jsonify, redirect, request, send_file, send_from_directory

load_dotenv(Path(__file__).resolve().parent / ".env")

from crm.auth.context import get_agency_id, get_current_user, require_api_auth

from crawler.engine import engine
from crawler.storage import (
    add_source,
    backup_database,
    checkpoint_database,
    db_status,
    delete_source,
    refresh_source_names_and_logos,
    get_activities,
    cancel_all_active_crawl_jobs,
    cancel_crawl_job,
    expire_stale_crawl_jobs,
    get_active_crawl_job,
    get_agency_name,
    get_agency_settings,
    get_agency_primary_city,
    get_crawl_job,
    get_crawl_logs_for_job,
    delete_all_leads,
    delete_lead,
    get_lead,
    get_leads,
    get_source,
    get_source_stats,
    get_sources,
    get_stats,
    init_db,
    patch_lead,
    compare_lead_dvf,
    compare_and_enrich_lead_dvf,
    compare_leads_dvf_batch,
    get_lead_by_source_url,
    update_source_fields,
    upsert_agency_settings,
    set_onboarding,
    export_leads_csv,
)

BASE_DIR = Path(__file__).resolve().parent
CRM_DIR = BASE_DIR / "crm"
VITRINE_DIR = BASE_DIR / "vitrine"
DATA_DIR = BASE_DIR / "data"
VITRINE_INDEX = VITRINE_DIR / "index.html"
CRM_INDEX = CRM_DIR / "index.html"

# Pages vitrine servies hors index (alias URL → fichier)
VITRINE_PAGE_ALIASES: dict[str, str] = {
    "estimation": "estimation.html",
    "estimer": "estimation.html",
    "estimer-votre-bien": "estimation.html",
    "annonces": "annonces.html",
    "portail": "annonces.html",
    "publier-annonce": "estimation.html",
}
VITRINE_ESTIMATION_HTML = VITRINE_DIR / "estimation.html"
VITRINE_ANNONCES_HTML = VITRINE_DIR / "annonces.html"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


def _log_ai_startup() -> None:
    """Trace la configuration IA au boot — visible direct dans `scalingo logs`.

    Aide à diagnostiquer en prod : si l'agent voit « Statut IA inconnu » sur
    l'UI, un coup d'œil aux logs Scalingo dit immédiatement quel provider
    tourne, quel modèle, et si la clé est définie (sans la divulguer).
    """
    try:
        from crm.ai.config import AI_API_KEY, AI_MODEL, AI_PROVIDER, OLLAMA_BASE_URL

        masked_key = (
            f"{AI_API_KEY[:6]}…{AI_API_KEY[-4:]}" if AI_API_KEY else "(vide)"
        )
        if AI_PROVIDER == "ollama":
            logging.info(
                "Veliora IA : provider=ollama base_url=%s model=%s",
                OLLAMA_BASE_URL,
                AI_MODEL or "qwen2.5:7b-instruct",
            )
        else:
            logging.info(
                "Veliora IA : provider=%s model=%s key=%s",
                AI_PROVIDER,
                AI_MODEL or "(défaut provider)",
                masked_key,
            )
    except Exception as exc:
        logging.warning("Impossible de logger la config IA : %s", exc)


_log_ai_startup()

_db_init_lock = threading.Lock()
_refresh_all_batches: dict[str, dict] = {}
_refresh_all_lock = threading.Lock()


def _refresh_all_batch_snapshot(batch_id: str) -> dict | None:
    with _refresh_all_lock:
        b = _refresh_all_batches.get(batch_id)
        if not b:
            return None
        # Copie shallow + copie de la liste logs pour éviter les courses.
        out = {**b}
        out["logs"] = list(b.get("logs") or [])
        return out


def _append_refresh_log(batch: dict, message: str) -> None:
    logs = batch.setdefault("logs", [])
    logs.append(message)
    if len(logs) > 18:
        del logs[: len(logs) - 18]


@app.before_request
def handle_api_preflight():
    """Évite 405 sur OPTIONS (CORS / Live Server) et prépare les POST JSON."""
    if request.method == "OPTIONS" and request.path.startswith("/api/"):
        resp = app.make_response("")
        resp.status_code = 204
        origin = request.headers.get("Origin")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Max-Age"] = "86400"
        return resp


def _allowed_cors_origin() -> str | None:
    origin = request.headers.get("Origin")
    if not origin:
        return None
    if origin.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
        return origin
    return origin


@app.after_request
def add_cors_headers(response):
    if request.path.startswith("/api/"):
        origin = _allowed_cors_origin()
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Max-Age"] = "86400"
    return response


# Types texte compressibles (JSON API + assets statiques). Les images/binaires
# sont déjà compressés : on les ignore pour ne pas gaspiller de CPU.
_COMPRESSIBLE_TYPES = (
    "application/json",
    "application/javascript",
    "application/manifest+json",
    "text/",
    "image/svg+xml",
    "application/xml",
    "application/rss+xml",
)
# En dessous de ce seuil, l'en-tête gzip coûte plus cher que le gain.
_GZIP_MIN_BYTES = 1024


@app.after_request
def compress_response(response):
    """Compresse gzip les réponses texte (JSON, JS, CSS, HTML).

    ~750 Ko de JS/CSS + les payloads JSON tombent à ~1/5 de leur taille :
    chargement bien plus rapide, surtout en mobile / réseau lent. On reste
    en stdlib (aucune dépendance) et on respecte Accept-Encoding + Vary.
    """
    accept = request.headers.get("Accept-Encoding", "")
    if "gzip" not in accept.lower():
        return response

    ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    compressible = any(ctype.startswith(t) for t in _COMPRESSIBLE_TYPES)
    if not compressible:
        return response

    # Le contenu varie selon l'encodage : indispensable pour les caches / CDN.
    vary = response.headers.get("Vary", "")
    if "accept-encoding" not in vary.lower():
        response.headers["Vary"] = f"{vary}, Accept-Encoding".lstrip(", ")

    cache_control = (response.headers.get("Cache-Control") or "").lower()
    if (
        not (200 <= response.status_code < 300)
        or "Content-Encoding" in response.headers
        or "no-transform" in cache_control
    ):
        return response

    # send_file / send_from_directory renvoient un flux fichier en passthrough :
    # on le désactive pour pouvoir lire (et compresser) les octets. Un VRAI flux
    # généré (générateur, ex. NDJSON IA) garde direct_passthrough=False mais un
    # content-type non compressible — donc déjà écarté plus haut.
    if response.direct_passthrough:
        response.direct_passthrough = False
    elif response.is_streamed:
        return response

    data = response.get_data()
    if len(data) < _GZIP_MIN_BYTES:
        return response

    import gzip

    compressed = gzip.compress(data, compresslevel=6)
    # Si gzip n'apporte rien (contenu déjà dense), on garde l'original.
    if len(compressed) >= len(data):
        return response
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    return response


def _request_needs_db() -> bool:
    """Vraie uniquement pour les routes qui lisent/écrivent la base.

    La vitrine et tous les assets statiques sont du HTML/CSS/JS pur (les données
    sont chargées ensuite via /api/*) : les servir ne doit JAMAIS attendre
    l'initialisation de SQLite. Au cold start, la page d'accueil s'affiche donc
    instantanément au lieu de bloquer le temps de l'init/backup de la base.
    """
    p = request.path
    return p.startswith("/api/") or p == "/sitemap.xml"


def _run_db_housekeeping() -> None:
    """Tâches d'entretien non bloquantes — exécutées en arrière-plan.

    backup SQLite, normalisation des logos sources et expiration des crawl jobs
    ne sont pas nécessaires pour répondre à la première requête : les déporter
    hors du chemin requête supprime plusieurs secondes de latence au démarrage.
    """
    try:
        backup_database()
    except OSError as exc:
        logging.warning("Sauvegarde SQLite ignorée : %s", exc)
    except Exception:
        logging.exception("Sauvegarde SQLite (arrière-plan)")
    try:
        refresh_source_names_and_logos()
    except Exception:
        logging.warning("Mise à jour logos sources ignorée", exc_info=True)
    try:
        expire_stale_crawl_jobs()
    except Exception:
        logging.exception("Expiration crawl jobs (arrière-plan)")


@app.before_request
def ensure_db():
    if getattr(app, "_db_ready", False):
        return
    # Les pages/assets statiques (vitrine, CRM shell, favicon…) n'attendent pas
    # la base : chargement immédiat même pendant l'init ou au réveil du dyno.
    if not _request_needs_db():
        return
    with _db_init_lock:
        if getattr(app, "_db_ready", False):
            return
        try:
            init_db()
        except Exception as exc:
            logging.exception("Initialisation DB échouée")
            if request.path.startswith("/api/"):
                return jsonify(
                    {
                        "error": "Base de données indisponible",
                        "code": "database_unavailable",
                        "detail": str(exc),
                    }
                ), 503
            raise
        from crawler.storage import mark_crawl_jobs_interrupted_on_startup

        mark_crawl_jobs_interrupted_on_startup()
        app._db_ready = True
        logging.info("Base Veliora : %s", db_status())
        # Entretien lourd hors du chemin requête : la réponse part sans l'attendre.
        threading.Thread(
            target=_run_db_housekeeping, name="db-housekeeping", daemon=True
        ).start()


def _register_database_busy_handler(flask_app: Flask) -> None:
    from velora_db.connection import DatabaseBusyError

    @flask_app.errorhandler(DatabaseBusyError)
    def _handle_database_busy(exc):
        if request.path.startswith("/api/"):
            return jsonify({"error": str(exc), "code": "database_busy"}), 503
        raise exc


_register_database_busy_handler(app)


@app.before_request
def protect_api():
    from velora_db.connection import DatabaseBusyError

    try:
        return require_api_auth()
    except DatabaseBusyError as exc:
        if request.path.startswith("/api/"):
            return jsonify({"error": str(exc), "code": "database_busy"}), 503
        raise


def _aid() -> str:
    agency_id = get_agency_id()
    if not agency_id:
        raise RuntimeError("agency_id manquant")
    return agency_id


def _rescore_after_client_change(agency_id: str) -> None:
    """Recalcule les scores des leads en arrière-plan après modif des profils
    acheteurs/locataires (la demande compatible alimente le Score Mandat)."""
    if not agency_id:
        return
    try:
        from crm.scoring.batch import schedule_agency_rescore

        schedule_agency_rescore(agency_id)
    except Exception:
        logging.exception("rescore après modification client")


def _sources_payload():
    from crawler.storage import invalidate_sources_cache

    aid = _aid()
    invalidate_sources_cache(aid)
    return get_sources(aid, sync=True, live_counts=True)


def _paid_portal_crawl_response(*, source: dict | None = None, url: str | None = None):
    """Bloque le crawl des portails anti-bot non encore activés (« Bientôt disponible »)."""
    from crawler.portals import is_coming_soon_url
    from crawler.storage import is_antibot_source

    blocked = False
    name = ""
    if source and is_antibot_source(source):
        blocked = True
        name = source.get("name") or "ce portail"
    elif url and is_coming_soon_url(url):
        blocked = True
        name = url.split("/")[2] if "/" in url else url
    if not blocked:
        return None
    return (
        jsonify(
            {
                "error": (
                    f"{name} est protégé (anti-bot / Cloudflare). "
                    "Crawl bientôt disponible — portail pas encore activé."
                ),
                "code": "portal_coming_soon",
            }
        ),
        402,
    )


@app.route("/api/geo/communes", methods=["GET"])
def api_geo_communes():
    """Autocomplete : toutes les communes françaises (référentiel geo.api.gouv.fr)."""
    from crawler.fr_communes import all_communes, search_communes

    q = (request.args.get("q") or request.args.get("query") or "").strip()
    postcode = (request.args.get("postcode") or request.args.get("cp") or "").strip() or None
    limit = request.args.get("limit", 25, type=int)
    if not q:
        return jsonify({"ok": True, "total": len(all_communes()), "communes": []})
    return jsonify(
        {
            "ok": True,
            "total": len(all_communes()),
            "communes": search_communes(q, limit=limit, postcode=postcode),
        }
    )


@app.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "server": "veliora-flask",
        "product": "Veliora",
        "tagline": __import__("crm.config", fromlist=["PRODUCT_TAGLINE"]).PRODUCT_TAGLINE,
        "api_version": __import__("crm.constants", fromlist=["API_VERSION"]).API_VERSION,
        "lead_refresh": True,
        "radar": True,
        "clients": True,
        "mandates": True,
        "mandate_preview": True,
        "dvf": True,
        "dvf_parallel": True,
        "crawl_speed_profile": __import__("crawler.config", fromlist=["CRAWL_SPEED_PROFILE"]).CRAWL_SPEED_PROFILE,
        "proxies_enabled": __import__("crawler.config", fromlist=["proxies_enabled"]).proxies_enabled(),
        "proxy_count": len(__import__("crawler.config", fromlist=["CRAWL_PROXIES"]).CRAWL_PROXIES),
        "background_crawl": __import__(
            "crawler.config", fromlist=["background_crawl_config"]
        ).background_crawl_config(),
        "dvf_app": "https://app.dvf.etalab.gouv.fr/",
        "vitrine": "/",
        "vitrine_ok": VITRINE_INDEX.is_file(),
        "estimation": "/estimation",
        "estimation_ok": VITRINE_ESTIMATION_HTML.is_file(),
        "annonces": "/annonces",
        "annonces_ok": VITRINE_ANNONCES_HTML.is_file(),
        "home": "/",
        "auth_page": "/crm/auth",
        "auth_required": True,
        "billing": __import__("crm.billing.config", fromlist=["public_stripe_config"]).public_stripe_config(),
        "database": db_status(),
        "multi_agency": True,
        "post_sources": True,
        "delete_sources": True,
        "delete_sources_post": True,
        "delete_leads": True,
        "delete_leads_all": True,
        "transactions": True,
        "agents": True,
        "portal_publish_from_lead": True,
        "publish_requires_signed_mandate": True,
        "patch_source_url": True,
        "post_source_url": True,
        "radar_analyze_url": True,
    })


@app.errorhandler(404)
def handle_not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({
            "error": (
                f"Route API introuvable : {request.path}. "
                "Relancez Veliora avec : python app.py (pas http.server)."
            ),
        }), 404
    legal = VITRINE_DIR / "legal.html"
    p = request.path.lower().rstrip("/")
    if legal.is_file() and (
        p.startswith("/legal") or p in ("/cgv", "/mentions", "/mentions-legales", "/confidentialite", "/dpa")
    ):
        return _serve_legal_page()
    slug = p.strip("/")
    if p == "/vitrine/estimation.html":
        resp = _estimation_page_response()
        if resp is not None:
            return resp
    if p == "/vitrine/annonces.html":
        resp = _annonces_page_response()
        if resp is not None:
            return resp
    if slug in VITRINE_PAGE_ALIASES:
        if slug in ("estimation", "estimer", "estimer-votre-bien"):
            resp = _estimation_page_response()
        else:
            resp = _serve_vitrine_page(slug)
        if resp is not None:
            return resp
    hint = ""
    if request.path.startswith("/crm"):
        hint = " Relancez demarrer.bat (Ctrl+C puis python app.py) si vous venez de déplacer les fichiers."
    if p.startswith("/legal") or "cgv" in p or "confidentialite" in p:
        hint = " Lancez Veliora avec demarrer.bat ou python app.py (pas Live Server seul)."
    if slug in VITRINE_PAGE_ALIASES:
        hint = (
            " Fichier vitrine manquant ou serveur non relancé."
            " Lancez demarrer.bat (python app.py sur le port 8000), pas Live Server."
        )
    return f"Not Found — {request.path}.{hint}", 404


@app.errorhandler(405)
def handle_method_not_allowed(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Méthode {request.method} non autorisée sur {request.path}."}), 405
    return "Method Not Allowed", 405


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Garantit une réponse JSON sur /api/* même en cas d'exception non gérée.

    Sans ce filet, Flask renvoie une page HTML d'erreur 500 et le front
    plante sur `r.json()` (« Unexpected token '<' »).
    """
    from werkzeug.exceptions import HTTPException

    if isinstance(e, HTTPException):
        # Laisse les handlers dédiés (404/405) et erreurs HTTP normales gérer.
        return e
    logging.exception("Erreur serveur non gérée sur %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Erreur serveur inattendue. Réessayez dans un instant."}), 500
    raise e


def _static_mimetype(filename: str) -> str | None:
    if filename.endswith(".svg"):
        return "image/svg+xml"
    if filename.endswith(".js"):
        return "application/javascript"
    if filename.endswith(".css"):
        return "text/css"
    if filename.endswith(".webmanifest"):
        return "application/manifest+json"
    return None


def _serve_html_file(path: Path) -> object:
    if not path.is_file():
        logging.error("Fichier HTML introuvable : %s", path)
        abort(404, description=f"Fichier introuvable : {path}")
    resp = send_file(path, mimetype="text/html; charset=utf-8")
    return _apply_static_gzip(resp, path)


def _serve_vitrine_page(slug: str):
    """Renvoie la réponse HTML ou None si page absente."""
    key = (slug or "").strip("/").lower()
    filename = VITRINE_PAGE_ALIASES.get(key)
    if not filename:
        return None
    path = VITRINE_DIR / filename
    if path.is_file():
        return _serve_html_file(path)
    logging.warning("Page vitrine manquante : %s (attendu %s)", key, path)
    return None


def _estimation_page_response():
    """Page LP estimation — chemin explicite pour éviter les échecs de résolution."""
    if VITRINE_ESTIMATION_HTML.is_file():
        return _serve_html_file(VITRINE_ESTIMATION_HTML)
    page = _serve_vitrine_page("estimation")
    if page is not None:
        return page
    return redirect("/#estimateur")


def _annonces_page_response():
    """Portail d'annonces public."""
    if VITRINE_ANNONCES_HTML.is_file():
        return _serve_html_file(VITRINE_ANNONCES_HTML)
    page = _serve_vitrine_page("annonces")
    if page is not None:
        return page
    return redirect("/")


def _ensure_vitrine_public_routes() -> None:
    """Ré-enregistre les pages vitrine au boot (process Flask pas redémarré)."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    to_register = [
        ("/estimation", _estimation_page_response),
        ("/estimer", _estimation_page_response),
        ("/estimer-votre-bien", _estimation_page_response),
        ("/vitrine/estimation.html", _estimation_page_response),
        ("/annonces", _annonces_page_response),
        ("/portail", _annonces_page_response),
        ("/vitrine/annonces.html", _annonces_page_response),
    ]
    if "/annonces/<slug>" not in rules:
        app.add_url_rule(
            "/annonces/<slug>",
            "vitrine_annonce_detail_boot",
            vitrine_annonce_detail,
        )
    for rule, handler in to_register:
        if rule not in rules:
            app.add_url_rule(rule, f"vitrine_page_{rule.replace('/', '_')}", handler)


@app.route("/")
@app.route("/accueil")
@app.route("/vitrine")
@app.route("/vitrine/")
def vitrine_home():
    if VITRINE_INDEX.is_file():
        return _serve_html_file(VITRINE_INDEX)
    logging.error("Page d'accueil introuvable : %s", VITRINE_INDEX)
    landing = VITRINE_DIR / "landing.html"
    if landing.is_file():
        return _serve_html_file(landing)
    return "Page d'accueil introuvable", 404


@app.route("/estimation")
@app.route("/estimer")
@app.route("/estimer-votre-bien")
@app.route("/vitrine/estimation.html")
def vitrine_estimation():
    return _estimation_page_response()


@app.route("/publier-annonce")
def vitrine_publier_annonce_redirect():
    return redirect("/estimation", code=302)


@app.route("/annonces/<slug>")
def vitrine_annonce_detail(slug: str):
    from crm.portal.public_page import listing_detail_response

    return listing_detail_response(slug)


@app.route("/annonces")
@app.route("/portail")
@app.route("/vitrine/annonces.html")
def vitrine_annonces():
    return _annonces_page_response()


@app.route("/offre")
@app.route("/tarifs")
def vitrine_offre():
    offre = VITRINE_DIR / "offre.html"
    if offre.is_file():
        return _serve_html_file(offre)
    return redirect("/#tarifs")


@app.route("/robots.txt")
def robots_txt():
    from crm.config import SITE_URL

    body = f"""User-agent: *
Allow: /
Disallow: /crm/
Disallow: /api/

Sitemap: {SITE_URL}/sitemap.xml
"""
    return body, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/sitemap.xml")
def sitemap_xml():
    from crm.config import SITE_URL
    from crm.portal.storage import list_published_slugs

    pages = ["/", "/offre", "/estimation", "/annonces", "/legal"]
    static_urls = [
        f"  <url><loc>{SITE_URL}{p}</loc><changefreq>weekly</changefreq></url>"
        for p in pages
    ]
    listing_urls = []
    for row in list_published_slugs():
        slug = row.get("slug")
        if not slug:
            continue
        lastmod = (row.get("published_at") or "")[:10]
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        listing_urls.append(
            f"  <url><loc>{SITE_URL}/annonces/{slug}</loc>"
            f"<changefreq>daily</changefreq>{lastmod_tag}</url>"
        )
    urls = "\n".join(static_urls + listing_urls)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>"""
    return xml, 200, {"Content-Type": "application/xml; charset=utf-8"}


@app.route("/favicon.ico")
@app.route("/vitrine/favicon.svg")
def favicon():
    icon = VITRINE_DIR / "favicon.svg"
    if icon.is_file():
        return send_file(icon, mimetype="image/svg+xml")
    abort(404)


@app.route("/landing")
def landing_legacy():
    landing = VITRINE_DIR / "landing.html"
    if landing.is_file():
        return _serve_html_file(landing)
    return redirect("/")


def _serve_legal_page(fragment: str | None = None):
    legal = VITRINE_DIR / "legal.html"
    if not legal.is_file():
        logging.error("Page légale introuvable : %s", legal)
        abort(404, description="vitrine/legal.html manquant — relancez python app.py")
    if fragment:
        return redirect(f"/legal#{fragment}")
    return _serve_html_file(legal)


@app.route("/legal")
@app.route("/legal/")
def vitrine_legal_index():
    return _serve_legal_page()


@app.route("/legal/<path:doc>")
def vitrine_legal_doc(doc: str):
    slug = (doc or "").strip("/").lower()
    aliases = {
        "cgv": "cgv",
        "mentions": "mentions",
        "mentions-legales": "mentions",
        "mentions_legales": "mentions",
        "confidentialite": "confidentialite",
        "privacy": "confidentialite",
        "politique-de-confidentialite": "confidentialite",
        "dpa": "dpa",
        "donnees": "dpa",
    }
    if slug in aliases:
        return _serve_legal_page(aliases[slug])
    return _serve_legal_page()


@app.route("/cgv")
def legal_cgv():
    return redirect("/legal#cgv")


@app.route("/mentions-legales")
@app.route("/mentions")
def legal_mentions():
    return redirect("/legal#mentions")


@app.route("/confidentialite")
@app.route("/politique-confidentialite")
def legal_privacy():
    return redirect("/legal#confidentialite")


@app.route("/dpa")
def legal_dpa():
    return redirect("/legal#dpa")


# ─── CRM (pages) ───
@app.route("/crm", strict_slashes=False)
@app.route("/crm/", strict_slashes=False)
@app.route("/crm/index.html", strict_slashes=False)
def crm_app():
    return _serve_html_file(CRM_INDEX)


@app.route("/login")
@app.route("/auth")
@app.route("/crm/auth")
@app.route("/crm/auth.html")
def crm_auth_page():
    return _serve_html_file(CRM_DIR / "auth.html")


@app.route("/crm/manifest.webmanifest")
@app.route("/manifest.webmanifest")
def pwa_manifest():
    return send_from_directory(
        CRM_DIR,
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/crm/sw.js")
@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(CRM_DIR, "sw.js", mimetype="application/javascript")
    # Autorise un scope plus large que /crm/ pour contrôler aussi la page /crm
    # (sans slash) — sinon l'interception réseau hors connexion ne s'applique pas.
    resp.headers["Service-Worker-Allowed"] = "/crm"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _with_asset_cache(resp):
    """Cache navigateur : immuable si versionné par ?v= (cache-bust), sinon court.

    Accélère fortement les chargements répétés (les assets ne sont re-téléchargés
    que lorsque ?v= change). Le service worker reste en network-first.
    """
    if request.args.get("v"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    else:
        resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# Cache mémoire des assets statiques pré-compressés gzip. Les fichiers CSS/JS/HTML
# ne changent pas entre deux déploiements : les recompresser à chaque requête
# (gzip niveau 6 sur 300 Ko de JS) gaspille du CPU et augmente le TTFB. On garde
# les octets gzip en mémoire, invalidés dès que le mtime/la taille du fichier change.
_STATIC_GZIP_CACHE: dict[str, tuple[int, int, bytes]] = {}
_STATIC_GZIP_LOCK = threading.Lock()


def _cached_gzip(path: Path) -> bytes | None:
    """Retourne les octets gzip d'un fichier (mis en cache), ou None si inutile."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _STATIC_GZIP_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    import gzip

    # Compression maximale : calculée une seule fois puis réutilisée.
    gz = gzip.compress(raw, compresslevel=9)
    if len(gz) >= len(raw):
        return None
    with _STATIC_GZIP_LOCK:
        if len(_STATIC_GZIP_CACHE) > 128:
            _STATIC_GZIP_CACHE.clear()
        _STATIC_GZIP_CACHE[key] = (st.st_mtime_ns, st.st_size, gz)
    return gz


def _apply_static_gzip(resp, path: Path):
    """Sert la version gzip mise en cache d'un fichier statique si possible.

    Pose ``Content-Encoding: gzip`` pour que ``compress_response`` n'essaie pas
    de recompresser. Respecte ``Accept-Encoding`` et les réponses 304/partielles.
    """
    if resp.status_code != 200:
        return resp
    if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return resp
    if "Content-Encoding" in resp.headers:
        return resp
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not any(ctype.startswith(t) for t in _COMPRESSIBLE_TYPES):
        return resp
    gz = _cached_gzip(path)
    if gz is None:
        return resp
    resp.direct_passthrough = False
    resp.set_data(gz)
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(gz))
    vary = resp.headers.get("Vary", "")
    if "accept-encoding" not in vary.lower():
        resp.headers["Vary"] = f"{vary}, Accept-Encoding".lstrip(", ")
    return resp


@app.route("/crm/assets/<path:filename>")
def crm_assets(filename):
    base = CRM_DIR / "assets"
    resp = send_from_directory(
        base,
        filename,
        mimetype=_static_mimetype(filename),
    )
    return _with_asset_cache(_apply_static_gzip(resp, base / filename))


@app.route("/vitrine/assets/<path:filename>")
def vitrine_assets(filename):
    base = VITRINE_DIR / "assets"
    resp = send_from_directory(
        base,
        filename,
        mimetype=_static_mimetype(filename),
    )
    return _with_asset_cache(_apply_static_gzip(resp, base / filename))


@app.route("/assets/<path:filename>")
def legacy_assets(filename):
    """Anciens liens /assets/ — compatibilité CRM / vitrine."""
    if "vitrine" in filename and "/" in filename:
        folder, name = filename.split("/", 1)
        return send_from_directory(
            VITRINE_DIR / "assets" / folder,
            name,
            mimetype=_static_mimetype(name),
        )
    return crm_assets(filename)


@app.route("/api/leads")
def api_leads():
    try:
        return jsonify(get_leads(_aid()))
    except Exception as exc:
        logging.exception("GET /api/leads")
        return jsonify({"error": f"Prospects indisponibles : {exc}"}), 500


@app.route("/api/bootstrap")
def api_bootstrap():
    """Chargement initial CRM — une requête, un passage base pour les leads."""
    from velora_db.connection import DatabaseBusyError

    agency_id = _aid()
    try:
        from crawler.storage import claim_orphan_leads

        try:
            claim_orphan_leads(agency_id)
        except Exception as exc:
            logging.warning("claim_orphan_leads agency=%s: %s", agency_id, exc)
        leads = get_leads(agency_id, claim_orphans=False)
    except DatabaseBusyError as exc:
        return jsonify({"error": str(exc), "code": "database_busy"}), 503
    except Exception as exc:
        logging.exception("GET /api/bootstrap leads")
        return jsonify({"error": f"Prospects indisponibles : {exc}"}), 500
    stats = get_stats(agency_id)
    sources = get_sources(agency_id, sync=True, live_counts=True)
    engine_status = engine.status()
    return jsonify({
        "leads": leads,
        "stats": stats,
        "source_stats": get_source_stats(agency_id),
        "activities": get_activities(agency_id),
        "sources": sources,
        "crawler": {
            **engine_status,
            "found_today": sum(
                s.get("leads_updated_today", s.get("today", 0)) for s in sources
            ),
            "active_sources": sum(1 for s in sources if s["enabled"]),
            "total_leads": stats["total"],
            "prospects_in_db": sum(
                s.get("leads_count", s.get("found", 0)) for s in sources
            ),
        },
        "settings": get_agency_settings(agency_id),
    })


@app.route("/api/radar/summary")
def api_radar_summary():
    """Briefing + playbook — un seul chargement des leads."""
    from crm.radar import build_briefing, build_playbook

    agency_id = _aid()
    # Briefing consultatif : réutilise l'instantané frais produit par
    # /api/bootstrap (~1 s plus tôt) plutôt que de tout recalculer.
    leads = get_leads(agency_id, prefer_snapshot=True)
    settings = get_agency_settings(agency_id)
    agency_name = get_agency_name(agency_id)
    target_cities = settings.get("target_cities") or []
    user = get_current_user() or {}
    caller = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ) or "votre conseiller"
    try:
        return jsonify({
            "briefing": build_briefing(
                leads, agency_name, target_cities=target_cities
            ),
            "playbook": build_playbook(
                leads,
                agency_name,
                caller=caller,
                target_cities=target_cities,
            ),
        })
    except Exception:
        app.logger.exception("api_radar_summary failed for agency %s", agency_id)
        from crm.radar import playbook_static_shell

        return jsonify({
            "briefing": build_briefing(leads, agency_name, target_cities=target_cities),
            "playbook": playbook_static_shell(
                agency_name,
                caller=caller,
                target_cities=target_cities,
                partial=True,
            ),
        })


@app.route("/api/leads/<int:lead_id>/outcome", methods=["POST"])
def api_lead_outcome(lead_id):
    """Enregistre un outcome terrain (call, rdv, mandat_signe, refuse…)."""
    from crawler.storage import get_lead, record_lead_outcome_event

    data = request.get_json(silent=True) or {}
    outcome_type = (data.get("outcome_type") or "").strip().lower()
    allowed = {"call", "rdv", "mandat_signe", "mandat_perdu", "refuse"}
    if outcome_type not in allowed:
        return jsonify({"error": "outcome_type invalide"}), 400
    lead = get_lead(lead_id, _aid())
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    record_lead_outcome_event(
        lead_id,
        _aid(),
        outcome_type,
        agent_id=data.get("agent_id"),
        notes=data.get("notes"),
        lead_snapshot=lead,
    )
    return jsonify({"ok": True, "lead": get_lead(lead_id, _aid())})


@app.route("/api/scoring/weights", methods=["GET"])
def api_scoring_weights():
    """Poids de scoring calibrés pour l'agence (explicables, ±30 %)."""
    from crawler.storage import get_connection
    from crm.scoring.weights import NATIONAL_DEFAULT_WEIGHTS, load_agency_weights

    agency_id = _aid()
    with get_connection() as conn:
        weights = load_agency_weights(conn, agency_id)
    return jsonify({
        "ok": True,
        "agency_id": agency_id,
        "weights": weights,
        "defaults": NATIONAL_DEFAULT_WEIGHTS,
    })


@app.route("/api/leads/<int:lead_id>/image", methods=["GET", "POST"])
def api_lead_image(lead_id):
    from crm.leads.images import (
        resolve_lead_image_path,
        revert_lead_image_to_crawl,
        save_custom_lead_image,
        schedule_lead_image_sync,
    )
    from velora_db.connection import DatabaseBusyError, get_connection

    try:
        agency_id = _aid()
    except DatabaseBusyError as exc:
        return jsonify({"error": str(exc), "code": "database_busy"}), 503

    if request.method == "GET":
        path = resolve_lead_image_path(agency_id, lead_id)
        if path:
            resp = send_file(
                path,
                mimetype="image/webp",
                max_age=86400,
                conditional=True,
            )
            resp.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
            return resp

        try:
            with get_connection() as conn:
                row = conn.execute(
                    """SELECT listing_image_url, source_url, image_custom
                       FROM leads WHERE id = ? AND agency_id = ?""",
                    (lead_id, agency_id),
                ).fetchone()
        except DatabaseBusyError as exc:
            return jsonify({"error": str(exc), "code": "database_busy"}), 503

        if not row:
            return jsonify({"error": "Prospect introuvable"}), 404

        url = (row["listing_image_url"] or "").strip()
        if url and not int(row["image_custom"] or 0):
            schedule_lead_image_sync(
                lead_id,
                agency_id,
                url,
                respect_custom=True,
                referer=(row["source_url"] or "").strip() or None,
                force=True,
            )
        return jsonify({"error": "Image en cours de téléchargement"}), 404

    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404

    action = (request.form.get("action") or "").strip().lower()
    if request.is_json:
        body = request.get_json(silent=True) or {}
        action = action or (body.get("action") or "").strip().lower()

    if action == "revert":
        if not revert_lead_image_to_crawl(lead_id, agency_id):
            return jsonify({"error": "Image crawl introuvable — recrawlez l'annonce"}), 400
        return jsonify({"ok": True, "lead": get_lead(lead_id, agency_id)})

    raw = None
    if request.files and request.files.get("image"):
        raw = request.files["image"].read()
    if not raw:
        return jsonify({"error": "Fichier image requis"}), 400
    if len(raw) > 8 * 1024 * 1024:
        return jsonify({"error": "Image trop lourde (max 8 Mo)"}), 400
    if not save_custom_lead_image(lead_id, agency_id, raw):
        return jsonify({"error": "Impossible d'enregistrer l'image"}), 400
    return jsonify({"ok": True, "lead": get_lead(lead_id, agency_id)})


@app.route("/api/leads/<int:lead_id>/image/<int:idx>", methods=["GET"])
def api_lead_gallery_image(lead_id, idx):
    """Sert une image de la galerie de l'annonce (WebP, marquages retirés)."""
    from crm.leads.images import resolve_lead_gallery_path
    from velora_db.connection import DatabaseBusyError

    try:
        agency_id = _aid()
    except DatabaseBusyError as exc:
        return jsonify({"error": str(exc), "code": "database_busy"}), 503

    path = resolve_lead_gallery_path(agency_id, lead_id, idx)
    if not path:
        return jsonify({"error": "Image introuvable"}), 404
    resp = send_file(path, mimetype="image/webp", max_age=86400, conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    return resp


@app.route("/api/leads/<int:lead_id>/image/sync", methods=["POST"])
def api_lead_image_sync(lead_id):
    """Re-télécharge l'image depuis l'URL portail (si pas d'image personnalisée)."""
    from crm.leads.images import sync_lead_image_from_url

    agency_id = _aid()
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    url = (lead.get("listing_image_url") or "").strip()
    if not url:
        return jsonify({"error": "Aucune URL d'image sur cette fiche — recrawlez l'annonce"}), 400
    ok = sync_lead_image_from_url(lead_id, agency_id, url, respect_custom=True)
    if not ok and lead.get("image_custom"):
        return jsonify({
            "ok": False,
            "error": "Image personnalisée conservée — utilisez « Garder l'image crawl » pour réinitialiser",
            "lead": lead,
        }), 409
    if not ok:
        return jsonify({"error": "Téléchargement impossible"}), 502
    return jsonify({"ok": True, "lead": get_lead(lead_id, agency_id)})


@app.route("/api/leads/<int:lead_id>", methods=["GET", "PATCH", "DELETE"])
def api_lead(lead_id):
    if request.method == "DELETE":
        if not delete_lead(lead_id, _aid()):
            return jsonify({"error": "Prospect introuvable"}), 404
        return jsonify({"ok": True, "leads": get_leads(_aid())})

    if request.method == "PATCH":
        data = request.get_json(silent=True) or {}
        try:
            lead = patch_lead(lead_id, _aid(), data)
        except Exception as exc:
            logging.exception("PATCH /api/leads/%s", lead_id)
            return jsonify({"error": f"Enregistrement impossible : {exc}"}), 500
        if not lead:
            return jsonify({"error": "Prospect introuvable"}), 404
        return jsonify({"ok": True, "lead": lead})

    lead = get_lead(lead_id, _aid())
    if not lead:
        return jsonify({"error": "Lead introuvable"}), 404
    return jsonify(lead)


@app.route("/api/radar/briefing", methods=["GET"])
@app.route("/api/radar/briefing/", methods=["GET"])
def api_radar_briefing():
    from crm.radar import build_briefing

    agency_id = _aid()
    leads = get_leads(agency_id)
    settings = get_agency_settings(agency_id)
    return jsonify(
        build_briefing(
            leads,
            get_agency_name(agency_id),
            target_cities=settings.get("target_cities") or [],
        )
    )


@app.route("/api/radar/playbook", methods=["GET"])
@app.route("/api/radar/playbook/", methods=["GET"])
def api_radar_playbook():
    from crm.radar import build_playbook, playbook_static_shell

    agency_id = _aid()
    settings = get_agency_settings(agency_id)
    user = get_current_user() or {}
    caller = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ) or "votre conseiller"
    agency_name = get_agency_name(agency_id)
    target_cities = settings.get("target_cities") or []
    try:
        leads = get_leads(agency_id)
        return jsonify(
            build_playbook(
                leads,
                agency_name,
                caller=caller,
                target_cities=target_cities,
            )
        )
    except Exception:
        app.logger.exception("api_radar_playbook failed for agency %s", agency_id)
        return jsonify(
            playbook_static_shell(
                agency_name,
                caller=caller,
                target_cities=target_cities,
                partial=True,
            )
        )


@app.route("/api/radar/playbook/static", methods=["GET"])
def api_radar_playbook_static():
    """Guide + modèles sans calcul sur les leads (secours client)."""
    from crm.radar import playbook_static_shell

    agency_id = _aid()
    settings = get_agency_settings(agency_id)
    user = get_current_user() or {}
    caller = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ) or "votre conseiller"
    return jsonify(
        playbook_static_shell(
            get_agency_name(agency_id),
            caller=caller,
            target_cities=settings.get("target_cities") or [],
            partial=True,
        )
    )


@app.route("/api/radar/settings", methods=["GET", "PATCH"])
def api_radar_settings():
    agency_id = _aid()
    if request.method == "GET":
        return jsonify({"ok": True, "settings": get_agency_settings(agency_id)})
    data = request.get_json(silent=True) or {}
    settings = upsert_agency_settings(agency_id, data)
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/leads/<int:lead_id>/estimate", methods=["GET", "POST"])
def api_lead_price_estimate(lead_id):
    from crm.estimator.service import build_price_estimate, estimator_form_schema

    agency_id = _aid()
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    if request.method == "GET":
        return jsonify({"ok": True, "schema": estimator_form_schema(), "lead": lead})
    data = request.get_json(silent=True) or {}
    try:
        result = build_price_estimate(lead, data.get("inputs") or data)
    except Exception as exc:
        logging.exception("POST /api/leads/%s/estimate", lead_id)
        return jsonify({"ok": False, "error": str(exc)}), 500

    # Persistance pour cohérence inter-onglets : l'estimation vit sur le lead,
    # visible depuis Prospects, Carte, fiche, etc.
    if result.get("ok") and data.get("save"):
        try:
            from crm.estimator.storage import save_lead_estimate

            from crm.leads.shared_pool import pool_agency_id

            saved_at = save_lead_estimate(lead_id, pool_agency_id(), result)
            result["saved_at"] = saved_at
            result["lead"] = get_lead(lead_id, agency_id)
        except Exception:
            logging.exception("save_lead_estimate %s", lead_id)
    return jsonify(result)


@app.route("/api/leads/manual", methods=["POST"])
def api_lead_manual_create():
    """Crée un prospect depuis l'estimateur CRM (propriétaire optionnel dans l'UI)."""
    from crm.estimator.public_lead import create_prospect_from_estimate_form

    agency_id = _aid()
    data = request.get_json(silent=True) or {}
    if data.get("inputs"):
        data = {**data, **(data.get("inputs") or {})}

    result = create_prospect_from_estimate_form(
        data,
        source_label="Estimation",
        origin="crm",
        require_owner=True,
        discovering_agency_id=agency_id,
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result), 201


@app.route("/api/leads/<int:lead_id>/matches")
def api_lead_matches(lead_id):
    """Acquéreurs / locataires compatibles + transaction la plus pertinente."""
    from crm.mandates.storage import list_property_clients
    from crm.matching.service import build_lead_matches

    agency_id = _aid()
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    try:
        clients = list_property_clients(agency_id)
        return jsonify(build_lead_matches(lead, clients))
    except Exception as exc:
        logging.exception("GET /api/leads/%s/matches", lead_id)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/leads/<int:lead_id>/address", methods=["GET", "POST"])
def api_lead_address(lead_id):
    """Adresse probable estimée + score de confiance + candidats justifiés.

    GET  : renvoie le dernier rapprochement persisté (ou 404 si jamais calculé).
    POST : (re)calcule à la demande en croisant DPE / BAN / DVF / cadastre.
    """
    agency_id = _aid()
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404

    from crawler.address_match.storage import get_address_match

    if request.method == "GET":
        resolution, updated_at = get_address_match(lead_id, agency_id)
        if not resolution:
            return jsonify({
                "ok": False,
                "reason": "Aucun rapprochement calculé — lancez un POST pour estimer.",
                "lead_id": lead_id,
            }), 404
        return jsonify({"ok": True, "updated_at": updated_at, **resolution})

    try:
        from crawler.address_match.queue import resolve_and_store_lead_address

        resolution = resolve_and_store_lead_address(lead_id, agency_id)
    except Exception as exc:
        logging.exception("POST /api/leads/%s/address", lead_id)
        return jsonify({"ok": False, "error": str(exc)}), 500
    if resolution.get("error"):
        return jsonify({"ok": False, **resolution}), 502
    return jsonify(resolution)


@app.route("/api/dvf/compare/<int:lead_id>", methods=["POST"])
def api_dvf_compare_lead(lead_id):
    try:
        comp = compare_lead_dvf(lead_id, _aid())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    lead = get_lead(lead_id, _aid())
    return jsonify({"ok": True, "comparison": comp, "lead": lead})


@app.route("/api/dvf/compare-all", methods=["POST"])
def api_dvf_compare_all():
    data = request.get_json(silent=True) or {}
    try:
        limit = min(int(data.get("limit") or 25), 50)
    except (TypeError, ValueError):
        limit = 25
    try:
        result = compare_leads_dvf_batch(_aid(), limit=limit)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({
        "ok": True,
        **result,
        "leads": get_leads(_aid()),
    })


@app.route("/api/radar/leads/<int:lead_id>/analysis")
def api_radar_lead_analysis(lead_id):
    from crm.radar import build_listing_analysis

    agency_id = _aid()
    lead = get_lead(lead_id, agency_id)
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    if (lead.get("transaction_type") or "vente") == "vente" and lead.get("price") and lead.get("surface"):
        try:
            compare_and_enrich_lead_dvf(lead_id, agency_id, force_recompare=False)
            lead = get_lead(lead_id, agency_id) or lead
        except Exception:
            pass
    analysis = build_listing_analysis(lead, agency_name=get_agency_name(agency_id))
    return jsonify({"status": "ready", "analysis": analysis, "lead": lead})


def _api_radar_analyze_url_impl():
    """Mode 2 — analyse à la demande : import si besoin, score + facteurs + reco."""
    from crawler.extractors import normalize_listing_url
    from crm.radar import build_listing_analysis

    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL requise"}), 400

    agency_id = _aid()
    norm = normalize_listing_url(url)
    row = get_lead_by_source_url(norm, None)
    if not row:
        job = engine.import_listing_url(url, agency_id=agency_id)
        return jsonify({
            "status": "importing",
            "job_id": job.get("id") or job.get("job_id"),
            "url": norm,
        })

    lead_id = row["id"]
    if (row.get("transaction_type") or "vente") == "vente" and row.get("price") and row.get("surface"):
        try:
            compare_and_enrich_lead_dvf(lead_id, agency_id, force_recompare=False)
        except Exception:
            pass
    lead = get_lead(lead_id, agency_id) or row
    analysis = build_listing_analysis(lead, agency_name=get_agency_name(agency_id))
    return jsonify({"status": "ready", "analysis": analysis, "lead": lead})


@app.route("/api/radar/analyze-url", methods=["POST"])
@app.route("/api/radar/analyze-url/", methods=["POST"])
@app.route("/api/crawler/analyze-listing", methods=["POST"])
def api_radar_analyze_url():
    return _api_radar_analyze_url_impl()


@app.route("/api/radar/leads/<int:lead_id>/script")
def api_radar_call_script(lead_id):
    from crm.radar import build_call_script

    lead = get_lead(lead_id, _aid())
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    user = get_current_user() or {}
    caller = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ) or "votre conseiller"
    # Objet structuré : script d'appel + probabilité de signature + plan
    # multicanal (quand appeler, SMS, email, cadence). full_text_plain sert au copier.
    return jsonify({"script": build_call_script({
        **lead,
        "_caller": caller,
        "_agency": get_agency_name(_aid()),
    })})


@app.route("/api/leads/<int:lead_id>/delete", methods=["POST"])
def api_delete_lead_post(lead_id):
    if not delete_lead(lead_id, _aid()):
        return jsonify({"error": "Prospect introuvable"}), 404
    return jsonify({"ok": True, "leads": get_leads(_aid())})


def _api_refresh_lead_impl(lead_id: int):
    row = get_lead(lead_id, _aid())
    if not row:
        return jsonify({"error": "Prospect introuvable"}), 404
    if not (row.get("source_url") or "").strip():
        return jsonify({"error": "Ce prospect n'a pas de lien d'annonce"}), 400
    try:
        job = engine.refresh_lead(lead_id, agency_id=_aid())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logging.exception("refresh_lead %s", lead_id)
        return jsonify({"error": f"Erreur serveur : {exc}"}), 500
    return jsonify(_job_response(job))


@app.route("/api/leads/<int:lead_id>/refresh", methods=["POST"])
def api_refresh_lead(lead_id):
    """Recrawl approfondi du lien d'annonce pour mettre à jour le prospect."""
    return _api_refresh_lead_impl(lead_id)


@app.route("/api/leads/refresh", methods=["POST"])
def api_refresh_lead_body():
    """Repli si POST sur /leads/<id>/refresh est bloqué par un proxy."""
    data = request.get_json(silent=True) or {}
    lead_id = data.get("lead_id") or data.get("id")
    try:
        lead_id = int(lead_id)
    except (TypeError, ValueError):
        return jsonify({"error": "lead_id requis"}), 400
    return _api_refresh_lead_impl(lead_id)


@app.route("/api/leads/refresh-all", methods=["POST"])
def api_refresh_all_leads():
    """Lance un recrawl séquentiel annonce-par-annonce pour tous les prospects actifs."""
    agency_id = _aid()
    with _refresh_all_lock:
        running = next(
            (
                b
                for b in _refresh_all_batches.values()
                if b.get("agency_id") == agency_id and b.get("status") == "running"
            ),
            None,
        )
    if running:
        return jsonify({
            "ok": True,
            "already_running": True,
            "batch_id": running["id"],
            "queued": running.get("total", 0),
            "skipped_no_url": running.get("skipped_no_url", 0),
            "failed": running.get("failed", 0),
        })

    leads = get_leads(agency_id)
    queued_ids: list[int] = []
    skipped_no_url = 0
    for row in leads:
        if (row.get("status") or "nouveau") == "retire":
            continue
        if not (row.get("source_url") or "").strip():
            skipped_no_url += 1
            continue
        try:
            queued_ids.append(int(row["id"]))
        except (TypeError, ValueError):
            continue

    batch_id = uuid.uuid4().hex
    batch = {
        "id": batch_id,
        "agency_id": agency_id,
        "status": "running",
        "total": len(queued_ids),
        "completed": 0,
        "failed": 0,
        "skipped_no_url": skipped_no_url,
        "current_lead_id": None,
        "current_job_id": None,
        "started_at": time.time(),
        "ended_at": None,
        "logs": [],
    }
    with _refresh_all_lock:
        _refresh_all_batches[batch_id] = batch

    def _worker():
        try:
            for lead_id in queued_ids:
                with _refresh_all_lock:
                    batch["current_lead_id"] = lead_id
                    batch["current_job_id"] = None
                _append_refresh_log(batch, f"Recrawl prospect #{lead_id}…")
                try:
                    job = engine.refresh_lead(lead_id, agency_id=agency_id)
                except Exception as exc:
                    is_gone = "introuvable" in str(exc).lower()
                    with _refresh_all_lock:
                        batch["failed"] += 1
                    if is_gone:
                        # Fiche supprimée / passée hors secteur entre-temps : skip discret.
                        _append_refresh_log(
                            batch, f"Prospect #{lead_id} : ignoré (hors secteur ou supprimé)"
                        )
                    else:
                        _append_refresh_log(batch, f"Prospect #{lead_id} : échec lancement")
                        logging.exception("refresh_all launch failed for lead %s", lead_id)
                    continue
                job_id = (job or {}).get("id")
                with _refresh_all_lock:
                    batch["current_job_id"] = job_id
                # Attente de fin du job en cours (le moteur n'autorise qu'un job actif).
                terminal = None
                for _ in range(180):  # ~6 min max par annonce
                    if not job_id:
                        break
                    j = get_crawl_job(job_id, agency_id)
                    if not j:
                        break
                    st = (j.get("status") or "").lower()
                    if st in ("completed", "failed", "cancelled"):
                        terminal = st
                        break
                    time.sleep(2)
                with _refresh_all_lock:
                    batch["completed"] += 1
                    if terminal in ("failed", "cancelled"):
                        batch["failed"] += 1
                if terminal in ("failed", "cancelled"):
                    _append_refresh_log(batch, f"Prospect #{lead_id} : échec")
                else:
                    _append_refresh_log(batch, f"Prospect #{lead_id} : terminé")
        finally:
            with _refresh_all_lock:
                batch["status"] = "completed"
                batch["current_lead_id"] = None
                batch["current_job_id"] = None
                batch["ended_at"] = time.time()

    threading.Thread(target=_worker, daemon=True).start()

    return jsonify({
        "ok": True,
        "batch_id": batch_id,
        "queued": len(queued_ids),
        "skipped_no_url": skipped_no_url,
        "failed": 0,
    })


@app.route("/api/leads/refresh-all/<batch_id>/status", methods=["GET"])
def api_refresh_all_leads_status(batch_id: str):
    batch = _refresh_all_batch_snapshot(batch_id)
    if not batch or batch.get("agency_id") != _aid():
        return jsonify({"ok": False, "error": "Batch introuvable"}), 404
    total = int(batch.get("total") or 0)
    completed = int(batch.get("completed") or 0)
    pct = int(round((completed / total) * 100)) if total > 0 else 100
    return jsonify({
        "ok": True,
        "id": batch["id"],
        "status": batch.get("status") or "running",
        "total": total,
        "completed": completed,
        "failed": int(batch.get("failed") or 0),
        "skipped_no_url": int(batch.get("skipped_no_url") or 0),
        "current_lead_id": batch.get("current_lead_id"),
        "current_job_id": batch.get("current_job_id"),
        "progress_pct": pct,
        "logs": batch.get("logs") or [],
    })


@app.route("/api/leads/delete-all", methods=["POST"])
def api_delete_all_leads():
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Confirmation requise (confirm: true)"}), 400
    deleted = delete_all_leads(_aid())
    return jsonify({"ok": True, "deleted": deleted, "leads": get_leads(_aid())})


@app.route("/api/stats")
def api_stats():
    try:
        agency_id = _aid()
        return jsonify({
            "stats": get_stats(agency_id),
            "source_stats": get_source_stats(agency_id),
            "activities": get_activities(agency_id),
        })
    except Exception as exc:
        logging.exception("GET /api/stats")
        return jsonify({"error": f"Stats indisponibles : {exc}"}), 500


@app.route("/api/roi/stats")
def api_roi_stats():
    """Tableau de bord ROI mensuel — appels → RDV → mandats signés."""
    import datetime

    from crawler.storage import get_connection

    agency_id = _aid()
    now = datetime.datetime.now(datetime.timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT outcome_type, COUNT(*) AS n
               FROM lead_outcomes
               WHERE agency_id = ? AND outcome_at >= ?
               GROUP BY outcome_type""",
            (agency_id, month_start),
        ).fetchall()
        all_rows = conn.execute(
            """SELECT outcome_type, COUNT(*) AS n
               FROM lead_outcomes
               WHERE agency_id = ?
               GROUP BY outcome_type""",
            (agency_id,),
        ).fetchall()

    counts = {r["outcome_type"]: r["n"] for r in rows}
    all_counts = {r["outcome_type"]: r["n"] for r in all_rows}
    calls = counts.get("call", 0)
    rdvs = counts.get("rdv", 0)
    mandats = counts.get("mandat_signe", 0)
    subscription_eur = 500
    commission_eur = 3000
    roi_multiple = round((mandats * commission_eur) / subscription_eur, 1) if mandats else 0
    return jsonify({
        "ok": True,
        "period": "month",
        "month_start": month_start,
        "calls": calls,
        "rdvs": rdvs,
        "mandats": mandats,
        "roi_multiple": roi_multiple,
        "subscription_eur": subscription_eur,
        "commission_eur": commission_eur,
        "all_time": {
            "calls": all_counts.get("call", 0),
            "rdvs": all_counts.get("rdv", 0),
            "mandats": all_counts.get("mandat_signe", 0),
        },
    })


@app.route("/api/notifications/prefs", methods=["GET", "POST"])
def api_notification_prefs():
    """Préférences d'email : briefing quotidien + alertes opportunités."""
    from crm.notifications.service import get_notification_prefs, set_notification_prefs

    agency_id = _aid()
    if request.method == "GET":
        return jsonify({"ok": True, "prefs": get_notification_prefs(agency_id)})
    data = request.get_json(silent=True) or {}
    prefs = set_notification_prefs(
        agency_id,
        **{k: data[k] for k in ("daily_briefing", "alerts", "min_score") if k in data},
    )
    return jsonify({"ok": True, "prefs": prefs})


@app.route("/api/notifications/test", methods=["POST"])
def api_notification_test():
    """Envoie immédiatement un briefing (et/ou des alertes) pour vérifier l'email."""
    from crm.email.service import email_enabled
    from crm.notifications.service import send_alert_digest, send_daily_briefing

    agency_id = _aid()
    if not email_enabled():
        return jsonify({
            "ok": False,
            "error": "SMTP non configuré — renseignez SMTP_HOST/SMTP_FROM pour activer l'envoi.",
        }), 400
    kind = ((request.get_json(silent=True) or {}).get("kind") or "briefing").strip().lower()
    if kind == "alerts":
        n = send_alert_digest(agency_id, force=True)
        return jsonify({"ok": True, "sent": "alerts", "opportunities": n})
    sent = send_daily_briefing(agency_id, force=True)
    return jsonify({"ok": sent, "sent": "briefing"})


@app.route("/api/sources", methods=["GET", "POST"])
def api_sources():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or data.get("link") or "").strip()
        name = (data.get("name") or "").strip() or None
        base_url = (data.get("base_url") or "").strip()
        search_url = (data.get("search_url") or "").strip() or None

        if not url and not base_url:
            return jsonify({"error": "Collez un lien, ex. https://www.paruvendu.fr/immobilier/"}), 400

        try:
            if url:
                source = add_source(_aid(), url=url, name=name)
            else:
                if not name:
                    return jsonify({"error": "Nom requis si vous utilisez deux champs URL"}), 400
                source = add_source(
                    _aid(), name=name, base_url=base_url, search_url=search_url
                )
            engine.refresh_adapters(_aid())
            return jsonify({"ok": True, "source": source, "sources": _sources_payload()}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    return jsonify(_sources_payload())


@app.route("/api/sources/preview-urls", methods=["GET"])
def api_sources_preview_urls():
    """Aperçu des liens de recherche par source pour une ville (sans enregistrement)."""
    from crawler.adapters import build_adapters
    from crawler.city_urls import preview_search_urls_for_sources

    city = (request.args.get("city") or request.args.get("ville") or "").strip() or None
    agency_id = _aid()
    try:
        sources = get_sources(agency_id)
        adapters = build_adapters(sources)
        adapter_urls = {
            sid: ad.config.search_url
            for sid, ad in adapters.items()
            if ad and getattr(ad, "config", None)
        }
        urls = preview_search_urls_for_sources(
            sources, city, adapter_search_urls=adapter_urls
        )
        return jsonify({"ok": True, "city": city, "urls": urls})
    except Exception as exc:
        logging.exception("preview-urls agency=%s city=%s", agency_id, city)
        return jsonify({"error": f"Aperçu des liens indisponible : {exc}"}), 500


def _api_delete_source_impl(source_id: str):
    try:
        if not delete_source(source_id, _aid()):
            return jsonify({"error": "Source introuvable ou déjà supprimée"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    engine.refresh_adapters(_aid())
    return jsonify({"ok": True, "sources": _sources_payload()})


@app.route("/api/sources/remove", methods=["POST"])
def api_remove_source_by_body():
    """Repli : id dans le corps JSON (si DELETE sur l’URL est bloqué)."""
    data = request.get_json(silent=True) or {}
    source_id = (data.get("id") or data.get("source_id") or "").strip()
    if not source_id:
        return jsonify({"error": "id requis"}), 400
    return _api_delete_source_impl(source_id)


@app.route("/api/sources/<source_id>", methods=["PATCH", "DELETE"])
def api_source_detail(source_id):
    if request.method == "DELETE":
        return _api_delete_source_impl(source_id)

    data = request.get_json(silent=True) or {}
    try:
        source = update_source_fields(
            source_id,
            _aid(),
            enabled=data.get("enabled") if "enabled" in data else None,
            url=(data.get("url") or data.get("search_url") or "").strip() or None,
            name=(data.get("name") or "").strip() or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if source is None:
        return jsonify({"error": "Source introuvable"}), 404

    engine.refresh_adapters(_aid())
    return jsonify({"ok": True, "source": source, "sources": _sources_payload()})


@app.route("/api/sources/<source_id>/delete", methods=["POST"])
def api_delete_source_post(source_id):
    """Repli si DELETE bloqué (ancien serveur / proxy)."""
    return _api_delete_source_impl(source_id)


def _api_update_source_url_impl(source_id: str, url: str, name: str | None = None):
    try:
        source = update_source_fields(source_id, _aid(), url=url, name=name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if source is None:
        return jsonify({"error": "Source introuvable"}), 404
    engine.refresh_adapters(_aid())
    return jsonify({"ok": True, "source": source, "sources": _sources_payload()})


@app.route("/api/sources/<source_id>/url", methods=["POST", "PUT"])
def api_update_source_url(source_id):
    """Repli si PATCH bloqué — enregistre le lien de crawl."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or data.get("search_url") or "").strip()
    if not url:
        return jsonify({"error": "Lien requis"}), 400
    name = (data.get("name") or "").strip() or None
    return _api_update_source_url_impl(source_id, url, name)


def _job_response(job: dict) -> dict:
    return {
        "ok": True,
        "job_id": job["id"],
        "job": job,
    }


@app.route("/api/crawler/status")
def api_crawler_status():
    from crawler.storage import crawl_veille_readiness, maybe_expire_stale_crawl_jobs

    maybe_expire_stale_crawl_jobs()
    agency_id = _aid()
    status = engine.status()
    stats = get_stats(agency_id)
    sources = get_sources(agency_id, sync=False, live_counts=False)
    from crawler.storage import get_veille_feed

    veille = crawl_veille_readiness(agency_id)
    from crawler.config import (
        CRAWL_PROXIES,
        antibot_portals_readiness,
        antibot_setup_hint,
    )

    readiness = antibot_portals_readiness()
    return jsonify({
        **status,
        "found_today": sum(s.get("leads_updated_today", s.get("today", 0)) for s in sources),
        "active_sources": sum(1 for s in sources if s["enabled"]),
        "total_leads": stats["total"],
        "prospects_in_db": stats["total"],
        "veille": veille,
        "veille_feed": get_veille_feed(agency_id, limit=30),
        "antibot_readiness": readiness,
        "antibot_hint": antibot_setup_hint("Les portails protégés (SeLoger, LBC, PAP…)", readiness),
        "proxy_count": len(CRAWL_PROXIES),
        "proxy_host": (CRAWL_PROXIES[0].split("@")[-1] if CRAWL_PROXIES else None),
    })


@app.route("/api/crawler/proxy-test", methods=["POST"])
def api_crawler_proxy_test():
    """Teste le proxy Decodo configuré (ou un proxy collé) — renvoie IP, pays, latence.

    Ne renvoie JAMAIS le mot de passe du proxy au client. Permet de vérifier depuis
    le CRM que Decodo répond et sort bien sur une IP française, sans toucher au .env.
    """
    _aid()
    import json as _json
    import time as _time

    import requests as _rq

    from crawler.config import CRAWL_PROXIES

    data = request.get_json(silent=True) or {}
    proxy_url = (data.get("proxy_url") or "").strip()
    if not proxy_url:
        if not CRAWL_PROXIES:
            return jsonify({
                "ok": False,
                "configured": False,
                "error": "Aucun proxy configuré (CRAWL_PROXIES vide). Renseignez vos identifiants Decodo.",
            })
        proxy_url = CRAWL_PROXIES[0]

    host = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
    proxies = {"http": proxy_url, "https": proxy_url}
    endpoints = ("https://ip.decodo.com/json", "https://api.ipify.org?format=json")
    t0 = _time.monotonic()
    last_err = ""
    for endpoint in endpoints:
        try:
            resp = _rq.get(endpoint, proxies=proxies, timeout=15)
            body = (resp.text or "").strip()
            if "FortiGate" in body or "Application Control Violation" in body:
                return jsonify({
                    "ok": False,
                    "host": host,
                    "error": "Bloqué par un pare-feu réseau (FortiGate) — testez en 4G ou ouvrez les ports Decodo.",
                })
            if resp.ok and body:
                ip = country = None
                try:
                    parsed = _json.loads(body)
                    ip = parsed.get("ip") or (parsed.get("proxy") or {}).get("ip")
                    raw_country = parsed.get("country")
                    country = raw_country.get("code") if isinstance(raw_country, dict) else raw_country
                except Exception:
                    ip = body[:40]
                return jsonify({
                    "ok": True,
                    "host": host,
                    "ip": ip,
                    "country": country,
                    "latency_ms": int((_time.monotonic() - t0) * 1000),
                })
            last_err = f"HTTP {resp.status_code}"
        except Exception as exc:
            last_err = str(exc)[:160]
    return jsonify({"ok": False, "host": host, "error": last_err or "échec du test"})


@app.route("/api/crawler/veille-feed")
def api_crawler_veille_feed():
    from crawler.storage import get_veille_feed

    limit = request.args.get("limit", 40, type=int)
    return jsonify({"ok": True, "items": get_veille_feed(_aid(), limit=limit)})


@app.route("/api/crawler/live-frame")
def api_crawler_live_frame():
    """Dernière capture d'écran du crawler (heatmap live) — JPEG récent ou 204."""
    import time as _t

    from crawler.browser import LIVE_FRAME_PATH

    p = LIVE_FRAME_PATH
    try:
        if not p.is_file() or _t.time() - p.stat().st_mtime > 12:
            return ("", 204)
    except OSError:
        return ("", 204)
    resp = send_file(str(p), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/api/crawler/jobs/<job_id>")
def api_crawl_job(job_id):
    from velora_db.connection import DatabaseBusyError
    from crawler.storage import peek_crawl_job_for_poll

    agency_id = _aid()
    lite = request.args.get("lite") in ("1", "true", "yes")
    include_logs = request.args.get("logs") in ("1", "true", "yes")

    try:
        from_cache = False
        job = None
        if lite and not include_logs:
            job = peek_crawl_job_for_poll(job_id, agency_id)
            from_cache = job is not None
        if job is None:
            job = get_crawl_job(job_id, agency_id)
        if not job:
            return jsonify({"error": "Job introuvable"}), 404

        payload = {**job}
        if lite and not include_logs:
            payload["logs"] = []
        else:
            payload["logs"] = get_crawl_logs_for_job(job_id)

        # Ne pas renvoyer toute la base à chaque poll (timeout / 500 côté client).
        payload["leads"] = None
        payload["sources"] = None
        payload["stats"] = None
        if from_cache:
            payload["_from_cache"] = True

        return jsonify(payload)
    except DatabaseBusyError:
        cached = peek_crawl_job_for_poll(job_id, agency_id) if lite else None
        if cached:
            payload = {**cached, "logs": [], "leads": None, "sources": None, "stats": None}
            payload["_from_cache"] = True
            payload["_database_busy"] = True
            return jsonify(payload), 200
        raise
    except Exception as exc:
        logging.exception("crawl job %s", job_id)
        return jsonify({"error": f"État du crawl indisponible : {exc}"}), 500


@app.route("/api/crawler/jobs/active")
def api_active_job():
    job = get_active_crawl_job(_aid())
    return jsonify({"job": job})


@app.route("/api/crawler/jobs/cancel", methods=["POST"])
def api_cancel_active_jobs():
    n = cancel_all_active_crawl_jobs(_aid())
    return jsonify({"ok": True, "cancelled": n})


@app.route("/api/crawler/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id):
    if not cancel_crawl_job(job_id, _aid()):
        return jsonify({"error": "Aucun crawl actif à annuler"}), 404
    return jsonify({"ok": True})


@app.route("/api/crawler/start", methods=["POST"])
def api_crawler_start():
    data = request.get_json(silent=True) or {}
    interval = data.get("interval_sec") or data.get("interval")
    if interval is not None:
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = None
    engine.start_background(interval=interval)
    return jsonify({"ok": True, **engine.status()})


@app.route("/api/crawler/stop", methods=["POST"])
def api_crawler_stop():
    engine.stop_background()
    return jsonify({"ok": True, **engine.status()})


def _resolve_crawl_city(agency_id: str) -> str | None:
    """Ville du crawl : requête explicite, sinon 1ʳᵉ ville territoire, sinon national (None)."""
    from crawler.storage import resolve_crawl_city

    return resolve_crawl_city(agency_id=agency_id, request_data=request.get_json(silent=True) or {})


@app.route("/api/crawler/scan", methods=["POST"])
def api_crawler_scan():
    city = _resolve_crawl_city(_aid())
    job = engine.scan_all_enabled(city=city, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/crawler/streamestate/verify", methods=["POST"])
def api_crawler_streamestate_verify():
    """Vérifie/complète les annonces déjà en base via StreamEstate, à coût crédit minimal."""
    from crawler.streamestate import StreamEstateError, streamestate_display_name, verify_existing_leads
    from crawler.storage import is_streamestate_enabled_for_agency

    if not is_streamestate_enabled_for_agency(_aid()):
        return jsonify(
            {
                "ok": False,
                "error": (
                    f"{streamestate_display_name()} est désactivé — "
                    "activez-le dans Portails pour vérifier les fiches."
                ),
            }
        ), 403

    data = request.get_json(silent=True) or {}
    kwargs: dict = {}
    if data.get("max_pages") is not None:
        try:
            kwargs["max_pages"] = int(data["max_pages"])
        except (TypeError, ValueError):
            return jsonify({"error": "max_pages invalide"}), 400
    if data.get("only_incomplete") is False:
        kwargs["only_incomplete"] = False
    try:
        summary = verify_existing_leads(_aid(), **kwargs)
    except StreamEstateError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        logging.exception("streamestate verify")
        return jsonify({"ok": False, "error": f"Erreur serveur : {exc}"}), 500
    return jsonify({"ok": True, "summary": summary})


@app.route("/api/crawler/scan/<source_id>", methods=["POST"])
def api_crawler_scan_source(source_id):
    source = get_source(source_id, _aid())
    if not source:
        return jsonify({"error": "Source introuvable pour votre agence"}), 404
    if not source.get("enabled"):
        return jsonify(
            {
                "error": (
                    f"{source.get('name') or 'Cette source'} est désactivée — "
                    "activez-la dans Portails pour lancer une mise à jour."
                ),
                "code": "source_disabled",
            }
        ), 403
    paid = _paid_portal_crawl_response(source=source)
    if paid:
        return paid
    city = _resolve_crawl_city(_aid())
    job = engine.scan_source(source_id, city=city, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/public/config")
def api_public_config():
    from crm.config import public_site_config
    from crm.billing.config import public_stripe_config

    return jsonify({"ok": True, **public_site_config(), "stripe": public_stripe_config()})


@app.route("/api/public/estimate/schema", methods=["GET"])
def api_public_estimate_schema():
    from crm.estimator.service import estimator_form_schema

    return jsonify({"ok": True, "schema": estimator_form_schema()})


@app.route("/api/public/estimate", methods=["POST"])
def api_public_vitrine_estimate():
    """Étape 1 vitrine : bien + détails → prospect pool + estimation (sans coordonnées)."""
    from crm.estimator.public_lead import handle_public_vitrine_estimate

    data = request.get_json(silent=True) or {}
    if data.get("inputs") and isinstance(data.get("inputs"), dict):
        data = {**data, **data["inputs"]}
    if (data.get("website") or "").strip():
        return jsonify({"ok": False, "error": "Requête refusée."}), 400

    try:
        out = handle_public_vitrine_estimate(data)
    except Exception as exc:
        logging.exception("POST /api/public/estimate")
        return jsonify({"ok": False, "error": "Erreur serveur."}), 500

    status = 201 if out.get("ok") else 400
    return jsonify(out), status


@app.route("/api/public/estimate/contact", methods=["POST"])
def api_public_vitrine_estimate_contact():
    """Étape 2 : souhaite vendre / contact agences du secteur ou prospect seul."""
    from crm.estimator.public_lead import handle_public_vitrine_contact

    data = request.get_json(silent=True) or {}
    if (data.get("website") or "").strip():
        return jsonify({"ok": False, "error": "Requête refusée."}), 400

    try:
        out = handle_public_vitrine_contact(data)
    except Exception:
        logging.exception("POST /api/public/estimate/contact")
        return jsonify({"ok": False, "error": "Erreur serveur."}), 500

    status = 200 if out.get("ok") else 400
    return jsonify(out), status


@app.route("/embed/estimation")
def embed_estimation_page():
    """Page d'estimation intégrable en iframe (widget marque blanche agence)."""
    if VITRINE_ESTIMATION_HTML.is_file():
        resp = _serve_html_file(VITRINE_ESTIMATION_HTML)
        try:
            resp.headers.pop("X-Frame-Options", None)
            resp.headers["Content-Security-Policy"] = "frame-ancestors *"
        except Exception:
            pass
        return resp
    return redirect("/estimation")


@app.route("/embed/estimation.js")
def embed_estimation_loader():
    """Snippet JS à coller sur le site de l'agence : injecte l'iframe d'estimation.

    Usage : <script src="https://veliora.fr/embed/estimation.js" data-agency="slug"></script>
    """
    from crm.config import SITE_URL

    base = (SITE_URL or request.host_url).rstrip("/")
    js = """(function(){
  var s = document.currentScript;
  if (!s) return;
  var agency = s.getAttribute('data-agency') || '';
  var height = s.getAttribute('data-height') || '780';
  var src = '__BASE__/embed/estimation' + (agency ? ('?agency=' + encodeURIComponent(agency)) : '');
  var iframe = document.createElement('iframe');
  iframe.src = src;
  iframe.title = 'Estimation immobilière';
  iframe.loading = 'lazy';
  iframe.style.width = '100%';
  iframe.style.maxWidth = '720px';
  iframe.style.border = '0';
  iframe.style.height = height + 'px';
  s.parentNode.insertBefore(iframe, s);
  window.addEventListener('message', function(e){
    if (e && e.data && e.data.veliora_embed_height) {
      iframe.style.height = e.data.veliora_embed_height + 'px';
    }
  });
})();""".replace("__BASE__", base)
    resp = Response(js, mimetype="application/javascript")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/api/public/portal/listings", methods=["GET", "POST"])
def api_public_portal_listings():
    if request.method == "POST":
        return jsonify({
            "ok": False,
            "error": "La publication d’annonces est réservée aux agences connectées au CRM.",
        }), 403

    from crm.portal.storage import list_listings, public_listing_payload

    city = (request.args.get("city") or "").strip()
    tx = (request.args.get("transaction_type") or "").strip().lower() or None
    items = list_listings(
        public_only=True,
        publisher_type="agency",
        city=city or None,
        transaction_type=tx,
        limit=80,
    )
    return jsonify({"ok": True, "listings": [public_listing_payload(x) for x in items]})


@app.route("/api/public/portal/listings/<listing_id>", methods=["GET"])
def api_public_portal_listing_detail(listing_id):
    from crm.portal.storage import get_listing, get_listing_by_slug, public_listing_payload

    item = get_listing(listing_id, public=True)
    if not item:
        item = get_listing_by_slug(listing_id, public=True)
    if not item:
        return jsonify({"ok": False, "error": "Annonce introuvable."}), 404
    return jsonify({"ok": True, "listing": public_listing_payload(item)})


@app.route("/api/public/portal/listings/<listing_id>/image/<int:idx>", methods=["GET"])
def api_public_portal_listing_image(listing_id, idx):
    """Sert une image d'annonce portail (WebP, marquages retirés, cache local)."""
    from crm.portal.images import resolve_portal_image_path

    path = resolve_portal_image_path(listing_id, idx)
    if not path:
        abort(404)
    resp = send_file(path, mimetype="image/webp", max_age=86400, conditional=True)
    resp.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    return resp


@app.route("/api/public/portal/listings/<listing_id>/inquiry", methods=["POST"])
def api_public_portal_listing_inquiry(listing_id):
    from crm.portal.inquiry import submit_listing_inquiry

    data = request.get_json(silent=True) or {}
    try:
        out = submit_listing_inquiry(listing_id, data)
    except Exception:
        logging.exception("POST inquiry listing %s", listing_id)
        return jsonify({"ok": False, "error": "Erreur serveur."}), 500
    status = 200 if out.get("ok") else 400
    return jsonify(out), status


@app.route("/api/portal/listings", methods=["GET", "POST"])
def api_portal_listings():
    from crm.portal.service import create_agency_listing
    from crm.portal.storage import count_unread_inquiries, ensure_listing_public_slug, list_listings

    aid = _aid()
    if request.method == "GET":
        status = (request.args.get("status") or "").strip() or None
        items = list_listings(agency_id=aid, status=status, limit=120)
        from crm.portal.storage import get_listing

        for it in items:
            if it.get("status") == "published" and not it.get("public_slug"):
                ensure_listing_public_slug(it["id"])
            full = get_listing(it["id"], agency_id=aid) if it.get("status") == "published" else None
            slug = (full or it).get("public_slug")
            if slug:
                it["public_slug"] = slug
                it["public_url"] = f"/annonces/{slug}"
            it["inquiry_unread_count"] = count_unread_inquiries(aid, it["id"])
        return jsonify({"ok": True, "listings": items})
    data = request.get_json(silent=True) or {}
    out = create_agency_listing(aid, data)
    return jsonify(out), 201 if out.get("ok") else 400


@app.route("/api/portal/listings/<listing_id>/inquiries", methods=["GET"])
def api_portal_listing_inquiries(listing_id):
    from crm.portal.inquiry import agency_listing_inquiries

    aid = _aid()
    out = agency_listing_inquiries(aid, listing_id)
    return jsonify(out), 200 if out.get("ok") else 404


@app.route("/api/portal/listings/<listing_id>", methods=["GET", "PATCH", "DELETE"])
def api_portal_listing(listing_id):
    from crm.portal.service import update_agency_listing
    from crm.portal.storage import count_unread_inquiries, delete_listing, get_listing

    aid = _aid()
    if request.method == "GET":
        item = get_listing(listing_id, agency_id=aid)
        if not item:
            return jsonify({"ok": False, "error": "Annonce introuvable."}), 404
        item["inquiry_unread_count"] = count_unread_inquiries(aid, listing_id)
        if item.get("public_slug"):
            item["public_url"] = f"/annonces/{item['public_slug']}"
        return jsonify({"ok": True, "listing": item})
    if request.method == "DELETE":
        if delete_listing(listing_id, aid):
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Annonce introuvable."}), 404
    data = request.get_json(silent=True) or {}
    out = update_agency_listing(aid, listing_id, data)
    return jsonify(out), 200 if out.get("ok") else 400


@app.route("/api/portal/listings/from-lead", methods=["POST"])
@app.route("/api/portal/listings/from-lead/<int:lead_id>", methods=["POST"])
def api_portal_publish_from_lead(lead_id: int | None = None):
    """Publie une annonce détectée — exige un mandat signé (workflow transaction)."""
    from crm.portal.service import publish_listing_from_lead

    aid = _aid()
    data = request.get_json(silent=True) or {}
    lid = lead_id if lead_id is not None else data.get("lead_id")
    try:
        lid = int(lid)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "lead_id requis"}), 400
    out = publish_listing_from_lead(aid, lid, data)
    if out.get("ok"):
        return jsonify(out), 201
    code = 409 if out.get("code") == "signed_mandate_required" else 400
    return jsonify(out), code


# ─── Agents (collaborateurs) & prise en charge ───

@app.route("/api/agents", methods=["GET"])
def api_agents():
    from crm.agents.storage import list_agents

    return jsonify({"ok": True, "agents": list_agents(_aid())})


@app.route("/api/leads/<int:lead_id>/assign", methods=["POST"])
def api_assign_lead(lead_id: int):
    """Prise en charge d'un prospect par un agent de l'agence."""
    from crm.agents.storage import assign_lead

    aid = _aid()
    lead = get_lead(lead_id, aid)
    if not lead:
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    data = request.get_json(silent=True) or {}
    agent_id = (data.get("agent_id") or "").strip()
    if not agent_id:
        return jsonify({"ok": False, "error": "agent_id requis"}), 400
    out = assign_lead(aid, lead_id, agent_id)
    if out.get("ok"):
        # Prise en charge = ouverture automatique d'un dossier pour rassembler les
        # pièces (identité, diagnostics, titre de propriété…) que l'agent importera.
        try:
            from crm.mandates.dossiers import ensure_lead_dossier

            dossier = ensure_lead_dossier(aid, lead_id, lead)
            if dossier:
                out["dossier_id"] = dossier["id"]
        except Exception:
            logging.exception("ensure_lead_dossier after assign lead=%s", lead_id)
    return jsonify(out), 200 if out.get("ok") else 400


@app.route("/api/leads/<int:lead_id>/document-folder", methods=["GET"])
def api_lead_document_folder(lead_id: int):
    """Dossier de pièces du prospect (créé à la prise en charge) + checklist fusionnée."""
    from crm.mandates.dossiers import ensure_lead_dossier, get_dossier_documents

    aid = _aid()
    lead = get_lead(lead_id, aid)
    if not lead:
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    dossier = ensure_lead_dossier(aid, lead_id, lead)
    if not dossier:
        return jsonify({"ok": False, "error": "Dossier indisponible"}), 500
    mandate = None
    if dossier.get("mandate_id") and not str(dossier["mandate_id"]).startswith("lead:"):
        from crm.mandates.storage import get_seller_mandate

        mandate = get_seller_mandate(dossier["mandate_id"], aid)
    return jsonify({
        "ok": True,
        "dossier_id": dossier["id"],
        "title": dossier.get("title"),
        "documents": get_dossier_documents(dossier["id"], aid, mandate),
    })


@app.route("/api/leads/<int:lead_id>/unassign", methods=["POST"])
def api_unassign_lead(lead_id: int):
    from crm.agents.storage import unassign_lead

    unassign_lead(_aid(), lead_id)
    return jsonify({"ok": True})


# ─── Transactions (cycle de vie complet d'une affaire) ───

@app.route("/api/transactions", methods=["GET"])
def api_transactions():
    """Affaires de l'agence. `?scope=mine` = uniquement celles de l'agent connecté."""
    from crm.transactions.service import build_transactions

    scope = (request.args.get("scope") or "").strip().lower()
    agent_id = (request.args.get("agent_id") or "").strip() or None
    if scope == "mine":
        agent_id = (get_current_user() or {}).get("id")
    try:
        return jsonify(build_transactions(_aid(), for_agent_id=agent_id))
    except Exception as exc:
        logging.exception("GET /api/transactions")
        return jsonify({"ok": False, "error": f"Transactions indisponibles : {exc}"}), 500


@app.route("/api/transactions/<int:lead_id>/dossier", methods=["GET"])
def api_transaction_dossier(lead_id: int):
    """Dossier dynamique complet (annonce + vendeur + acquéreur + agent + agence)."""
    from crm.transactions.service import compose_dossier

    aid = _aid()
    if not get_lead(lead_id, aid):
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    return jsonify(compose_dossier(aid, lead_id))


@app.route("/api/mandates/<mandate_id>/validate", methods=["POST"])
def api_mandate_validate(mandate_id: str):
    """Validation d'une partie (owner|agent). Les 2 → mandat signé + dossier auto."""
    from crm.mandates.storage import validate_mandate_party

    aid = _aid()
    data = request.get_json(silent=True) or {}
    party = (data.get("party") or "").strip().lower()
    user = get_current_user() or {}
    agent_id = data.get("agent_id") or (user.get("id") if party == "agent" else None)
    agent_name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or None
    try:
        out = validate_mandate_party(
            mandate_id, aid, party, agent_id=agent_id, agent_name=agent_name
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not out:
        return jsonify({"ok": False, "error": "Mandat introuvable"}), 404
    return jsonify({"ok": True, **out})


@app.route("/api/transactions/<int:lead_id>/buyer", methods=["POST"])
def api_transaction_buyer(lead_id: int):
    """Rapproche un acquéreur / locataire (étape 7)."""
    from crm.mandates.storage import get_property_client, list_property_clients
    from crm.matching.service import eligible_clients_for_lead
    from crm.transactions.storage import set_progress

    aid = _aid()
    lead = get_lead(lead_id, aid)
    if not lead:
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if client_id:
        if not get_property_client(client_id, aid):
            return jsonify({"ok": False, "error": "Client introuvable"}), 404
        allowed = {
            c["client_id"]
            for c in eligible_clients_for_lead(lead, list_property_clients(aid))
        }
        if client_id not in allowed:
            return jsonify({
                "ok": False,
                "error": "Ce profil ne correspond pas à ce bien (secteur, budget, type ou pièces).",
            }), 400
    prog = set_progress(aid, lead_id, buyer_client_id=client_id or None)
    return jsonify({"ok": True, "progress": prog})


@app.route("/api/transactions/<int:lead_id>/milestone", methods=["POST"])
def api_transaction_milestone(lead_id: int):
    """Pose un jalon : visite, dossier acquéreur, compromis (étapes 8-10)."""
    import datetime

    from crm.mandates.dossiers import create_mandate_dossier, dossier_from_mandate_fields
    from crm.mandates.storage import list_seller_mandates
    from crm.transactions.storage import set_progress

    aid = _aid()
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip().lower()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if kind == "visit":
        return jsonify({"ok": True, "progress": set_progress(aid, lead_id, visit_at=now)})
    if kind == "compromis":
        return jsonify({"ok": True, "progress": set_progress(aid, lead_id, compromis_at=now)})
    if kind == "buyer_dossier":
        # Dossier acquéreur : on réutilise la mécanique dossier liée au mandat.
        dossier_id = None
        mandates = list_seller_mandates(aid, lead_id=lead_id, status="signed")
        if mandates:
            d = create_mandate_dossier(
                aid, mandates[0]["id"],
                {**dossier_from_mandate_fields(mandates[0]), "title": "Dossier acquéreur", "status": "acquereur"},
            )
            dossier_id = d["id"]
        return jsonify({"ok": True, "progress": set_progress(aid, lead_id, buyer_dossier_id=dossier_id or "pending")})
    return jsonify({"ok": False, "error": "kind invalide (visit|buyer_dossier|compromis)"}), 400


@app.route("/api/transactions/<int:lead_id>/finalize", methods=["POST"])
def api_transaction_finalize(lead_id: int):
    """Clôture l'affaire (vendu) + enregistre la commission (split agence / agent)."""
    import datetime

    from crm.agents.storage import get_assignment
    from crm.transactions.service import signed_mandate_for_lead
    from crm.transactions.storage import record_commission, set_progress

    aid = _aid()
    lead = get_lead(lead_id, aid)
    if not lead:
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    data = request.get_json(silent=True) or {}
    try:
        total = float(data.get("total_amount") or data.get("commission") or 0)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0:
        return jsonify({"ok": False, "error": "Montant de commission requis (> 0)."}), 400
    agent_pct = data.get("agent_pct")
    assignment = get_assignment(aid, lead_id) or {}
    mandate = signed_mandate_for_lead(aid, lead_id)
    comm = record_commission(
        aid,
        lead_id=lead_id,
        mandate_id=(mandate or {}).get("id"),
        agent_id=data.get("agent_id") or assignment.get("agent_id"),
        agent_name=assignment.get("agent_name"),
        total_amount=total,
        agent_pct=float(agent_pct) if agent_pct is not None else None,
    )
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    set_progress(aid, lead_id, sold_at=now)
    from crawler.storage import retire_lead_after_sale

    retire_lead_after_sale(lead_id, aid)
    return jsonify({"ok": True, "commission": comm, "retired_from_prospects": True})


@app.route("/api/transactions/<int:lead_id>/email", methods=["POST"])
def api_transaction_email(lead_id: int):
    """Envoie le dossier/annonce au bon correspondant — reply-to = agent connecté."""
    from crm.email.service import email_enabled, send_email
    from crm.transactions.service import compose_dossier

    aid = _aid()
    if not get_lead(lead_id, aid):
        return jsonify({"ok": False, "error": "Prospect introuvable"}), 404
    data = request.get_json(silent=True) or {}
    dossier = compose_dossier(aid, lead_id)
    target = (data.get("to_role") or "seller").strip().lower()
    to = (data.get("email") or "").strip()
    if not to:
        if target == "buyer" and dossier.get("buyer"):
            to = (dossier["buyer"].get("email") or "").strip()
        else:
            to = (dossier.get("seller") or {}).get("email") or ""
    to = (to or "").strip()
    if not to or to == "—":
        return jsonify({"ok": False, "error": "Email du correspondant requis"}), 400

    user = get_current_user() or {}
    agent_email = (user.get("email") or "").strip()
    agent_name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or None
    agency_name = user.get("agency_name") or get_agency_name(aid) or "votre agence"
    p = dossier.get("property") or {}
    subject = (data.get("subject") or f"Votre bien — {p.get('title') or 'dossier'}").strip()
    body = (data.get("message") or "").strip()
    html = (
        f"<p>Bonjour,</p><p>{body or 'Voici les informations concernant votre bien.'}</p>"
        f"<ul><li><strong>Bien :</strong> {p.get('title','')}</li>"
        f"<li><strong>Adresse :</strong> {p.get('address','')} {p.get('postcode','')} {p.get('city','')}</li>"
        f"<li><strong>Surface :</strong> {p.get('surface') or '—'} m²</li>"
        f"<li><strong>Prix :</strong> {p.get('price') or '—'} €</li></ul>"
        f"<p>{agent_name or 'Votre conseiller'} — {agency_name}"
        + (f"<br><a href='mailto:{agent_email}'>{agent_email}</a>" if agent_email else "")
        + "</p>"
    )
    sent = send_email(
        to, subject, html,
        reply_to=agent_email or None,
        from_name=f"{agent_name} · {agency_name}" if agent_name else agency_name,
    )
    return jsonify({"ok": True, "sent_smtp": sent, "email_configured": email_enabled(), "to": to})


@app.route("/api/commissions", methods=["GET"])
def api_commissions():
    """Suivi des commissions encaissées (agence + par agent)."""
    from crm.transactions.storage import list_commissions

    return jsonify({"ok": True, **list_commissions(_aid())})


@app.route("/api/onboarding", methods=["GET", "PATCH"])
def api_onboarding():
    from velora_db.connection import DatabaseBusyError

    agency_id = _aid()
    if request.method == "GET":
        try:
            from crawler.storage import (
                _agency_settings_from_row,
                get_agency_settings,
                get_connection,
                row_scalar,
            )

            with get_connection() as conn:
                row = conn.execute(
                    "SELECT * FROM agency_settings WHERE agency_id = ?",
                    (agency_id,),
                ).fetchone()
                sources_count = row_scalar(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM sources WHERE agency_id = ?",
                        (agency_id,),
                    ).fetchone()
                )
                leads_count = row_scalar(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM leads WHERE agency_id = ?",
                        (agency_id,),
                    ).fetchone()
                )
            settings = _agency_settings_from_row(row)
        except DatabaseBusyError as exc:
            return jsonify({"error": str(exc), "code": "database_busy"}), 503
        return jsonify(
            {
                "ok": True,
                "settings": settings,
                "progress": {
                    "has_source": sources_count > 0,
                    "has_leads": leads_count > 0,
                    "sources_count": sources_count,
                    "leads_count": leads_count,
                },
            }
        )
    data = request.get_json(silent=True) or {}
    try:
        if data.get("complete"):
            settings = set_onboarding(agency_id, step=3, completed=True)
        else:
            step = data.get("step")
            step_int = None
            if step is not None:
                try:
                    step_int = int(step)
                except (TypeError, ValueError):
                    return jsonify({"error": "Étape d'onboarding invalide"}), 400
            settings = set_onboarding(
                agency_id,
                step=step_int,
                completed=bool(data.get("completed")),
            )
    except Exception as exc:
        logging.exception("PATCH /api/onboarding")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "settings": settings})


@app.route("/api/leads/export")
def api_leads_export():
    from crm.config import LEGAL_COMPANY_NAME

    csv_data = export_leads_csv(_aid())
    filename = f"veliora-prospects-{get_agency_name(_aid()) or 'agence'}.csv"
    filename = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    return Response(
        "\ufeff" + csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Exported-By": LEGAL_COMPANY_NAME,
        },
    )


@app.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
    import os

    from crm.auth.service import request_password_reset

    data = request.get_json(silent=True) or {}
    try:
        result = request_password_reset(
            data.get("email"),
            app_url=os.getenv("APP_PUBLIC_URL", "http://localhost:8000"),
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/reset-password", methods=["POST"])
def api_reset_password():
    from crm.auth.service import reset_password_with_token

    data = request.get_json(silent=True) or {}
    try:
        result = reset_password_with_token(data.get("token"), data.get("password"))
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/register-agency", methods=["POST"])
def api_register_agency():
    from crm.auth.service import register_agency

    data = request.get_json(silent=True) or {}
    try:
        result = register_agency(
            agency_name=data.get("agency_name") or data.get("name"),
            admin_email=data.get("email"),
            password=data.get("password"),
            admin_first_name=data.get("first_name", ""),
            admin_last_name=data.get("last_name", ""),
            city=data.get("city") or data.get("ville", ""),
        )
        return jsonify({"ok": True, **result}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    from crm.auth.service import login_user

    data = request.get_json(silent=True) or {}
    result = login_user(data.get("email"), data.get("password"))
    if not result:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401
    return jsonify({"ok": True, **result})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    from crm.auth.context import _extract_token
    from crm.auth.service import logout_user

    logout_user(_extract_token())
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_auth_me():
    from crm.auth.context import resolve_current_user
    from crm.billing.access import billing_status_payload

    user = resolve_current_user()
    if not user:
        return jsonify({"error": "Non connecté"}), 401
    billing = billing_status_payload(user["agency_id"])
    settings = get_agency_settings(user["agency_id"])
    return jsonify({"ok": True, "user": user, "billing": billing, "settings": settings})


@app.route("/api/billing/config", methods=["GET"])
def api_billing_config():
    from crm.billing.config import public_stripe_config

    return jsonify({"ok": True, **public_stripe_config()})


@app.route("/api/billing/status", methods=["GET"])
def api_billing_status():
    from crm.billing.access import billing_status_payload

    return jsonify({"ok": True, **billing_status_payload(_aid())})


@app.route("/api/billing/create-checkout-session", methods=["POST"])
def api_billing_checkout():
    from crm.billing.stripe_service import create_checkout_session

    user = get_current_user()
    try:
        session = create_checkout_session(
            _aid(),
            customer_email=user.get("email") if user else None,
        )
        return jsonify({"ok": True, **session})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/billing/create-portal-session", methods=["POST"])
def api_billing_portal():
    from crm.billing.stripe_service import create_portal_session

    user = get_current_user()
    if not user or user.get("role") != "admin":
        return jsonify({"error": "Réservé à l'administrateur d'agence"}), 403
    try:
        session = create_portal_session(_aid())
        return jsonify({"ok": True, **session})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/billing/verify-session", methods=["POST"])
def api_billing_verify_session():
    from crm.billing.stripe_service import verify_checkout_session

    data = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"error": "session_id requis"}), 400
    try:
        result = verify_checkout_session(session_id, _aid())
        return jsonify(result)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/billing/webhook", methods=["POST"])
def api_billing_webhook():
    from crm.billing.stripe_service import handle_webhook

    try:
        result = handle_webhook(
            request.get_data(),
            request.headers.get("Stripe-Signature"),
        )
        return jsonify(result)
    except RuntimeError as exc:
        logging.error("Webhook Stripe : %s", exc)
        return jsonify({"error": str(exc)}), 503
    except ValueError as exc:
        logging.warning("Webhook Stripe rejeté : %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/invite", methods=["POST"])
def api_invite_collaborator():
    from crm.auth.service import get_session_user, invite_collaborator

    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    admin = get_session_user(token)
    if not admin or admin.get("role") not in ("admin",):
        return jsonify({"error": "Réservé aux administrateurs d'agence"}), 403

    data = request.get_json(silent=True) or {}
    try:
        collab = invite_collaborator(
            admin["agency_id"],
            data.get("email"),
            data.get("password"),
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
        )
        return jsonify({"ok": True, "collaborator": collab}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ─── Mandats & acheteurs / locataires ───

@app.route("/api/mandates/templates", methods=["GET"])
def api_mandate_templates():
    from crm.mandates.templates import get_template_meta

    t = request.args.get("type", "vente")
    return jsonify({"ok": True, "template": get_template_meta(t)})


@app.route("/api/map")
def api_map():
    """Marqueurs carte : agence (fiche légale) + annonces géocodées."""
    from crm.maps.service import build_map_payload, geocode_map_leads_sync
    from velora_db.connection import DatabaseBusyError

    agency_id = _aid()
    try:
        if request.args.get("geocode") in ("1", "true", "yes"):
            geocode_map_leads_sync(agency_id)
        return jsonify(build_map_payload(agency_id))
    except DatabaseBusyError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "database_busy"}), 503
    except Exception as exc:
        logging.exception("GET /api/map")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/map/reverse-city", methods=["POST"])
def api_map_reverse_city():
    from crm.maps.service import reverse_geocode_city

    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Coordonnées invalides"}), 400
    try:
        result = reverse_geocode_city(lat, lng)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logging.exception("POST /api/map/reverse-city")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/mandates/agency-profile", methods=["GET", "PATCH"])
def api_mandate_agency_profile():
    from crm.mandates.storage import get_agency_legal_profile, upsert_agency_legal_profile

    agency_id = _aid()
    if request.method == "GET":
        return jsonify({"ok": True, "profile": get_agency_legal_profile(agency_id)})
    data = request.get_json(silent=True) or {}
    profile = upsert_agency_legal_profile(agency_id, data.get("profile") or data)
    return jsonify({"ok": True, "profile": profile})


@app.route("/api/mandates/preview", methods=["POST"])
def api_mandate_preview():
    from crm.mandates.storage import get_agency_legal_profile
    from crm.mandates.templates import render_mandate_html

    data = request.get_json(silent=True) or {}
    html = render_mandate_html(
        data.get("mandate_type") or "vente",
        data.get("exclusivity") or "exclusif",
        data.get("fields") or {},
        get_agency_legal_profile(_aid()),
    )
    return jsonify({"ok": True, "body_html": html})


@app.route("/api/mandates", methods=["GET", "POST"])
def api_mandates_list():
    from crm.mandates.storage import create_seller_mandate, list_seller_mandates

    agency_id = _aid()
    if request.method == "GET":
        try:
            lead_id = int(request.args["lead_id"]) if request.args.get("lead_id") else None
        except (TypeError, ValueError):
            return jsonify({"error": "lead_id doit être un entier"}), 400
        mandates = list_seller_mandates(
            agency_id,
            mandate_type=request.args.get("type") or None,
            status=request.args.get("status") or None,
            lead_id=lead_id,
        )
        return jsonify({"ok": True, "mandates": mandates})
    data = request.get_json(silent=True) or {}
    try:
        mandate = create_seller_mandate(
            agency_id,
            mandate_type=data.get("mandate_type") or "vente",
            lead_id=data.get("lead_id"),
            exclusivity=data.get("exclusivity") or "exclusif",
            fields=data.get("fields"),
            title=data.get("title"),
        )
        # Raccroche le dossier de pièces ouvert dès la prise en charge au vrai mandat,
        # pour qu'il reste accessible (et profite de la checklist adaptée au profil).
        if data.get("lead_id") and mandate:
            try:
                from crm.mandates.dossiers import link_lead_dossier_to_mandate

                link_lead_dossier_to_mandate(agency_id, int(data["lead_id"]), mandate["id"])
            except Exception:
                logging.exception("link_lead_dossier_to_mandate lead=%s", data.get("lead_id"))
        return jsonify({"ok": True, "mandate": mandate}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/mandates/<mandate_id>", methods=["GET", "PATCH", "DELETE"])
def api_mandate_detail(mandate_id):
    from crm.mandates.storage import (
        delete_seller_mandate,
        get_seller_mandate,
        update_seller_mandate,
    )

    agency_id = _aid()
    if request.method == "GET":
        mandate = get_seller_mandate(mandate_id, agency_id)
        if not mandate:
            return jsonify({"error": "Mandat introuvable"}), 404
        return jsonify({"ok": True, "mandate": mandate})
    if request.method == "DELETE":
        if delete_seller_mandate(mandate_id, agency_id):
            return jsonify({"ok": True})
        return jsonify({"error": "Mandat introuvable"}), 404
    data = request.get_json(silent=True) or {}
    mandate = update_seller_mandate(mandate_id, agency_id, data)
    if not mandate:
        return jsonify({"error": "Mandat introuvable"}), 404
    return jsonify({"ok": True, "mandate": mandate})


@app.route("/api/mandates/<mandate_id>/send", methods=["POST"])
def api_mandate_send(mandate_id):
    from urllib.parse import quote

    from crm.email.service import email_enabled, send_email
    from crm.mandates.storage import get_seller_mandate, update_seller_mandate

    agency_id = _aid()
    mandate = get_seller_mandate(mandate_id, agency_id)
    if not mandate:
        return jsonify({"error": "Mandat introuvable"}), 404
    data = request.get_json(silent=True) or {}
    to = (data.get("email") or mandate.get("recipient_email") or "").strip()
    if not to or to == "—":
        return jsonify({"error": "Email du vendeur requis pour l'envoi"}), 400

    user = get_current_user() or {}
    agent_email = (user.get("email") or "").strip()
    agent_name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or None
    agency_name = user.get("agency_name") or get_agency_name(agency_id) or "votre agence"

    type_label = "vente" if mandate["mandate_type"] == "vente" else "location"
    subject = f"Mandat de {type_label} — {mandate.get('title', 'Bien')}"
    html = mandate.get("body_html") or ""
    signature = (
        f"<p style='margin-top:8px'>{agent_name or 'Votre conseiller'} — {agency_name}"
        + (f"<br>Répondez à cet email : <a href='mailto:{agent_email}'>{agent_email}</a>" if agent_email else "")
        + "</p>"
    )
    intro = (
        "<p>Bonjour,</p>"
        "<p>Veuillez trouver ci-dessous votre mandat à valider et signer. "
        "N'hésitez pas à nous contacter pour toute question.</p>"
        f"{signature}<hr>"
    )
    full_html = intro + html

    # Réponses dirigées vers l'agent connecté (from_name lisible côté destinataire).
    sent_smtp = send_email(
        to, subject, full_html,
        reply_to=agent_email or None,
        from_name=f"{agent_name} · {agency_name}" if agent_name else agency_name,
    )
    mandate = update_seller_mandate(
        mandate_id,
        agency_id,
        {"recipient_email": to, "mark_sent": sent_smtp},
    )
    _mailto_body = "Bonjour,\n\nVeuillez trouver votre mandat en pièce jointe ou via le lien que nous vous enverrons.\n\nCordialement"
    mailto = (
        f"mailto:{quote(to)}?subject={quote(subject)}"
        f"&body={quote(_mailto_body)}"
    )
    return jsonify({
        "ok": True,
        "mandate": mandate,
        "sent_smtp": sent_smtp,
        "email_configured": email_enabled(),
        "mailto": mailto,
    })


@app.route("/api/mandates/<mandate_id>/esign", methods=["POST"])
def api_mandate_esign(mandate_id):
    """Lance la signature électronique du mandat (Yousign, opt-in)."""
    from crm.mandates.esign import esign_enabled, send_for_signature
    from crm.mandates.storage import get_seller_mandate, set_mandate_esign

    agency_id = _aid()
    if not esign_enabled():
        return jsonify({
            "ok": False,
            "error": "Signature électronique non activée — configurez ESIGN_PROVIDER/YOUSIGN_API_KEY.",
            "code": "esign_disabled",
        }), 400
    mandate = get_seller_mandate(mandate_id, agency_id)
    if not mandate:
        return jsonify({"ok": False, "error": "Mandat introuvable"}), 404
    data = request.get_json(silent=True) or {}
    fields = mandate.get("fields") or {}
    signer_email = (
        data.get("email") or mandate.get("recipient_email")
        or fields.get("owner_email") or ""
    ).strip()
    signer_name = (
        data.get("name")
        or " ".join(p for p in (fields.get("owner_first_name"), fields.get("owner_last_name")) if p)
    ).strip()
    out = send_for_signature(mandate, signer_name, signer_email)
    if not out.get("ok"):
        return jsonify(out), 400
    set_mandate_esign(
        mandate_id,
        agency_id,
        esign_provider=out.get("provider"),
        esign_request_id=out.get("request_id"),
        esign_status=out.get("status") or "pending",
        esign_url=out.get("signer_url"),
        esign_signer_email=signer_email,
    )
    return jsonify({"ok": True, "mandate": get_seller_mandate(mandate_id, agency_id), "esign": out})


@app.route("/api/webhooks/esign/yousign", methods=["POST"])
def api_webhook_esign_yousign():
    """Webhook Yousign — marque le mandat signé à la complétion (public, opt-in)."""
    from crm.mandates.esign import parse_completion_webhook
    from crm.mandates.storage import (
        find_mandate_by_esign_request,
        set_mandate_esign,
        update_seller_mandate,
    )

    payload = request.get_json(silent=True) or {}
    parsed = parse_completion_webhook(payload)
    if not parsed:
        return jsonify({"ok": True, "ignored": True})
    found = find_mandate_by_esign_request(parsed["request_id"])
    if not found:
        return jsonify({"ok": True, "ignored": True})
    mandate_id, agency_id = found
    if parsed["completed"]:
        set_mandate_esign(mandate_id, agency_id, esign_status="signed")
        update_seller_mandate(mandate_id, agency_id, {"mark_signed": True})
    else:
        set_mandate_esign(mandate_id, agency_id, esign_status=parsed.get("status") or "pending")
    return jsonify({"ok": True})


@app.route("/api/mandates/<mandate_id>/dossiers", methods=["GET", "POST"])
def api_mandate_dossiers(mandate_id):
    from crm.mandates.dossiers import (
        create_mandate_dossier,
        dossier_from_mandate_fields,
        list_mandate_dossiers,
    )
    from crm.mandates.storage import get_seller_mandate

    agency_id = _aid()
    mandate = get_seller_mandate(mandate_id, agency_id)
    if not mandate:
        return jsonify({"error": "Mandat introuvable"}), 404
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "dossiers": list_mandate_dossiers(mandate_id, agency_id),
        })
    data = request.get_json(silent=True) or {}
    if data.get("from_mandate"):
        data = {**dossier_from_mandate_fields(mandate), **data}
    dossier = create_mandate_dossier(agency_id, mandate_id, data)
    return jsonify({"ok": True, "dossier": dossier}), 201


@app.route("/api/mandates/dossiers/<dossier_id>", methods=["GET", "PATCH", "DELETE"])
def api_mandate_dossier_detail(dossier_id):
    from crm.mandates.dossiers import (
        delete_mandate_dossier,
        get_mandate_dossier,
        update_mandate_dossier,
    )

    agency_id = _aid()
    if request.method == "GET":
        dossier = get_mandate_dossier(dossier_id, agency_id)
        if not dossier:
            return jsonify({"error": "Dossier introuvable"}), 404
        return jsonify({"ok": True, "dossier": dossier})
    if request.method == "DELETE":
        if delete_mandate_dossier(dossier_id, agency_id):
            return jsonify({"ok": True})
        return jsonify({"error": "Dossier introuvable"}), 404
    data = request.get_json(silent=True) or {}
    dossier = update_mandate_dossier(dossier_id, agency_id, data)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    return jsonify({"ok": True, "dossier": dossier})


@app.route("/api/mandates/dossiers/<dossier_id>/photos", methods=["POST"])
def api_mandate_dossier_photos(dossier_id):
    from crm.mandates.dossiers import add_dossier_photo

    agency_id = _aid()
    upload = request.files.get("file") or request.files.get("photo")
    if not upload or not upload.filename:
        return jsonify({"error": "Fichier photo requis"}), 400
    raw = upload.read()
    if not raw:
        return jsonify({"error": "Fichier vide"}), 400
    caption = (request.form.get("caption") or "").strip()
    try:
        dossier = add_dossier_photo(
            dossier_id,
            agency_id,
            upload.filename,
            raw,
            caption=caption,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    return jsonify({"ok": True, "dossier": dossier})


@app.route("/api/mandates/dossiers/<dossier_id>/photos/<photo_id>", methods=["DELETE"])
def api_mandate_dossier_photo_delete(dossier_id, photo_id):
    from crm.mandates.dossiers import remove_dossier_photo

    agency_id = _aid()
    dossier = remove_dossier_photo(dossier_id, agency_id, photo_id)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    return jsonify({"ok": True, "dossier": dossier})


@app.route("/api/mandates/dossiers/<dossier_id>/clients", methods=["POST", "DELETE"])
def api_mandate_dossier_clients(dossier_id):
    from crm.mandates.dossiers import link_client_to_dossier, unlink_client_from_dossier

    agency_id = _aid()
    data = request.get_json(silent=True) or {}
    client_id = (data.get("client_id") or "").strip()
    if not client_id:
        return jsonify({"error": "client_id requis"}), 400
    try:
        if request.method == "DELETE":
            dossier = unlink_client_from_dossier(dossier_id, agency_id, client_id)
        else:
            dossier = link_client_to_dossier(
                dossier_id,
                agency_id,
                client_id,
                notes=data.get("notes") or "",
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    return jsonify({"ok": True, "dossier": dossier})


def _dossier_mandate(dossier_id, agency_id):
    """Retourne (dossier, mandate) ou (None, None) si le dossier est introuvable."""
    from crm.mandates.dossiers import get_mandate_dossier
    from crm.mandates.storage import get_seller_mandate

    dossier = get_mandate_dossier(dossier_id, agency_id)
    if not dossier:
        return None, None
    mandate = get_seller_mandate(dossier.get("mandate_id"), agency_id)
    return dossier, mandate


@app.route("/api/mandates/dossiers/<dossier_id>/documents", methods=["GET", "POST"])
def api_mandate_dossier_documents(dossier_id):
    from crm.mandates.dossiers import add_dossier_document, get_dossier_documents

    agency_id = _aid()
    dossier, mandate = _dossier_mandate(dossier_id, agency_id)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404

    if request.method == "GET":
        return jsonify({"ok": True, "documents": get_dossier_documents(dossier_id, agency_id, mandate)})

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Fichier requis"}), 400
    raw = upload.read()
    if not raw:
        return jsonify({"error": "Fichier vide"}), 400
    folder_key = (request.form.get("folder_key") or "").strip()
    folder_name = (request.form.get("folder_name") or "").strip()
    try:
        add_dossier_document(
            dossier_id, agency_id, folder_key, upload.filename, raw, folder_name=folder_name
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "documents": get_dossier_documents(dossier_id, agency_id, mandate)})


@app.route(
    "/api/mandates/dossiers/<dossier_id>/documents/<folder_key>/<file_id>",
    methods=["DELETE"],
)
def api_mandate_dossier_document_delete(dossier_id, folder_key, file_id):
    from crm.mandates.dossiers import remove_dossier_document

    agency_id = _aid()
    dossier, mandate = _dossier_mandate(dossier_id, agency_id)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    documents = remove_dossier_document(dossier_id, agency_id, folder_key, file_id)
    return jsonify({"ok": True, "documents": documents})


@app.route("/api/mandates/dossiers/<dossier_id>/folders", methods=["POST"])
def api_mandate_dossier_folder_create(dossier_id):
    from crm.mandates.dossiers import create_dossier_folder

    agency_id = _aid()
    dossier, mandate = _dossier_mandate(dossier_id, agency_id)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    data = request.get_json(silent=True) or {}
    try:
        documents = create_dossier_folder(dossier_id, agency_id, data.get("name") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "documents": documents})


@app.route("/api/mandates/dossiers/<dossier_id>/folders/<folder_key>", methods=["DELETE"])
def api_mandate_dossier_folder_delete(dossier_id, folder_key):
    from crm.mandates.dossiers import delete_dossier_folder

    agency_id = _aid()
    dossier, mandate = _dossier_mandate(dossier_id, agency_id)
    if not dossier:
        return jsonify({"error": "Dossier introuvable"}), 404
    documents = delete_dossier_folder(dossier_id, agency_id, folder_key)
    return jsonify({"ok": True, "documents": documents})


@app.route("/api/mandates/dossier-docs/<agency_id>/<dossier_id>/<filename>")
def api_mandate_dossier_document_file(agency_id, dossier_id, filename):
    from crm.mandates.dossiers import (
        document_original_name,
        resolve_dossier_document_path,
    )

    if agency_id != _aid():
        return jsonify({"error": "Accès refusé"}), 403
    path = resolve_dossier_document_path(agency_id, dossier_id, filename)
    if not path:
        abort(404)
    download_name = document_original_name(agency_id, dossier_id, filename)
    as_attachment = request.args.get("download") == "1"
    return send_file(path, as_attachment=as_attachment, download_name=download_name)


@app.route("/api/mandates/dossier-files/<agency_id>/<dossier_id>/<filename>")
def api_mandate_dossier_file(agency_id, dossier_id, filename):
    from crm.mandates.dossiers import resolve_dossier_photo_path

    if agency_id != _aid():
        return jsonify({"error": "Accès refusé"}), 403
    path = resolve_dossier_photo_path(agency_id, dossier_id, filename)
    if not path:
        abort(404)
    ext = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")
    return send_file(path, mimetype=mime)


@app.route("/api/clients", methods=["GET", "POST"])
def api_property_clients():
    from crm.mandates.storage import create_property_client, list_property_clients

    agency_id = _aid()
    if request.method == "GET":
        clients = list_property_clients(
            agency_id,
            segment=request.args.get("segment") or None,
        )
        return jsonify({"ok": True, "clients": clients})
    data = request.get_json(silent=True) or {}
    try:
        client = create_property_client(agency_id, data)
        _rescore_after_client_change(agency_id)
        return jsonify({"ok": True, "client": client}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/clients/seed-demo", methods=["POST"])
def api_property_clients_seed_demo():
    from crm.mandates.storage import create_property_client

    agency_id = _aid()
    data = request.get_json(silent=True) or {}
    try:
        count = int(data.get("count") or 50)
    except (TypeError, ValueError):
        count = 50
    count = max(1, min(count, 200))
    cities = data.get("cities") or ["Chaville", "Lorient"]
    cities = [str(c).strip() for c in cities if str(c).strip()] or ["Chaville", "Lorient"]

    first_names = [
        "Emma", "Lucas", "Lea", "Hugo", "Chloe", "Nathan", "Sarah", "Louis", "Manon",
        "Jules", "Camille", "Noah", "Ines", "Tom", "Mila", "Mathis", "Louise", "Leo",
        "Anna", "Theo", "Nina", "Maxime", "Elsa", "Gabriel", "Jade",
    ]
    last_names = [
        "Martin", "Bernard", "Petit", "Robert", "Richard", "Durand", "Dubois", "Moreau",
        "Laurent", "Simon", "Michel", "Lefebvre", "Garcia", "David", "Roux", "Fournier",
        "Girard", "Andre", "Mercier", "Dupont", "Lambert", "Bonnet", "Francois", "Martinez",
    ]
    property_types = ["Appartement", "Maison", "Studio", "T2", "T3", "Loft"]
    notes_pool = [
        "Proche transports",
        "Souhaite balcon/terrasse",
        "Recherche quartier calme",
        "Parking souhaité",
        "Prêt à visiter rapidement",
        "Flexible sur secteur",
    ]

    created = 0
    for i in range(count):
        segment = "acheteur" if i % 2 == 0 else "locataire"
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        city = cities[i % len(cities)]
        rooms_min = random.choice([1, 2, 3, 4])
        surface_min = random.choice([28, 35, 42, 55, 68, 82])
        if segment == "acheteur":
            budget_min = random.choice([120000, 150000, 180000, 220000, 260000, 320000])
            budget_max = budget_min + random.choice([40000, 70000, 100000, 150000])
        else:
            budget_min = random.choice([550, 650, 750, 850, 1000])
            budget_max = budget_min + random.choice([150, 250, 350, 450])
        suffix = str(int(time.time()))[-6:]
        email = f"{fn.lower()}.{ln.lower()}.{i}.{suffix}@veliora-demo.local"
        phone = "06" + f"{random.randint(10000000, 99999999)}"
        payload = {
            "segment": segment,
            "first_name": fn,
            "last_name": ln,
            "phone": phone,
            "email": email,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "property_type": random.choice(property_types),
            "rooms_min": rooms_min,
            "surface_min": surface_min,
            "cities": [city],
            "status": "actif",
            "notes": random.choice(notes_pool),
        }
        try:
            create_property_client(agency_id, payload)
            created += 1
        except Exception:
            logging.exception("seed-demo client failed")
    _rescore_after_client_change(agency_id)
    return jsonify({"ok": True, "created": created, "requested": count, "cities": cities})


@app.route("/api/clients/import/template")
def api_clients_import_template():
    from crm.mandates.client_import import IMPORT_TEMPLATE_CSV

    return Response(
        "\ufeff" + IMPORT_TEMPLATE_CSV,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="veliora-acheteurs-locataires-modele.csv"',
        },
    )


@app.route("/api/clients/import", methods=["POST"])
def api_clients_import():
    from crm.mandates.client_import import (
        import_clients_from_rows,
        parse_csv_bytes,
        parse_xlsx_bytes,
    )

    agency_id = _aid()
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "Fichier requis (.csv ou .xlsx)"}), 400
    name = upload.filename.lower()
    raw = upload.read()
    if not raw:
        return jsonify({"error": "Fichier vide"}), 400
    default_segment = (request.form.get("segment") or "").strip().lower() or None
    if default_segment and default_segment not in ("acheteur", "locataire"):
        default_segment = None
    try:
        if name.endswith(".csv"):
            rows = parse_csv_bytes(raw)
        elif name.endswith((".xlsx", ".xlsm", ".xls")):
            rows = parse_xlsx_bytes(raw)
        else:
            return jsonify({"error": "Format accepté : .csv ou .xlsx"}), 400
        result = import_clients_from_rows(
            agency_id, rows, default_segment=default_segment
        )
        if result.get("imported") or result.get("created") or result.get("updated"):
            _rescore_after_client_change(agency_id)
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/clients/<client_id>", methods=["GET", "PATCH", "DELETE"])
def api_property_client_detail(client_id):
    from crm.mandates.storage import (
        delete_property_client,
        get_property_client,
        update_property_client,
    )

    agency_id = _aid()
    if request.method == "GET":
        client = get_property_client(client_id, agency_id)
        if not client:
            return jsonify({"error": "Fiche introuvable"}), 404
        return jsonify({"ok": True, "client": client})
    if request.method == "DELETE":
        if delete_property_client(client_id, agency_id):
            _rescore_after_client_change(agency_id)
            return jsonify({"ok": True})
        return jsonify({"error": "Fiche introuvable"}), 404
    data = request.get_json(silent=True) or {}
    client = update_property_client(client_id, agency_id, data)
    if not client:
        return jsonify({"error": "Fiche introuvable"}), 404
    _rescore_after_client_change(agency_id)
    return jsonify({"ok": True, "client": client})


@app.route("/api/clients/<client_id>/matches")
def api_property_client_matches(client_id):
    """Annonces du portefeuille compatibles avec un acheteur/locataire donné."""
    from crm.mandates.storage import get_property_client
    from crm.matching.service import build_client_matches

    agency_id = _aid()
    client = get_property_client(client_id, agency_id)
    if not client:
        return jsonify({"error": "Fiche introuvable"}), 404
    try:
        leads = get_leads(agency_id)
        return jsonify(build_client_matches(client, leads))
    except Exception as exc:
        logging.exception("GET /api/clients/%s/matches", client_id)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/ai/health")
def api_ai_health():
    from crm.ai.providers import get_provider, AIProviderError

    try:
        return jsonify(get_provider().health())
    except AIProviderError as exc:
        return jsonify({"ok": False, "reachable": False, "error": str(exc)}), 200


@app.route("/api/ai/conversations", methods=["GET", "POST"])
def api_ai_conversations():
    from crm.ai.storage import create_conversation, list_conversations

    agency_id = _aid()
    if request.method == "GET":
        return jsonify({"ok": True, "conversations": list_conversations(agency_id)})
    data = request.get_json(silent=True) or {}
    user = get_current_user() or {}
    conv = create_conversation(
        agency_id,
        user_id=user.get("id"),
        title=(data.get("title") or "").strip() or None,
    )
    return jsonify({"ok": True, "conversation": conv}), 201


@app.route("/api/ai/conversations/<conv_id>", methods=["GET", "PATCH", "DELETE"])
def api_ai_conversation_detail(conv_id):
    from crm.ai.storage import (
        delete_conversation,
        get_conversation,
        get_messages,
        rename_conversation,
    )

    agency_id = _aid()
    if request.method == "GET":
        conv = get_conversation(conv_id, agency_id)
        if not conv:
            return jsonify({"error": "Conversation introuvable"}), 404
        return jsonify({
            "ok": True,
            "conversation": conv,
            "messages": get_messages(conv_id, agency_id),
        })
    if request.method == "DELETE":
        if delete_conversation(conv_id, agency_id):
            return jsonify({"ok": True})
        return jsonify({"error": "Conversation introuvable"}), 404
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Titre requis"}), 400
    if not rename_conversation(conv_id, agency_id, title):
        return jsonify({"error": "Conversation introuvable"}), 404
    return jsonify({"ok": True})


@app.route("/api/ai/chat", methods=["POST"])
def api_ai_chat():
    """Streame la réponse Ollama (NDJSON line-delimited).

    Le frontend lit la réponse via fetch + reader.read() et concatène les tokens
    au fur et à mesure. NDJSON plutôt que SSE pour rester simple côté JS, mais
    on positionne quand même `X-Accel-Buffering: no` pour empêcher les proxies
    de bufferiser.
    """
    import json

    from crm.ai.service import ensure_conversation, stream_assistant_reply

    agency_id = _aid()
    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    if not user_message:
        return jsonify({"error": "Message vide"}), 400
    conv_id_raw = (data.get("conversation_id") or "").strip() or None
    user = get_current_user() or {}
    conv = ensure_conversation(
        agency_id,
        conv_id_raw,
        user_id=user.get("id"),
        user_first_text=user_message,
    )

    def generate():
        yield json.dumps({"type": "meta", "conversation": conv}) + "\n"
        for event in stream_assistant_reply(
            agency_id,
            conv["id"],
            user_message,
            user_first_name=user.get("first_name"),
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    resp = Response(generate(), mimetype="application/x-ndjson; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache, no-transform"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.charset = "utf-8"
    return resp


@app.route("/api/ai/action", methods=["POST"])
def api_ai_action():
    """Exécute une action proposée par l'IA (après validation explicite côté UI)."""
    from crm.ai.tools import execute_action

    agency_id = _aid()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if not isinstance(action, dict):
        return jsonify({"ok": False, "error": "Payload `action` manquant"}), 400
    result = execute_action(agency_id, action)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/ai/memory", methods=["GET", "POST"])
def api_ai_memory():
    from crm.ai.storage import add_memory, list_memories

    agency_id = _aid()
    if request.method == "GET":
        return jsonify({"ok": True, "memories": list_memories(agency_id)})
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "content requis"}), 400
    mem = add_memory(
        agency_id,
        content,
        scope=(data.get("scope") or "general"),
        source=data.get("source") or "user",
    )
    return jsonify({"ok": True, "memory": mem}), 201


@app.route("/api/ai/memory/<memory_id>", methods=["DELETE"])
def api_ai_memory_delete(memory_id):
    from crm.ai.storage import delete_memory

    agency_id = _aid()
    if delete_memory(memory_id, agency_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Souvenir introuvable"}), 404


@app.route("/api/crawler/crawl-url", methods=["POST"])
def api_crawler_crawl_url():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL requise"}), 400
    paid = _paid_portal_crawl_response(url=url)
    if paid:
        return paid
    job = engine.crawl_url(url, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/crawler/import-listing", methods=["POST"])
def api_crawler_import_listing():
    """Import d'une seule fiche annonce (tous sites, extraction poussée)."""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL requise"}), 400
    paid = _paid_portal_crawl_response(url=url)
    if paid:
        return paid
    job = engine.import_listing_url(url, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/crawler/leads/<int:lead_id>/refresh", methods=["POST"])
@app.route("/api/crawler/refresh-lead/<int:lead_id>", methods=["POST"])
def api_crawler_refresh_lead(lead_id):
    """Recrawl d'un prospect — namespace crawler (compatible anciens redémarrages partiels)."""
    return _api_refresh_lead_impl(lead_id)


def main():
    import atexit
    import os

    init_db()
    from crawler.storage import mark_crawl_jobs_interrupted_on_startup

    mark_crawl_jobs_interrupted_on_startup()
    try:
        from crawler.engine import bootstrap_background_services

        bootstrap_background_services()
    except Exception:
        logging.exception("Veille auto — échec au démarrage local")
    atexit.register(checkpoint_database)
    port = int(os.getenv("PORT", "8000"))
    _ensure_vitrine_public_routes()
    if VITRINE_INDEX.is_file():
        print(f"Page d'accueil : http://localhost:{port}/", flush=True)
    else:
        print(f"ATTENTION — vitrine/index.html introuvable : {VITRINE_INDEX}", flush=True)
    if VITRINE_ESTIMATION_HTML.is_file():
        print(f"Estimation  : http://localhost:{port}/estimation", flush=True)
    else:
        print(f"ATTENTION — vitrine/estimation.html introuvable : {VITRINE_ESTIMATION_HTML}", flush=True)
    if VITRINE_ANNONCES_HTML.is_file():
        print(f"Annonces    : http://localhost:{port}/annonces", flush=True)
    else:
        print(f"ATTENTION — vitrine/annonces.html introuvable : {VITRINE_ANNONCES_HTML}", flush=True)
    if CRM_INDEX.is_file():
        print(f"CRM : http://localhost:{port}/crm  ({CRM_INDEX})", flush=True)
    else:
        print(f"ATTENTION — crm/index.html introuvable : {CRM_INDEX}", flush=True)
    print(f"Veliora démarré sur http://localhost:{port}", flush=True)
    print("API v7 — mise à jour prospect : POST /api/crawler/leads/<id>/refresh", flush=True)
    print("Dossier projet :", BASE_DIR, flush=True)
    print("Appuyez sur Ctrl+C pour arrêter.", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
