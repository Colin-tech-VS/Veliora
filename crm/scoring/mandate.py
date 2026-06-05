"""Score Mandat 0–100 — grille complète, explicable."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date

from crm.scoring.weights import apply_weight, merge_weights

# Signaux de motivation détectés dans le titre/description (texte normalisé, sans
# accents). Forts = vendeur sous contrainte (souvent prêt à mandater) ; flexibilité
# = ouverture explicite sur le prix.
_MOTIVATION_STRONG = (
    "succession", "heritage", "deces", "divorce", "separation",
    "mutation", "mute ", "muté", "cause depart", "doit vendre", "vente rapide",
    "urgent", "depart etranger", "demenagement", "demenage", "cause sante",
    "liquidation", "saisie", "depart retraite", "maison de retraite", "ehpad",
    "vente cause", "cause demenagement",
)
_MOTIVATION_FLEX = (
    "negociable", "a debattre", "faire offre", "prix en baisse", "prix revise",
    "baisse de prix", "offre bienvenue", "prix a negocier", "prix negocie",
)


def _normalize_text(text: str | None) -> str:
    """Minuscules + suppression des accents pour une détection robuste."""
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


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


def _has_phone(lead: dict) -> bool:
    p = lead.get("phone")
    return bool(p and str(p).strip() not in ("", "—"))


def _has_email(lead: dict) -> bool:
    e = lead.get("email")
    return bool(e and str(e).strip() not in ("", "—"))


@dataclass
class ScoreContribution:
    key: str
    label: str
    points: int
    detail: str = ""


@dataclass
class MandateScoreResult:
    score: int
    reason: str
    tags: list[str]
    positive: list[dict[str, str]] = field(default_factory=list)
    negative: list[dict[str, str]] = field(default_factory=list)
    contributions: list[ScoreContribution] = field(default_factory=list)
    capped_reason: str | None = None


def compute_mandate_score(
    lead: dict,
    *,
    weights: dict[str, float] | None = None,
) -> MandateScoreResult:
    w = merge_weights(weights)
    tags: list[str] = []
    contributions: list[ScoreContribution] = []
    positive: list[dict[str, str]] = []
    negative: list[dict[str, str]] = []

    is_particulier = lead.get("type") != "agence"
    if is_particulier:
        pts = apply_weight(28, "sans_agence", w)
        contributions.append(
            ScoreContribution("sans_agence", "Sans agence", pts, "Vendeur particulier")
        )
        tags.append("sans_agence")
        positive.append({"key": "sans_agence", "label": "Sans agence", "detail": "Vendeur particulier"})
    else:
        pts = apply_weight(-35, "malus_agence", w)
        contributions.append(
            ScoreContribution(
                "malus_agence",
                "Déjà en agence",
                pts,
                lead.get("agency") or "Annonce mandatée concurrent",
            )
        )
        tags.append("en_agence")
        negative.append({
            "key": "malus_agence",
            "label": "Déjà en agence",
            "detail": "Opportunité mandat limitée",
        })

    pub_days = days_since(lead.get("published_at") or lead.get("listedAt"))
    if pub_days is not None:
        if pub_days >= 60:
            pts = apply_weight(22, "ancienne_60", w)
            contributions.append(
                ScoreContribution(
                    "ancienne_60",
                    f"{pub_days} jours en ligne",
                    pts,
                    "Annonce installée — vendeur potentiellement ouvert",
                )
            )
            tags.append("ancienne")
            positive.append({
                "key": "days_online",
                "label": f"{pub_days} jours en ligne",
                "detail": "Ancienneté forte",
            })
        elif pub_days >= 45:
            pts = apply_weight(18, "ancienne_45", w)
            contributions.append(
                ScoreContribution("ancienne_45", f"{pub_days} j en ligne", pts, "")
            )
            tags.append("ancienne")
            positive.append({"key": "days_online", "label": f"{pub_days} jours en ligne", "detail": ""})
        elif pub_days >= 30:
            pts = apply_weight(12, "ancienne_30", w)
            contributions.append(
                ScoreContribution("ancienne_30", f"{pub_days} j en ligne", pts, "")
            )
            tags.append("ancienne")
        elif pub_days >= 15:
            pts = apply_weight(6, "ancienne_15", w)
            contributions.append(
                ScoreContribution("ancienne_15", f"{pub_days} j en ligne", pts, "")
            )

    drop_count = int(lead.get("price_change_count") or 0)
    prev = lead.get("previous_price")
    price = lead.get("price") or 0
    drop_pct = lead.get("last_price_drop_pct")
    if drop_pct is None and prev and price and prev > price:
        drop_pct = int((prev - price) / prev * 100)

    if drop_pct is not None and drop_pct >= 3:
        if drop_pct >= 10:
            base = 18
        elif drop_pct >= 5:
            base = 14
        else:
            base = 10
        pts = apply_weight(base, "baisse_prix", w)
        contributions.append(
            ScoreContribution(
                "baisse_prix",
                f"Baisse de prix −{drop_pct} %",
                pts,
                "Signal de flexibilité",
            )
        )
        tags.append("baisse_prix")
        positive.append({
            "key": "price_drop",
            "label": f"Prix baissé de {drop_pct} %",
            "detail": "Ajustement récent",
        })

    if drop_count >= 2:
        pts = apply_weight(8, "multi_baisse", w)
        contributions.append(
            ScoreContribution(
                "multi_baisse",
                f"{drop_count} baisses de prix",
                pts,
                "Pression vendeur",
            )
        )
        tags.append("multi_baisse")
        positive.append({
            "key": "multi_baisse",
            "label": f"{drop_count} baisses enregistrées",
            "detail": "Historique de flexibilité prix",
        })

    # Fraîcheur de la baisse : un ajustement de prix récent = vendeur actif MAINTENANT.
    has_drop = (drop_pct is not None and drop_pct >= 3) or drop_count >= 1
    drop_days = days_since(lead.get("last_price_change_at"))
    if has_drop and drop_days is not None:
        fresh = 6 if drop_days <= 7 else (4 if drop_days <= 14 else (2 if drop_days <= 30 else 0))
        if fresh:
            pts = apply_weight(fresh, "baisse_recente", w)
            contributions.append(
                ScoreContribution(
                    "baisse_recente",
                    f"Baisse il y a {drop_days} j",
                    pts,
                    "Ajustement récent — vendeur en mouvement",
                )
            )
            tags.append("baisse_recente")
            positive.append({
                "key": "baisse_recente",
                "label": f"Baisse de prix récente ({drop_days} j)",
                "detail": "Signal chaud : agir vite",
            })

    dvf_v = lead.get("dvf_verdict")
    if dvf_v == "sous_marche":
        pts = apply_weight(20, "dvf_sous", w)
        delta = lead.get("dvf_delta_pct") or 0
        contributions.append(
            ScoreContribution(
                "dvf_sous",
                "Sous marché DVF",
                pts,
                f"≈ {abs(int(delta))} % sous la médiane locale",
            )
        )
        tags.append("dvf_sous_marche")
        positive.append({
            "key": "dvf",
            "label": "Sous marché DVF",
            "detail": f"Environ {abs(int(delta))} % sous la médiane (Etalab)",
        })
    elif dvf_v == "leger_sous_marche":
        pts = apply_weight(12, "dvf_leger", w)
        contributions.append(
            ScoreContribution("dvf_leger", "Léger sous marché DVF", pts, "")
        )
        tags.append("dvf_sous_marche")
        positive.append({"key": "dvf", "label": "Léger sous marché DVF", "detail": ""})
    elif dvf_v == "marche":
        pts = apply_weight(4, "dvf_aligne", w)
        contributions.append(ScoreContribution("dvf_aligne", "Prix aligné marché", pts, ""))
    elif dvf_v == "sur_marche":
        delta = abs(int(lead.get("dvf_delta_pct") or 0))
        installed = pub_days is not None and pub_days >= 45
        if is_particulier and installed:
            # Particulier qui s'entête au-dessus du marché et ne vend pas : il finira
            # par confier un mandat. Pour la CHASSE AU MANDAT, c'est une cible prime.
            base = 16 if pub_days is not None and pub_days >= 90 else 12
            pts = apply_weight(base, "sureval_opportunite", w)
            contributions.append(
                ScoreContribution(
                    "sureval_opportunite",
                    "Surévalué et invendu",
                    pts,
                    f"Particulier ~{delta} % au-dessus du marché depuis {pub_days} j",
                )
            )
            tags.append("sureval_opportunite")
            positive.append({
                "key": "sureval",
                "label": "Surévalué et toujours en ligne",
                "detail": "Vendeur qui s'entête au-dessus du marché → futur mandat",
            })
        else:
            pts = apply_weight(-12, "malus_sur_marche", w)
            contributions.append(
                ScoreContribution("malus_sur_marche", "Sur marché DVF", pts, "Prix élevé vs secteur")
            )
            tags.append("dvf_sur_marche")
            negative.append({
                "key": "dvf_sur",
                "label": "Sur marché DVF",
                "detail": "Prix au-dessus des ventes récentes du secteur",
            })

    if _has_phone(lead):
        pts = apply_weight(7, "contact_phone", w)
        contributions.append(
            ScoreContribution("contact_phone", "Téléphone disponible", pts, "Contact direct")
        )
        positive.append({"key": "phone", "label": "Téléphone disponible", "detail": ""})
    if _has_email(lead):
        pts = apply_weight(3, "contact_email", w)
        contributions.append(
            ScoreContribution("contact_email", "Email disponible", pts, "")
        )

    if lead.get("transaction_type") == "vente":
        contributions.append(
            ScoreContribution("vente", "Vente", apply_weight(5, "vente", w), "")
        )

    surface_raw = lead.get("surface")
    try:
        surface = float(surface_raw) if surface_raw is not None else None
    except (TypeError, ValueError):
        surface = None
    if surface and 25 <= surface <= 200:
        contributions.append(
            ScoreContribution(
                "bien_cible",
                "Bien type mandat",
                apply_weight(5, "bien_cible", w),
                f"{int(surface)} m²",
            )
        )

    created = lead.get("created_at") or ""
    if created:
        # created_at peut être un datetime (Postgres) ou une chaîne : days_since
        # normalise déjà via str(iso)[:10], inutile (et risqué) de faire len()/slice ici.
        d_new = days_since(created)
        if d_new is not None and d_new <= 2:
            pts = apply_weight(12, "nouveau", w)
            contributions.append(
                ScoreContribution("nouveau", "Nouveau sur Veliora", pts, "Premier sur le radar")
            )
            tags.append("nouveau")
            positive.append({
                "key": "nouveau",
                "label": "Nouveau sur le radar",
                "detail": "Soyez parmi les premiers à contacter",
            })

    # Demande interne : acquéreurs / locataires compatibles déjà au portefeuille.
    # Un bien qui répond à une demande enregistrée est plus facile à rentrer/placer.
    demand = lead.get("demand_matches") or {}
    tx = (lead.get("transaction_type") or "vente").lower()
    relevant = int(demand.get("location" if tx == "location" else "vente") or 0)
    if relevant > 0:
        base = 6 if relevant == 1 else (10 if relevant == 2 else 14)
        if int(demand.get("strong") or 0) > 0:
            base += 3
        base = min(base, 16)
        pts = apply_weight(base, "demande", w)
        seg = "locataire" if tx == "location" else "acquéreur"
        label = f"{relevant} {seg}{'s' if relevant > 1 else ''} compatible{'s' if relevant > 1 else ''}"
        contributions.append(
            ScoreContribution("demande", label, pts, "Demande déjà en portefeuille")
        )
        tags.append("demande_interne")
        positive.append({
            "key": "demande",
            "label": label,
            "detail": "Acheteur/locataire en base — mise en relation rapide",
        })

    # Motivation détectée dans le texte (titre + description si dispo) — souvent le
    # signal le plus fort : un vendeur sous contrainte mandate plus facilement.
    motif_text = _normalize_text(
        f"{lead.get('description') or ''} {lead.get('listing_title') or lead.get('title') or ''}"
    )
    if motif_text.strip():
        strong_hits = [k.strip() for k in _MOTIVATION_STRONG if k in motif_text]
        flex_hits = [k for k in _MOTIVATION_FLEX if k in motif_text]
        base = (14 if strong_hits else 0) + (8 if flex_hits else 0)
        base = min(base, 16)
        if base:
            pts = apply_weight(base, "motivation_texte", w)
            hits = (strong_hits + flex_hits)[:3]
            detail = "Mots-clés : " + ", ".join(hits)
            contributions.append(
                ScoreContribution("motivation_texte", "Motivation vendeur (texte)", pts, detail)
            )
            tags.append("motivation_vendeur")
            positive.append({
                "key": "motivation",
                "label": "Signaux de motivation dans l'annonce",
                "detail": detail,
            })

    raw = sum(c.points for c in contributions)
    capped_reason: str | None = None
    has_contact = _has_phone(lead) or _has_email(lead)
    has_property = bool(
        (lead.get("address") and str(lead.get("address")) not in ("", "—"))
        and (lead.get("price") or lead.get("surface"))
    )
    if not has_contact and not has_property:
        if raw > 45:
            raw = 45
            capped_reason = "Données de contact insuffisantes — score plafonné"
            negative.append({
                "key": "cap_contact",
                "label": "Contact manquant",
                "detail": "Complétez téléphone ou adresse+prix pour monter le score",
            })

    score = max(0, min(100, raw))
    parts = [c.label for c in contributions if c.points > 0][:4]
    reason = " · ".join(parts) if parts else "Opportunité à qualifier"
    if capped_reason:
        reason = f"{reason} ({capped_reason})" if parts else capped_reason

    return MandateScoreResult(
        score=score,
        reason=reason[:240],
        tags=tags,
        positive=positive,
        negative=negative,
        contributions=contributions,
        capped_reason=capped_reason,
    )
