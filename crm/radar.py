"""Radar mandats — scoring, alertes et briefing quotidien."""

from __future__ import annotations

import re
from datetime import date
from typing import Any


def _parse_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None


def days_since(iso: str | None) -> int | None:
    d = _parse_date(iso)
    if not d:
        return None
    return (date.today() - d).days


def compute_mandate_score(lead: dict) -> tuple[int, str, list[str]]:
    """Score 0–100 + raison lisible + tags (délègue au module scoring V2)."""
    from crm.scoring.mandate import compute_mandate_score as _compute

    result = _compute(lead)
    return result.score, result.reason, result.tags


def mandate_call_recommendation(score: int) -> dict[str, str]:
    """Recommandation d'appel lisible (Mode 2 — analyse à la demande)."""
    s = score or 0
    if s >= 85:
        return {
            "label": "À appeler aujourd'hui",
            "horizon": "24h",
            "urgency": "urgent",
            "detail": "Plusieurs signaux mandat — priorité immédiate.",
        }
    if s >= 65:
        return {
            "label": "À appeler sous 48h",
            "horizon": "48h",
            "urgency": "high",
            "detail": "Bonne opportunité — planifier l'appel rapidement.",
        }
    if s >= 45:
        return {
            "label": "À traiter cette semaine",
            "horizon": "7j",
            "urgency": "medium",
            "detail": "Profil intéressant — qualifier au téléphone.",
        }
    return {
        "label": "À surveiller",
        "horizon": "—",
        "urgency": "low",
        "detail": "Peu de signaux pour l'instant — compléter la fiche ou relancer après crawl.",
    }


def build_positive_factors(lead: dict) -> list[dict[str, str]]:
    """Facteurs positifs structurés pour l'UI Mode 2."""
    stored = lead.get("score_positive_factors")
    if stored:
        return list(stored)[:8]
    expl = lead.get("score_explanation")
    if isinstance(expl, dict) and expl.get("positive_factors"):
        return list(expl["positive_factors"])[:8]

    factors: list[dict[str, str]] = []
    tags = lead.get("alert_tags") or []

    if lead.get("type") != "agence" or "sans_agence" in tags:
        factors.append({
            "key": "sans_agence",
            "label": "Sans agence",
            "detail": "Annonce publiée par un particulier",
        })

    days = lead.get("days_on_market")
    if days is None:
        days = days_since(lead.get("published_at") or lead.get("listedAt"))
    if days is not None and days >= 30:
        factors.append({
            "key": "days_online",
            "label": f"{days} jours en ligne",
            "detail": "Ancienneté — vendeur potentiellement plus ouvert",
        })
    elif days is not None and days >= 7:
        factors.append({
            "key": "days_online",
            "label": f"{days} jours en ligne",
            "detail": "Annonce déjà visible sur le marché",
        })

    pct = lead.get("price_change_pct")
    if pct is not None and pct < 0:
        drop = abs(int(pct))
        factors.append({
            "key": "price_drop",
            "label": f"Prix baissé de {drop} %",
            "detail": "Ajustement récent — signe de motivation",
        })
    elif lead.get("previous_price") and lead.get("price"):
        prev = lead["previous_price"]
        price = lead["price"]
        if prev > price:
            drop = int((prev - price) / prev * 100)
            if drop >= 3:
                factors.append({
                    "key": "price_drop",
                    "label": f"Prix baissé de {drop} %",
                    "detail": "Ajustement récent — signe de motivation",
                })

    if "dvf_sous_marche" in tags or lead.get("dvf_verdict") in (
        "sous_marche",
        "leger_sous_marche",
    ):
        delta = lead.get("dvf_delta_pct")
        if delta is not None:
            factors.append({
                "key": "dvf",
                "label": "Sous marché DVF",
                "detail": f"Environ {abs(int(delta))} % sous la médiane locale (Etalab)",
            })
        else:
            factors.append({
                "key": "dvf",
                "label": "Sous marché DVF",
                "detail": lead.get("dvf_verdict_label")
                or "Prix inférieur aux ventes récentes du secteur",
            })

    if "nouveau" in tags:
        factors.append({
            "key": "nouveau",
            "label": "Nouveau sur le radar",
            "detail": "Fiche récemment détectée — soyez parmi les premiers",
        })

    if lead.get("phone") and lead.get("phone") != "—":
        factors.append({
            "key": "phone",
            "label": "Téléphone disponible",
            "detail": "Contact direct possible",
        })

    if (lead.get("mandate_score") or 0) >= 85:
        factors.append({
            "key": "hot",
            "label": "Score Mandat™ élevé",
            "detail": "Cumul de signaux forts sur cette fiche",
        })

    return factors[:8]


