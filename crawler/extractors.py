"""Extraction des champs obligatoires depuis le HTML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from crawler.hub_detection import is_hub_listing_address, is_listing_title_name

PHONE_RE = re.compile(
    r"(?:\+33|0033|0)\s*[1-9](?:[\s.\-]{0,2}[0-9]{2}){4}"
)
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
SURFACE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:m²|m2|m\s*²|square\s*meters?)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(
    r"(\d{1,3}(?:[\s\u00a0.]\d{3})+|\d+)\s*€",
    re.IGNORECASE,
)

EXCLUDE_ZONE_RE = re.compile(
    r"similar|similaire|suggest|recommend|related|aussi|voisin|proche|compar|"
    r"carousel|slider|footer|header|nav|menu|breadcrumb|cookie|banner|"
    r"sidebar|aside|widget|plus-de-biens|other-listing|annonces-liees|"
    r"meme-agence|cross-sell|upsell|teaser|pub-|advert",
    re.IGNORECASE,
)

# Prix à ignorer (honoraires, DPE, charges annuelles… — pas les prix au m², gérés à part)
FALSE_PRICE_LABEL_RE = re.compile(
    r"honoraire|frais\s+de|notaire|taxe|dpe|ges|charges?\s+annuel|"
    r"copro|provision|estimation|à\s+partir|budget|crédit|mensualité|"
    r"taxe\s+foncière|fai|fonds\s+de\s+roulement",
    re.IGNORECASE,
)

PRICE_PER_M2_RE = re.compile(
    r"€\s*/\s*m[²2]|eur\s*/\s*m[²2]|"
    r"par\s+m[²2]|au\s+m[²2]|"
    r"/\s*m[²2]\b|"
    r"prix\s+au\s+m[²2]|"
    r"€\s*m[²2]",
    re.IGNORECASE,
)

RENT_HINT_RE = re.compile(
    r"/\s*mois|par\s+mois|€\s*/\s*mois|loyer|location|louer|rent|mensuel|"
    r"charges?\s+comprises|cc\b|hc\b|ht\b",
    re.IGNORECASE,
)

SALE_HINT_RE = re.compile(
    r"\bvente\b|vendre|à\s+vendre|achat|acheter|sale|buy|hors\s+honoraire|"
    r"fai\b|frais\s+d.agence",
    re.IGNORECASE,
)

DOMAIN_SURFACE_SELECTORS: dict[str, list[str]] = {
    "leboncoin": [
        "[data-qa-id='criteria_item_surface']",
        "[data-qa-id*='surface']",
    ],
    "pap": [".item-caracteristiques", ".item-title + .surface", "h1 ~ .surface"],
    "seloger": ["[data-testid='ad-surface']", "[data-testid*='surface']"],
    "logic-immo": [".property-surface", ".criterion-area"],
    "bienici": [".adSummarySurface", "[data-test='surface']"],
    "paruvendu": [".criteria-surface", "h1 ~ .surface"],
    "lefigaro": [".surface-annonce", ".listing-surface"],
}

DOMAIN_ADDRESS_SELECTORS: dict[str, list[str]] = {
    "leboncoin": ["[data-qa-id='adview_location_informations']"],
    "pap": [".item-localisation", ".item-address"],
    "seloger": ["[data-testid='ad-address']", ".Title__Address"],
    "logic-immo": [".property-address", ".annonceTitre"],
    "bienici": [".adSummaryAddress", "[data-test='address']"],
    "paruvendu": ["h1", ".detail-title"],
    "lefigaro": [".adresse-annonce", "[itemprop=address]", ".listing-address"],
}

RELATED_SECTION_HEADING_RE = re.compile(
    r"annonces?\s+similaires?|biens?\s+similaires?|vous\s+(?:aimerez|pourrez)|"
    r"similar\s+listing|related\s+propert|suggested|recommend|"
    r"autres?\s+annonces?|d[']autres?\s+biens|découvrez\s+(?:aussi|également)|"
    r"nearby|à\s+proximité|autour\s+de\s+vous|same\s+agency|m[êe]me\s+agence|"
    r"plus\s+de\s+biens|other\s+listing",
    re.IGNORECASE,
)

DOMAIN_LISTING_ROOT: dict[str, list[str]] = {
    "leboncoin": [
        "[data-qa-id='adview']",
        "[data-qa-id*='adview_container']",
        "main article",
    ],
    "pap": [".item-page", ".item-content", ".item"],
    "seloger": ["[data-testid='ad-detail']", ".Detail", ".property-detail"],
    "logic-immo": [".property-detail", ".detail-main", ".annonceDetail"],
    "bienici": [".adDetailContainer", ".ad-summary", "[data-test='ad-detail']"],
    "paruvendu": [".detail-annonce", ".fiche-detail"],
    "lefigaro": [".annonce-detail", ".detail-annonce", "article.annonce"],
}

DOMAIN_PRICE_SELECTORS: dict[str, list[str]] = {
    "leboncoin": [
        "[data-qa-id='adview_price']",
        "[data-qa-id*='adview_price']",
        "span[data-qa-id*='price']",
    ],
    "pap": [".item-price", ".prix-main", "p.price"],
    "seloger": [
        "[data-testid='price']",
        "[data-testid='ad-price']",
        ".Price__Amount",
    ],
    "logic-immo": [".property-price", ".price-main", ".detail-price"],
    "bienici": [".adSummaryPrice", "[data-test='price']"],
    "paruvendu": [".detail-price", ".price-main", "h1 + .price"],
    "lefigaro": [".price-annonce", ".listing-price", ".price"],
    "immobilier-france": [
        "[class*='price']",
        "[class*='prix']",
        "h1 ~ [class*='price']",
        "h1 ~ [class*='prix']",
    ],
}

LISTING_MAIN_TYPES = (
    "realestatelisting",
    "product",
    "apartment",
    "house",
    "residence",
    "accommodation",
    "offer",
    "singlefamilyresidence",
)
NAME_RE = re.compile(
    r"(?:contact|vendeur|propri[eé]taire|nom)\s*[:\-]?\s*([A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]+(?:\s+[A-ZÀ-ÖØ-Ý][a-zà-öø-ÿ]+)+)",
    re.IGNORECASE,
)

# Minimum produit pour tout crawl / Mode 2
CORE_CRAWL_FIELDS = ("address", "phone", "email", "surface")

REQUIRED_FIELDS = (
    *CORE_CRAWL_FIELDS,
    "first_name",
    "last_name",
    "published_at",
)

BROAD_PHONE_SELECTORS = (
    "a[href^='tel:']",
    "[href*='tel:']",
    "[data-phone]",
    "[data-testid*='phone']",
    "[data-qa-id*='phone']",
    "[class*='phone']",
    "[class*='telephone']",
    "button[class*='phone']",
)
BROAD_EMAIL_SELECTORS = (
    "a[href^='mailto:']",
    "[href*='mailto:']",
    "[data-email]",
    "[data-testid*='email']",
    "[class*='email']",
    "[class*='mail']",
)
GENERIC_ADDRESS_SELECTORS = (
    "h1",
    "[itemprop=address]",
    "address",
    ".address",
    ".adresse",
    ".localisation",
    "[class*='address']",
    "[class*='localisation']",
    "[data-testid*='address']",
)
GENERIC_SURFACE_SELECTORS = (
    "[class*='surface']",
    "[class*='superficie']",
    "[data-testid*='surface']",
    "[data-qa-id*='surface']",
    ".criteria",
    ".caracteristiques",
    ".features",
)

PUBLISHED_HINT_RE = re.compile(
    r"publié|mise en ligne|en ligne depuis|date de parution|depuis le|"
    r"posted on|dateposted|datepublished",
    re.IGNORECASE,
)

ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


TransactionType = Literal["vente", "location"]
PublisherType = Literal["particulier", "agence"]

PLATFORM_EMAIL_RE = re.compile(
    r"@(?:leboncoin|lbc-group|seloger|pap\.fr|logic-immo|bienici|paruvendu|"
    r"vivastreet|figaro|support-|noreply|no-reply|donotreply)",
    re.IGNORECASE,
)

AGENCY_EMAIL_DOMAIN_RE = re.compile(
    r"@(?:[\w.-]*\.)?(?:orpi|century21|laforet|guyhoquet|guy-hoquet|era-immobilier|"
    r"erafrance|immobili|agence[\w.-]*|cabinet[\w.-]*|naxos|foncia|safti|"
    r"stephaneplaza|megagence|nexity|iad[\w.-]*|engelvoelkers|coldwellbanker|"
    r"remax|barnes|danielfeau|christiesrealestate|john-taylor|la-reserve|"
    r"optimhome|capifrance|proprietes-privees|bsk|eiffage|human|squarehabitat)",
    re.IGNORECASE,
)

PERSONAL_EMAIL_RE = re.compile(
    r"@(?:gmail|googlemail|outlook|hotmail|live|msn|yahoo|ymail|orange|wanadoo|"
    r"free\.fr|laposte|icloud|me\.com|sfr|bbox|neuf|club-internet|protonmail|"
    r"gmx|mail\.com)\.",
    re.IGNORECASE,
)

AGENCY_PAGE_HINT_RE = re.compile(
    r"agence\s+immobili|mandataire|professionnel\s+de\s+l.immo|négociateur|"
    r"agent\s+immobilier|commercialisateur|carte\s+professionnelle|"
    r"honoraires\s+.*charge\s+(?:acquéreur|locataire)|frais\s+d.agence|"
    r"mandat\s+exclusif|iad\s+france|safti|orpi|century\s*21|stephane\s+plaza|"
    r"guy\s+hoquet|laforêt|logic\s+immo\s+contact|réseau\s+immobilier|"
    r"société\s+immobili|siret\s*[:\s]?\d|rcs\s+[a-z\d]",
    re.IGNORECASE,
)

PARTICULIER_PAGE_HINT_RE = re.compile(
    r"entre\s+particuliers|particulier\s+à\s+particulier|propriétaire\s+direct|"
    r"contactez\s+(?:le\s+)?propriétaire|sans\s+agence|sans\s+frais\s+d.agence|"
    r"pas\s+d.agence|vendeur\s+particulier|particulier\s+uniquement|"
    r"annonceur\s+particulier|type\s+de\s+vendeur\s*:\s*particulier",
    re.IGNORECASE,
)

PHONE_AGENCY_CONTEXT_RE = re.compile(
    r"\bagence\b|agence\s+immobili|agences?\s+[A-ZÀ-ÖØ-Ý]|"
    r"professionnel(?:le)?|mandataire|n[ée]gociateur|agent\s+immobilier|"
    r"conseiller(?:\s+immobilier)?|commercialisateur|carte\s+professionnelle|"
    r"réseau\s+(?:immobilier|orpi|iad)|\b(?:orpi|century\s*21|foncia|safti|iad|"
    r"stephane\s+plaza|guy\s+hoquet|lafor[eê]t|nexity|optimhome|capifrance)\b|"
    r"votre\s+(?:agence|conseiller)|annonce\s+professionnelle|"
    r"contact\s+(?:agence|professionnel)|commercial\s+immobilier",
    re.IGNORECASE,
)

PHONE_PARTICULIER_CONTEXT_RE = re.compile(
    r"\bparticulier\b|propri[eé]taire|entre\s+particuliers|sans\s+agence|"
    r"vendeur\s+particulier|contact\s+propri[eé]taire|annonceur\s+particulier|"
    r"type\s*:\s*particulier|particulier\s+à\s+particulier",
    re.IGNORECASE,
)

DOMAIN_PHONE_CONTACT_SELECTORS: dict[str, list[str]] = {
    "leboncoin": [
        "[data-qa-id*='contact']",
        "[data-qa-id*='store']",
        "[data-qa-id*='professionnel']",
        ".ProfessionalSeller",
    ],
    "bienici": [
        ".adContactAgency",
        "[data-test='agency-name']",
        "[data-test='contact-agency']",
        ".adContactProfessional",
    ],
    "seloger": ["[data-testid*='agency']", "[data-testid*='contact']", ".Agency__Name"],
    "pap": [".owner-agency", ".annonceur-pro", ".agency-name"],
    "lefigaro": [".agency-name", ".annonceur-agence", ".annonceur-pro"],
    "immobilier-france": [
        ".contact", ".annonceur", "[class*='agence']", "[class*='vendeur']",
        "[class*='contact']", "[class*='phone']", "[class*='telephone']",
    ],
}

ORGANIZATION_JSON_TYPES = (
    "organization",
    "realestateagent",
    "localbusiness",
    "corporation",
    "company",
    "store",
)

DOMAIN_AGENCY_SELECTORS: dict[str, list[str]] = {
    "leboncoin": [
        "[data-qa-id*='store']",
        "[data-qa-id*='professionnel']",
        ".ProfessionalSeller",
        "[class*='Professional']",
    ],
    "pap": [".agency-name", ".owner-agency", ".professionnel", ".annonceur-pro"],
    "seloger": ["[data-testid*='agency']", ".Agency__Name", ".agency-name"],
    "logic-immo": [".agency-name", ".agent-agency", ".professional-name"],
    "bienici": [".adContactAgency", "[data-test='agency-name']"],
    "paruvendu": [".agency-name", ".seller-agency"],
    "lefigaro": [".agency-name", ".annonceur-agence"],
    "immobilier-france": [
        ".agency-name", "[class*='agence']", ".annonceur", "[class*='professionnel']",
        "[class*='mandataire']",
    ],
}

DEEP_CONTACT_PHONE_SELECTORS = (
    'a[href^="tel:"]',
    'a[href*="tel:"]',
    "[data-phone]",
    "[data-telephone]",
    "[data-testid*='phone']",
    "[data-test*='phone']",
    "[data-qa-id*='phone']",
    "button[class*='phone']",
    "[class*='contact'][class*='phone']",
    "[class*='telephone']",
)

DEEP_CONTACT_EMAIL_SELECTORS = (
    'a[href^="mailto:"]',
    "[data-email]",
    "[class*='email'][class*='contact']",
)


@dataclass
class ListingPrice:
    amount: int
    transaction: TransactionType
    period: str | None = None  # "month" pour location


@dataclass
class LeadData:
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    postcode: str | None = None
    sector: str | None = None
    surface: float | None = None
    price: int | None = None
    transaction_type: TransactionType = "vente"
    price_period: str | None = None
    published_at: str | None = None
    source: str = "generic"
    source_url: str = ""
    agency: str | None = None
    type: str = "particulier"
    raw_extras: dict[str, Any] = field(default_factory=dict)

    @property
    def owner(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts)

    def missing_core_fields(self) -> list[str]:
        from crawler.validation import missing_core_fields

        return missing_core_fields(self)

    def missing_fields(self) -> list[str]:
        missing = list(self.missing_core_fields())
        if not self.first_name:
            missing.append("first_name")
        if not self.last_name:
            missing.append("last_name")
        if not self.published_at:
            missing.append("published_at")
        return missing

    def is_complete(self) -> bool:
        return len(self.missing_fields()) == 0

    def completeness_score(self) -> int:
        filled = len(REQUIRED_FIELDS) - len(self.missing_fields())
        return int((filled / len(REQUIRED_FIELDS)) * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "email": self.email,
            "address": self.address,
            "surface": self.surface,
            "price": self.price,
            "transaction_type": self.transaction_type,
            "price_period": self.price_period,
            "published_at": self.published_at,
            "source": self.source,
            "source_url": self.source_url,
            "agency": self.agency,
            "type": self.type,
            "score": self.completeness_score(),
            "missing_fields": self.missing_fields(),
        }


def is_price_per_m2_snippet(text: str) -> bool:
    """True si le texte décrit un prix unitaire au m², pas le prix total."""
    return bool(PRICE_PER_M2_RE.search(text or ""))


def _strip_combined_price_per_m2(text: str) -> str:
    """Retire le prix au m² quand il est collé au prix total (ex. immobilier-france.fr)."""
    t = (text or "").replace("\xa0", " ").strip()
    if not t or not is_price_per_m2_snippet(t):
        return t
    stripped = re.sub(
        r"[\d\s\u00a0.]+\s*€\s*/\s*m[²2][^\d]*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    if stripped and stripped != t:
        return stripped
    for m in PRICE_RE.finditer(t):
        if _price_match_is_per_m2(t, m):
            return t[: m.start()].strip()
    return t


def _price_match_is_per_m2(text: str, match: re.Match[str]) -> bool:
    """True si ce montant € correspond au prix au m² (pas au total)."""
    after = text[match.end() : match.end() + 16]
    return bool(re.search(r"^\s*/\s*m[²2]", after, re.IGNORECASE))


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    if len(digits) == 10:
        return " ".join(digits[i : i + 2] for i in range(0, 10, 2))
    return raw.strip()


# Numéros manifestement bidons / placeholders rencontrés sur les fiches.
_PHONE_FAKE_SET = frozenset({
    "0123456789", "0102030405", "0606060606", "0707070707",
    "0612345678", "0698765432", "0123456788", "0000000000",
    "0102030400", "0101010101",
})


def is_fake_phone_digits(digits: str) -> bool:
    """True si le numéro (10 chiffres normalisés) est factice (placeholder/test).

    On refuse : numéros connus de test, < 3 chiffres distincts
    (06 06 06 06 06, 01 11 11 11 11…) et les séquences strictement
    croissantes/décroissantes (0123456789).
    """
    if len(digits) != 10 or not digits.isdigit():
        return True
    if digits in _PHONE_FAKE_SET:
        return True
    if len(set(digits)) < 3:
        return True
    asc = all(int(digits[i + 1]) - int(digits[i]) == 1 for i in range(9))
    desc = all(int(digits[i]) - int(digits[i + 1]) == 1 for i in range(9))
    return asc or desc


# Emails « placeholder » qui ne sont jamais le vrai contact du vendeur.
_EMAIL_FAKE_LOCAL_RE = re.compile(
    r"^(?:no[-_.]?reply|donotreply|do-not-reply|ne[-_]?pas[-_]?repondre|"
    r"nepasrepondre|mailer-daemon|postmaster|abuse|test|exemple|example|"
    r"votre[._-]?(?:email|mail|adresse)|prenom[._-]?nom|nom[._-]?prenom|"
    r"john[._-]?doe)$",
    re.I,
)
_EMAIL_FAKE_DOMAIN_RE = re.compile(
    r"@(?:example|exemple|domain|domaine|votredomaine|mondomaine|test)\."
    r"(?:com|fr|org|net|eu)$",
    re.I,
)


def is_placeholder_email(email: str | None) -> bool:
    """True si l'email est un placeholder/no-reply (pas un vrai contact)."""
    em = (email or "").strip().lower()
    if "@" not in em:
        return True
    local = em.split("@", 1)[0]
    if _EMAIL_FAKE_LOCAL_RE.match(local):
        return True
    return bool(_EMAIL_FAKE_DOMAIN_RE.search(em))


