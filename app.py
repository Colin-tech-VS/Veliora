#!/usr/bin/env python3
"""Veliora — Serveur Flask (API + frontend pige immobilière IA)."""

from __future__ import annotations

import logging
import threading
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

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
_db_init_lock = threading.Lock()


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


@app.before_request
def ensure_db():
    if getattr(app, "_db_ready", False):
        return
    with _db_init_lock:
        if getattr(app, "_db_ready", False):
            return
        init_db()
        try:
            backup_database()
        except OSError as exc:
            logging.warning("Sauvegarde SQLite ignorée : %s", exc)
        try:
            refresh_source_names_and_logos()
        except Exception as exc:
            logging.warning("Mise à jour logos sources ignorée : %s", exc)
        from crawler.storage import mark_crawl_jobs_interrupted_on_startup

        mark_crawl_jobs_interrupted_on_startup()
        expire_stale_crawl_jobs()
        app._db_ready = True
        logging.info("Base Veliora : %s", db_status())


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
        "api_version": 7,
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
        "dvf_app": "https://app.dvf.etalab.gouv.fr/",
        "vitrine": "/",
        "vitrine_ok": VITRINE_INDEX.is_file(),
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
    hint = ""
    if request.path.startswith("/crm"):
        hint = " Relancez demarrer.bat (Ctrl+C puis python app.py) si vous venez de déplacer les fichiers."
    if p.startswith("/legal") or "cgv" in p or "confidentialite" in p:
        hint = " Lancez Veliora avec demarrer.bat ou python app.py (pas Live Server seul)."
    return f"Not Found — {request.path}.{hint}", 404


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
    return send_file(path, mimetype="text/html; charset=utf-8")


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

    pages = ["/", "/offre", "/legal"]
    urls = "\n".join(
        f"  <url><loc>{SITE_URL}{p}</loc><changefreq>weekly</changefreq></url>"
        for p in pages
    )
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
    return send_from_directory(CRM_DIR, "sw.js", mimetype="application/javascript")


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


@app.route("/crm/assets/<path:filename>")
def crm_assets(filename):
    resp = send_from_directory(
        CRM_DIR / "assets",
        filename,
        mimetype=_static_mimetype(filename),
    )
    return _with_asset_cache(resp)