def build_ai_analysis(lead: dict) -> dict[str, Any]:
    """
    Synthèse narrative « Analyse IA » — phrases contextualisées à partir des
    signaux réels (ancienneté, sans agence, baisse, DVF) pour crédibiliser l'approche.
    """
    enriched = enrich_lead_row(dict(lead))
    tags = enriched.get("alert_tags") or []
    paragraphs: list[str] = []

    days = enriched.get("days_on_market")
    if days is None:
        days = days_since(enriched.get("published_at") or enriched.get("listedAt"))

    is_particulier = enriched.get("type") != "agence" or "sans_agence" in tags
    tx = enriched.get("transaction_type") or "vente"
    city = (enriched.get("city") or enriched.get("dvf_commune") or "").strip()
    sector = (enriched.get("dvf_sector") or enriched.get("sector") or "").strip()
    if sector:
        place = f"le secteur {sector}"
    elif city:
        place = city
    else:
        place = "le secteur"

    if is_particulier:
        if days is not None and days >= 1:
            paragraphs.append(
                f"Ce bien est diffusé depuis {days} jours sans agence."
            )
        else:
            paragraphs.append(
                "Ce bien est publié en direct par un particulier, sans intermédiaire agence."
            )
    else:
        paragraphs.append(
            "Ce bien est diffusé par une agence concurrente — l'opportunité mandat "
            "est plus limitée, mais l'analyse du positionnement prix reste pertinente."
        )

    pct = enriched.get("price_change_pct")
    prev = enriched.get("previous_price")
    price = enriched.get("price") or 0
    if pct is not None and pct < 0:
        drop = abs(int(pct))
        if drop >= 3:
            paragraphs.append(
                f"Son prix a déjà baissé de {drop} % — le vendeur semble prêt "
                f"à ajuster son attente."
            )
        else:
            paragraphs.append("Son prix a déjà baissé une fois.")
    elif prev and price and prev > price:
        drop = int((prev - price) / prev * 100)
        if drop >= 3:
            paragraphs.append(
                f"Son prix a déjà baissé de {drop} % — le vendeur semble prêt "
                f"à ajuster son attente."
            )
        else:
            paragraphs.append("Son prix a déjà baissé une fois.")
    elif days is not None and days >= 30 and is_particulier:
        paragraphs.append(
            "Le prix affiché n'a pas encore baissé, mais l'ancienneté de l'annonce "
            "peut traduire une attente qui mûrit."
        )

    dvf_v = enriched.get("dvf_verdict")
    dvf_label = enriched.get("dvf_verdict_label") or ""
    delta = enriched.get("dvf_delta_pct")

    if dvf_v in ("sous_marche", "leger_sous_marche"):
        if delta is not None:
            paragraphs.append(
                f"Les ventes DVF récentes ({place}) suggèrent un prix "
                f"d'environ {abs(int(delta))} % sous le marché local."
            )
        else:
            paragraphs.append(
                "Les ventes DVF récentes du secteur suggèrent un prix "
                "inférieur au marché local."
            )
    elif dvf_v == "sur_marche":
        paragraphs.append(
            f"Les ventes DVF récentes ({place}) placent ce bien au-dessus de la "
            f"médiane locale — un angle « estimation objective » peut ouvrir la discussion."
        )
    elif dvf_v == "aligne":
        paragraphs.append(
            f"Les ventes DVF récentes ({place}) indiquent un prix aligné sur le "
            f"marché — misez sur la visibilité et l'accompagnement plutôt que sur le prix."
        )
    elif tx == "vente" and enriched.get("price") and enriched.get("surface"):
        paragraphs.append(
            "Un comparatif DVF sur cette vente renforcera votre argumentaire chiffré "
            "auprès du propriétaire."
        )

    if "nouveau" in tags and is_particulier:
        paragraphs.append(
            "Annonce récemment détectée : le propriétaire est peut-être encore en "
            "phase de choix — une prise de contact rapide est pertinente."
        )
    elif (enriched.get("mandate_score") or 0) >= 65 and is_particulier:
        paragraphs.append(
            "Le vendeur pourrait être plus réceptif à une proposition d'accompagnement."
        )
    elif days is not None and days >= 45 and is_particulier:
        paragraphs.append(
            "Après plusieurs semaines en ligne, une proposition structurée "
            "(estimation, stratégie de diffusion) peut être bien accueillie."
        )
    elif is_particulier:
        paragraphs.append(
            "Une prise de contact personnalisée, appuyée sur ces éléments, "
            "renforcera votre crédibilité auprès du propriétaire."
        )
    else:
        paragraphs.append(
            "Croisez ces éléments avec votre connaissance du quartier pour "
            "personnaliser votre approche."
        )

    if enriched.get("phone") and enriched.get("phone") != "—":
        paragraphs.append(
            "Un numéro de téléphone est disponible — privilégiez l'appel direct."
        )

    # Dédupliquer tout en gardant l'ordre
    seen: set[str] = set()
    unique: list[str] = []
    for p in paragraphs:
        key = p[:80]
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    if len(unique) < 2:
        unique.append(
            "Complétez la fiche (contacts, date de publication) puis relancez "
            "l'analyse pour enrichir cette synthèse."
        )

    return {
        "title": "Analyse IA",
        "subtitle": "Synthèse contextualisée à partir des signaux détectés sur l'annonce",
        "paragraphs": unique[:6],
        "disclaimer": "Généré à partir des données crawlées et DVF (Etalab) — à valider en rendez-vous.",
    }