def parse_euro_amount(
    raw: str,
    *,
    transaction: TransactionType | None = None,
) -> int | None:
    """Parse un montant € français (249 900 €, 1 200 €/mois). Ignore les €/m²."""
    if not raw:
        return None
    raw_str = str(raw).replace("\xa0", " ")
    if is_price_per_m2_snippet(raw_str):
        primary = _strip_combined_price_per_m2(raw_str)
        if primary and primary != raw_str:
            return parse_euro_amount(primary, transaction=transaction)
        return None
    from crawler.config import (
        PRICE_MAX_RENT_EUR,
        PRICE_MAX_SALE_EUR,
        PRICE_MIN_RENT_EUR,
        PRICE_MIN_SALE_EUR,
    )

    text = str(raw).replace("\xa0", " ").strip()
    if "€" in text:
        text = text.split("€")[0].strip()
    text = re.sub(r"[^\d\s.]", "", text)
    if not text:
        return None

    if re.search(r"\.\d{3}", raw) or (text.count(".") >= 2):
        digits = re.sub(r"[^\d]", "", text)
    elif re.search(r"(?:\s|\u00a0)\d{3}", raw):
        digits = re.sub(r"[\s\u00a0.]", "", text)
    else:
        digits = re.sub(r"[^\d]", "", text)

    if not digits or len(digits) > 9:
        return None
    val = int(digits)

    tx = transaction or _transaction_from_text(raw_str)
    if tx == "location":
        if val < PRICE_MIN_RENT_EUR or val > PRICE_MAX_RENT_EUR:
            return None
    elif val < PRICE_MIN_SALE_EUR or val > PRICE_MAX_SALE_EUR:
        return None
    if tx == "vente" and val < 5000 and not SALE_HINT_RE.search(raw_str):
        return None
    return val


