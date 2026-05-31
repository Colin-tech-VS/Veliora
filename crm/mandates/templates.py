"""Modèles de mandats immobiliers (France) — vente et location."""

from __future__ import annotations

MANDATE_TYPES = ("vente", "location")
EXCLUSIVITY_TYPES = ("exclusif", "simple", "semi-exclusif")

_YES_NO = ["Oui", "Non"]
_PROPERTY_TYPES = [
    "Appartement",
    "Maison",
    "Studio",
    "Terrain",
    "Immeuble",
    "Local commercial",
    "Parking",
    "Autre",
]
_OWNER_STATUS = ["Particulier", "SCI", "Société"]
_HEATING = ["Électrique", "Gaz", "Fioul", "Pompe à chaleur", "Collectif", "Autre"]
_CONDITION = ["Neuf", "Rénové", "Bon état", "À rafraîchir", "À rénover"]
_TENSION = ["Forte", "Moyenne", "Faible"]
_MOTIVATION = ["Divorce", "Succession", "Mutation", "Investissement", "Autre", "Non renseigné"]
_DPE = ["A", "B", "C", "D", "E", "F", "G", "Non renseigné"]
_KITCHEN = ["Ouverte", "Fermée", "Semi-ouverte", "Non renseigné"]
_FURNISHED = ["Meublé", "Non meublé"]
_FEE_PAYER = ["Locataire", "Propriétaire", "Partagé"]
_TENANT_PROFILE = ["CDI", "CDD", "Étudiant", "Garant accepté", "Indépendant", "Retraité"]


def default_agency_profile() -> dict:
    return {
        "legal_name": "",
        "brand_name": "",
        "address": "",
        "postal_code": "",
        "city": "",
        "siret": "",
        "rcs": "",
        "capital": "",
        "tva_intra": "",
        "professional_card": "",
        "insurance_company": "",
        "insurance_policy": "",
        "representative_name": "",
        "representative_title": "Gérant",
        "phone": "",
        "email": "",
        "website": "",
    }