def build_listing_analysis(lead: dict, *, agency_name: str = "") -> dict[str, Any]:
    """Analyse complète pour Mode 2 (après import ou fiche existante)."""
    enriched = enrich_lead_row(dict(lead))
    score = enriched.get("mandate_score") or 0
    factors = build_positive_factors(enriched)
    reco = mandate_call_recommendation(score)
    scenario = detect_lead_scenario(enriched)

    expl = enriched.get("score_explanation") or {}
    reco = expl.get("recommendation") if isinstance(expl, dict) and expl.get("recommendation") else reco

    return {
        "mandate_score": score,
        "mandate_score_max": 100,
        "mandate_score_reason": enriched.get("mandate_score_reason") or "",
        "priority_tier": enriched.get("priority_tier"),
        "positive_factors": factors,
        "negative_factors": enriched.get("score_negative_factors")
        or (expl.get("negative_factors") if isinstance(expl, dict) else []),
        "score_breakdown": expl.get("contributions") if isinstance(expl, dict) else [],
        "recommendation": reco,
        "scenario": scenario,
        "scenario_label": SCENARIO_LABELS.get(scenario, SCENARIO_LABELS["default"]),
        "lead_id": enriched.get("id"),
        "source_url": enriched.get("source_url"),
        "address": enriched.get("address") or enriched.get("property_title"),
        "owner": enriched.get("owner"),
        "price_label": format_price_short(enriched),
        "portal": _portal_from_url(enriched.get("source_url") or ""),
        "agency_name": agency_name,
        "alert_tags": enriched.get("alert_tags") or [],
        "dvf_verdict": enriched.get("dvf_verdict"),
        "dvf_verdict_label": enriched.get("dvf_verdict_label"),
        "days_on_market": enriched.get("days_on_market"),
        "ai_analysis": build_ai_analysis(enriched),
    }


def _portal_from_url(url: str) -> str:
    u = (url or "").lower()
    if "seloger" in u:
        return "SeLoger"
    if "leboncoin" in u:
        return "Leboncoin"
    if "bienici" in u:
        return "Bien'ici"
    if "pap.fr" in u:
        return "PAP"
    if "logic-immo" in u or "logicimmo" in u:
        return "Logic-Immo"
    if "paruvendu" in u:
        return "ParuVendu"
    if "figaro" in u:
        return "Le Figaro"
    return "Site immobilier"


def enrich_lead_row(lead: dict, *, force: bool = False) -> dict:
    from crm.scoring.recalc import enrich_lead_row as _enrich

    return _enrich(lead, force=force)