def _transaction_from_text(text: str) -> TransactionType:
    t = (text or "").lower()
    if RENT_HINT_RE.search(t):
        return "location"
    if SALE_HINT_RE.search(t):
        return "vente"
    return "vente"


def _domain_key_from_url(page_url: str) -> str:
    host = urlparse(page_url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if "immobilier-france" in host:
        return "immobilier-france"
    for key in DOMAIN_PRICE_SELECTORS:
        if key in host:
            return key
    if "figaro" in host:
        return "lefigaro"
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2 and parts[-1] in ("fr", "com", "net", "org", "eu"):
        return parts[-2]
    return parts[0] if parts else ""


def _css_selector_bundle(selectors: str | list[str] | tuple[str, ...]) -> str:
    if isinstance(selectors, str):
        return selectors
    return ", ".join(str(s).strip() for s in selectors if str(s).strip())


def _iter_css_selector_parts(selector: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(selector, (list, tuple)):
        parts: list[str] = []
        for item in selector:
            parts.extend(_iter_css_selector_parts(str(item)))
        return parts
    raw = str(selector or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for part in raw.split(","):
        p = part.strip()
        if not p or re.fullmatch(r"\d+", p):
            continue
        if len(p) == 1 and not re.search(r"[#.\[:a-zA-Z]", p):
            continue
        out.append(p)
    return out


def detect_transaction_type(soup: BeautifulSoup, page_url: str = "") -> TransactionType:
    """Déduit vente ou location depuis URL, titre, JSON-LD, fil d'Ariane."""
    u = (page_url or "").lower()
    if re.search(r"location|/louer|/rent|/loyer|types?=2|projects?=1", u):
        return "location"
    if re.search(r"vente|/vendre|/achat|/buy|types?=1|projects?=2|ventes_", u):
        return "vente"

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        blob = json.dumps(data, ensure_ascii=False).lower()
        if "rent" in blob or "lease" in blob or "location" in blob:
            if "businessfunction" in blob and "rent" in blob:
                return "location"
        if '"pricecurrency"' in blob and ("month" in blob or "mois" in blob):
            return "location"

    main = get_main_content_root(soup, page_url)
    for sel in ("h1", ".item-title", "[data-qa-id*='title']", "title"):
        el = main.select_one(sel)
        if el:
            tx = _transaction_from_text(el.get_text(" ", strip=True))
            if RENT_HINT_RE.search(el.get_text(" ", strip=True)):
                return "location"
            if SALE_HINT_RE.search(el.get_text(" ", strip=True)):
                return "vente"

    crumbs = " ".join(
        a.get_text(" ", strip=True) for a in main.select("nav a, .breadcrumb a, [class*='breadcrumb'] a")[:8]
    )
    if RENT_HINT_RE.search(crumbs):
        return "location"
    if SALE_HINT_RE.search(crumbs):
        return "vente"

    page_text = _get_hero_text(main, 2500).lower()
    if re.search(r"\b(?:à|en)\s+location\b|\blouer\b", page_text):
        return "location"
    if re.search(r"\b(?:à|en)\s+vente\b|\bvendre\b", page_text):
        return "vente"

    return "vente"


def _extract_contact_zone_text(main) -> str:
    parts: list[str] = []
    selectors = (
        '[class*="contact"]', '[id*="contact"]', '[data-qa-id*="contact"]',
        '[data-testid*="contact"]', ".seller-info", ".annonceur", ".owner-info",
        ".ad-contact", '[class*="annonceur"]', '[class*="vendeur"]',
    )
    for sel in selectors:
        for el in main.select(sel):
            if is_in_excluded_zone(el):
                continue
            parts.append(el.get_text(" ", strip=True)[:600])
    for el in main.select('a[href^="mailto:"], a[href^="tel:"]'):
        if is_in_excluded_zone(el):
            continue
        parent = el.find_parent(["div", "section", "aside", "li"])
        if parent:
            parts.append(parent.get_text(" ", strip=True)[:400])
    return " ".join(parts)[:2500]


def _phone_contact_context(tel_el) -> str:
    """Texte autour du lien téléphone — libellés « Agence », « Professionnel », etc."""
    parts: list[str] = []
    for attr in ("aria-label", "title", "data-test", "data-testid", "data-qa-id", "alt"):
        val = tel_el.get(attr)
        if val:
            parts.append(str(val))

    node = tel_el
    for _ in range(8):
        if not node or node.name in ("body", "html"):
            break
        blob = _element_zone_attrs(node).lower()
        if node != tel_el and EXCLUDE_ZONE_RE.search(blob):
            break
        parts.append(node.get_text(" ", strip=True)[:600])
        for sib in (node.find_previous_sibling(), node.find_next_sibling()):
            if sib and hasattr(sib, "get_text"):
                parts.append(sib.get_text(" ", strip=True)[:250])
        label = node.find(["label", "span", "p", "div"], recursive=False)
        if label and label != node:
            parts.append(label.get_text(" ", strip=True)[:200])
        node = node.parent
    return " ".join(parts)[:1500]


def _extract_agency_name_near_phone(ctx: str) -> str | None:
    if not ctx:
        return None
    cleaned = PHONE_RE.sub(" ", ctx)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for pattern in (
        r"(?:agence|mandataire|professionnel(?:le)?)\s*[·:\-—|]\s*([^·\n|]{3,60})",
        r"\bagence\s+([A-ZÀ-ÖØ-Ý][\w''\-&\.]{1,35}(?:\s+[A-ZÀ-ÖØ-Ý0-9][\w''\-&\.]{1,25}){0,2})",
        r"([A-ZÀ-ÖØ-Ý][\w\s''\-&\.]{2,50})\s*[·|]\s*(?:orpi|century|foncia|iad|safti)\b",
    ):
        m = re.search(pattern, cleaned, re.I)
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group(1).strip())
        name = re.split(r"\b(?:t[eé]l|phone|appeler|contact|voir|agence)\b", name, flags=re.I)[0].strip()
        if len(name) >= 3 and not PHONE_PARTICULIER_CONTEXT_RE.search(name):
            if not re.match(r"^(t[eé]l|phone|appeler|contact|voir)$", name, re.I):
                return name[:120]
    return None


def _score_phone_publisher(main) -> tuple[int, int, str | None]:
    """Score agence/particulier depuis le bloc téléphone de la fiche."""
    score_agency = 0
    score_particulier = 0
    agency_name: str | None = None

    phone_nodes: list = []
    for sel in (
        'a[href^="tel:"]',
        "button[data-qa-id*='phone']",
        "[data-testid*='phone']",
        "[data-test*='phone']",
        "[class*='phone'][class*='contact']",
    ):
        for el in main.select(sel):
            if is_in_excluded_zone(el):
                continue
            if el not in phone_nodes:
                phone_nodes.append(el)

    for tel in phone_nodes:
        ctx = _phone_contact_context(tel)
        if not ctx:
            continue
        if PHONE_AGENCY_CONTEXT_RE.search(ctx):
            score_agency += 50
            name = _extract_agency_name_near_phone(ctx)
            if name and not agency_name:
                agency_name = name
        if PHONE_PARTICULIER_CONTEXT_RE.search(ctx):
            score_particulier += 40

    return score_agency, score_particulier, agency_name


def _agency_name_from_email(email: str) -> str | None:
    em = email.lower().strip()
    if PLATFORM_EMAIL_RE.search(em) or PERSONAL_EMAIL_RE.search(em):
        return None
    domain = em.split("@")[-1] if "@" in em else ""
    if not domain:
        return None
    label = domain.split(".")[0]
    for prefix in ("contact", "info", "agence", "commercial", "bureau"):
        if label.startswith(prefix) and len(label) > len(prefix) + 2:
            label = label[len(prefix):]
    if len(label) < 3:
        return None
    return label.replace("-", " ").replace("_", " ").title()[:120]


def _score_email_publisher(email: str | None) -> tuple[int, str | None]:
    """Score >0 = agence, <0 = particulier."""
    if not email or PLATFORM_EMAIL_RE.search(email):
        return 0, None
    if AGENCY_EMAIL_DOMAIN_RE.search(email):
        return 40, _agency_name_from_email(email)
    if PERSONAL_EMAIL_RE.search(email):
        return -35, None
    domain = email.lower().split("@")[-1]
    if any(k in domain for k in ("immobilier", "immo", "agence", "habitat", "logement")):
        return 28, _agency_name_from_email(email)
    return 0, None


def _classify_seller_json_ld(seller: dict, lead: LeadData) -> None:
    stype = seller.get("@type", "")
    if isinstance(stype, list):
        stype = " ".join(str(t) for t in stype)
    stype_l = str(stype).lower()
    name = seller.get("name") or seller.get("legalName")
    if any(t in stype_l for t in ORGANIZATION_JSON_TYPES):
        lead.type = "agence"
        if name and len(str(name).strip()) > 2:
            lead.agency = str(name).strip()[:120]
    elif "person" in stype_l and lead.type != "agence":
        lead.type = "particulier"


def detect_publisher_type(
    soup: BeautifulSoup,
    lead: LeadData,
    page_url: str = "",
) -> tuple[PublisherType, str | None]:
    main = get_main_content_root(soup, page_url)
    contact_text = _extract_contact_zone_text(main)
    main_head = _get_hero_text(main, 5000)
    score_agency = 0
    score_particulier = 0
    agency_name = lead.agency

    email_score, email_agency = _score_email_publisher(lead.email)
    score_agency += max(0, email_score)
    score_particulier += max(0, -email_score)
    if email_agency and not agency_name:
        agency_name = email_agency

    phone_agency, phone_particulier, phone_agency_name = _score_phone_publisher(main)
    score_agency += phone_agency
    score_particulier += phone_particulier
    if phone_agency_name and not agency_name:
        agency_name = phone_agency_name

    if AGENCY_PAGE_HINT_RE.search(contact_text):
        score_agency += 30
    elif AGENCY_PAGE_HINT_RE.search(main_head):
        score_agency += 12

    if PARTICULIER_PAGE_HINT_RE.search(contact_text):
        score_particulier += 35
    elif PARTICULIER_PAGE_HINT_RE.search(main_head):
        score_particulier += 15

    domain_key = _domain_key_from_url(page_url)
    for selector in DOMAIN_PHONE_CONTACT_SELECTORS.get(domain_key, []):
        el = _pick_listing_element(main, [selector])
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        if len(text) >= 3:
            if PHONE_AGENCY_CONTEXT_RE.search(text) or AGENCY_PAGE_HINT_RE.search(text):
                score_agency += 45
                if len(text) < 80 and not agency_name and not PHONE_PARTICULIER_CONTEXT_RE.search(text):
                    agency_name = text.strip()[:120]
            elif PHONE_PARTICULIER_CONTEXT_RE.search(text):
                score_particulier += 35
            break

    for selector in DOMAIN_AGENCY_SELECTORS.get(domain_key, []):
        el = _pick_listing_element(main, [selector])
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        if len(text) >= 3:
            score_agency += 35
            if len(text) < 80 and not agency_name:
                agency_name = text.strip()[:120]
            break

    for el in main.select(
        '[class*="professionnel"], [class*="professional"], [data-professional="true"]'
    ):
        if is_in_excluded_zone(el):
            continue
        score_agency += 25
        break

    if lead.type == "agence":
        score_agency += 20
    elif lead.type == "particulier":
        score_particulier += 10

    margin = abs(score_agency - score_particulier)

    if score_agency > score_particulier and score_agency >= 25:
        lead.raw_extras["publisher_audit"] = {
            "type": "agence",
            "score_agency": score_agency,
            "score_particulier": score_particulier,
            "agency": agency_name,
            "confidence": "high" if margin >= 25 else "medium",
        }
        return "agence", agency_name

    # Particulier = valeur par défaut. On distingue le « vrai » particulier
    # (signaux nets) du simple « aucun signal » pour éviter d'affirmer un type
    # à tort : confidence=low => à vérifier côté CRM.
    if score_particulier >= 25 and score_particulier > score_agency:
        confidence = "high" if margin >= 25 else "medium"
    else:
        confidence = "low"
    lead.raw_extras["publisher_audit"] = {
        "type": "particulier",
        "score_agency": score_agency,
        "score_particulier": score_particulier,
        "confidence": confidence,
    }
    return "particulier", None


def _normalize_iso_date(year: int, month: int, day: int) -> str | None:
    from datetime import date

    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    today = date.today()
    if d > today:
        return None
    if d.year < 2005:
        return None
    return d.isoformat()


def _parse_date_string(raw: str) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    if "T" in text:
        text = text.split("T")[0]
    if ISO_DATE_RE.match(text[:10]):
        return text[:10]

    rel = re.search(
        r"il y a\s+(\d+)\s+(jour|jours|semaine|semaines|mois|an|ans|heure|heures)",
        text,
        re.I,
    )
    if rel:
        from datetime import date, timedelta

        n = int(rel.group(1))
        unit = rel.group(2).lower()
        if unit.startswith("jour") or unit.startswith("heure"):
            delta = timedelta(days=max(1, n if unit.startswith("jour") else 0))
        elif unit.startswith("semaine"):
            delta = timedelta(days=n * 7)
        elif unit.startswith("mois"):
            delta = timedelta(days=n * 30)
        else:
            delta = timedelta(days=n * 365)
        return (date.today() - delta).isoformat()

    m = re.search(r"(\d{1,2})[\s/.-](\d{1,2})[\s/.-](\d{2,4})", text)
    if m:
        return _normalize_iso_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


def extract_listing_published_date(soup: BeautifulSoup, page_url: str = "") -> str | None:
    """Date de publication de l'annonce (ISO YYYY-MM-DD)."""
    main = get_main_content_root(soup, page_url)
    candidates: list[tuple[int, str]] = []

    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in (
            "article:published_time",
            "og:published_time",
            "datepublished",
            "date",
            "parsely-pub-date",
        ):
            parsed = _parse_date_string(meta.get("content") or "")
            if parsed:
                candidates.append((50, parsed))

    for el in main.select("time[datetime]"):
        if is_in_excluded_zone(el):
            continue
        parsed = _parse_date_string(el.get("datetime") or el.get_text(" ", strip=True))
        if parsed:
            candidates.append((45, parsed))

    domain_key = _domain_key_from_url(page_url)
    date_selectors = {
        "leboncoin": [
            "[data-qa-id='adview_publication_date']",
            "[data-qa-id*='adview_date']",
            "[data-qa-id*='publication']",
            "p[data-test-id='ad-date']",
        ],
        "pap": [".date-pub", ".item-date", "[class*='date-pub']"],
        "seloger": ["[data-testid*='publication']", "[data-testid*='date']"],
        "logic-immo": [".date-publication", ".publication-date"],
        "bienici": ["[data-test='publication-date']", ".adSummaryDate"],
        "paruvendu": [".date-publication", ".detail-date"],
    }
    for selector in date_selectors.get(domain_key, []):
        el = _pick_listing_element(main, [selector])
        if not el:
            continue
        parsed = _parse_date_string(el.get_text(" ", strip=True))
        if parsed:
            candidates.append((55, parsed))

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for key in ("datePosted", "datePublished", "uploadDate"):
            found = _find_json_date(blob, key)
            if found:
                candidates.append((60, found))

    snippet = _get_hero_text(main, 8000)
    for m in re.finditer(
        r"(?:publié|mise en ligne|en ligne depuis|depuis le)\s*(?:le\s*)?"
        r"(\d{1,2})[\s/.-](\d{1,2})[\s/.-](\d{2,4})",
        snippet,
        re.IGNORECASE,
    ):
        parsed = _normalize_iso_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if parsed:
            candidates.append((30, parsed))

    for m in re.finditer(
        r"(?:publié|en ligne)\s*(?:il y a\s*)?(\d+)\s*(jour|jours|semaine|semaines|mois)",
        snippet,
        re.IGNORECASE,
    ):
        parsed = _parse_date_string(m.group(0))
        if parsed:
            candidates.append((28, parsed))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def _find_json_date(data: Any, key: str) -> str | None:
    if isinstance(data, dict):
        if key in data:
            parsed = _parse_date_string(str(data[key]))
            if parsed:
                return parsed
        for v in data.values():
            found = _find_json_date(v, key)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_json_date(item, key)
            if found:
                return found
    return None


def apply_listing_published_to_lead(
    lead: LeadData,
    soup: BeautifulSoup,
    page_url: str = "",
) -> LeadData:
    published = extract_listing_published_date(soup, page_url)
    if published:
        lead.published_at = published
    return lead


def apply_listing_classification_to_lead(
    lead: LeadData,
    soup: BeautifulSoup,
    page_url: str = "",
) -> LeadData:
    """Vente / location + particulier / agence (email, texte, JSON-LD)."""
    tx = detect_transaction_type(soup, page_url)
    if lead.price_period == "month":
        tx = "location"
    lead.transaction_type = tx
    if tx == "location":
        lead.price_period = lead.price_period or "month"
    else:
        lead.price_period = None

    pub, agency = detect_publisher_type(soup, lead, page_url)
    lead.type = pub
    lead.agency = agency if pub == "agence" else None
    return lead


def _phone_digits_valid(phone: str | None) -> bool:
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("33") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return len(digits) == 10 and digits[0] == "0" and digits[1] != "0"


def _normalize_email_raw(raw: str) -> str:
    em = raw.strip().lower()
    if em.startswith("mailto:"):
        em = em[7:]
    return em.split("?")[0].strip()


def _email_candidate_ok(email: str) -> bool:
    em = _normalize_email_raw(email)
    if not EMAIL_RE.fullmatch(em):
        return False
    if PLATFORM_EMAIL_RE.search(em):
        return False
    return not em.endswith((".png", ".jpg", ".webp", ".gif"))


def _score_email_candidate(email: str) -> int:
    em = _normalize_email_raw(email)
    score = 30
    if AGENCY_EMAIL_DOMAIN_RE.search(em):
        score += 35
    elif PERSONAL_EMAIL_RE.search(em):
        score += 20
    domain = em.split("@")[-1] if "@" in em else ""
    if any(k in domain for k in ("immobilier", "immo", "agence", "habitat")):
        score += 25
    return score


def _collect_phones_deep(html: str, main, *, page_url: str) -> list[tuple[str, int]]:
    scored: dict[str, int] = {}

    def add(raw: str, score: int) -> None:
        parsed = normalize_phone(raw)
        if not _phone_digits_valid(parsed):
            return
        scored[parsed] = max(scored.get(parsed, 0), score)

    for m in re.finditer(
        r'"phone(?:Number)?"\s*:\s*"(?:\+33|0)(\d{9})"',
        html,
    ):
        add("0" + m.group(1), 38)
    for m in re.finditer(r'href=["\']tel:([^"\']+)', html, re.I):
        add(m.group(1), 32)

    selector_blob = ", ".join(DEEP_CONTACT_PHONE_SELECTORS)
    for tel in main.select(selector_blob):
        if is_in_excluded_zone(tel):
            continue
        href = (tel.get("href") or "").strip()
        raw = href.split(":", 1)[-1] if "tel:" in href.lower() else tel.get_text(" ", strip=True)
        ctx = _phone_contact_context(tel)
        score = 42
        if PHONE_AGENCY_CONTEXT_RE.search(ctx):
            score += 28
        if PHONE_PARTICULIER_CONTEXT_RE.search(ctx):
            score += 18
        if tel.name == "button" or "button" in " ".join(tel.get("class") or []):
            score += 12
        add(raw, score)

    domain_key = _domain_key_from_url(page_url)
    for sel in DOMAIN_PHONE_CONTACT_SELECTORS.get(domain_key, []):
        el = _pick_listing_element(main, [sel])
        if el and not is_in_excluded_zone(el):
            for sub in el.select('a[href^="tel:"]'):
                add(sub.get("href", "").replace("tel:", ""), 45)

    zone = _extract_contact_zone_text(main)
    for m in PHONE_RE.finditer(zone):
        add(m.group(), 28)
    for m in PHONE_RE.finditer(_get_hero_text(main, 14000)):
        add(m.group(), 18)

    return sorted(scored.items(), key=lambda x: x[1], reverse=True)


def _collect_emails_deep(html: str, main) -> list[tuple[str, int]]:
    scored: dict[str, int] = {}

    def add(raw: str, score: int) -> None:
        em = _normalize_email_raw(raw)
        if not _email_candidate_ok(em):
            return
        scored[em] = max(scored.get(em, 0), score + _score_email_candidate(em))

    for m in re.finditer(
        r'"email(?:Address)?"\s*:\s*"([^"]+@[^"]+)"',
        html,
        re.I,
    ):
        add(m.group(1), 36)
    for m in re.finditer(r'href=["\']mailto:([^"\']+)', html, re.I):
        add(m.group(1), 34)

    selector_blob = ", ".join(DEEP_CONTACT_EMAIL_SELECTORS)
    for mail in main.select(selector_blob):
        if is_in_excluded_zone(mail):
            continue
        href = (mail.get("href") or "").strip()
        raw = href.split(":", 1)[-1] if "mailto:" in href.lower() else mail.get_text(" ", strip=True)
        add(raw, 40)

    zone = _extract_contact_zone_text(main)
    for em in EMAIL_RE.findall(zone):
        add(em, 26)
    for em in EMAIL_RE.findall(_get_hero_text(main, 14000)):
        add(em, 16)

    return sorted(scored.items(), key=lambda x: x[1], reverse=True)


def _deep_json_ld_contacts(soup: BeautifulSoup, phones: dict[str, int], emails: dict[str, int]) -> None:
    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if obj.get("telephone"):
                p = normalize_phone(str(obj["telephone"]))
                if _phone_digits_valid(p):
                    phones[p] = max(phones.get(p, 0), 40)
            if obj.get("email"):
                em = str(obj["email"]).strip().lower()
                if _email_candidate_ok(em):
                    emails[em] = max(emails.get(em, 0), 38 + _score_email_candidate(em))
            for key in ("seller", "offeredBy", "author", "provider", "brand"):
                sub = obj.get(key)
                if isinstance(sub, dict):
                    st = str(sub.get("@type", "")).lower()
                    if any(t in st for t in ORGANIZATION_JSON_TYPES):
                        walk(sub)
                    elif "person" in st:
                        walk(sub)
                elif isinstance(sub, list):
                    for item in sub:
                        walk(item)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        walk(data)


def deep_enhance_listing_contacts(html: str, url: str, lead: LeadData) -> LeadData:
    """
    Extraction renforcée pour la mise à jour prospect : téléphone, email,
    type agence/particulier (page entière + JSON embarqué).
    """
    soup = BeautifulSoup(html, "lxml")
    main = get_main_content_root(soup, url)

    phone_scores: dict[str, int] = {}
    email_scores: dict[str, int] = {}
    _deep_json_ld_contacts(soup, phone_scores, email_scores)

    for phone, score in _collect_phones_deep(html, main, page_url=url):
        phone_scores[phone] = max(phone_scores.get(phone, 0), score)
    for email, score in _collect_emails_deep(html, main):
        email_scores[email] = max(email_scores.get(email, 0), score)

    if phone_scores:
        lead.phone = max(phone_scores.items(), key=lambda x: x[1])[0]
    if email_scores:
        lead.email = max(email_scores.items(), key=lambda x: x[1])[0]

    lead = extract_from_selectors(
        soup,
        {
            "phone": ", ".join(DEEP_CONTACT_PHONE_SELECTORS),
            "email": ", ".join(DEEP_CONTACT_EMAIL_SELECTORS),
            "name": (
                ".owner-name, .contact-name, .nom-annonceur, .adContactName, "
                ".seller-name, .annonceur-nom, [data-testid*='contact-name'], "
                "[data-qa-id*='seller'], [class*='annonceur'], [class*='vendeur']"
            ),
        },
        lead,
        overwrite=True,
        page_url=url,
    )
    extract_from_text(_extract_contact_zone_text(main), lead)
    if not lead.phone or not lead.email:
        extract_from_text(_get_hero_text(main, 14000), lead)

    extract_from_json_ld(soup, lead, page_url=url)
    apply_listing_classification_to_lead(lead, soup, url)
    lead.raw_extras["deep_contact_refresh"] = True
    return lead


def _price_context_text(el) -> str:
    parts: list[str] = []
    if el:
        parts.append(el.get_text(" ", strip=True))
        parent = el.parent
        for _ in range(4):
            if not parent or parent.name in ("body", "html"):
                break
            parts.append(parent.get_text(" ", strip=True)[:200])
            parent = parent.parent
    return " ".join(parts)


def _is_false_price_element(el) -> bool:
    own = el.get_text(" ", strip=True)
    if not own:
        return True
    if is_price_per_m2_snippet(own) and parse_euro_amount(own) is None:
        return True
    label = el.find_previous(["label", "span", "dt", "th"])
    small_ctx = own
    if label and label != el:
        small_ctx += " " + label.get_text(" ", strip=True)[:120]
    if FALSE_PRICE_LABEL_RE.search(small_ctx):
        return True
    classes = _element_zone_attrs(el).lower()
    if re.search(r"similar|similaire|related|suggest|carousel|widget|footer|header", classes):
        return True
    return is_in_excluded_zone(el)


def _dom_price_score(el, main) -> int:
    score = 0
    attrs = _element_zone_attrs(el).lower()
    if re.search(r"price|prix|amount|loyer", attrs):
        score += 40
    if el.get("itemprop") in ("price", "offers"):
        score += 50
    testid = el.get("data-testid") or el.get("data-qa-id") or ""
    if testid and re.search(r"price|prix|adview_price", str(testid), re.I):
        score += 60
    h1 = main.select_one("h1")
    if h1:
        for p in el.parents:
            if p == h1:
                score += 30
                break
    return score


def extract_listing_price(soup: BeautifulSoup, page_url: str = "") -> ListingPrice | None:
    """Prix affiché sur la fiche — pas les annonces similaires ni honoraires."""
    main = get_main_content_root(soup, page_url)
    hero_text = _get_hero_text(main, 4000)
    transaction = detect_transaction_type(soup, page_url)
    domain_key = _domain_key_from_url(page_url)

    for selector in DOMAIN_PRICE_SELECTORS.get(domain_key, []):
        el = main.select_one(selector)
        if not el or _is_false_price_element(el):
            continue
        ctx = _price_context_text(el)
        tx = _transaction_from_text(ctx) if RENT_HINT_RE.search(ctx) or SALE_HINT_RE.search(ctx) else transaction
        amount = parse_euro_amount(el.get("content") or el.get_text(" ", strip=True), transaction=tx)
        if amount:
            period = "month" if tx == "location" or RENT_HINT_RE.search(ctx) else None
            return ListingPrice(amount=amount, transaction=tx, period=period)

    candidates: list[tuple[int, int, int, TransactionType, str | None]] = []

    for el in main.select(
        '[itemprop="price"], [itemprop=price], meta[property="product:price:amount"], '
        '[data-testid*="price"], [data-qa-id*="price"], [data-test*="price"]'
    ):
        if _is_false_price_element(el):
            continue
        ctx = _price_context_text(el)
        tx = _transaction_from_text(ctx) if RENT_HINT_RE.search(ctx) or SALE_HINT_RE.search(ctx) else transaction
        amount = parse_euro_amount(el.get("content") or el.get_text(" ", strip=True), transaction=tx)
        if amount:
            period = "month" if tx == "location" or RENT_HINT_RE.search(ctx) else None
            order = list(main.descendants).index(el) if el in main.descendants else 9999
            candidates.append((order, _dom_price_score(el, main), amount, tx, period))

    for el in main.select(
        "[class*='price-main'], [class*='main-price'], [class*='detail-price'], "
        "[class*='summary-price'], [class*='property-price'], [class*='item-price'], "
        "[class*='adSummaryPrice'], [class*='Price__']"
    ):
        if _is_false_price_element(el):
            continue
        ctx = _price_context_text(el)
        tx = _transaction_from_text(ctx) if RENT_HINT_RE.search(ctx) or SALE_HINT_RE.search(ctx) else transaction
        amount = parse_euro_amount(el.get_text(" ", strip=True), transaction=tx)
        if amount:
            period = "month" if tx == "location" or RENT_HINT_RE.search(ctx) else None
            order = list(main.descendants).index(el) if el in main.descendants else 9999
            candidates.append((order, _dom_price_score(el, main) + 20, amount, tx, period))

    if not candidates:
        for m in PRICE_RE.finditer(hero_text):
            snippet = hero_text[max(0, m.start() - 40) : m.end() + 40]
            if FALSE_PRICE_LABEL_RE.search(snippet) or _price_match_is_per_m2(hero_text, m):
                continue
            tx = _transaction_from_text(snippet) if RENT_HINT_RE.search(snippet) else transaction
            amount = parse_euro_amount(m.group(0), transaction=tx)
            if amount:
                period = "month" if tx == "location" or RENT_HINT_RE.search(snippet) else None
                candidates.append((m.start(), 5, amount, tx, period))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (-x[1], x[0]))
    best_score = candidates[0][1]
    top = [c for c in candidates if c[1] >= best_score - 15]
    top.sort(key=lambda x: x[0])
    _, _, amount, tx, period = top[0]
    return ListingPrice(amount=amount, transaction=tx, period=period)


def apply_listing_price_to_lead(lead: LeadData, soup: BeautifulSoup, page_url: str = "") -> LeadData:
    info = extract_listing_price(soup, page_url)
    if info:
        lead.price = info.amount
        lead.transaction_type = info.transaction
        lead.price_period = info.period
    elif lead.price:
        lead.transaction_type = detect_transaction_type(soup, page_url)
        if not parse_euro_amount(str(lead.price), transaction=lead.transaction_type):
            lead.price = None
    return lead


def extract_primary_price(soup: BeautifulSoup, page_url: str = "") -> int | None:
    """Rétrocompat — retourne uniquement le montant."""
    info = extract_listing_price(soup, page_url)
    return info.amount if info else None


def _element_zone_attrs(el) -> str:
    parts: list[str] = []
    for attr in ("class", "id", "role", "data-testid", "aria-label"):
        val = el.get(attr)
        if isinstance(val, list):
            parts.extend(val)
        elif val:
            parts.append(str(val))
    return " ".join(parts)


def is_in_excluded_zone(el) -> bool:
    """Exclut sidebars, footers, blocs « annonces similaires »."""
    if el is None:
        return False
    for parent in el.parents:
        if parent.name in ("nav", "footer", "header", "aside", "noscript"):
            return True
        blob = _element_zone_attrs(parent)
        if EXCLUDE_ZONE_RE.search(blob):
            return True
    return False


def _trim_listing_noise(root):
    """Retire carrousels « annonces similaires » et blocs connexes."""
    clone = BeautifulSoup(str(root), "lxml")
    target = clone.body if clone.body else clone

    for bad in target.select(
        "nav, footer, header, aside, noscript, "
        '[class*="similar"], [class*="similaire"], [class*="related"], '
        '[class*="suggest"], [class*="carousel"], [id*="similar"], '
        '[class*="same-agency"], [class*="plus-de-biens"], [class*="other-listing"], '
        '[data-qa-id*="similar"], [data-testid*="similar"], [data-test*="similar"]'
    ):
        bad.decompose()

    for el in target.find_all(["h2", "h3", "h4", "h5"]):
        if not RELATED_SECTION_HEADING_RE.search(el.get_text(" ", strip=True)):
            continue
        container = el.find_parent(["section", "article", "aside", "div"])
        if container and container is not target:
            for sib in list(container.find_next_siblings()):
                if hasattr(sib, "decompose"):
                    sib.decompose()
            container.decompose()
        else:
            for sib in list(el.find_next_siblings()):
                if hasattr(sib, "decompose"):
                    sib.decompose()
            el.decompose()

    return target


def _get_hero_block(main):
    """Bloc DOM autour du h1 — avant les sections « similaires »."""
    h1 = main.select_one("h1")
    if not h1:
        return main
    block = h1
    for _ in range(6):
        parent = block.parent
        if not parent or parent == main or parent.name in ("body", "html"):
            break
        has_related = any(
            RELATED_SECTION_HEADING_RE.search(h.get_text(" ", strip=True))
            for h in parent.find_all(["h2", "h3", "h4", "h5"])
        )
        if has_related:
            break
        block = parent
    return block


def _get_hero_text(main, max_chars: int = 3500) -> str:
    return _get_hero_block(main).get_text(" ", strip=True)[:max_chars]


def _element_order(main, el) -> int:
    try:
        return list(main.descendants).index(el)
    except ValueError:
        return 99999


def get_main_content_root(soup: BeautifulSoup, page_url: str = ""):
    """Zone principale d'une seule fiche (hors suggestions en bas de page)."""
    domain_key = _domain_key_from_url(page_url)
    for sel in DOMAIN_LISTING_ROOT.get(domain_key, []):
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 80:
            return _trim_listing_noise(el)

    selectors = [
        "main",
        '[role="main"]',
        "#main-content",
        ".annonce-detail",
        ".detail-annonce",
        ".adview",
        ".ad-detail",
        ".property-detail",
        ".listing-detail",
        "article.listing",
        "article",
        ".content-detail",
        '[itemtype*="RealEstate"]',
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 80:
            return _trim_listing_noise(el)
    body = soup.body or soup
    clone = BeautifulSoup(str(body), "lxml")
    root = clone.body or clone
    return _trim_listing_noise(root)


def normalize_listing_url(url: str) -> str:
    """URL canonique d’annonce (sans # ni paramètres de tracking)."""
    if not url:
        return url
    p = urlparse(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme or "https", p.netloc.lower(), path, "", "", ""))


def _urls_match(page_url: str, candidate: str) -> bool:
    if not page_url or not candidate:
        return False
    return normalize_listing_url(page_url) == normalize_listing_url(
        candidate if candidate.startswith("http") else urljoin(page_url, candidate)
    )


def _is_listing_entity(ent: dict) -> bool:
    atype = str(ent.get("@type", "")).lower()
    if isinstance(ent.get("@type"), list):
        atype = " ".join(str(t).lower() for t in ent["@type"])
    return any(t in atype for t in LISTING_MAIN_TYPES)


def _parse_surface_value(raw: str) -> float | None:
    if not raw:
        return None
    m = SURFACE_RE.search(str(raw))
    if not m:
        return None
    val = float(m.group(1).replace(",", "."))
    if val < 5 or val > 50_000:
        return None
    return val


def _dom_listing_score(el, main) -> int:
    return _dom_price_score(el, main)


def _pick_listing_element(main, selectors: list[str]):
    best_el = None
    best_score = -1
    for selector in selectors:
        for el in main.select(selector):
            if is_in_excluded_zone(el):
                continue
            score = _dom_listing_score(el, main)
            if score > best_score:
                best_score = score
                best_el = el
    return best_el


def extract_listing_surface(soup: BeautifulSoup, page_url: str = "") -> float | None:
    main = get_main_content_root(soup, page_url)
    hero = _get_hero_block(main)
    hero_text = _get_hero_text(main, 5000)
    domain_key = _domain_key_from_url(page_url)
    candidates: list[tuple[int, int, float]] = []

    for selector in DOMAIN_SURFACE_SELECTORS.get(domain_key, []):
        el = _pick_listing_element(main, [selector])
        if not el:
            continue
        val = _parse_surface_value(el.get_text(" ", strip=True))
        if val:
            candidates.append(
                (_dom_listing_score(el, main) + 50, _element_order(main, el), val)
            )

    for el in main.select('[itemprop="floorSize"], [itemprop=floorSize]'):
        if is_in_excluded_zone(el):
            continue
        content = el.get("content") or el.get_text(" ", strip=True)
        val = _parse_surface_value(content)
        if val:
            candidates.append(
                (_dom_listing_score(el, main) + 60, _element_order(main, el), val)
            )

    h1 = main.select_one("h1")
    if h1:
        block = hero
        for el in block.select("[class*='surface'], [class*='caracteristique'], [class*='criteria']"):
            if is_in_excluded_zone(el):
                continue
            val = _parse_surface_value(el.get_text(" ", strip=True))
            if val:
                candidates.append(
                    (_dom_listing_score(el, main) + 35, _element_order(main, el), val)
                )

    if not candidates:
        for m in SURFACE_RE.finditer(hero_text):
            snippet = hero_text[max(0, m.start() - 30) : m.end() + 30]
            if FALSE_PRICE_LABEL_RE.search(snippet):
                continue
            val = _parse_surface_value(m.group(0))
            if val:
                candidates.append((5, m.start(), val))

    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2]


