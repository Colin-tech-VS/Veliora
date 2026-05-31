"""Codes d'erreur crawl et messages en français."""

from __future__ import annotations

FIELD_LABELS = {
    "address": "adresse",
    "phone": "téléphone",
    "email": "email",
    "first_name": "prénom",
    "last_name": "nom",
    "surface": "surface (m²)",
    "published_at": "date de publication",
}


def format_missing_fields(fields: list[str]) -> str:
    return ", ".join(FIELD_LABELS.get(f, f) for f in fields)


class CrawlError:
    SITE_BLOCKED = "SITE_BLOCKED"
    FETCH_FAILED = "FETCH_FAILED"
    PLAYWRIGHT_MISSING = "PLAYWRIGHT_MISSING"
    NO_LISTINGS = "NO_LISTINGS"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"
    DUPLICATE = "DUPLICATE"
    SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
    TIMEOUT = "TIMEOUT"

    MESSAGES = {
        SITE_BLOCKED: "Accès bloqué par le site (anti-bot / Cloudflare). Le crawl furtif a échoué — réessayez plus tard ou ciblez une URL moins protégée.",
        FETCH_FAILED: "Impossible d'accéder à la page — vérifiez l'URL ou votre connexion.",
        PLAYWRIGHT_MISSING: "Playwright non installé. Exécutez : playwright install chromium",
        NO_LISTINGS: "Aucune annonce trouvée sur cette page de recherche.",
        INCOMPLETE_DATA: "Annonce trouvée mais données contact incomplètes.",
        DUPLICATE: "Annonce déjà en base de données.",
        SOURCE_UNKNOWN: "Source de crawl introuvable.",
        TIMEOUT: "Délai d'attente dépassé — le site met trop de temps à répondre.",
    }

    @classmethod
    def message(cls, code: str, detail: str | None = None) -> str:
        base = cls.MESSAGES.get(code, detail or "Erreur inconnue")
        return f"{base} {detail}".strip() if detail and code not in cls.MESSAGES else base

    @classmethod
    def issue(cls, code: str, detail: str | None = None, url: str | None = None) -> dict:
        return {
            "code": code,
            "message": cls.message(code, detail),
            "detail": detail,
            "url": url,
        }
