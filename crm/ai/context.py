"""Construit le contexte CRM passé en system prompt à l'assistant.

Objectif : donner à l'IA une vision complète mais compacte du portefeuille de
l'agence pour qu'elle puisse conseiller / analyser sans poser dix questions de
clarification. Format Markdown (lisible par les LLM, court à parser).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from crm.ai.config import (
    RECENT_ACTIVITY_LIMIT,
    TOP_CLIENTS_IN_CONTEXT,
    TOP_LEADS_IN_CONTEXT,
)
from crm.ai.storage import list_memories
from crawler.storage import (
    get_activities,
    get_agency_name,
    get_agency_settings,
    get_leads,
    get_stats,
)
from crm.mandates.storage import list_property_clients

logger = logging.getLogger(__name__)


def _fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _fmt_price(lead: dict) -> str:
    p = lead.get("price")
    if not p:
        return "prix inconnu"
    base = f"{_fmt_int(p)} €"
    if (lead.get("transaction_type") or "vente").lower() == "location":
        base += "/mois"
    return base


def _short_lead(lead: dict) -> str:
    bits = [f"#{lead.get('id')}"]
    title = lead.get("listing_title") or lead.get("address") or "annonce"
    bits.append(str(title)[:80])
    bits.append(_fmt_price(lead))
    if lead.get("surface"):
        bits.append(f"{lead.get('surface')} m²")
    if lead.get("city"):
        bits.append(str(lead.get("city")))
    pipeline = lead.get("pipeline") or lead.get("status") or "—"
    bits.append(f"pipeline={pipeline}")
    score = lead.get("mandate_score") or 0
    if score:
        bits.append(f"Score Mandat™ {score}/100")
    tags = lead.get("alert_tags") or []
    if tags:
        bits.append("tags=" + ",".join(tags[:4]))
    return " · ".join(bits)


def _lead_index_line(lead: dict) -> str:
    """Ligne ultra-compacte pour l'index complet des annonces (budget tokens maîtrisé)."""
    bits = [f"#{lead.get('id')}"]
    title = lead.get("listing_title") or lead.get("address") or "annonce"
    bits.append(str(title)[:48])
    if lead.get("city"):
        bits.append(str(lead.get("city")))
    bits.append(_fmt_price(lead))
    score = lead.get("mandate_score") or 0
    if score:
        bits.append(f"SM{score}")
    return " · ".join(bits)


# Plafond du nombre d'annonces décrites dans le prompt (garde le contexte borné
# même pour les gros portefeuilles, tout en couvrant largement les cas réels).
LEAD_INDEX_CAP = 150