def extract_listing_address(soup: BeautifulSoup, page_url: str = "") -> str | None:
    main = get_main_content_root(soup, page_url)
    domain_key = _domain_key_from_url(page_url)
    candidates: list[tuple[int, str]] = []

    for selector in DOMAIN_ADDRESS_SELECTORS.get(domain_key, []):
        el = _pick_listing_element(main, [selector])
        if not el:
            continue
        text = el.get_text(" ", strip=True)
        if len(text) >= 6 and not is_hub_listing_address(text):
            candidates.append((_dom_listing_score(el, main) + 50, text))

    for el in main.select('[itemprop="address"], address'):
        if is_in_excluded_zone(el):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) >= 6 and not is_hub_listing_address(text):
            candidates.append((_dom_listing_score(el, main) + 55, text))

    h1 = main.select_one("h1")
    if h1 and not candidates:
        text = h1.get_text(" ", strip=True)
        if len(text) >= 6 and not is_hub_listing_address(text):
            candidates.append((_dom_listing_score(h1, main) + 10, text))

    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1][:300]


def apply_listing_facts_to_lead(lead: LeadData, soup: BeautifulSoup, page_url: str = "") -> LeadData:
    """Surface et adresse de la fiche principale (pas les annonces similaires)."""
    surface = extract_listing_surface(soup, page_url)
    if surface is not None:
        lead.surface = surface

    address = extract_listing_address(soup, page_url)
    if address and not is_hub_listing_address(address):
        lead.address = address

    return lead


