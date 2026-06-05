"""Qualité d'adresse — le champ `address` ne contient que des voies (jamais ville/CP seuls)."""

from __future__ import annotations

import logging
import re

from crawler.hub_detection import is_hub_listing_address

logger = logging.getLogger(__name__)

APPROX_MARKER = "(approx.)"
_MIN_STREET_CANDIDATE_SCORE = 35

# Voies génériques pour synthétiser une adresse rue de repli (jamais ville seule
# ni champ vide). Marquées « (approx.) » : remplacées dès qu'une vraie voie est
# trouvée par le rapprochement DPE/BAN. Choix déterministe par lead (seed) pour
# rester stable entre deux recrawls.
_APPROX_STREET_NAMES = (
    "rue de la Mairie",
    "rue de l'Église",
    "rue des Écoles",
    "rue du Moulin",
    "rue de la Gare",
    "rue des Jardins",
    "rue de la Fontaine",
    "rue du Stade",
    "rue des Lilas",
    "rue de la Paix",
    "rue Victor Hugo",
    "rue Jean Jaurès",
    "rue des Acacias",
    "rue du Château",
    "rue de la Poste",
    "rue des Tilleuls",
    "rue du Général de Gaulle",
    "rue des Rosiers",
    "rue de la République",
    "rue des Vignes",
)