def template_fields_vente() -> list[dict]:
    return [
        # 1. Identification du bien (OBLIGATOIRE)
        {"key": "property_type", "label": "Type de bien", "type": "select", "options": _PROPERTY_TYPES, "default": "Appartement", "required": True, "section": "1. Identification du bien"},
        {"key": "property_address", "label": "Adresse complète", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "postal_code", "label": "Code postal", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "city", "label": "Ville", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "neighborhood", "label": "Quartier", "type": "text", "section": "1. Identification du bien"},
        {"key": "surface_carrez", "label": "Surface habitable (m²)", "type": "number", "required": True, "section": "1. Identification du bien"},
        {"key": "rooms", "label": "Nombre de pièces", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "bedrooms", "label": "Nombre de chambres", "type": "number", "section": "1. Identification du bien"},
        {"key": "floor", "label": "Étage", "type": "text", "section": "1. Identification du bien"},
        {"key": "has_elevator", "label": "Ascenseur", "type": "select", "options": _YES_NO, "section": "1. Identification du bien"},
        {"key": "construction_year", "label": "Année de construction", "type": "number", "section": "1. Identification du bien"},
        # 2. Prix et conditions
        {"key": "price_fai", "label": "Prix de vente demandé / FAI (€)", "type": "number", "required": True, "section": "2. Prix et conditions"},
        {"key": "price_net_seller", "label": "Prix net vendeur (€)", "type": "number", "section": "2. Prix et conditions"},
        {"key": "honoraires_pct", "label": "Honoraires agence (%)", "type": "text", "default": "5", "section": "2. Prix et conditions"},
        {"key": "honoraires_amount", "label": "Honoraires agence (montant €)", "type": "number", "section": "2. Prix et conditions"},
        {"key": "honoraires_charge", "label": "Honoraires à la charge de", "type": "select", "options": ["Vendeur", "Acquéreur"], "default": "Vendeur", "section": "2. Prix et conditions"},
        {"key": "price_hai", "label": "Prix HAI (€)", "type": "number", "section": "2. Prix et conditions"},
        {"key": "negotiable", "label": "Négociation possible", "type": "select", "options": _YES_NO, "section": "2. Prix et conditions"},
        {"key": "market_estimate", "label": "Estimation prix de marché (€)", "type": "number", "section": "2. Prix et conditions"},
        # 3. Nature du mandat
        {"key": "mandate_duration_months", "label": "Durée du mandat (mois)", "type": "text", "default": "3", "section": "3. Nature du mandat"},
        {"key": "mandate_start_date", "label": "Date de début", "type": "date", "section": "3. Nature du mandat"},
        {"key": "mandate_end_date", "label": "Date de fin", "type": "date", "section": "3. Nature du mandat"},
        # 4. Informations propriétaire (OBLIGATOIRE)
        {"key": "seller_civility", "label": "Civilité", "type": "text", "default": "M.", "section": "4. Propriétaire"},
        {"key": "seller_first_name", "label": "Prénom", "type": "text", "required": True, "section": "4. Propriétaire"},
        {"key": "seller_last_name", "label": "Nom", "type": "text", "required": True, "section": "4. Propriétaire"},
        {"key": "seller_address", "label": "Adresse (si différente du bien)", "type": "text", "section": "4. Propriétaire"},
        {"key": "seller_phone", "label": "Téléphone", "type": "tel", "required": True, "section": "4. Propriétaire"},
        {"key": "seller_email", "label": "Email", "type": "email", "required": True, "section": "4. Propriétaire"},
        {"key": "owner_legal_status", "label": "Statut juridique", "type": "select", "options": _OWNER_STATUS, "default": "Particulier", "section": "4. Propriétaire"},
        {"key": "owner_count", "label": "Nombre de propriétaires", "type": "number", "default": "1", "section": "4. Propriétaire"},
        # 5. Caractéristiques techniques
        {"key": "dpe_energy", "label": "DPE — Classe énergie", "type": "select", "options": _DPE, "section": "5. Caractéristiques techniques"},
        {"key": "dpe_ges", "label": "DPE — GES", "type": "select", "options": _DPE, "section": "5. Caractéristiques techniques"},
        {"key": "heating_type", "label": "Chauffage", "type": "select", "options": _HEATING, "section": "5. Caractéristiques techniques"},
        {"key": "kitchen_type", "label": "Type de cuisine", "type": "select", "options": _KITCHEN, "section": "5. Caractéristiques techniques"},
        {"key": "general_condition", "label": "État général", "type": "select", "options": _CONDITION, "section": "5. Caractéristiques techniques"},
        {"key": "monthly_charges", "label": "Charges mensuelles copropriété (€)", "type": "number", "section": "5. Caractéristiques techniques"},
        {"key": "property_tax", "label": "Taxe foncière annuelle (€)", "type": "number", "section": "5. Caractéristiques techniques"},
        # 6. Localisation & attractivité
        {"key": "transport_proximity", "label": "Proximité transports", "type": "textarea", "section": "6. Localisation & attractivité"},
        {"key": "schools_shops", "label": "Écoles / commerces à proximité", "type": "textarea", "section": "6. Localisation & attractivité"},
        {"key": "zone_attractiveness", "label": "Attractivité zone", "type": "select", "options": _TENSION, "section": "6. Localisation & attractivité"},
        {"key": "local_market_tension", "label": "Tension immobilière locale", "type": "select", "options": _TENSION, "section": "6. Localisation & attractivité"},
        # 7. Commercialisation
        {"key": "has_photos", "label": "Photos disponibles", "type": "select", "options": _YES_NO, "section": "7. Commercialisation"},
        {"key": "photo_quality", "label": "Qualité des photos", "type": "select", "options": ["Bonne", "Moyenne", "Faible", "Non renseigné"], "section": "7. Commercialisation"},
        {"key": "virtual_tour", "label": "Visite virtuelle", "type": "select", "options": _YES_NO, "section": "7. Commercialisation"},
        {"key": "portal_listings", "label": "Diffusion portails (lesquels)", "type": "textarea", "section": "7. Commercialisation"},
        {"key": "first_listed_date", "label": "Date de mise en ligne initiale", "type": "date", "section": "7. Commercialisation"},
        # 8. Historique du bien
        {"key": "first_sale_date", "label": "Date de première mise en vente", "type": "date", "section": "8. Historique du bien"},
        {"key": "price_drop_count", "label": "Nombre de baisses de prix", "type": "number", "section": "8. Historique du bien"},
        {"key": "previous_price", "label": "Ancien prix historique (€)", "type": "number", "section": "8. Historique du bien"},
        {"key": "days_on_market", "label": "Durée totale sur le marché (jours)", "type": "number", "section": "8. Historique du bien"},
        {"key": "visit_count", "label": "Nombre de visites", "type": "number", "section": "8. Historique du bien"},
        {"key": "offer_count", "label": "Nombre d'offres reçues", "type": "number", "section": "8. Historique du bien"},
        {"key": "sale_reason", "label": "Raison de la vente", "type": "select", "options": _MOTIVATION, "section": "8. Historique du bien"},
        # 9. Signaux de motivation vendeur
        {"key": "recent_price_drop", "label": "Baisse de prix récente", "type": "select", "options": _YES_NO, "section": "9. Signaux de motivation"},
        {"key": "recent_price_drop_pct", "label": "Baisse récente (%)", "type": "number", "section": "9. Signaux de motivation"},
        {"key": "long_listing", "label": "Durée longue (>30 / >60 / >90 j)", "type": "select", "options": ["Non", ">30 jours", ">60 jours", ">90 jours"], "section": "9. Signaux de motivation"},
        {"key": "urgent_sale", "label": "Vente urgente", "type": "select", "options": _YES_NO, "section": "9. Signaux de motivation"},
        {"key": "motivation_context", "label": "Contexte (divorce, succession…)", "type": "select", "options": _MOTIVATION, "section": "9. Signaux de motivation"},
        {"key": "multi_agency", "label": "Diffusé par plusieurs agences", "type": "select", "options": _YES_NO, "section": "9. Signaux de motivation"},
        {"key": "private_seller", "label": "Vendeur particulier sans agence", "type": "select", "options": _YES_NO, "section": "9. Signaux de motivation"},
        # 10. Données légales
        {"key": "diagnostics_ok", "label": "Diagnostics obligatoires présents", "type": "select", "options": _YES_NO, "section": "10. Données légales"},
        {"key": "clear_title", "label": "Titre de propriété clair", "type": "select", "options": _YES_NO, "section": "10. Données légales"},
        {"key": "easements", "label": "Servitudes connues", "type": "textarea", "section": "10. Données légales"},
        {"key": "is_copro", "label": "Copropriété", "type": "select", "options": _YES_NO, "section": "10. Données légales"},
        {"key": "copro_procedure", "label": "Procédure en cours (copropriété)", "type": "textarea", "section": "10. Données légales"},
        {"key": "clauses", "label": "Clauses particulières", "type": "textarea", "default": "", "section": "10. Données légales"},
    ]