def split_name(full: str) -> tuple[str | None, str | None]:
    full = re.sub(r"\s+", " ", full.strip())
    if not full:
        return None, None
    parts = full.split(" ", 1)
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], parts[1]


def extract_from_json_ld(soup: BeautifulSoup, lead: LeadData, page_url: str = "") -> LeadData:
    entities: list[dict] = []

    def _collect(data: Any) -> None:
        if isinstance(data, list):
            for item in data:
                _collect(item)
            return
        if not isinstance(data, dict):
            return
        atype = data.get("@type", "")
        if isinstance(atype, list):
            atype = " ".join(atype)
        atype_l = str(atype).lower()
        if "itemlist" in atype_l:
            for key in ("mainEntity", "itemListElement"):
                if key in data:
                    _collect(data[key])
            return
        if any(t in atype_l for t in LISTING_MAIN_TYPES) or "address" in data or "telephone" in data:
            entities.append(data)
        for key in ("@graph", "mainEntity"):
            if key in data:
                _collect(data[key])

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        _collect(data)

    if not entities:
        return lead

    def _score(ent: dict) -> int:
        s = 0
        if page_url and _urls_match(page_url, str(ent.get("url") or ent.get("@id") or "")):
            s += 100
        atype = str(ent.get("@type", "")).lower()
        if "realestate" in atype or "apartment" in atype or "house" in atype:
            s += 30
        if ent.get("telephone"):
            s += 10
        if ent.get("address"):
            s += 10
        return s

    if page_url:
        matched = [
            e
            for e in entities
            if _urls_match(page_url, str(e.get("url") or e.get("@id") or ""))
        ]
        if matched:
            _apply_json_ld(matched[0], lead, page_url)
            return lead
        listing_only = [e for e in entities if _is_listing_entity(e)]
        if len(listing_only) == 1:
            _apply_json_ld(listing_only[0], lead, page_url)
            return lead
        return lead

    entities.sort(key=_score, reverse=True)
    _apply_json_ld(entities[0], lead, page_url)
    return lead