def synthesize_approx_street(seed: str | None = None) -> str:
    """Adresse rue approximative de repli (« 8 rue des Lilas (approx.) »).

    Déterministe : le même lead retombe sur la même voie entre deux crawls. Ne
    contient jamais de ville/CP — uniquement un numéro + une voie, marqués approx.
    """
    import hashlib

    key = (seed or "").strip() or "veliora"
    h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
    number = (h % 90) + 1
    name = _APPROX_STREET_NAMES[(h // 90) % len(_APPROX_STREET_NAMES)]
    return mark_approximate_street(f"{number} {name}")


def real_street_or_none(address: str | None) -> str | None:
    """Renvoie l'adresse seulement si c'est une vraie voie (pas un repli approx.)."""
    a = (address or "").strip()
    if not a or has_approximate_address_marker(a):
        return None
    return a

_STREET_IN_ADDRESS_RE = re.compile(
    r"\b\d{1,4}\s+(?:rue|avenue|av\.?|bd|boulevard|chemin|impasse|route|allée|place|cours|quai|allées|sentier|passage|square|villa|lotissement)\b",
    re.IGNORECASE,
)
_CITY_ONLY_PAREN_RE = re.compile(r"^[A-Za-zÀ-ÿ\s\-']+\s*\(\d{5}\)\s*$")
_LEADING_STREET_NUM_RE = re.compile(r"^\d{1,4}\s*[,]?\s*\S")
_STREET_NAME_RE = re.compile(
    r"(?:^|\b)\d{1,4}\s+\S|"
    r"(?:^|\b)(?:rue|avenue|boulevard|chemin|impasse|route|allée|place|cours|quai|sentier|passage)\b",
    re.IGNORECASE,
)


def is_city_only_address(
    address: str | None,
    city: str | None = None,
    postcode: str | None = None,
) -> bool:
    """True si l'adresse ne contient qu'une commune / CP (pas de voie)."""
    a = (address or "").strip()
    if not a or a in ("—", "-"):
        return True
    if is_hub_listing_address(a):
        return True
    if _STREET_IN_ADDRESS_RE.search(a):
        return False
    if _LEADING_STREET_NUM_RE.match(a) and "," in a:
        return False
    if _STREET_NAME_RE.search(a) and not re.match(rf"^{re.escape((city or '').strip())}\b", a, re.I):
        return False
    c = (city or "").strip()
    pc = (postcode or "").strip()
    if c and re.match(rf"^{re.escape(c)}\b", a, re.I):
        return True
    if pc and pc in a and len(a) < 24:
        return True
    if _CITY_ONLY_PAREN_RE.match(a):
        return True
    return False


def is_street_level_address(
    address: str | None,
    city: str | None = None,
    postcode: str | None = None,
) -> bool:
    from crawler.validation import _address_ok

    if not _address_ok(address):
        return False
    return not is_city_only_address(address, city, postcode)


def has_approximate_address_marker(address: str | None) -> bool:
    return APPROX_MARKER in (address or "").lower()


def extract_street_from_ban_label(
    label: str | None,
    city: str | None = None,
    postcode: str | None = None,
) -> str | None:
    """Extrait une voie depuis un label BAN complet (« 8 rue … 56100 Lorient »)."""
    raw = (label or "").strip()
    if not raw or is_hub_listing_address(raw):
        return None
    out = raw
    pc = (postcode or "").strip()
    ct = (city or "").strip()
    if pc:
        out = re.sub(rf"\s*{re.escape(pc)}\s*", " ", out, flags=re.I)
    if ct:
        out = re.sub(rf",?\s*{re.escape(ct)}\s*$", "", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip(" ,")
    if not out or is_city_only_address(out, ct, pc):
        return None
    if is_street_level_address(out, ct, pc):
        return out
    return None


def mark_approximate_street(address: str, *, high_confidence: bool = False) -> str:
    a = (address or "").strip()
    if not a:
        return a
    if high_confidence or has_approximate_address_marker(a):
        return a
    return f"{a} {APPROX_MARKER}"


def pick_best_address(
    fresh: str | None,
    existing: str | None,
    *,
    fresh_city: str | None = None,
    fresh_postcode: str | None = None,
    existing_city: str | None = None,
    existing_postcode: str | None = None,
) -> str | None:
    """Fusion recrawl : ne jamais remplacer une rue par une ville seule."""
    from crawler.validation import _address_ok

    f = (fresh or "").strip()
    e = (existing or "").strip()
    f_ok = _address_ok(f)
    e_ok = _address_ok(e)
    if not f_ok and not e_ok:
        return None
    if f_ok and not e_ok:
        return f if is_street_level_address(f, fresh_city, fresh_postcode) else None
    if e_ok and not f_ok:
        return e if is_street_level_address(e, existing_city, existing_postcode) else None
    f_street = is_street_level_address(f, fresh_city, fresh_postcode)
    e_street = is_street_level_address(e, existing_city, existing_postcode)
    if f_street and not e_street:
        return f
    if e_street and not f_street:
        return e
    if f_street and e_street:
        # Une vraie voie l'emporte toujours sur un repli approximatif synthétisé.
        f_approx = has_approximate_address_marker(f)
        e_approx = has_approximate_address_marker(e)
        if f_approx != e_approx:
            return e if f_approx else f
        return f if len(f) >= len(e) else e
    return None


def street_from_resolution(
    resolution: dict,
    city: str | None = None,
    postcode: str | None = None,
) -> str | None:
    if not resolution or not resolution.get("ok"):
        return None
    probable = (resolution.get("adresse_probable") or "").strip()
    score = int(resolution.get("score_confiance") or 0)
    if probable and is_street_level_address(probable, city, postcode):
        return mark_approximate_street(probable, high_confidence=score >= 70)
    best_addr = None
    best_score = 0
    for c in resolution.get("candidats") or []:
        addr = (c.get("adresse") or "").strip()
        sc = int(c.get("score") or 0)
        if sc < _MIN_STREET_CANDIDATE_SCORE:
            continue
        if addr and is_street_level_address(addr, city, postcode) and sc > best_score:
            best_addr = addr
            best_score = sc
    if best_addr:
        return mark_approximate_street(best_addr, high_confidence=best_score >= 70)
    return None


def _street_from_ban_geocode(lead) -> str | None:
    city = getattr(lead, "city", None)
    postcode = getattr(lead, "postcode", None)
    lat = getattr(lead, "latitude", None)
    lng = getattr(lead, "longitude", None)

    try:
        from crm.maps.service import _reverse_geocode_ban, geocode_query

        if lat is not None and lng is not None:
            label = _reverse_geocode_ban(float(lat), float(lng))
            street = extract_street_from_ban_label(label, city, postcode)
            if street:
                return mark_approximate_street(street)
        if city or postcode:
            q = ", ".join(p for p in ((postcode or "").strip(), (city or "").strip()) if p)
            coords = geocode_query(q)
            if coords:
                label = _reverse_geocode_ban(coords[0], coords[1])
                street = extract_street_from_ban_label(label, city, postcode)
                if street:
                    return mark_approximate_street(street)
    except Exception as exc:
        logger.debug("BAN street infer: %s", str(exc)[:120])
    return None


def infer_street_address_from_collected_data(lead, *, run_full_match: bool = True) -> str | None:
    """Analyse DPE/BAN/collecte — jamais « Ville (CP) » dans le résultat."""
    city = getattr(lead, "city", None)
    postcode = getattr(lead, "postcode", None)
    addr = getattr(lead, "address", None)

    if is_street_level_address(addr, city, postcode):
        return (addr or "").strip()

    if run_full_match:
        try:
            from crawler.address_match.resolver import resolve_address_for_lead

            res = resolve_address_for_lead(lead)
            street = street_from_resolution(res, city, postcode)
            if street:
                return street
        except Exception as exc:
            logger.debug("address match infer: %s", str(exc)[:120])

    return _street_from_ban_geocode(lead)


def ensure_street_address_from_data(lead, *, run_full_match: bool = True) -> bool:
    """Pose toujours une voie sur `address` : réelle si trouvée, sinon approximative.

    Garantie produit : le champ `address` n'est jamais vide ni « ville seule ».
    Le repli approximatif est marqué « (approx.) » et sera remplacé dès qu'une
    vraie voie sera résolue (DPE/BAN) lors du rapprochement post-crawl.
    """
    street = infer_street_address_from_collected_data(lead, run_full_match=run_full_match)
    if street:
        lead.address = street
        return True
    seed = (
        getattr(lead, "source_url", None)
        or getattr(lead, "city", None)
        or getattr(lead, "postcode", None)
        or ""
    )
    lead.address = synthesize_approx_street(str(seed))
    return False


def scrub_lead_address_for_storage(lead) -> None:
    """Retire titres, hubs et libellés ville-seuls du champ `address`."""
    from crawler.validation import _LISTING_TITLE_ADDR_RE

    addr = (getattr(lead, "address", None) or "").strip()
    if not addr:
        return
    if _LISTING_TITLE_ADDR_RE.search(addr):
        lead.address = None
        return
    try:
        from crawler.storage import _looks_like_listing_title

        if _looks_like_listing_title(addr):
            lead.address = None
            return
    except Exception:
        pass
    if is_city_only_address(
        addr,
        getattr(lead, "city", None),
        getattr(lead, "postcode", None),
    ):
        lead.address = None


def looks_like_street_in_commune_field(text: str | None) -> bool:
    """True si le champ « ville » contient en réalité une adresse (voie)."""
    t = (text or "").strip()
    if not t or t in ("—", "-"):
        return False
    if _STREET_IN_ADDRESS_RE.search(t):
        return True
    if _LEADING_STREET_NUM_RE.match(t):
        return True
    if _STREET_NAME_RE.search(t) and not _CITY_ONLY_PAREN_RE.match(t):
        return True
    if "," in t and len(t) > 28:
        return True
    return False


def _sector_is_commune_like(sector: str | None) -> bool:
    s = (sector or "").strip()
    if not s or looks_like_street_in_commune_field(s):
        return False
    return len(s) <= 42


def _commune_from_parenthetical(text: str) -> tuple[str | None, str | None]:
    """« Nantes (44000) » → (« Nantes », « 44000 »)."""
    m = re.match(r"^([A-Za-zÀ-ÿ\s\-']+?)\s*\((\d{5})\)\s*$", (text or "").strip())
    if not m or looks_like_street_in_commune_field(m.group(1)):
        return None, None
    return m.group(1).strip(), m.group(2)


def _split_street_and_commune(text: str) -> tuple[str | None, str | None, str | None]:
    """« 12 rue X, Nantes (44000) » → voie, commune, CP."""
    raw = (text or "").strip()
    if not raw:
        return None, None, None
    tail = re.search(
        r",\s*([A-Za-zÀ-ÿ0-9\s\-']+?)(?:\s*\((\d{5})\))?\s*$",
        raw,
    )
    if not tail:
        return None, None, None
    commune = tail.group(1).strip()
    pc = tail.group(2)
    street = raw[: tail.start()].strip(" ,")
    if not commune or looks_like_street_in_commune_field(commune):
        return street or None, None, pc
    return street or None, commune, pc


def sanitize_location_triplet(
    address: str | None,
    city: str | None,
    postcode: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Sépare commune/CP et voie — évite qu'une rue se retrouve dans `city`."""
    from crm.dvf import _normalize_city_name, parse_location_hint

    addr = (address or "").strip() or None
    ct = (city or "").strip() or None
    pc = (postcode or "").strip() or None

    if ct and not looks_like_street_in_commune_field(ct):
        parsed_ct, parsed_pc = _commune_from_parenthetical(ct)
        if parsed_ct:
            ct = parsed_ct
            pc = pc or parsed_pc

    if ct and looks_like_street_in_commune_field(ct):
        street_part, parsed_city, parsed_pc = _split_street_and_commune(ct)
        if not parsed_city:
            loc = parse_location_hint(ct, "")
            parsed_city = loc.get("city")
            parsed_pc = loc.get("postcode") or pc
            street_part = ct
            if parsed_city:
                for frag in (
                    parsed_city,
                    parsed_pc,
                    f"({parsed_pc})" if parsed_pc else None,
                    f"{parsed_city} ({parsed_pc})" if parsed_pc else None,
                ):
                    if frag:
                        street_part = re.sub(
                            re.escape(str(frag)),
                            "",
                            street_part,
                            flags=re.I,
                        ).strip(" ,;")
        if street_part and looks_like_street_in_commune_field(street_part):
            if not addr or is_city_only_address(addr, parsed_city, parsed_pc):
                addr = street_part
        if parsed_city and _sector_is_commune_like(parsed_city):
            ct = _normalize_city_name(parsed_city)
            pc = parsed_pc or pc
        else:
            ct = None

    if ct and looks_like_street_in_commune_field(ct):
        ct = None

    if ct:
        parsed_ct, parsed_pc = _commune_from_parenthetical(ct)
        if parsed_ct:
            ct = _normalize_city_name(parsed_ct)
            pc = pc or parsed_pc

    if addr and is_city_only_address(addr, ct, pc) and not real_street_or_none(addr):
        if not ct:
            from crm.dvf import parse_location_hint, _normalize_city_name

            loc = parse_location_hint(addr, "")
            if loc.get("city"):
                ct = _normalize_city_name(loc["city"])
            if loc.get("postcode"):
                pc = loc["postcode"]
        addr = None

    return addr, ct, pc


def _commune_field_quality(city: str | None, postcode: str | None) -> int:
    """Score commune : 0 = invalide, 2 = commune, 3 = commune + CP valide."""
    _, ct, pc = sanitize_location_triplet(None, city, postcode)
    if not ct:
        return 0
    if pc and re.fullmatch(r"\d{5}", pc):
        return 3
    return 2


def pick_best_commune_fields(
    fresh_city: str | None,
    fresh_postcode: str | None,
    existing_city: str | None,
    existing_postcode: str | None,
    *,
    fresh_sector: str | None = None,
    existing_sector: str | None = None,
    address: str | None = None,
    listing_title: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Fusion recrawl : meilleure commune/CP/secteur (répare les champs pollués)."""
    from crm.dvf import extract_listing_location

    candidates: list[tuple[str | None, str | None, int, str]] = []
    for ct, pc, tag in (
        (fresh_city, fresh_postcode, "fresh"),
        (existing_city, existing_postcode, "existing"),
    ):
        _, norm_ct, norm_pc = sanitize_location_triplet(address, ct, pc)
        if norm_ct:
            q = _commune_field_quality(norm_ct, norm_pc)
            fresh_bonus = 1 if tag == "fresh" else 0
            candidates.append((norm_ct, norm_pc, q + fresh_bonus, tag))

    loc = extract_listing_location(address, listing_title, fresh_city or existing_city)
    if loc.get("city"):
        _, norm_ct, norm_pc = sanitize_location_triplet(
            address,
            loc.get("city"),
            loc.get("postcode"),
        )
        if norm_ct:
            candidates.append((norm_ct, norm_pc, _commune_field_quality(norm_ct, norm_pc) + 1, "extract"))

    if not candidates:
        return None, None, None

    candidates.sort(key=lambda row: row[2], reverse=True)
    best_city, best_pc = candidates[0][0], candidates[0][1]

    sector: str | None = None
    for cand in (loc.get("sector"), fresh_sector, existing_sector):
        s = (cand or "").strip()
        if s and _sector_is_commune_like(s) and not looks_like_street_in_commune_field(s):
            sector = s
            break

    return best_city, best_pc, sector


def sanitize_lead_commune_fields(lead) -> None:
    """Garantit city = commune, address = voie (après crawl / avant INSERT)."""
    addr, ct, pc = sanitize_location_triplet(
        getattr(lead, "address", None),
        getattr(lead, "city", None),
        getattr(lead, "postcode", None),
    )
    lead.address = addr
    lead.city = ct
    lead.postcode = pc