def template_fields_location() -> list[dict]:
    return [
        # 1. Identification du bien
        {"key": "property_type", "label": "Type de bien", "type": "select", "options": _PROPERTY_TYPES, "default": "Appartement", "required": True, "section": "1. Identification du bien"},
        {"key": "property_address", "label": "Adresse complète", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "postal_code", "label": "Code postal", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "city", "label": "Ville", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "neighborhood", "label": "Quartier", "type": "text", "section": "1. Identification du bien"},
        {"key": "surface", "label": "Surface habitable (m²)", "type": "number", "required": True, "section": "1. Identification du bien"},
        {"key": "rooms", "label": "Nombre de pièces", "type": "text", "required": True, "section": "1. Identification du bien"},
        {"key": "floor", "label": "Étage", "type": "text", "section": "1. Identification du bien"},
        {"key": "has_elevator", "label": "Ascenseur", "type": "select", "options": _YES_NO, "section": "1. Identification du bien"},
        {"key": "furnished", "label": "Meublé / non meublé", "type": "select", "options": _FURNISHED, "default": "Non meublé", "section": "1. Identification du bien"},
        # 2. Loyer et charges
        {"key": "rent_hc", "label": "Loyer mensuel hors charges (€)", "type": "number", "required": True, "section": "2. Loyer et charges"},
        {"key": "charges", "label": "Charges mensuelles (€)", "type": "number", "section": "2. Loyer et charges"},
        {"key": "rent_cc", "label": "Loyer total CC (€/mois)", "type": "number", "required": True, "section": "2. Loyer et charges"},
        {"key": "deposit", "label": "Dépôt de garantie (€)", "type": "number", "section": "2. Loyer et charges"},
        {"key": "honoraires_location", "label": "Frais d'agence (€ TTC)", "type": "number", "section": "2. Loyer et charges"},
        {"key": "fee_paid_by", "label": "Frais d'agence à la charge de", "type": "select", "options": _FEE_PAYER, "default": "Locataire", "section": "2. Loyer et charges"},
        {"key": "rent_control_zone", "label": "Encadrement des loyers (zone)", "type": "select", "options": _YES_NO, "section": "2. Loyer et charges"},
        # 3. Type de mandat
        {"key": "mandate_duration_months", "label": "Durée du mandat (mois)", "type": "text", "default": "3", "section": "3. Nature du mandat"},
        {"key": "mandate_start_date", "label": "Date de début", "type": "date", "section": "3. Nature du mandat"},
        {"key": "mandate_end_date", "label": "Date de fin", "type": "date", "section": "3. Nature du mandat"},
        # 4. Propriétaire
        {"key": "owner_civility", "label": "Civilité", "type": "text", "default": "M.", "section": "4. Propriétaire"},
        {"key": "owner_first_name", "label": "Prénom", "type": "text", "required": True, "section": "4. Propriétaire"},
        {"key": "owner_last_name", "label": "Nom", "type": "text", "required": True, "section": "4. Propriétaire"},
        {"key": "owner_address", "label": "Adresse (si différente)", "type": "text", "section": "4. Propriétaire"},
        {"key": "owner_phone", "label": "Téléphone", "type": "tel", "required": True, "section": "4. Propriétaire"},
        {"key": "owner_email", "label": "Email", "type": "email", "required": True, "section": "4. Propriétaire"},
        {"key": "owner_type", "label": "Type de propriétaire", "type": "select", "options": ["Particulier", "SCI"], "default": "Particulier", "section": "4. Propriétaire"},
        {"key": "visit_availability", "label": "Disponibilité pour visites", "type": "textarea", "section": "4. Propriétaire"},
        # 5. Caractéristiques du bien
        {"key": "dpe_energy", "label": "DPE — Classe énergie", "type": "select", "options": _DPE, "section": "5. Caractéristiques"},
        {"key": "heating_type", "label": "Chauffage", "type": "select", "options": _HEATING, "section": "5. Caractéristiques"},
        {"key": "general_condition", "label": "État du logement", "type": "select", "options": _CONDITION, "section": "5. Caractéristiques"},
        {"key": "equipped_kitchen", "label": "Cuisine équipée", "type": "select", "options": _YES_NO, "section": "5. Caractéristiques"},
        {"key": "furniture_level", "label": "Mobilier (si meublé)", "type": "textarea", "section": "5. Caractéristiques"},
        {"key": "internet_fiber", "label": "Internet / fibre disponible", "type": "select", "options": _YES_NO, "section": "5. Caractéristiques"},
        # 6. Localisation
        {"key": "transport_proximity", "label": "Proximité transports", "type": "textarea", "section": "6. Localisation"},
        {"key": "rental_attractiveness", "label": "Attractivité location", "type": "select", "options": _TENSION, "section": "6. Localisation"},
        {"key": "tense_zone", "label": "Zone tendue", "type": "select", "options": _YES_NO, "section": "6. Localisation"},
        # 7. Commercialisation
        {"key": "has_photos", "label": "Photos disponibles", "type": "select", "options": _YES_NO, "section": "7. Commercialisation"},
        {"key": "portal_listings", "label": "Diffusion portails", "type": "textarea", "section": "7. Commercialisation"},
        {"key": "first_listed_date", "label": "Date de mise en ligne", "type": "date", "section": "7. Commercialisation"},
        {"key": "vacancy_history", "label": "Historique vacance locative", "type": "textarea", "section": "7. Commercialisation"},
        # 8. Candidat locataire
        {"key": "min_income_required", "label": "Revenus minimum requis (€/mois)", "type": "number", "section": "8. Candidat locataire"},
        {"key": "tenant_profile", "label": "Type de profil recherché", "type": "select", "options": _TENANT_PROFILE, "section": "8. Candidat locataire"},
        {"key": "acceptance_conditions", "label": "Conditions d'acceptation dossier", "type": "textarea", "section": "8. Candidat locataire"},
        # 9. Signaux de marché location
        {"key": "long_vacancy", "label": "Vacance locative longue", "type": "select", "options": _YES_NO, "section": "9. Signaux de marché"},
        {"key": "rent_above_market", "label": "Loyer au-dessus du marché", "type": "select", "options": _YES_NO, "section": "9. Signaux de marché"},
        {"key": "high_local_demand", "label": "Forte demande locale", "type": "select", "options": _TENSION, "section": "9. Signaux de marché"},
        {"key": "avg_rental_speed", "label": "Rapidité location moyenne secteur (jours)", "type": "number", "section": "9. Signaux de marché"},
        {"key": "clauses", "label": "Clauses particulières", "type": "textarea", "default": "", "section": "9. Signaux de marché"},
    ]