def _apply_json_ld(data: Any, lead: LeadData, page_url: str = "") -> None:
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and "item" in data[0]:
            for wrapper in data:
                item = wrapper.get("item")
                if not isinstance(item, dict):
                    continue
                if page_url and not _urls_match(
                    page_url, str(item.get("url") or item.get("@id") or "")
                ):
                    continue
                _apply_json_ld(item, lead, page_url)
            return
        elif len(data) == 1:
            _apply_json_ld(data[0], lead, page_url)
        return
    if not isinstance(data, dict):
        return

    atype = data.get("@type", "")
    if isinstance(atype, list):
        atype = " ".join(atype)

    if "address" in data and not lead.address:
        addr = data["address"]
        if isinstance(addr, dict):
            parts = [
                addr.get("streetAddress"),
                addr.get("postalCode"),
                addr.get("addressLocality"),
            ]
            lead.address = ", ".join(p for p in parts if p)
        elif isinstance(addr, str):
            lead.address = addr

    if "floorSize" in data and lead.surface is None:
        fs = data["floorSize"]
        if isinstance(fs, dict) and "value" in fs:
            lead.surface = float(fs["value"])
        elif isinstance(fs, (int, float)):
            lead.surface = float(fs)

    if "telephone" in data and not lead.phone:
        lead.phone = normalize_phone(str(data["telephone"]))

    if "email" in data and not lead.email:
        lead.email = str(data["email"]).lower()

    if "price" in data and lead.price is None:
        price = data["price"]
        unit = str(data.get("priceCurrency") or data.get("unitText") or "")
        if isinstance(price, (int, float)):
            parsed = int(price)
            tx = "location" if "month" in unit.lower() or "mois" in unit.lower() else "vente"
            if parse_euro_amount(str(parsed), transaction=tx) == parsed:
                lead.price = parsed
                lead.transaction_type = tx
                if tx == "location":
                    lead.price_period = "month"
        elif isinstance(price, str):
            tx = _transaction_from_text(price + " " + unit)
            lead.price = parse_euro_amount(price, transaction=tx)
            lead.transaction_type = tx
        elif isinstance(price, dict) and "value" in price:
            tx = _transaction_from_text(json.dumps(price))
            lead.price = parse_euro_amount(str(price["value"]), transaction=tx)
            lead.transaction_type = tx

    if "name" in data:
        nm = str(data["name"]).strip()
        if _is_listing_entity(data):
            if nm and not lead.raw_extras.get("listing_title"):
                lead.raw_extras["listing_title"] = nm[:300]
        elif not lead.first_name and nm and not is_listing_title_name(nm):
            fn, ln = split_name(nm)
            if not is_listing_title_name(fn, ln):
                lead.first_name = fn
                lead.last_name = ln

    seller = data.get("seller") or data.get("offeredBy") or data.get("author")
    if seller and isinstance(seller, dict):
        _classify_seller_json_ld(seller, lead)
        _apply_json_ld(seller, lead, page_url)
    elif seller and isinstance(seller, str):
        if not is_listing_title_name(seller):
            fn, ln = split_name(seller)
            if not is_listing_title_name(fn, ln):
                lead.first_name = lead.first_name or fn
                lead.last_name = lead.last_name or ln