@app.route("/vitrine/assets/<path:filename>")
def vitrine_assets(filename):
    resp = send_from_directory(
        VITRINE_DIR / "assets",
        filename,
        mimetype=_static_mimetype(filename),
    )
    return _with_asset_cache(resp)


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
    agency_id = _aid()
    try:
        from crawler.storage import claim_orphan_leads

        claim_orphan_leads(agency_id)
        leads = get_leads(agency_id, claim_orphans=False)
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
    leads = get_leads(agency_id)
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
        lead = patch_lead(lead_id, _aid(), data)
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

            saved_at = save_lead_estimate(lead_id, agency_id, result)
            result["saved_at"] = saved_at
            result["lead"] = get_lead(lead_id, agency_id)
        except Exception:
            logging.exception("save_lead_estimate %s", lead_id)
    return jsonify(result)


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
    limit = min(int(data.get("limit") or 25), 50)
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
    row = get_lead_by_source_url(norm, agency_id)
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
    from crm.radar import call_script_for_lead

    lead = get_lead(lead_id, _aid())
    if not lead:
        return jsonify({"error": "Prospect introuvable"}), 404
    user = get_current_user() or {}
    caller = " ".join(
        p for p in (user.get("first_name"), user.get("last_name")) if p
    ) or "votre conseiller"
    return jsonify({"script": call_script_for_lead({
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
    from crawler.storage import expire_stale_crawl_jobs

    expire_stale_crawl_jobs()
    agency_id = _aid()
    status = engine.status()
    stats = get_stats(agency_id)
    sources = get_sources(agency_id, sync=False, live_counts=False)
    return jsonify({
        **status,
        "found_today": sum(s.get("leads_updated_today", s.get("today", 0)) for s in sources),
        "active_sources": sum(1 for s in sources if s["enabled"]),
        "total_leads": stats["total"],
        "prospects_in_db": stats["total"],
    })


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
    try:
        job = get_crawl_job(job_id, _aid())
        if not job:
            return jsonify({"error": "Job introuvable"}), 404

        lite = request.args.get("lite") in ("1", "true", "yes")
        done = job["status"] in ("completed", "failed")
        include_logs = request.args.get("logs") in ("1", "true", "yes")

        payload = {**job}
        if lite and not include_logs:
            payload["logs"] = []
        else:
            payload["logs"] = get_crawl_logs_for_job(job_id)

        # Ne pas renvoyer toute la base à chaque poll (timeout / 500 côté client).
        payload["leads"] = None
        payload["sources"] = None
        payload["stats"] = None

        return jsonify(payload)
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
    if not cancel_crawl_job(job_id):
        return jsonify({"error": "Aucun crawl actif à annuler"}), 404
    return jsonify({"ok": True})


@app.route("/api/crawler/start", methods=["POST"])
def api_crawler_start():
    engine.start_background()
    return jsonify({"ok": True, **engine.status()})


@app.route("/api/crawler/stop", methods=["POST"])
def api_crawler_stop():
    engine.stop_background()
    return jsonify({"ok": True, **engine.status()})


def _resolve_crawl_city(agency_id: str) -> str:
    """Ville du crawl : celle de la requête sinon la ville enregistrée de l'agence.

    Le crawl est exclusivement local (par ville) — aucun crawl national.
    Lève ValueError si aucune ville n'est disponible.
    """
    data = request.get_json(silent=True) or {}
    city = (data.get("city") or data.get("ville") or "").strip()
    if not city:
        city = get_agency_primary_city(agency_id) or ""
    if not city:
        raise ValueError(
            "Renseignez la ville de votre agence (Territoire) — le crawl se fait par ville."
        )
    return city


@app.route("/api/crawler/scan", methods=["POST"])
def api_crawler_scan():
    try:
        city = _resolve_crawl_city(_aid())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    job = engine.scan_all_enabled(city=city, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/crawler/scan/<source_id>", methods=["POST"])
def api_crawler_scan_source(source_id):
    source = get_source(source_id, _aid())
    if not source:
        return jsonify({"error": "Source introuvable pour votre agence"}), 404
    paid = _paid_portal_crawl_response(source=source)
    if paid:
        return paid
    try:
        city = _resolve_crawl_city(_aid())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    job = engine.scan_source(source_id, city=city, agency_id=_aid())
    return jsonify(_job_response(job))


@app.route("/api/public/config")
def api_public_config():
    from crm.config import public_site_config
    from crm.billing.config import public_stripe_config

    return jsonify({"ok": True, **public_site_config(), "stripe": public_stripe_config()})


@app.route("/api/onboarding", methods=["GET", "PATCH"])
def api_onboarding():
    agency_id = _aid()
    if request.method == "GET":
        settings = get_agency_settings(agency_id)
        from crawler.storage import get_connection, row_scalar

        with get_connection() as conn:
            sources_count = row_scalar(
                conn.execute(
                    "SELECT COUNT(*) FROM sources WHERE agency_id = ?",
                    (agency_id,),
                ).fetchone()
            )
            leads_count = row_scalar(
                conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE agency_id = ?",
                    (agency_id,),
                ).fetchone()
            )
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
    if data.get("complete"):
        settings = set_onboarding(agency_id, step=3, completed=True)
    else:
        step = data.get("step")
        settings = set_onboarding(
            agency_id,
            step=int(step) if step is not None else None,
            completed=bool(data.get("completed")),
        )
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

    agency_id = _aid()
    try:
        if request.args.get("geocode") in ("1", "true", "yes"):
            geocode_map_leads_sync(agency_id)
        return jsonify(build_map_payload(agency_id))
    except Exception as exc:
        logging.exception("GET /api/map")
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
        mandates = list_seller_mandates(
            agency_id,
            mandate_type=request.args.get("type") or None,
            status=request.args.get("status") or None,
            lead_id=int(request.args["lead_id"]) if request.args.get("lead_id") else None,
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

    type_label = "vente" if mandate["mandate_type"] == "vente" else "location"
    subject = f"Mandat de {type_label} — {mandate.get('title', 'Bien')}"
    html = mandate.get("body_html") or ""
    intro = (
        "<p>Bonjour,</p>"
        "<p>Veuillez trouver ci-dessous votre mandat à valider et signer. "
        "N'hésitez pas à nous contacter pour toute question.</p>"
        "<hr>"
    )
    full_html = intro + html

    sent_smtp = send_email(to, subject, full_html)
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
            return jsonify({"ok": True})
        return jsonify({"error": "Fiche introuvable"}), 404
    data = request.get_json(silent=True) or {}
    client = update_property_client(client_id, agency_id, data)
    if not client:
        return jsonify({"error": "Fiche introuvable"}), 404
    return jsonify({"ok": True, "client": client})


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
    atexit.register(checkpoint_database)
    port = int(os.getenv("PORT", "8000"))
    if VITRINE_INDEX.is_file():
        print(f"Page d'accueil : {VITRINE_INDEX}", flush=True)
    else:
        print(f"ATTENTION — vitrine/index.html introuvable : {VITRINE_INDEX}", flush=True)
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