def compute_alerts(leads: list[dict]) -> list[dict]:
    alerts: list[dict] = []
    for lead in leads:
        lid = lead.get("id")
        for tag in lead.get("alert_tags") or []:
            if tag == "sans_agence" and "nouveau" in (lead.get("alert_tags") or []):
                alerts.append({
                    "type": "nouveau_sans_agence",
                    "priority": "high",
                    "lead_id": lid,
                    "title": "Nouveau sans agence",
                    "message": f"{lead.get('address', 'Bien')} — {format_price_short(lead)}",
                })
                break
        if "baisse_prix" in (lead.get("alert_tags") or []):
            alerts.append({
                "type": "baisse_prix",
                "priority": "high",
                "lead_id": lid,
                "title": "Baisse de prix",
                "message": f"{lead.get('mandate_score_reason', '')} — {lead.get('address', '')}",
            })
        if (lead.get("mandate_score") or 0) >= 85:
            alerts.append({
                "type": "fort_potentiel",
                "priority": "urgent",
                "lead_id": lid,
                "title": "Fort potentiel mandat",
                "message": lead.get("mandate_score_reason", ""),
            })
        if "ancienne" in (lead.get("alert_tags") or []) and is_particulier_lead(lead):
            alerts.append({
                "type": "annonce_ancienne",
                "priority": "medium",
                "lead_id": lid,
                "title": "Annonce ancienne",
                "message": f"{lead.get('days_on_market', '?')} j — vendeur potentiellement motivé",
            })
        if "dvf_sous_marche" in (lead.get("alert_tags") or []):
            alerts.append({
                "type": "dvf_opportunite",
                "priority": "high",
                "lead_id": lid,
                "title": "Sous le marché DVF",
                "message": lead.get("dvf_verdict_label") or "Prix inférieur aux ventes récentes",
            })

    seen: set[tuple] = set()
    unique: list[dict] = []
    for a in sorted(alerts, key=lambda x: (0 if x["priority"] == "urgent" else 1 if x["priority"] == "high" else 2)):
        key = (a["type"], a.get("lead_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique[:30]


def is_particulier_lead(lead: dict) -> bool:
    return lead.get("type") != "agence"


def is_active_lead(lead: dict) -> bool:
    """Prospect visible dans le radar (fiches retirées = incohérentes, conservées en base)."""
    return (lead.get("status") or "").lower() != "retire"


def _lead_matches_cities(lead: dict, cities: list[str]) -> bool:
    if not cities:
        return True
    addr = (lead.get("address") or "").lower()
    city = (lead.get("city") or "").lower()
    for raw in cities:
        c = raw.strip().lower()
        if c and (c in addr or c in city):
            return True
    return False


def format_price_short(lead: dict) -> str:
    p = lead.get("price") or 0
    if not p:
        return "—"
    s = f"{p:,}".replace(",", " ") + " €"
    if lead.get("transaction_type") == "location":
        s += "/mois"
    return s


def build_briefing(
    leads: list[dict],
    agency_name: str = "",
    *,
    target_cities: list[str] | None = None,
) -> dict:
    cities = [c.strip() for c in (target_cities or []) if c and str(c).strip()]
    filtered = leads
    if cities:
        filtered = [l for l in leads if _lead_matches_cities(l, cities)]
    enriched = [l for l in filtered if is_active_lead(l)]
    particuliers = [l for l in enriched if is_particulier_lead(l)]

    new_sans_agence = [
        l for l in particuliers
        if "nouveau" in (l.get("alert_tags") or []) or l.get("status") == "nouveau"
    ]
    baisses = [l for l in enriched if "baisse_prix" in (l.get("alert_tags") or [])]
    hot = [l for l in enriched if (l.get("mandate_score") or 0) >= 85]
    anciennes = [
        l for l in particuliers
        if (l.get("days_on_market") or 0) >= 30 and l.get("pipeline", "nouveau") in ("nouveau", "a_contacter")
    ]

    today_list = sorted(
        enriched,
        key=lambda x: (x.get("mandate_score") or 0),
        reverse=True,
    )[:50]

    mandats_month = sum(1 for l in enriched if l.get("pipeline") == "mandat" or l.get("status") == "mandat")
    contactes_week = sum(
        1 for l in enriched
        if l.get("pipeline") in ("contacte", "rdv", "mandat") or l.get("status") == "contacte"
    )

    return {
        "agency_name": agency_name,
        "date": date.today().isoformat(),
        "target_cities": cities,
        "filtered_by_cities": bool(cities),
        "counts": {
            "new_without_agency": len(new_sans_agence),
            "price_drops": len(baisses),
            "hot_mandate": len(hot),
            "old_listings": len(anciennes),
            "total_opportunities": len(enriched),
            "sans_agence": len(particuliers),
            "mandats_month": mandats_month,
            "dvf_sous_marche": sum(
                1 for l in enriched if "dvf_sous_marche" in (l.get("alert_tags") or [])
            ),
            "dvf_compared": sum(1 for l in enriched if l.get("dvf_verdict")),
        },
        "priorities": today_list[:20],
        "alerts": compute_alerts(enriched),
        "activity": {
            "mandats_month": mandats_month,
            "contactes_pipeline": contactes_week,
        },
    }


def call_script_for_lead(lead: dict) -> str:
    """Script d'appel contextuel (texte brut, sans markdown)."""
    script = build_call_script(lead)
    return script.get("full_text_plain") or _plain_script_text(script.get("full_text") or "")


def detect_lead_scenario(lead: dict) -> str:
    """Scénario principal pour le discours commercial."""
    tags = lead.get("alert_tags") or []
    score = lead.get("mandate_score") or 0
    if score >= 85:
        return "fort_potentiel"
    if "dvf_sous_marche" in tags:
        return "dvf_sous_marche"
    if "baisse_prix" in tags:
        return "baisse_prix"
    if "nouveau" in tags and "sans_agence" in tags:
        return "nouveau_sans_agence"
    if "ancienne" in tags:
        return "annonce_ancienne"
    if "sans_agence" in tags:
        return "sans_agence"
    return "default"


SCENARIO_LABELS = {
    "fort_potentiel": "Fort potentiel mandat",
    "dvf_sous_marche": "Sous le marché DVF",
    "baisse_prix": "Baisse de prix",
    "nouveau_sans_agence": "Nouveau sans agence",
    "annonce_ancienne": "Annonce ancienne",
    "sans_agence": "Particulier sans agence",
    "default": "Premier contact",
}


PLAYBOOK_GUIDE: list[dict[str, Any]] = [
    {
        "id": "mandate_score",
        "title": "Score mandat",
        "emoji": "🎯",
        "summary": "De 0 à 100 — plus le score est élevé, plus le prospect mérite un appel rapide.",
        "blocks": [
            {
                "label": "85 – 100 · Priorité immédiate",
                "detail": "Plusieurs signaux forts (sans agence, DVF, baisse, ancienneté…). Appelez dans la journée.",
                "tone": "urgent",
            },
            {
                "label": "60 – 84 · Bonne opportunité",
                "detail": "Profil intéressant à qualifier au téléphone. Préparez un angle d'accroche (DVF ou ancienneté).",
                "tone": "high",
            },
            {
                "label": "30 – 59 · À suivre",
                "detail": "Relancez si le bien évolue (baisse, DVF favorable) ou après un crawl qui enrichit la fiche.",
                "tone": "medium",
            },
            {
                "label": "0 – 29 · Veille",
                "detail": "Peu de signaux mandat pour l'instant. Gardez en base pour suivi automatique.",
                "tone": "low",
            },
        ],
        "tips": [
            "Le score combine : particulier sans agence, ancienneté de l'annonce, baisse de prix, comparatif DVF, surface cohérente, téléphone/email renseignés.",
            "Un score élevé ne garantit pas le mandat — c'est un ordre d'appel, pas une promesse de signature.",
        ],
    },
    {
        "id": "alerts",
        "title": "Alertes & signaux",
        "emoji": "🔔",
        "summary": "Chaque badge sur une fiche correspond à une opportunité commerciale.",
        "blocks": [
            {
                "label": "Sans agence",
                "detail": "Annonce publiée par un particulier — vous pouvez proposer votre accompagnement sans intermédiaire.",
                "tone": "high",
            },
            {
                "label": "Nouveau détecté",
                "detail": "Fiche fraîchement crawlée (≤ 2 j). Le vendeur vient de se lancer : soyez parmi les premiers à le contacter.",
                "tone": "high",
            },
            {
                "label": "Baisse de prix",
                "detail": "Le vendeur a déjà ajusté son prix — signe de motivation ou d'urgence à vendre.",
                "tone": "urgent",
            },
            {
                "label": "Annonce ancienne (30 j+)",
                "detail": "Peu de visiteurs ou pas le bon prix ? Proposez une stratégie de remise en marché.",
                "tone": "medium",
            },
            {
                "label": "Sous marché DVF",
                "detail": "Prix/m² inférieur aux ventes récentes (Etalab). Argument chiffré pour justifier une estimation.",
                "tone": "high",
            },
        ],
        "tips": [
            "Combinez deux signaux dans votre accroche : ex. « sans agence + sous le marché DVF ».",
            "Les annonces avec agence restent utiles pour connaître le secteur, mais le mandat est plus difficile à grappiller.",
        ],
    },
    {
        "id": "dvf",
        "title": "Comparatif DVF",
        "emoji": "📊",
        "summary": "Compare le prix affiché aux ventes réelles enregistrées (Demandes de valeurs foncières).",
        "blocks": [
            {
                "label": "Sous le marché",
                "detail": "Le bien est affiché en dessous de la médiane locale — le vendeur sous-estime peut-être son bien, ou cherche un acheteur vite.",
                "tone": "high",
            },
            {
                "label": "Aligné sur le marché",
                "detail": "Prix cohérent avec les ventes récentes. Insistez sur la visibilité et la négociation plutôt que sur le prix.",
                "tone": "medium",
            },
            {
                "label": "Au-dessus du marché",
                "detail": "Prix élevé vs ventes DVF — expliquez le risque de stagnation et proposez une estimation objective.",
                "tone": "low",
            },
        ],
        "tips": [
            "Lancez « Comparatif DVF » sur vos ventes (pas les locations) avant vos sessions d'appels.",
            "Formule type : « D'après les ventes récentes du quartier, le marché tourne autour de X €/m²… »",
            "Source : données ouvertes Etalab / DGFiP — médiane sur les ventes récentes de la commune.",
        ],
    },
    {
        "id": "pipeline",
        "title": "Pipeline commercial",
        "emoji": "📋",
        "summary": "Faites avancer chaque prospect étape par étape pour ne rien perdre.",
        "blocks": [
            {"label": "Nouveau", "detail": "Prospect détecté, pas encore contacté.", "tone": "medium"},
            {"label": "À contacter", "detail": "Priorisé dans le radar — planifier l'appel.", "tone": "high"},
            {"label": "Contacté", "detail": "Premier échange fait — notez la date de relance.", "tone": "medium"},
            {"label": "RDV / Estimation", "detail": "Rendez-vous fixé — préparez le dossier DVF et les comparables.", "tone": "high"},
            {"label": "Mandat", "detail": "Signature en cours ou obtenue — créez le mandat dans l'onglet dédié.", "tone": "urgent"},
        ],
        "tips": [
            "Après chaque appel, mettez à jour le pipeline et ajoutez une note (objections, disponibilités).",
            "Relancez sous 48 h si le vendeur hésite — « sans engagement » rassure toujours.",
        ],
    },
    {
        "id": "best_practices",
        "title": "Bonnes pratiques d'appel",
        "emoji": "📞",
        "summary": "Structure d'un appel efficace en 4 temps.",
        "blocks": [
            {"label": "1. Accroche (15 s)", "detail": "Présentation + mention du bien (adresse ou quartier).", "tone": "medium"},
            {"label": "2. Observation (30 s)", "detail": "Signale concret : DVF, ancienneté, baisse, sans agence.", "tone": "high"},
            {"label": "3. Valeur (30 s)", "detail": "Résultats secteur, estimation gratuite, accompagnement complet.", "tone": "medium"},
            {"label": "4. Closing (15 s)", "detail": "Proposition de créneau : « Êtes-vous disponible mardi ou jeudi ? »", "tone": "urgent"},
        ],
        "tips": [
            "Évitez « je vous dérange ? » — préférez « je me permets de vous appeler concernant… »",
            "Préparez une réponse aux objections : « Je veux vendre seul » → visibilité + négociation + gain de temps.",
            "Envoyez un SMS de confirmation après un RDV accepté.",
        ],
    },
]


SCRIPT_TEMPLATES: dict[str, dict[str, Any]] = {
    "fort_potentiel": {
        "hook": "votre annonce cumule plusieurs signaux qui montrent un réel potentiel de vente",
        "value": "nous signons régulièrement des mandats dans ce secteur en moins de 60 jours",
        "closing": "Je peux passer demain ou après-demain pour une estimation offerte — qu'est-ce qui vous arrange ?",
        "objections": [
            ("Je n'ai pas besoin d'agence", "Justement, notre rôle est de maximiser le prix net vendeur et de filtrer les curieux — vous gardez la main sur la décision finale."),
            ("J'ai déjà eu des visites", "Parfait — on peut analyser pourquoi ça n'a pas converti et ajuster la stratégie (prix, photos, diffusion)."),
        ],
    },
    "dvf_sous_marche": {
        "hook": "votre prix au m² semble en retrait par rapport aux ventes récentes du quartier",
        "value": "une estimation professionnelle permet de calibrer le bon prix sans laisser d'argent sur la table",
        "closing": "Seriez-vous disponible cette semaine pour un rendez-vous d'estimation, sans engagement ?",
        "objections": [
            ("Mon prix est déjà bas", "C'est justement ce qui attire les acheteurs — vérifions ensemble que vous ne vendez pas en dessous du marché réel."),
            ("Je veux vendre vite", "On peut viser la rapidité sans brader — le bon prix attire les bonnes offres en moins de délais."),
        ],
    },
    "baisse_prix": {
        "hook": "j'ai vu que vous aviez ajusté votre prix récemment",
        "value": "c'est souvent le moment où un accompagnement pro accélère la vente et évite d'autres baisses",
        "closing": "Seriez-vous ouvert à un échange de 15 minutes sur la stratégie la plus efficace ?",
        "objections": [
            ("Je baisserai encore si besoin", "Avant une nouvelle baisse, une estimation objective évite de partir trop bas d'un coup."),
        ],
    },
    "nouveau_sans_agence": {
        "hook": "votre annonce vient d'être mise en ligne et vous la gérez en direct",
        "value": "beaucoup de vendeurs particuliers nous contactent dès le départ pour sécuriser la vente sans stress",
        "closing": "Puis-je vous proposer une estimation gratuite pour calibrer le bon prix dès le départ ?",
        "objections": [
            ("Je commence seul", "C'est très bien — une estimation offerte vous donne un repère marché sans engagement."),
        ],
    },
    "annonce_ancienne": {
        "hook": "votre bien est en ligne depuis un moment",
        "value": "nous aidons les vendeurs à relancer : photos, diffusion élargie, repositionnement prix",
        "closing": "Un diagnostic gratuit de votre annonce vous intéresserait-il cette semaine ?",
        "objections": [
            ("Pas pressé", "Parfait — justement, c'est le bon moment pour optimiser sans urgence."),
        ],
    },
    "sans_agence": {
        "hook": "vous publiez en direct sans passer par une agence",
        "value": "nous pouvons vous faire gagner du temps sur les visites, les diagnostics et la négociation",
        "closing": "Êtes-vous disponible pour une estimation sans engagement ?",
        "objections": [
            ("Les agences coûtent cher", "Notre honoraire est discuté en amont — l'objectif est le net vendeur, pas le prix affiché."),
        ],
    },
    "default": {
        "hook": "votre annonce a retenu notre attention dans le secteur",
        "value": "nous accompagnons des vendeurs avec de bons résultats sur des biens similaires",
        "closing": "Seriez-vous disponible cette semaine pour une estimation gratuite ?",
        "objections": [
            ("Pas intéressé", "Je comprends — puis-je vous envoyer une fourchette de marché par email, sans engagement ?"),
        ],
    },
}


_STREET_IN_ADDRESS_RE = re.compile(
    r"\b\d{1,4}\s+(?:rue|avenue|av\.?|bd|boulevard|chemin|impasse|route|allée|place|cours|quai)\b",
    re.IGNORECASE,
)


def _bold(text: str) -> str:
    t = (text or "").strip()
    return f"**{t}**" if t else ""


def _plain_script_text(text: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", text or "")


def _fmt_int_fr(n: int | float) -> str:
    return f"{int(n):,}".replace(",", " ")


def _guess_property_type_word(lead: dict) -> str:
    for src in (
        lead.get("listing_title"),
        lead.get("property_title"),
        lead.get("property"),
        lead.get("address"),
    ):
        s = str(src or "")
        m = re.search(
            r"\b(appartement|maison|studio|villa|loft|duplex|terrain|local|bureau|immeuble|parking)\b",
            s,
            re.I,
        )
        if m:
            return m.group(1).lower()
    try:
        surf = lead.get("surface")
        if surf is not None:
            sf = float(surf)
            if sf < 45:
                return "studio"
            if sf > 120:
                return "maison"
    except (TypeError, ValueError):
        pass
    return "bien"


def _is_city_only_address(addr: str, city: str, postcode: str) -> bool:
    a = (addr or "").strip()
    if not a or a in ("—", "-"):
        return True
    if _STREET_IN_ADDRESS_RE.search(a):
        return False
    c = (city or "").strip()
    pc = (postcode or "").strip()
    if c and re.match(rf"^{re.escape(c)}\b", a, re.I):
        return True
    if pc and pc in a:
        return True
    if re.match(r"^[A-Za-zÀ-ÿ\s\-']+\s*\(\d{5}\)\s*$", a):
        return True
    return False


def property_reference_for_script(lead: dict) -> str:
    """Libellé naturel du bien pour l'accroche (pas l'adresse brute type « Lorient (56100) »)."""
    from crawler.hub_detection import is_hub_listing_address, is_listing_title_name

    city = (lead.get("city") or lead.get("dvf_commune") or "").strip()
    postcode = (lead.get("postcode") or "").strip()
    ptype = _guess_property_type_word(lead)

    for key in ("property_title", "listing_title"):
        title = (lead.get(key) or "").strip()
        if (
            not title
            or title in ("—", "-")
            or is_hub_listing_address(title)
            or is_listing_title_name(title)
        ):
            continue
        if " · " in title:
            type_word, loc = title.split(" · ", 1)
            return f"votre {type_word.strip().lower()} à {loc.strip()}"
        if not _is_city_only_address(title, city, postcode):
            return f"votre annonce « {title[:80]} »"

    addr = (lead.get("address") or "").strip()
    if addr and not is_hub_listing_address(addr) and not _is_city_only_address(addr, city, postcode):
        return f"votre {ptype} situé {addr[:100]}"

    loc = city or "votre secteur"
    if city and postcode:
        loc = f"{city} ({postcode})"
    return f"votre {ptype} à {loc}"


def _natural_signal_phrase(lead: dict, scenario: str) -> str:
    """Observation en langage parlé — jamais la raison technique du score (tags · tags)."""
    tags = set(lead.get("alert_tags") or [])
    phrases: list[str] = []

    if "sans_agence" in tags:
        phrases.append("vous vendez en direct, sans passer par une agence")
    if "baisse_prix" in tags:
        phrases.append("vous avez récemment ajusté votre prix")
    elif "ancienne" in tags:
        days = lead.get("days_on_market")
        if days:
            phrases.append(f"votre annonce est en ligne depuis environ {days} jours")
        else:
            phrases.append("votre annonce est en ligne depuis un moment")
    elif "nouveau" in tags and "sans_agence" in tags:
        phrases.append("vous venez de publier votre annonce en particulier")
    elif "dvf_sous_marche" in tags and not lead.get("dvf_median_m2"):
        phrases.append("votre prix semble en retrait par rapport aux ventes récentes du secteur")

    if phrases:
        if len(phrases) == 1:
            return phrases[0]
        return f"{phrases[0]} et {phrases[1]}"

    tpl = SCRIPT_TEMPLATES.get(scenario, SCRIPT_TEMPLATES["default"])
    return tpl["hook"]


def _dvf_observation_sentence(lead: dict) -> str | None:
    """Phrase DVF correcte (écart annonce vs médiane, pas l'inverse)."""
    median = lead.get("dvf_median_m2")
    if not median:
        return None

    verdict = lead.get("dvf_verdict") or ""
    try:
        delta_f = float(lead.get("dvf_delta_pct")) if lead.get("dvf_delta_pct") is not None else None
    except (TypeError, ValueError):
        delta_f = None

    med_s = _fmt_int_fr(median)
    listing_m2 = None
    price, surface = lead.get("price"), lead.get("surface")
    try:
        if price and surface and float(surface) > 0:
            listing_m2 = round(float(price) / float(surface))
    except (TypeError, ValueError):
        listing_m2 = None

    if verdict in ("sous_marche", "leger_sous_marche") and delta_f is not None and delta_f < 0:
        pct = abs(round(delta_f))
        if listing_m2:
            return (
                f"D'après les ventes récentes du quartier ({_bold('données DVF')}), "
                f"la médiane locale est d'environ {_bold(f'{med_s} €/m²')}, "
                f"tandis que votre annonce est affichée à environ {_bold(f'{_fmt_int_fr(listing_m2)} €/m²')} "
                f"— soit environ {_bold(f'{pct} %')} en dessous de ce repère marché."
            )
        return (
            f"D'après les ventes récentes du quartier ({_bold('données DVF')}), "
            f"le marché tourne autour de {_bold(f'{med_s} €/m²')} ; "
            f"votre prix affiché semble environ {_bold(f'{pct} %')} en dessous de cette médiane."
        )

    if verdict in ("surmarche", "leger_surmarche") and delta_f is not None and delta_f > 0:
        pct = round(delta_f)
        return (
            f"Les ventes récentes ({_bold('DVF')}) situent la médiane à environ {_bold(f'{med_s} €/m²')} ; "
            f"votre annonce est environ {_bold(f'{pct} %')} au-dessus de ce niveau."
        )

    if verdict == "aligne":
        return (
            f"Les ventes récentes du quartier ({_bold('DVF')}) tournent autour de "
            f"{_bold(f'{med_s} €/m²')}, proche de votre prix affiché."
        )

    return None


def _build_observation(lead: dict, scenario: str, tpl: dict[str, Any]) -> str:
    city = (lead.get("city") or lead.get("dvf_commune") or "").strip()
    place = f"à {city}" if city else "sur votre secteur"
    signal = _natural_signal_phrase(lead, scenario)

    parts: list[str] = []
    if signal and signal != tpl.get("hook"):
        parts.append(f"En préparant mes appels {place}, j'ai repéré votre annonce : {signal}.")
    else:
        hook = (tpl.get("hook") or "").strip()
        if hook:
            parts.append(f"En préparant mes appels {place}, j'ai noté que {hook}.")

    dvf_line = _dvf_observation_sentence(lead)
    if dvf_line:
        parts.append(dvf_line)
    elif scenario == "dvf_sous_marche":
        hook = (tpl.get("hook") or "").strip()
        if hook:
            parts.append(hook[0].upper() + hook[1:] + ".")

    return " ".join(p for p in parts if p).strip()


def build_call_script(lead: dict) -> dict[str, Any]:
    """Script structuré + texte complet pour un prospect (markdown **gras**)."""
    scenario = detect_lead_scenario(lead)
    tpl = SCRIPT_TEMPLATES.get(scenario, SCRIPT_TEMPLATES["default"])
    caller = lead.get("_caller") or "votre conseiller"
    agency = lead.get("_agency") or "l'agence"
    city = lead.get("city") or lead.get("dvf_commune") or ""
    prop_ref = property_reference_for_script(lead)

    opening = (
        f"Bonjour, je suis {_bold(caller)} de {_bold(agency)}. "
        f"Je me permets de vous appeler au sujet de {_bold(prop_ref)}."
    )
    observation = _build_observation(lead, scenario, tpl)
    val = (tpl.get("value") or "").strip()
    value = f"Concrètement : **{val[0].upper()}{val[1:]}**." if val else ""
    closing = tpl["closing"]

    sections = [opening, observation, value, closing]
    full_text = "\n\n".join(sections)
    full_text_plain = "\n\n".join(_plain_script_text(s) for s in sections)
    advice = _advice_for_lead(lead, scenario)

    return {
        "scenario": scenario,
        "scenario_label": SCENARIO_LABELS.get(scenario, SCENARIO_LABELS["default"]),
        "property_reference": prop_ref,
        "opening": opening,
        "observation": observation,
        "value": value,
        "closing": closing,
        "objections": [{"q": q, "a": a} for q, a in tpl.get("objections", [])],
        "advice": advice,
        "full_text": full_text,
        "full_text_plain": full_text_plain,
        "city": city,
    }


def _advice_for_lead(lead: dict, scenario: str) -> list[str]:
    tips: list[str] = []
    if lead.get("phone") and lead.get("phone") != "—":
        tips.append("Téléphone disponible — privilégiez l'appel direct plutôt qu'un email.")
    else:
        tips.append("Pas de téléphone — utilisez l'email ou la messagerie du portail si disponible.")
    if lead.get("dvf_verdict") in ("sous_marche", "leger_sous_marche"):
        tips.append("Appuyez-vous sur le comparatif DVF : c'est un argument chiffré crédible.")
    if lead.get("days_on_market") and lead["days_on_market"] >= 30:
        tips.append(f"Annonce en ligne depuis {lead['days_on_market']} j — évoquez une remise en marché.")
    if lead.get("price_change_pct") and lead["price_change_pct"] < 0:
        tips.append(f"Baisse récente ({lead['price_change_pct']} %) — le vendeur est probablement négociable.")
    if scenario == "fort_potentiel":
        tips.append("Score mandat élevé — appelez en priorité, idéalement aujourd'hui.")
    if not tips:
        tips.append("Qualifiez le projet : délai de vente, bien occupé ou libre, travaux à prévoir.")
    return tips[:4]


def _playbook_script_templates_payload() -> dict[str, Any]:
    return {
        k: {
            **v,
            "label": SCENARIO_LABELS.get(k, k),
            "objections": [{"q": q, "a": a} for q, a in v.get("objections", [])],
        }
        for k, v in SCRIPT_TEMPLATES.items()
    }


def _playbook_counts(enriched: list[dict], particuliers: list[dict], opportunities: list) -> dict[str, int]:
    return {
        "total": len(enriched),
        "particuliers": len(particuliers),
        "with_script": len(opportunities),
        "hot": sum(1 for l in enriched if (l.get("mandate_score") or 0) >= 85),
        "dvf_sous_marche": sum(
            1 for l in enriched if "dvf_sous_marche" in (l.get("alert_tags") or [])
        ),
    }


def playbook_static_shell(
    agency_name: str = "",
    *,
    caller: str = "votre conseiller",
    target_cities: list[str] | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    """Guide + modèles toujours disponibles (même si le calcul prospect échoue)."""
    cities = [c.strip() for c in (target_cities or []) if c and str(c).strip()]
    payload: dict[str, Any] = {
        "agency_name": agency_name,
        "caller": caller,
        "date": date.today().isoformat(),
        "target_cities": cities,
        "filtered_by_cities": bool(cities),
        "guide": PLAYBOOK_GUIDE,
        "scenario_labels": SCENARIO_LABELS,
        "script_templates": _playbook_script_templates_payload(),
        "opportunities": [],
        "counts": {
            "total": 0,
            "particuliers": 0,
            "with_script": 0,
            "hot": 0,
            "dvf_sous_marche": 0,
        },
    }
    if partial:
        payload["_partial"] = True
    return payload


def build_playbook(
    leads: list[dict],
    agency_name: str = "",
    *,
    caller: str = "votre conseiller",
    target_cities: list[str] | None = None,
) -> dict[str, Any]:
    """Guide complet + opportunités avec scripts pour la page Conseils."""
    cities = [c.strip() for c in (target_cities or []) if c and str(c).strip()]
    filtered = leads
    if cities:
        filtered = [l for l in leads if _lead_matches_cities(l, cities)]

    enriched = [l for l in filtered if is_active_lead(l)]
    particuliers = [l for l in enriched if is_particulier_lead(l)]
    prioritized = sorted(particuliers, key=lambda x: (x.get("mandate_score") or 0), reverse=True)

    opportunities: list[dict[str, Any]] = []
    agency_label = agency_name or "l'agence"
    for lead in prioritized[:25]:
        try:
            script = build_call_script(
                {**lead, "_caller": caller, "_agency": agency_label}
            )
        except Exception:
            continue
        opportunities.append({
            "lead_id": lead.get("id"),
            "address": lead.get("address") or "—",
            "city": lead.get("city") or "",
            "price_label": format_price_short(lead),
            "mandate_score": lead.get("mandate_score") or 0,
            "mandate_score_reason": lead.get("mandate_score_reason") or "",
            "alert_tags": lead.get("alert_tags") or [],
            "dvf_verdict": lead.get("dvf_verdict"),
            "dvf_verdict_label": lead.get("dvf_verdict_label"),
            "dvf_delta_pct": lead.get("dvf_delta_pct"),
            "days_on_market": lead.get("days_on_market"),
            "scenario": script["scenario"],
            "scenario_label": script["scenario_label"],
            "advice": script["advice"],
            "script": script,
        })

    payload = playbook_static_shell(
        agency_name,
        caller=caller,
        target_cities=cities,
    )
    payload["opportunities"] = opportunities
    payload["counts"] = _playbook_counts(enriched, particuliers, opportunities)
    return payload