def extract_from_meta(soup: BeautifulSoup, lead: LeadData, page_url: str = "") -> LeadData:
    meta_map = {
        "og:street-address": "address",
        "og:locality": "_locality",
        "description": "_desc",
    }
    collected: dict[str, str] = {}
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name") or ""
        content = meta.get("content") or ""
        if not content:
            continue
        key = meta_map.get(prop.lower())
        if key:
            collected[key] = content

    if not lead.address and collected.get("address"):
        locality = collected.get("_locality", "")
        lead.address = f"{collected['address']}, {locality}".strip(", ")

    main = get_main_content_root(soup, page_url)
    return extract_from_text(_get_hero_text(main), lead)


def extract_from_text(text: str, lead: LeadData) -> LeadData:
    if not lead.phone:
        m = PHONE_RE.search(text)
        if m:
            lead.phone = normalize_phone(m.group())

    if not lead.email:
        for em in EMAIL_RE.findall(text):
            if em.endswith((".png", ".jpg", ".webp")):
                continue
            if PLATFORM_EMAIL_RE.search(em):
                continue
            lead.email = em.lower()
            break

    if lead.surface is None:
        sm = SURFACE_RE.search(text)
        if sm:
            val = _parse_surface_value(sm.group(0))
            if val is not None:
                lead.surface = val

    if not lead.first_name:
        m = NAME_RE.search(text)
        if m:
            fn, ln = split_name(m.group(1))
            lead.first_name = fn
            lead.last_name = ln

    return lead


