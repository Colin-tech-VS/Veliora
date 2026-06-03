"""Contexte requête authentifiée (agence isolée)."""

from __future__ import annotations

from functools import wraps

from flask import g, jsonify, request

from crm.auth.service import get_session_user

PUBLIC_API_PATHS = frozenset({
    "/api/health",
    "/api/public/config",
    "/api/public/estimate",
    "/api/public/estimate/contact",
    "/api/public/estimate/schema",
    "/api/public/portal/listings",
    "/api/auth/register-agency",
    "/api/auth/login",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/billing/webhook",
    "/api/billing/config",
    "/api/clients/import/template",
    "/api/geo/communes",
})


def _extract_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # <img src="…"> n'envoie pas Authorization — token en query pour GET image prospect.
    if request.method == "GET" and "/leads/" in request.path and request.path.endswith("/image"):
        q = (request.args.get("access_token") or request.args.get("token") or "").strip()
        if q:
            return q
    return (request.cookies.get("propscout_token") or "").strip()


def resolve_current_user() -> dict | None:
    return get_session_user(_extract_token())


def get_agency_id() -> str | None:
    return getattr(g, "agency_id", None)


def get_current_user() -> dict | None:
    return getattr(g, "current_user", None)


def require_api_auth():
    if request.method == "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    if request.path in PUBLIC_API_PATHS:
        return None
    if request.path.startswith("/api/public/portal/listings"):
        return None
    if request.path == "/api/auth/me":
        return None

    user = resolve_current_user()
    if not user:
        return jsonify({"error": "Connexion requise. Créez un compte agence ou connectez-vous."}), 401

    g.current_user = user
    g.agency_id = user["agency_id"]

    billing_exempt = request.path in ("/api/auth/me",) or request.path.startswith(
        "/api/billing/"
    )
    if not billing_exempt:
        from crm.billing.access import agency_has_active_subscription
        from crm.billing.config import billing_required

        if billing_required() and not agency_has_active_subscription(user["agency_id"]):
            return (
                jsonify(
                    {
                        "error": (
                            "Abonnement Veliora requis — finalisez le paiement pour accéder au CRM."
                        ),
                        "code": "subscription_required",
                    }
                ),
                402,
            )

    return None


def agency_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not get_agency_id():
            return jsonify({"error": "Connexion requise"}), 401
        return f(*args, **kwargs)

    return wrapper