def get_template_meta(mandate_type: str) -> dict:
    if mandate_type == "location":
        return {
            "type": "location",
            "title": "Mandat de location",
            "exclusivity_default": "exclusif",
            "fields": template_fields_location(),
        }
    return {
        "type": "vente",
        "title": "Mandat de vente",
        "exclusivity_default": "exclusif",
        "fields": template_fields_vente(),
    }


def render_mandate_html(
    mandate_type: str,
    exclusivity: str,
    fields: dict,
    agency: dict,
) -> str:
    """Génère le HTML du mandat à partir des champs remplis."""
    a = {**default_agency_profile(), **(agency or {})}
    f = fields or {}
    ag_name = a.get("legal_name") or a.get("brand_name") or "[Nom de l'agence]"
    ag_addr = ", ".join(p for p in [a.get("address"), f"{a.get('postal_code', '')} {a.get('city', '')}".strip()] if p)
    rep = a.get("representative_name") or "[Représentant]"
    card = a.get("professional_card") or "[Carte professionnelle]"

    if mandate_type == "location":
        return _render_location(f, a, ag_name, ag_addr, rep, card, exclusivity)
    return _render_vente(f, a, ag_name, ag_addr, rep, card, exclusivity)


def _fmt_price(val) -> str:
    if val is None or val == "":
        return "—"
    try:
        return f"{int(float(val)):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(val)