def extract_from_selectors(
    soup: BeautifulSoup,
    selectors: dict[str, str | list[str] | tuple[str, ...]],
    lead: LeadData,
    *,
    overwrite: bool = False,
    page_url: str = "",
) -> LeadData:
    main = get_main_content_root(soup, page_url)
    for field_name, selector in selectors.items():
        el = None
        for part in _iter_css_selector_parts(selector):
            try:
                el = _pick_listing_element(main, [part])
            except Exception:
                continue
            if el:
                break
        if not el:
            continue
        value = el.get("content") or el.get("href") or el.get_text(strip=True)
        if not value:
            continue
        if field_name == "phone":
            parsed = normalize_phone(value)
            if parsed and (overwrite or not lead.phone):
                lead.phone = parsed
        elif field_name == "email":
            em = _normalize_email_raw(value)
            if _email_candidate_ok(em) and (overwrite or not lead.email):
                lead.email = em
        elif field_name == "address":
            value = value.strip()[:300]
            if is_hub_listing_address(value):
                continue
            if overwrite or not lead.address:
                lead.address = value
        elif field_name == "surface":
            val = _parse_surface_value(value)
            if val is not None and (overwrite or lead.surface is None):
                lead.surface = val
        elif field_name == "name":
            fn, ln = split_name(value)
            if not is_listing_title_name(fn, ln):
                lead.first_name = lead.first_name or fn
                lead.last_name = lead.last_name or ln
        elif field_name == "price":
            if _is_false_price_element(el):
                continue
            ctx = _price_context_text(el)
            tx = _transaction_from_text(ctx)
            parsed = parse_euro_amount(value, transaction=tx)
            if parsed:
                lead.price = lead.price or parsed
                lead.transaction_type = tx
                if tx == "location" or RENT_HINT_RE.search(ctx):
                    lead.price_period = "month"
    return lead


def extract_embedded_phones_from_html(html: str, lead: LeadData) -> LeadData:
    """Téléphones dans JSON embarqué ou liens tel: (tous sites)."""
    if lead.phone:
        return lead
    for m in re.finditer(
        r'"phone(?:Number)?"\s*:\s*"(?:\+33|0)(\d{9})"',
        html,
    ):
        lead.phone = normalize_phone("0" + m.group(1))
        return lead
    for m in re.finditer(r'href="tel:([^"]+)"', html, re.I):
        lead.phone = normalize_phone(m.group(1))
        if lead.phone:
            return lead
    return lead


def enrich_core_listing_fields(html: str, url: str, lead: LeadData) -> LeadData:
    """
    Complète les 4 champs minimum (adresse, tél., email, m²) — tous portails / sites custom.
    Appelé systématiquement après generic_extract + enhance_listing.
    """
    soup = BeautifulSoup(html, "lxml")
    main = get_main_content_root(soup, url)
    domain = _domain_key_from_url(url)

    addr_sels = DOMAIN_ADDRESS_SELECTORS.get(domain) or []
    surf_sels = DOMAIN_SURFACE_SELECTORS.get(domain) or []
    lead = extract_from_selectors(
        soup,
        {
            "address": _css_selector_bundle(addr_sels or GENERIC_ADDRESS_SELECTORS),
            "surface": _css_selector_bundle(surf_sels or GENERIC_SURFACE_SELECTORS),
            "phone": _css_selector_bundle(BROAD_PHONE_SELECTORS),
            "email": _css_selector_bundle(BROAD_EMAIL_SELECTORS),
        },
        lead,
        overwrite=False,
        page_url=url,
    )

    lead = extract_embedded_phones_from_html(html, lead)

    extract_from_json_ld(soup, lead, page_url=url)
    extract_from_meta(soup, lead, page_url=url)
    apply_listing_facts_to_lead(lead, soup, url)

    page_text = _get_hero_text(main, 22000)
    extract_from_text(page_text, lead)

    if not lead.phone:
        for tel_el in main.select('a[href^="tel:"]'):
            if is_in_excluded_zone(tel_el):
                continue
            parsed = normalize_phone(tel_el.get("href", "").replace("tel:", ""))
            if parsed:
                lead.phone = parsed
                break

    if not lead.email:
        for mail_el in main.select('a[href^="mailto:"]'):
            if is_in_excluded_zone(mail_el):
                continue
            em = _normalize_email_raw(
                mail_el.get("href", "").replace("mailto:", "").split("?")[0]
            )
            if _email_candidate_ok(em):
                lead.email = em
                break

    if not lead.address:
        addr_el = main.find("address") or main.select_one("[itemprop=address]")
        if addr_el and not is_in_excluded_zone(addr_el):
            addr_text = addr_el.get_text(" ", strip=True)
            if not is_hub_listing_address(addr_text):
                lead.address = addr_text

    return lead


def generic_extract(html: str, url: str, source: str = "generic") -> LeadData:
    soup = BeautifulSoup(html, "lxml")
    lead = LeadData(source=source, source_url=url)
    main = get_main_content_root(soup, url)
    hero = _get_hero_block(main)

    extract_from_json_ld(soup, lead, page_url=url)
    extract_from_meta(soup, lead, page_url=url)

    apply_listing_price_to_lead(lead, soup, url)
    apply_listing_facts_to_lead(lead, soup, url)

    addr_el = hero.find("address") or hero.select_one("[itemprop=address]")
    if addr_el and not lead.address and not is_in_excluded_zone(addr_el):
        addr_text = addr_el.get_text(" ", strip=True)
        if not is_hub_listing_address(addr_text):
            lead.address = addr_text

    for tel_el in hero.select('a[href^="tel:"]'):
        if is_in_excluded_zone(tel_el):
            continue
        lead.phone = normalize_phone(tel_el.get("href", "").replace("tel:", ""))
        break

    for mail_el in hero.select('a[href^="mailto:"]'):
        if is_in_excluded_zone(mail_el):
            continue
        lead.email = mail_el.get("href", "").replace("mailto:", "").split("?")[0].lower()
        break

    extract_from_text(_get_hero_text(main), lead)

    apply_listing_published_to_lead(lead, soup, url)
    apply_listing_classification_to_lead(lead, soup, url)

    return enrich_core_listing_fields(html, url, lead)


EXCLUDE_LISTING_URL_RE = re.compile(
    r"boutique/|/edito/|/partenaire/|/services/|/emploi|devis-|postulez_|"
    r"/c/ventes_|/c/locations|/recherche\b|/login|/register|/cookie|"
    r"\.pdf$|/blog/|/aide/|/presse/|diagandgo|devis-energie|"
    r"immobilier-(vente|location)-(?:appartement|maison|bien)-france|"
    r"/annonces/immobilier-(vente|location)-(?:appartement|maison)-[a-z0-9+.+-]+:\d|"
    r"/annonces/immobilier-(vente|location)-(?:appartement|maison|bien|studio)-[a-z0-9+.+-]+\.html",
    re.IGNORECASE,
)


def is_excluded_listing_url(url: str) -> bool:
    if not url:
        return True
    return bool(EXCLUDE_LISTING_URL_RE.search(url))


def find_listing_links(
    html: str,
    base_url: str,
    patterns: list[str],
    limit: int = 150,
) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if not parsed.scheme.startswith("http"):
            continue
        if is_excluded_listing_url(full):
            continue
        for pattern in patterns:
            if re.search(pattern, full, re.IGNORECASE):
                links.add(full.split("#")[0])
                break
    return list(links)[:limit]


def find_pagination_links(html: str, base_url: str, current_url: str) -> list[str]:
    """Liens vers pages suivantes de résultats (page 2, 3…)."""
    soup = BeautifulSoup(html, "lxml")
    pages: list[str] = []
    seen: set[str] = {current_url.split("#")[0]}

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = (a.get_text() or "").strip().lower()
        rel = " ".join(a.get("rel") or []).lower()
        full = urljoin(base_url, href).split("#")[0]
        if full in seen:
            continue
        is_next = (
            "next" in rel
            or text in ("suivant", "suivante", ">", "›", "»")
            or re.search(r"[?&]page=\d+", full, re.I)
            or re.search(r"/page[/-]?\d+", full, re.I)
        )
        if is_next and full.startswith("http"):
            seen.add(full)
            pages.append(full)

    from crawler.config import MAX_SEARCH_PAGES

    return pages[: max(1, MAX_SEARCH_PAGES - 1)]


RELATED_SECTION_RE = re.compile(
    r"similar|similaire|suggest|recommend|related|aussi|voisin|proche|"
    r"compar|carousel|propos|selection|selectionn|decouvr|voir.aussi|"
    r"plus.de.biens|autres.annonces|annonces.liees|meme.secteur|a.proximit",
    re.IGNORECASE,
)


def find_related_listing_links(
    html: str,
    page_url: str,
    patterns: list[str],
    limit: int = 500,
) -> list[str]:
    """Liens des annonces suggérées / similaires en bas de fiche."""
    soup = BeautifulSoup(html, "lxml")
    current = page_url.split("#")[0].rstrip("/")
    found: list[str] = []
    seen: set[str] = {current}

    def _add_link(href: str) -> None:
        full = urljoin(page_url, href).split("#")[0].rstrip("/")
        if full in seen or not full.startswith("http"):
            return
        if current in full and full != current and full.endswith(current):
            return
        for pattern in patterns:
            if re.search(pattern, full, re.IGNORECASE):
                seen.add(full)
                found.append(full)
                return

    for heading in soup.find_all(["h2", "h3", "h4", "h5"]):
        title = heading.get_text(" ", strip=True)
        if not title or not RELATED_SECTION_RE.search(title):
            continue
        container = heading.find_parent(["section", "div", "aside"]) or heading.parent
        if not container:
            continue
        for a in container.find_all("a", href=True):
            _add_link(a["href"])
            if len(found) >= limit:
                return found[:limit]

    for block in soup.find_all(True):
        blob = _element_zone_attrs(block)
        if not RELATED_SECTION_RE.search(blob):
            continue
        if block.name not in ("div", "section", "aside", "ul", "ol"):
            continue
        for a in block.find_all("a", href=True):
            _add_link(a["href"])
            if len(found) >= limit:
                return found[:limit]

    return found[:limit]