def _short_client(c: dict) -> str:
    name = c.get("full_name") or " ".join(filter(None, [c.get("first_name"), c.get("last_name")])) or "Sans nom"
    seg = (c.get("segment") or "acheteur").lower()
    bits = [f"#{c.get('id')}", name, seg]
    if c.get("budget_min") or c.get("budget_max"):
        bits.append(
            f"budget {_fmt_int(c.get('budget_min'))}–{_fmt_int(c.get('budget_max'))} €"
        )
    if c.get("property_type"):
        bits.append(str(c.get("property_type")))
    if c.get("rooms_min"):
        bits.append(f"≥ {c.get('rooms_min')} p.")
    if c.get("surface_min"):
        bits.append(f"≥ {c.get('surface_min')} m²")
    cities = c.get("cities") or []
    if cities:
        bits.append("villes=" + ", ".join(str(x) for x in cities[:4]))
    status = c.get("status") or "actif"
    bits.append(f"statut={status}")
    return " · ".join(bits)


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def build_system_prompt(agency_id: str, *, user_first_name: str | None = None) -> str:
    """Assemble le system prompt complet pour l'agence donnée."""
    agency_name = get_agency_name(agency_id) or "Votre agence"
    try:
        stats = get_stats(agency_id)
    except Exception:
        stats = {}
    try:
        settings = get_agency_settings(agency_id) or {}
    except Exception:
        settings = {}
    try:
        leads = get_leads(agency_id)
    except Exception:
        leads = []
    try:
        clients = list_property_clients(agency_id)
    except Exception:
        clients = []
    try:
        activities = get_activities(agency_id, limit=RECENT_ACTIVITY_LIMIT)
    except Exception:
        activities = []
    try:
        memories = list_memories(agency_id, limit=20)
    except Exception:
        memories = []

    sorted_leads = sorted(
        leads,
        key=lambda l: (l.get("mandate_score") or 0),
        reverse=True,
    )
    top_leads = sorted_leads[:TOP_LEADS_IN_CONTEXT]
    actifs = [c for c in clients if (c.get("status") or "actif") == "actif"]
    top_clients = actifs[:TOP_CLIENTS_IN_CONTEXT]

    target_cities = settings.get("target_cities") or []

    pipeline_counts: dict[str, int] = {}
    for l in leads:
        key = (l.get("pipeline") or l.get("status") or "nouveau")
        pipeline_counts[key] = pipeline_counts.get(key, 0) + 1

    lines: list[str] = []
    lines.append("# Assistant IA Veliora — contexte agence")
    lines.append(
        f"Tu es l'assistant IA de l'agence immobilière **{agency_name}**. "
        f"Date du jour : {_today_iso()}. "
        f"L'utilisateur connecté est " + (f"**{user_first_name}**." if user_first_name else "un agent de cette agence.")
    )
    lines.append("")
    lines.append("## Mission")
    lines.append(
        "- Conseille, analyse, priorise les actions commerciales du jour.\n"
        "- Tu as la vision complète du portefeuille (annonces crawlées, acheteurs/locataires, pipeline).\n"
        "- Si on te demande de modifier une fiche, propose une action structurée en JSON "
        "uniquement dans le bloc ACTION_JSON en fin de message (jamais au milieu du texte).\n"
        "- Réponds en **français correct** (accents é à è ù ç œ, symbole €, signe ≥) — "
        "jamais de caractères cassés du type « analysÃ© » ou « â¬ ».\n"
        "- Ton professionnel et chaleureux ; va droit au but."
    )
    lines.append("")
    lines.append("## Mise en forme obligatoire (Markdown lisible dans l'UI)")
    lines.append(
        "Structure **toutes** tes réponses ainsi :\n"
        "1. Un titre `##` (ex. `## Correspondances annonces ↔ acheteurs`).\n"
        "2. Pour **chaque acheteur/locataire** : un sous-titre `### Prénom Nom` puis une ligne "
        "résumé en gras : budget, type, pièces, surface, villes.\n"
        "3. Sous chaque acheteur : liste à puces `-` avec les annonces pertinentes, une par ligne :\n"
        "   `- **#ID** · [titre court] · **ville** · **prix €** · courte note (dans/sous budget…)`\n"
        "   Utilise toujours le préfixe `#` devant l'id annonce (ex. `#60`) pour que l'interface crée un lien cliquable.\n"
        "4. Sépare les sections par une ligne `---` si besoin.\n"
        "5. Termine par `### Prochaine étape` : 1 à 3 actions concrètes en puces.\n"
        "6. **Ne jamais** afficher `ACTION_JSON`, du JSON brut ni de blocs ```json dans le corps "
        "visible — place-les seulement après tout le texte utilisateur, sur une ligne seule "
        "`ACTION_JSON` puis le bloc json (l'interface le masque et affiche des boutons).\n"
        "Utilise `**gras**` pour noms, prix et ids ; évite les pavés de texte."
    )
    lines.append("")
    lines.append("## Indicateurs clés")
    lines.append(
        f"- Annonces totales : {_fmt_int(stats.get('total'))} "
        f"(particuliers : {_fmt_int(stats.get('particuliers'))}, "
        f"sans agence : {_fmt_int(stats.get('sans_agence'))})"
    )
    lines.append(
        f"- Nouveaux à contacter : {_fmt_int(stats.get('nouveaux'))} · "
        f"Mandats en cours : {_fmt_int(stats.get('mandats'))}"
    )
    if pipeline_counts:
        pipeline_str = ", ".join(f"{k}={v}" for k, v in sorted(pipeline_counts.items(), key=lambda x: -x[1])[:6])
        lines.append(f"- Pipeline détaillé : {pipeline_str}")
    if target_cities:
        lines.append("- Villes cibles : " + ", ".join(target_cities[:8]))

    if top_leads:
        lines.append("")
        lines.append(f"## Top {len(top_leads)} annonces (par Score Mandat™ décroissant)")
        for lead in top_leads:
            lines.append("- " + _short_lead(lead))

    # Index complet (compact) du reste du portefeuille : l'IA doit pouvoir
    # répondre sur N'IMPORTE QUELLE annonce, pas seulement le top 15.
    remaining = sorted_leads[len(top_leads):LEAD_INDEX_CAP]
    if remaining:
        lines.append("")
        lines.append(
            f"## Index complet des annonces ({len(remaining)} autres — format compact)"
        )
        lines.append("Pour répondre sur n'importe quelle annonce : id · titre · ville · prix · score (SM).")
        for lead in remaining:
            lines.append("- " + _lead_index_line(lead))
        if len(sorted_leads) > LEAD_INDEX_CAP:
            lines.append(
                f"- … +{len(sorted_leads) - LEAD_INDEX_CAP} annonces non listées ici "
                "(demande-les par ville, prix ou Score Mandat™ et je les retrouve)."
            )

    if top_clients:
        lines.append("")
        lines.append(f"## Acheteurs / locataires actifs ({len(top_clients)} affichés)")
        for c in top_clients:
            lines.append("- " + _short_client(c))

    if activities:
        lines.append("")
        lines.append("## Activité récente (la mémoire courte)")
        for a in activities[:15]:
            text = a.get("text") or ""
            t = a.get("time") or ""
            tp = a.get("type") or ""
            lines.append(f"- [{tp}] {text} ({t})")

    if memories:
        lines.append("")
        lines.append("## Mémoire longue (faits que tu dois te rappeler)")
        for m in memories[:20]:
            lines.append(f"- {m.get('content')}")

    lines.append("")
    lines.append("## Format des actions modifiantes")
    lines.append(
        "Si tu proposes une action concrète (mettre à jour un prospect, ajouter une note, planifier "
        "une relance…), termine ta réponse par un bloc :\n\n"
        "ACTION_JSON ```json\n"
        '{"action": "update_pipeline", "lead_id": 123, "pipeline": "contacte", "note": "Premier appel OK"}\n'
        "```\n\n"
        "Actions reconnues côté UI : `update_pipeline`, `add_note`, `set_followup`, "
        "`remember`. L'agent valide chaque action d'un clic — toi tu te contentes "
        "de la suggérer.\n"
        "Valeurs `pipeline` acceptées (et uniquement celles-ci) : `nouveau`, "
        "`a_contacter`, `contacte`, `rdv`, `mandat`, `perdu`. N'invente jamais "
        "d'autre valeur."
    )
    return "\n".join(lines)


def trim_messages_for_model(messages: list[dict], max_messages: int) -> list[dict]:
    """Garde le system prompt en tête + les N derniers échanges."""
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    return system + others[-max_messages:]