def _fmt_val(val) -> str:
    if val is None or val == "":
        return "—"
    return str(val)


def _excl_label(exclusivity: str) -> str:
    return {
        "exclusif": "EXCLUSIF",
        "simple": "SIMPLE",
        "semi-exclusif": "SEMI-EXCLUSIF",
    }.get(exclusivity, exclusivity.upper())


def _excl_text(exclusivity: str) -> str:
    return {
        "exclusif": "exclusif",
        "simple": "simple",
        "semi-exclusif": "semi-exclusif",
    }.get(exclusivity, exclusivity)


def _render_li(label: str, val) -> str:
    v = _fmt_val(val)
    if v == "—":
        return ""
    return f"<li><strong>{label} :</strong> {v}</li>"


def _render_ul(items: list[str]) -> str:
    filtered = [i for i in items if i]
    if not filtered:
        return ""
    return f"<ul>{''.join(filtered)}</ul>"


def _excl_clause_vente(exclusivity: str) -> str:
    if exclusivity == "simple":
        return (
            "<p>Le mandant confie au mandataire un mandat <strong>simple</strong> : il conserve la faculté "
            "de vendre le bien par lui-même ou par l'intermédiaire d'un autre professionnel, sans commission "
            "due au mandataire dans ce cas. Si la vente est réalisée par le mandataire, les honoraires "
            "prévus au présent mandat restent dus.</p>"
        )
    if exclusivity == "semi-exclusif":
        return (
            "<p>Le mandant confie au mandataire un mandat <strong>semi-exclusif</strong> : en cas de vente "
            "conclue avec un acquéreur présenté par le mandataire ou ayant visité le bien par son "
            "intermédiaire, les honoraires sont dus intégralement. En cas de vente réalisée par le mandant "
            "seul, sans intervention du mandataire, les honoraires ne sont pas dus.</p>"
        )
    return (
        "<p>Le mandant confie au mandataire un mandat <strong>exclusif</strong> : il s'interdit de confier "
        "la vente du bien à un autre professionnel et de le vendre directement pendant la durée du mandat. "
        "Toute vente réalisée pendant cette période, même par le mandant seul, entraîne le paiement des "
        "honoraires au mandataire.</p>"
    )


