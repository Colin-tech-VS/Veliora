"""Vérification obligatoire des données avant enregistrement / mise à jour."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from crawler.extractors import CORE_CRAWL_FIELDS, LeadData, normalize_phone, parse_euro_amount
from crawler.hub_detection import is_hub_listing_address, is_site_navigation_name, is_listing_title_name
from crawler.config import (
    PRICE_MAX_RENT_EUR,
    PRICE_MAX_SALE_EUR,
    PRICE_MIN_RENT_EUR,
    PRICE_MIN_SALE_EUR,
)
from crawler.extractors import TransactionType

PHONE_DIGITS_RE = re.compile(r"^0[1-9]\d{8}$")
EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
GENERIC_NAMES = frozenset({
    "contact", "vendeur", "propriétaire", "propriete", "propriété", "annonceur", "nom",
    "n/a", "na", "inconnu", "anonymous", "particulier", "sans agence", "annonce",
})


@dataclass
class VerificationResult:
    ok: bool
    complete: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    score: int = 0

    def summary(self) -> str:
        if self.ok and self.complete:
            return "Données vérifiées — OK"
        if self.errors:
            return "Échec vérif. : " + ", ".join(self.errors)
        return "Vérification partielle"


def missing_core_fields(lead: LeadData) -> list[str]:
    """Champs minimum : adresse, téléphone, email, surface (m²)."""
    missing: list[str] = []
    if not _address_ok(lead.address):
        missing.append("address")
    if not _phone_ok(lead.phone):
        missing.append("phone")
    if not _email_ok(lead.email):
        missing.append("email")
    if not _surface_ok(lead.surface):
        missing.append("surface")
    return missing


def _phone_ok(phone: str | None) -> bool:
    if not phone or phone == "—":
        return False
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return bool(PHONE_DIGITS_RE.match(digits))


def _email_ok(email: str | None) -> bool:
    if not email or email == "—":
        return False
    em = email.strip().lower()
    if em.endswith((".png", ".jpg", ".webp", ".svg")):
        return False
    return bool(EMAIL_RE.match(em))


def _name_ok(first: str | None, last: str | None) -> bool:
    if is_listing_title_name(first, last):
        return False
    for part in (first, last):
        if not part or len(part.strip()) < 2:
            return False
        if part.strip().lower() in GENERIC_NAMES:
            return False
        if is_site_navigation_name(part):
            return False
        if re.fullmatch(r"[\d\s\W]+", part):
            return False
    combined = " ".join(p.strip() for p in (first, last) if p and p.strip())
    if is_site_navigation_name(combined):
        return False
    return True


def repair_mixed_listing(
    lead: LeadData,
    html: str | None,
    page_url: str = "",
    *,
    coherence_hint: str = "",
) -> LeadData:
    """
    Répare une fiche mélangée ou polluée (hub, mix prix/surface, contacts footer).
    Utilise le consensus multi-sources (facts) puis sanitize.
    """
    from bs4 import BeautifulSoup

    from crawler.listing_facts import verify_and_apply_listing_facts

    hint = (coherence_hint or "").lower()

    if html and len(html) > 400:
        try:
            soup = BeautifulSoup(html, "lxml")
            verify_and_apply_listing_facts(lead, soup, page_url or lead.source_url or "")
        except Exception:
            pass

    if any(
        x in hint
        for x in (
            "téléphone hors bloc",
            "mix contacts",
            "téléphone hors",
        )
    ):
        lead.phone = None

    if any(
        x in hint
        for x in (
            "nom = type de bien",
            "menu du site",
            "nom = menu",
        )
    ):
        lead.first_name = None
        lead.last_name = None

    if any(
        x in hint
        for x in (
            "titre de page liste",
            "page liste",
            "hub",
            "titre page",
        )
    ):
        if is_hub_listing_address(lead.address or ""):
            lead.address = None

    if any(
        x in hint
        for x in (
            "loyer/surface",
            "prix/surface",
            "confusion",
            "plusieurs annonces",
            "mix annonces",
        )
    ):
        fa = lead.raw_extras.get("facts_audit") or {}
        if fa.get("checks_failed") and not fa.get("checks_passed"):
            lead.price = None
            lead.surface = None
        if html and len(html) > 400:
            try:
                soup = BeautifulSoup(html, "lxml")
                verify_and_apply_listing_facts(lead, soup, page_url or lead.source_url or "")
            except Exception:
                pass

    return sanitize_lead(lead)


def sanitize_lead(lead: LeadData) -> LeadData:
    """Retire adresses hub, noms menu site et autres valeurs invalides."""
    if is_hub_listing_address(lead.address):
        lead.address = None
    if not _name_ok(lead.first_name, lead.last_name):
        lead.first_name = None
        lead.last_name = None
    if lead.email and not _email_ok(lead.email):
        lead.email = None
    if lead.phone and not _phone_ok(lead.phone):
        lead.phone = None
    if lead.surface is not None and not _surface_ok(lead.surface):
        lead.surface = None
    if lead.price is not None and not _price_ok(lead.price, lead.transaction_type):
        lead.price = None
    return lead


_BAD_ADDRESS_RE = re.compile(
    r"masqu[ée]|formulaire|votre\s+adresse|champ\s+est\s+masqu|placeholder|"
    r"saisissez\s+votre|exemple\s*:|lorem\s+ipsum",
    re.IGNORECASE,
)


def _address_ok(address: str | None) -> bool:
    if not address or address == "—":
        return False
    if is_hub_listing_address(address):
        return False
    a = address.strip()
    if _BAD_ADDRESS_RE.search(a):
        return False
    return len(a) >= 8 and re.search(r"\d|[A-Za-zÀ-ÿ]{3,}", a)


def _surface_ok(surface: float | None) -> bool:
    if surface is None:
        return False
    return 5 <= surface <= 50_000


def _published_at_ok(published_at: str | None) -> bool:
    if not published_at:
        return False
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", published_at.strip())
    if not m:
        return False
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    if d > date.today() or d.year < 2005:
        return False
    return True


def _price_ok(
    price: int | None,
    transaction: TransactionType = "vente",
) -> bool:
    if price is None:
        return True
    if transaction == "location":
        return PRICE_MIN_RENT_EUR <= price <= PRICE_MAX_RENT_EUR
    return PRICE_MIN_SALE_EUR <= price <= PRICE_MAX_SALE_EUR


def prepare_lead_defaults(lead: LeadData) -> LeadData:
    """Complète les champs manquants non bloquants avant enregistrement."""
    from datetime import date

    if is_hub_listing_address(lead.address):
        lead.address = None

    lead = sanitize_lead(lead)

    if not lead.published_at:
        lead.published_at = date.today().isoformat()
    if lead.first_name and not lead.last_name:
        if not is_listing_title_name(lead.first_name):
            lead.last_name = lead.first_name
        else:
            lead.first_name = None
    elif lead.last_name and not lead.first_name:
        if not is_listing_title_name(lead.last_name):
            lead.first_name = lead.last_name
        else:
            lead.last_name = None
    elif not lead.first_name and not lead.last_name:
        pass
    return lead


def verify_lead_minimal(lead: LeadData) -> VerificationResult:
    """
    Dernier recours crawl : enregistrer si l'annonce est identifiable.
    Au moins téléphone OU (adresse + surface/prix).
    """
    lead = prepare_lead_defaults(lead)
    errors: list[str] = []
    if not lead.source_url or not lead.source_url.startswith("http"):
        errors.append("URL annonce invalide")

    has_phone = _phone_ok(lead.phone)
    has_email = _email_ok(lead.email)
    has_address = _address_ok(lead.address)
    has_surface = _surface_ok(lead.surface)
    has_price = lead.price is not None and lead.price > 0

    contact_ok = has_phone or has_email
    property_ok = has_address and (has_surface or has_price)
    metrics_only = (has_surface or has_price) and bool(
        (lead.owner or "").strip() and (lead.owner or "").strip() != "—"
    )
    if not contact_ok and not property_ok and not metrics_only:
        errors.append("téléphone, adresse ou prix/surface requis")

    complete = len(errors) == 0 and (contact_ok or property_ok or metrics_only)
    score = 40
    if has_phone:
        score += 25
    if has_address:
        score += 20
    if has_surface or has_price:
        score += 15

    return VerificationResult(
        ok=complete,
        complete=False,
        errors=errors,
        warnings=["enregistrement minimal — compléter manuellement"] if complete else [],
        score=min(100, score),
    )


def verify_lead_actionable(lead: LeadData) -> VerificationResult:
    """
    Minimum obligatoire pour enregistrer un crawl : adresse, téléphone, email, m².
    """
    from crawler.errors import FIELD_LABELS

    lead = prepare_lead_defaults(lead)
    missing = missing_core_fields(lead)
    errors = [f"{FIELD_LABELS.get(f, f)} manquant" for f in missing]
    complete = len(errors) == 0
    score = max(0, int((len(CORE_CRAWL_FIELDS) - len(missing)) / len(CORE_CRAWL_FIELDS) * 100))

    return VerificationResult(
        ok=complete,
        complete=complete,
        errors=errors,
        warnings=["fiche complète (4 champs minimum)"] if complete else [],
        score=score,
    )


def verify_lead(lead: LeadData, *, strict_complete: bool = True) -> VerificationResult:
    """Contrôle qualité — obligatoire avant INSERT/UPDATE."""
    errors: list[str] = []
    warnings: list[str] = []

    if not lead.source_url or not lead.source_url.startswith("http"):
        errors.append("URL annonce invalide")

    if not _phone_ok(lead.phone):
        errors.append("téléphone invalide ou manquant")

    if not _email_ok(lead.email):
        errors.append("email invalide ou manquant")

    if not _name_ok(lead.first_name, lead.last_name):
        errors.append("nom / prénom invalides")

    if not _address_ok(lead.address):
        errors.append("adresse invalide ou manquante")

    if not _surface_ok(lead.surface):
        errors.append("surface invalide")

    if not _published_at_ok(lead.published_at):
        errors.append("date de publication invalide ou manquante")

    if lead.price is not None and not _price_ok(lead.price, lead.transaction_type):
        warnings.append("prix hors fourchette — ignoré")

    missing = lead.missing_fields()
    complete = len(missing) == 0 and not errors

    total_required = 7
    filled = total_required - len(missing)
    score = int((filled / total_required) * 100)
    if errors:
        score = max(0, score - len(errors) * 15)

    ok = complete if strict_complete else (not errors and filled >= 4)

    return VerificationResult(
        ok=ok and (complete or not strict_complete),
        complete=complete,
        errors=errors,
        warnings=warnings,
        score=score,
    )


def _facts_field_confirmed(lead: LeadData, field: str) -> bool:
    fa = lead.raw_extras.get("facts_audit") or {}
    passed = fa.get("checks_passed") or []
    if field == "price":
        return "prix confirmé" in passed
    if field == "surface":
        return "surface confirmée" in passed
    if field == "published_at":
        return "date confirmée" in passed
    return True


def lead_from_db_row(row: dict) -> LeadData:
    raw_extras: dict = {}
    if row.get("listing_title"):
        raw_extras["listing_title"] = row["listing_title"]
    fa = row.get("facts_audit")
    if fa:
        if isinstance(fa, str):
            import json

            try:
                raw_extras["facts_audit"] = json.loads(fa)
            except json.JSONDecodeError:
                pass
        elif isinstance(fa, dict):
            raw_extras["facts_audit"] = fa
    return LeadData(
        first_name=row.get("first_name"),
        last_name=row.get("last_name"),
        phone=row.get("phone") if row.get("phone") != "—" else None,
        email=row.get("email") if row.get("email") != "—" else None,
        address=row.get("address") if row.get("address") != "—" else None,
        surface=row.get("surface"),
        price=row.get("price") or None,
        transaction_type=row.get("transaction_type") or "vente",
        price_period=row.get("price_period"),
        published_at=row.get("published_at"),
        source=row.get("source") or "",
        source_url=row.get("source_url") or "",
        agency=row.get("agency"),
        type=row.get("listing_type") or row.get("type") or "particulier",
        raw_extras=raw_extras,
    )


def merge_lead_for_update(
    existing: LeadData,
    fresh: LeadData,
    *,
    deep_refresh: bool = False,
) -> LeadData:
    """
    Recrawl : fusionne ancien + nouveau en gardant les meilleures valeurs validées.
    Les champs incohérents en base sont effacés et remplacés si le crawl fournit mieux.
    deep_refresh : priorité aux contacts et au type agence/particulier du crawl poussé.
    """
    existing = sanitize_lead(existing)
    fresh = sanitize_lead(fresh)

    merged = LeadData(
        source=fresh.source or existing.source,
        source_url=fresh.source_url or existing.source_url,
    )

    if _name_ok(fresh.first_name, fresh.last_name):
        merged.first_name = fresh.first_name
        merged.last_name = fresh.last_name
    elif _name_ok(existing.first_name, existing.last_name):
        merged.first_name = existing.first_name
        merged.last_name = existing.last_name
    else:
        merged.first_name = fresh.first_name or existing.first_name
        merged.last_name = fresh.last_name or existing.last_name

    if _phone_ok(fresh.phone):
        merged.phone = normalize_phone(fresh.phone)
    elif _phone_ok(existing.phone):
        merged.phone = normalize_phone(existing.phone)
    else:
        merged.phone = None

    if _email_ok(fresh.email):
        merged.email = fresh.email.strip().lower()
    elif _email_ok(existing.email):
        merged.email = existing.email.strip().lower()
    else:
        merged.email = None

    if _address_ok(fresh.address):
        merged.address = fresh.address.strip()
    elif _address_ok(existing.address):
        merged.address = existing.address.strip()
    else:
        merged.address = None

    if _facts_field_confirmed(fresh, "surface") and _surface_ok(fresh.surface):
        merged.surface = fresh.surface
    elif _surface_ok(existing.surface):
        merged.surface = existing.surface
    else:
        merged.surface = fresh.surface if _surface_ok(fresh.surface) else None
    if merged.surface is None and _surface_ok(fresh.surface):
        merged.surface = fresh.surface

    new_price = (
        fresh.price
        if _facts_field_confirmed(fresh, "price") and _price_ok(fresh.price, fresh.transaction_type)
        else None
    )
    old_price = existing.price if _price_ok(existing.price, existing.transaction_type) else None
    if new_price is not None:
        merged.price = new_price
        merged.transaction_type = fresh.transaction_type
        merged.price_period = fresh.price_period
    else:
        merged.price = old_price
        merged.transaction_type = existing.transaction_type
        merged.price_period = existing.price_period
    if merged.price is None and _price_ok(fresh.price, fresh.transaction_type):
        merged.price = fresh.price
        merged.transaction_type = fresh.transaction_type or merged.transaction_type
        merged.price_period = fresh.price_period or merged.price_period

    merged.transaction_type = fresh.transaction_type or existing.transaction_type
    merged.price_period = fresh.price_period if fresh.price_period else existing.price_period
    if _facts_field_confirmed(fresh, "published_at") and _published_at_ok(fresh.published_at):
        merged.published_at = fresh.published_at
    elif _published_at_ok(existing.published_at):
        merged.published_at = existing.published_at
    else:
        merged.published_at = fresh.published_at or existing.published_at
    pub_audit = fresh.raw_extras.get("publisher_audit") if isinstance(
        fresh.raw_extras.get("publisher_audit"), dict
    ) else {}
    audit_type = pub_audit.get("type") if pub_audit else None

    if deep_refresh and audit_type in ("agence", "particulier"):
        merged.type = audit_type
    elif deep_refresh and fresh.type in ("agence", "particulier"):
        merged.type = fresh.type
    else:
        merged.type = fresh.type or existing.type or "particulier"
        if fresh.type == "agence":
            merged.type = "agence"
        elif existing.type == "agence" and fresh.type != "particulier":
            merged.type = "agence"

    if merged.type == "agence":
        agency_src = (
            (pub_audit.get("agency") if pub_audit else None)
            or fresh.agency
            or existing.agency
            or ""
        )
        merged.agency = agency_src.strip() or None
    else:
        merged.agency = None

    merged.raw_extras = {**existing.raw_extras, **fresh.raw_extras}

    return sanitize_lead(merged)
