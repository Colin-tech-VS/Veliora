"""Vérification obligatoire des données avant enregistrement / mise à jour."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from crawler.extractors import (
    CORE_CRAWL_FIELDS,
    LeadData,
    is_fake_phone_digits,
    is_placeholder_email,
    normalize_phone,
    parse_euro_amount,
)
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

# Fragments de descriptif d'annonce captés à tort comme « nom » (ex. « Nombre de
# lots de la copropriété »). Ces termes n'apparaissent jamais dans un vrai nom de
# vendeur / d'agence → on rejette pour ne pas enregistrer de fausse donnée.
_LISTING_DETAIL_IN_NAME_RE = re.compile(
    r"copropri[ée]t|copro\b|lots?\s+de|nombre\s+de|charges|honoraires|mensualit|"
    r"d[ée]p[ôo]t\s+de\s+garantie|diagnostic|\bdpe\b|loyer|\bm²|\bm2\b|"
    r"pi[èe]ces?|[ée]tages?|chambres?|s[ée]jour|surface\b",
    re.IGNORECASE,
)

# Phrases / CTA / champs d'annonce captés à tort comme nom de vendeur.
_NAME_GARBAGE_RE = re.compile(
    r"taxe|fonci[èe]r|contactez|appelez|d[ée]couvrez|conseiller|rangements|"
    r"lumineu|r[ée]nov|\bproche\b|id[ée]al|exclusivit|comprend|\bsitu[ée]e?\b|"
    r"\bvendu\b|coup\s+de\s+c|\bref\b|r[ée]f[ée]rence|\bprix\b|n[°o]\s*\d",
    re.IGNORECASE,
)

# Adresse qui est en réalité un titre / descriptif d'annonce (pas une vraie adresse).
_LISTING_TITLE_ADDR_RE = re.compile(
    r"^\s*(vente|achat|location|[àa]\s+vendre|appartement|maison|studio|terrain|"
    r"immeuble|local|parking|garage|bureau|villa|duplex|loft|programme|neuf)\b|"
    r"\b\d+\s*(m²|m2|pi[èe]ce)|€|\bt\s?[1-9]\b",
    re.IGNORECASE,
)


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
    if not PHONE_DIGITS_RE.match(digits):
        return False
    # Refuse les numéros factices (06 06 06 06 06, 0123456789, etc.)
    return not is_fake_phone_digits(digits)


def _email_ok(email: str | None) -> bool:
    if not email or email == "—":
        return False
    em = email.strip().lower()
    if em.endswith((".png", ".jpg", ".webp", ".svg")):
        return False
    if not EMAIL_RE.match(em):
        return False
    # Refuse les emails placeholder / no-reply (jamais le vrai vendeur)
    return not is_placeholder_email(em)


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
    if _LISTING_DETAIL_IN_NAME_RE.search(combined):
        return False
    # Un vrai nom commence par une majuscule (pas un fragment « ez … », « breux … »),
    # tient en ≤ 4 mots, et ne contient pas de CTA / champ d'annonce.
    if combined[:1].islower():
        return False
    if len(combined.split()) > 4:
        return False
    if _NAME_GARBAGE_RE.search(combined):
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
    if not _price_per_m2_plausible(lead):
        # Prix/m² aberrant = quasi toujours une concaténation à l'extraction
        # (ex. 12 362 250 € pour 232 m²). On abandonne le prix plutôt que
        # d'enregistrer une donnée faussée ; l'annonce reste valide via la surface.
        lead.price = None
    return lead


def _price_per_m2_plausible(lead: LeadData) -> bool:
    """Garde-fou anti-prix faussé : rejette un prix/m² manifestement impossible."""
    if not lead.price or not lead.surface or lead.surface <= 0:
        return True
    per_m2 = lead.price / lead.surface
    if (lead.transaction_type or "vente") == "location":
        # Loyer : un loyer/m²/mois > 200 € est presque toujours une erreur.
        return per_m2 <= 200
    # Vente : même le luxe parisien plafonne ~25 000 €/m². Au-delà = bug.
    return per_m2 <= 40_000


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
    if _LISTING_TITLE_ADDR_RE.search(a):
        # Titre / descriptif d'annonce capté comme adresse (ex. « Vente appartement
        # 3 pièces 65 m² ») → pas une vraie adresse.
        return False
    return bool(len(a) >= 8 and re.search(r"\d|[A-Za-zÀ-ÿ]{3,}", a))


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

    if is_hub_listing_address(lead.address):
        lead.address = None

    lead = sanitize_lead(lead)

    # Ne jamais inventer la date de publication (≠ date de crawl / created_at).
    if lead.published_at and not _published_at_ok(lead.published_at):
        lead.published_at = None

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
        if lead.price or lead.surface:
            lead.first_name = "Prospect"
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
    listing_ok = (has_surface or has_price) and (
        has_address or bool((lead.source_url or "").startswith("http"))
    )
    metrics_only = listing_ok and bool(
        (lead.owner or "").strip() and (lead.owner or "").strip() != "—"
    )
    if not contact_ok and not property_ok and not metrics_only and not listing_ok:
        errors.append("téléphone, adresse ou prix/surface requis")

    complete = len(errors) == 0 and (
        contact_ok or property_ok or metrics_only or listing_ok
    )
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


def verify_lead_crawl_snapshot(lead: LeadData) -> VerificationResult:
    """Enregistrement crawl : annonce identifiable (URL + au moins un indicateur bien)."""
    lead = prepare_lead_defaults(lead)
    errors: list[str] = []
    if not lead.source_url or not lead.source_url.startswith("http"):
        errors.append("URL annonce invalide")

    has_price = lead.price is not None and lead.price > 0
    has_surface = _surface_ok(lead.surface)
    addr = (lead.address or "").strip()
    has_loose_address = bool(addr and addr != "—" and len(addr) >= 5 and not is_hub_listing_address(addr))
    has_contact = _phone_ok(lead.phone) or _email_ok(lead.email)

    if not (has_price or has_surface or has_loose_address or has_contact):
        errors.append("aucune donnée exploitable (prix, surface, adresse ou contact)")

    ok = len(errors) == 0
    score = 35
    if has_contact:
        score += 25
    if has_loose_address:
        score += 20
    if has_price or has_surface:
        score += 20

    return VerificationResult(
        ok=ok,
        complete=False,
        errors=errors,
        warnings=["fiche crawl — compléter téléphone / email si besoin"] if ok else [],
        score=min(100, score),
    )


def verify_lead_actionable(lead: LeadData) -> VerificationResult:
    """
    Minimum pour enregistrer un crawl : contact (tél. ou email) + adresse + prix ou m².
    L'email seul sur l'annonce est rare — le téléphone suffit.
    """
    from crawler.errors import FIELD_LABELS

    lead = prepare_lead_defaults(lead)
    errors: list[str] = []
    has_phone = _phone_ok(lead.phone)
    has_email = _email_ok(lead.email)
    has_address = _address_ok(lead.address)
    has_surface = _surface_ok(lead.surface)
    has_price = lead.price is not None and lead.price > 0

    if not has_phone and not has_email:
        errors.append(f"{FIELD_LABELS.get('phone', 'téléphone')} ou email requis")
    if not has_address:
        errors.append(f"{FIELD_LABELS.get('address', 'adresse')} manquant")
    if not has_surface and not has_price:
        errors.append("surface ou prix requis")

    complete = len(errors) == 0
    filled = (
        int(has_phone or has_email)
        + int(has_address)
        + int(has_surface or has_price)
    )
    score = max(0, int(filled / 3 * 100))

    return VerificationResult(
        ok=complete,
        complete=complete,
        errors=errors,
        warnings=["fiche exploitable (contact + bien)"] if complete else [],
        score=score,
    )


def resolve_crawl_verification(
    lead: LeadData,
    *,
    require_verification: bool = True,
) -> tuple[VerificationResult, bool]:
    """Choisit le niveau de vérification (strict → snapshot). Retourne (résultat, partial)."""
    from crawler.config import (
        SAVE_ACTIONABLE_LEADS,
        SAVE_CRAWL_SNAPSHOT,
        SAVE_MINIMAL_LEADS,
    )

    lead = prepare_lead_defaults(lead)
    if not require_verification:
        snap = verify_lead_crawl_snapshot(lead)
        return snap, not snap.complete

    strict = verify_lead(lead, strict_complete=True)
    if strict.complete:
        return strict, False

    attempts: list[tuple[str, VerificationResult]] = []
    if SAVE_ACTIONABLE_LEADS:
        attempts.append(("actionable", verify_lead_actionable(lead)))
    if SAVE_MINIMAL_LEADS:
        attempts.append(("minimal", verify_lead_minimal(lead)))
    if SAVE_CRAWL_SNAPSHOT:
        attempts.append(("snapshot", verify_lead_crawl_snapshot(lead)))

    for _name, result in attempts:
        if result.ok:
            return result, not result.complete

    last = attempts[-1][1] if attempts else strict
    return last, False


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
    elif _published_at_ok(fresh.published_at):
        merged.published_at = fresh.published_at
    elif _published_at_ok(existing.published_at):
        merged.published_at = existing.published_at
    else:
        merged.published_at = None

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


def resolve_published_at(
    incoming: str | None,
    stored: str | None = None,
) -> str | None:
    """Date de mise en ligne portail — jamais la date de crawl."""
    if _published_at_ok(incoming):
        return str(incoming).strip()[:10]
    if _published_at_ok(stored):
        return str(stored).strip()[:10]
    return None