def _excl_clause_location(exclusivity: str) -> str:
    if exclusivity == "simple":
        return (
            "<p>Le mandant confie au mandataire un mandat <strong>simple</strong> de location : il peut "
            "rechercher un locataire par ses propres moyens. Les honoraires ne sont dus que si le bail "
            "est signé avec un locataire présenté par le mandataire.</p>"
        )
    if exclusivity == "semi-exclusif":
        return (
            "<p>Le mandant confie au mandataire un mandat <strong>semi-exclusif</strong> : les honoraires "
            "sont dus si le locataire a visité le bien par l'intermédiaire du mandataire, même si le bail "
            "est signé directement avec le mandant.</p>"
        )
    return (
        "<p>Le mandant confie au mandataire un mandat <strong>exclusif</strong> de location : il "
        "s'interdit de confier la recherche de locataire à un autre professionnel pendant la durée du "
        "mandat.</p>"
    )


def _render_vente(f: dict, a: dict, ag_name: str, ag_addr: str, rep: str, card: str, exclusivity: str) -> str:
    excl_label = _excl_label(exclusivity)
    excl_text = _excl_text(exclusivity)
    seller = f"{f.get('seller_civility', 'M.')} {f.get('seller_first_name', '')} {f.get('seller_last_name', '')}".strip()
    addr_full = ", ".join(
        p for p in [
            f.get("property_address"),
            f"{f.get('postal_code', '')} {f.get('city', '')}".strip(),
        ] if p
    )
    mandate_dates = ""
    if f.get("mandate_start_date") or f.get("mandate_end_date"):
        mandate_dates = (
            f" du <strong>{_fmt_val(f.get('mandate_start_date'))}</strong>"
            f" au <strong>{_fmt_val(f.get('mandate_end_date'))}</strong>"
        )

    identification = _render_ul([
        _render_li("Adresse", addr_full or f.get("property_address")),
        _render_li("Quartier", f.get("neighborhood")),
        _render_li("Type", f.get("property_type")),
        _render_li("Surface habitable", f"{f.get('surface_carrez')} m²" if f.get("surface_carrez") else None),
        _render_li("Pièces", f.get("rooms")),
        _render_li("Chambres", f.get("bedrooms")),
        _render_li("Étage", f.get("floor")),
        _render_li("Ascenseur", f.get("has_elevator")),
        _render_li("Année construction", f.get("construction_year")),
    ])

    prix = _render_ul([
        _render_li("Prix FAI", f"{_fmt_price(f.get('price_fai'))} €" if f.get("price_fai") else None),
        _render_li("Prix net vendeur", f"{_fmt_price(f.get('price_net_seller'))} €" if f.get("price_net_seller") else None),
        _render_li("Prix HAI", f"{_fmt_price(f.get('price_hai'))} €" if f.get("price_hai") else None),
        _render_li("Négociation", f.get("negotiable")),
        _render_li("Estimation marché", f"{_fmt_price(f.get('market_estimate'))} €" if f.get("market_estimate") else None),
    ])

    technique = _render_ul([
        _render_li("DPE énergie", f.get("dpe_energy")),
        _render_li("DPE GES", f.get("dpe_ges")),
        _render_li("Chauffage", f.get("heating_type")),
        _render_li("Cuisine", f.get("kitchen_type")),
        _render_li("État", f.get("general_condition")),
        _render_li("Charges mensuelles", f"{_fmt_price(f.get('monthly_charges'))} €" if f.get("monthly_charges") else None),
        _render_li("Taxe foncière", f"{_fmt_price(f.get('property_tax'))} €" if f.get("property_tax") else None),
    ])

    legal = _render_ul([
        _render_li("Diagnostics", f.get("diagnostics_ok")),
        _render_li("Titre de propriété", f.get("clear_title")),
        _render_li("Copropriété", f.get("is_copro")),
        _render_li("Servitudes", f.get("easements")),
        _render_li("Procédure copro", f.get("copro_procedure")),
    ])

    return f"""
<div class="mandate-doc">
  <h1>MANDAT DE VENTE {excl_label}</h1>
  <p class="mandate-meta">Document généré par Veliora — à faire signer par les parties</p>

  <h2>1. Le mandant (vendeur)</h2>
  <p><strong>{seller or '—'}</strong><br>
  Statut : {_fmt_val(f.get('owner_legal_status'))} · Propriétaires : {_fmt_val(f.get('owner_count') or '1')}<br>
  Demeurant : {f.get('seller_address') or '—'}<br>
  Email : {f.get('seller_email') or '—'} · Tél. : {f.get('seller_phone') or '—'}</p>

  <h2>2. Le mandataire (agence)</h2>
  <p><strong>{ag_name}</strong><br>
  {ag_addr}<br>
  SIRET : {a.get('siret') or '—'} · RCS : {a.get('rcs') or '—'}<br>
  Carte professionnelle : {card}<br>
  Représentée par : {rep}, {a.get('representative_title') or 'Gérant'}</p>

  <h2>3. Objet du mandat — Identification du bien</h2>
  <p>Le mandant confie au mandataire, qui l'accepte, la mission de rechercher un acquéreur pour le bien suivant :</p>
  {identification or '<ul><li>—</li></ul>'}
  {prix}

  <h2>4. Exclusivité et durée</h2>
  {_excl_clause_vente(exclusivity)}
  <p>Mandat <strong>{excl_text}</strong> d'une durée de <strong>{f.get('mandate_duration_months', '3')} mois</strong>{mandate_dates},
  renouvelable par tacite reconduction pour des périodes successives de même durée, sauf dénonciation
  avec préavis d'un mois par lettre recommandée.</p>

  <h2>5. Honoraires</h2>
  <p>En cas de réalisation de l'opération, le mandant s'engage à verser au mandataire des honoraires de
  <strong>{f.get('honoraires_pct', '5')} % TTC</strong>
  {f' (soit {_fmt_price(f.get("honoraires_amount"))} €)' if f.get('honoraires_amount') else ''}
  du prix de vente, à la charge du <strong>{f.get('honoraires_charge', 'Vendeur')}</strong>,
  exigibles à la signature de l'acte authentique de vente.</p>

  <h2>6. Caractéristiques et données légales</h2>
  {technique}
  {legal}

  <h2>7. Clauses particulières</h2>
  <p>{f.get('clauses') or 'Néant.'}</p>

  <h2>8. Protection des données</h2>
  <p>Les données personnelles sont traitées conformément au RGPD pour les besoins de la commercialisation du bien.</p>

  <div class="mandate-signatures">
    <p>Fait à {a.get('city') or '………………'}, le ……………………</p>
    <div class="sig-grid">
      <div><p><strong>Le mandant</strong><br><br><br>_____________________</p></div>
      <div><p><strong>Le mandataire</strong><br><br><br>_____________________</p></div>
    </div>
  </div>
</div>
"""


def _render_location(f: dict, a: dict, ag_name: str, ag_addr: str, rep: str, card: str, exclusivity: str) -> str:
    excl_label = _excl_label(exclusivity)
    excl_text = _excl_text(exclusivity)
    owner = f"{f.get('owner_civility', 'M.')} {f.get('owner_first_name', '')} {f.get('owner_last_name', '')}".strip()
    addr_full = ", ".join(
        p for p in [
            f.get("property_address"),
            f"{f.get('postal_code', '')} {f.get('city', '')}".strip(),
        ] if p
    )
    mandate_dates = ""
    if f.get("mandate_start_date") or f.get("mandate_end_date"):
        mandate_dates = (
            f" du <strong>{_fmt_val(f.get('mandate_start_date'))}</strong>"
            f" au <strong>{_fmt_val(f.get('mandate_end_date'))}</strong>"
        )

    identification = _render_ul([
        _render_li("Adresse", addr_full or f.get("property_address")),
        _render_li("Quartier", f.get("neighborhood")),
        _render_li("Type", f.get("property_type")),
        _render_li("Surface", f"{f.get('surface')} m²" if f.get("surface") else None),
        _render_li("Pièces", f.get("rooms")),
        _render_li("Étage", f.get("floor")),
        _render_li("Ascenseur", f.get("has_elevator")),
        _render_li("Meublé", f.get("furnished")),
    ])

    loyer = _render_ul([
        _render_li("Loyer HC", f"{_fmt_price(f.get('rent_hc'))} € / mois" if f.get("rent_hc") else None),
        _render_li("Charges", f"{_fmt_price(f.get('charges'))} € / mois" if f.get("charges") else None),
        _render_li("Loyer CC", f"{_fmt_price(f.get('rent_cc'))} € / mois" if f.get("rent_cc") else None),
        _render_li("Dépôt de garantie", f"{_fmt_price(f.get('deposit'))} €" if f.get("deposit") else None),
        _render_li("Encadrement loyers", f.get("rent_control_zone")),
    ])

    caracteristiques = _render_ul([
        _render_li("DPE", f.get("dpe_energy")),
        _render_li("Chauffage", f.get("heating_type")),
        _render_li("État", f.get("general_condition")),
        _render_li("Cuisine équipée", f.get("equipped_kitchen")),
        _render_li("Mobilier", f.get("furniture_level")),
        _render_li("Internet / fibre", f.get("internet_fiber")),
    ])

    return f"""
<div class="mandate-doc">
  <h1>MANDAT DE LOCATION {excl_label}</h1>
  <p class="mandate-meta">Document généré par Veliora — à faire signer par les parties</p>

  <h2>1. Le mandant (bailleur)</h2>
  <p><strong>{owner or '—'}</strong><br>
  Type : {_fmt_val(f.get('owner_type'))}<br>
  Demeurant : {f.get('owner_address') or '—'}<br>
  Email : {f.get('owner_email') or '—'} · Tél. : {f.get('owner_phone') or '—'}<br>
  Disponibilité visites : {_fmt_val(f.get('visit_availability'))}</p>

  <h2>2. Le mandataire (agence)</h2>
  <p><strong>{ag_name}</strong><br>
  {ag_addr}<br>
  SIRET : {a.get('siret') or '—'} · RCS : {a.get('rcs') or '—'}<br>
  Carte professionnelle : {card}<br>
  Représentée par : {rep}, {a.get('representative_title') or 'Gérant'}</p>

  <h2>3. Objet du mandat — Identification du bien</h2>
  <p>Le mandant confie au mandataire la mission de rechercher un locataire pour le bien suivant :</p>
  {identification or '<ul><li>—</li></ul>'}
  {loyer}

  <h2>4. Exclusivité et durée</h2>
  {_excl_clause_location(exclusivity)}
  <p>Mandat <strong>{excl_text}</strong> pour une durée de <strong>{f.get('mandate_duration_months', '3')} mois</strong>{mandate_dates}.</p>

  <h2>5. Honoraires</h2>
  <p>Honoraires de location d'un montant de <strong>{_fmt_price(f.get('honoraires_location'))} € TTC</strong>,
  à la charge du <strong>{f.get('fee_paid_by', 'Locataire')}</strong>,
  dus à la signature du bail par le locataire présenté par le mandataire.</p>

  <h2>6. Caractéristiques du bien</h2>
  {caracteristiques or '<p>—</p>'}

  <h2>7. Clauses particulières</h2>
  <p>{f.get('clauses') or 'Néant.'}</p>

  <div class="mandate-signatures">
    <p>Fait à {a.get('city') or '………………'}, le ……………………</p>
    <div class="sig-grid">
      <div><p><strong>Le mandant</strong><br><br><br>_____________________</p></div>
      <div><p><strong>Le mandataire</strong><br><br><br>_____________________</p></div>
    </div>
  </div>
</div>
"""
