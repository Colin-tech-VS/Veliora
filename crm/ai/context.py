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
    """Prospect = annonce crawlée (vendeur / propriétaire à mandater) — pas un acheteur."""
    lid = lead.get("id")
    bits = [f"PROSPECT annonce #{lid}"]
    owner = (lead.get("owner") or "").strip()
    if owner and owner != "—":
        bits.append(f"vendeur={owner[:60]}")
    lead_type = (lead.get("type") or "").strip()
    if lead_type:
        bits.append(f"type={lead_type}")
    title = lead.get("listing_title") or lead.get("address") or "bien"
    bits.append(str(title)[:80])
    bits.append(_fmt_price(lead))
    if lead.get("surface"):
        bits.append(f"{lead.get('surface')} m²")
    if lead.get("city"):
        bits.append(str(lead.get("city")))
    pipeline = lead.get("pipeline") or lead.get("status") or "—"
    bits.append(f"pipeline_prospect={pipeline}")
    score = lead.get("mandate_score") or 0
    if score:
        bits.append(f"Score Mandat™ {score}/100")
    tags = lead.get("alert_tags") or []
    if tags:
        bits.append("tags=" + ",".join(tags[:4]))
    return " · ".join(bits)


def _lead_index_line(lead: dict) -> str:
    """Ligne ultra-compacte pour l'index complet des annonces (budget tokens maîtrisé)."""
    bits = [f"PROSPECT #{lead.get('id')}"]
    title = lead.get("listing_title") or lead.get("address") or "bien"
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
    """Acheteur/locataire = fiche Clients (recherche d'achat/location) — pas une annonce."""
    name = c.get("full_name") or " ".join(filter(None, [c.get("first_name"), c.get("last_name")])) or "Sans nom"
    seg = (c.get("segment") or "acheteur").lower()
    cid = str(c.get("id") or "")
    short_ref = cid[:8] if len(cid) >= 8 else cid
    bits = [f"CLIENT {seg}", f"ref={short_ref}", name]
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
        "- Tu as la vision du portefeuille : **prospects/annonces** (veille crawl) et **clients acheteurs/locataires** (recherche).\n"
        "- Si on te demande de modifier une fiche **prospect**, propose une action structurée en JSON "
        "uniquement dans le bloc ACTION_JSON en fin de message (jamais au milieu du texte).\n"
        "- Réponds en **français correct** (accents é à è ù ç œ, symbole €, signe ≥) — "
        "jamais de caractères cassés du type « analysÃ© » ou « â¬ ».\n"
        "- Ton professionnel et chaleureux ; va droit au but."
    )
    lines.append("")
    lines.append("## Lexique Veliora — ne jamais confondre")
    lines.append(
        "| Concept CRM | Qui c'est | Où dans les données | Identifiant dans tes réponses |\n"
        "|---|---|---|---|\n"
        "| **Prospect** / **annonce** | Vendeur ou propriétaire d'un bien **à mandater** (particulier, PAP, portail…) | Sections « PROSPECT » / « annonces » ci-dessous | **`#123`** (nombre seul) → lien fiche prospect |\n"
        "| **Client acheteur** | Personne qui **cherche à acheter** | Section « CLIENT acheteur » | **Pas de `#id`** — nom + budget ; ref interne `ref=…` si besoin |\n"
        "| **Client locataire** | Personne qui **cherche à louer** | Section « CLIENT locataire » | **Pas de `#id`** — nom + loyer max ; ref interne `ref=…` |\n"
        "\n"
        "Règles strictes :\n"
        "- Le **pipeline** (`nouveau`, `a_contacter`, `mandat`…) s'applique **uniquement aux prospects/annonces**, jamais aux clients acheteurs/locataires.\n"
        "- Le **Score Mandat™** et la **veille portails** concernent **uniquement les prospects**.\n"
        "- Le **budget min/max**, **segment acheteur/locataire** et **villes recherchées** concernent **uniquement les clients**, pas les prospects.\n"
        "- Ne dis jamais qu'un **prospect** est un acheteur, ni qu'un **client** est une annonce crawlée.\n"
        "- Le champ `owner` / vendeur d'une annonce = **propriétaire à démarcher**, pas un client acheteur.\n"
        "- Pour rapprocher : tu proposes des **annonces (#id)** qui matchent un **client (acheteur/locataire)** — deux mondes distincts."
    )
    lines.append("")
    lines.append("## Mise en forme obligatoire (Markdown lisible dans l'UI)")
    lines.append(
        "Structure **toutes** tes réponses ainsi :\n"
        "1. Un titre `##` (ex. `## Annonces à proposer aux acheteurs`).\n"
        "2. Pour un **matching** : sous-titre `### Client — Prénom Nom (acheteur ou locataire)` puis budget, critères.\n"
        "3. Sous ce client : puces avec **annonces prospects** uniquement :\n"
        "   `- **#60** · [titre] · **ville** · **prix €** · note (dans budget, surface OK…)`\n"
        "   Le préfixe **`#` + nombre** = id **prospect/annonce** uniquement (lien CRM).\n"
        "4. Pour parler **d'un prospect seul** : `### Prospect #60 — [titre]` (pipeline, vendeur, Score Mandat™).\n"
        "5. Sépare les sections par `---` si besoin.\n"
        "6. Termine par `### Prochaine étape` : 1 à 3 actions concrètes en puces.\n"
        "7. **Ne jamais** afficher `ACTION_JSON`, du JSON brut ni de blocs ```json dans le corps visible.\n"
        "Utilise `**gras**` pour noms, prix et ids prospect ; évite les pavés de texte.\n"
        "\n"
        "### Lister les prospects (liste complète, priorités, briefing…)\n"
        f"- Le portefeuille compte **{len(sorted_leads)} prospects** — ne dis jamais « top 15 » ni « j'en affiche 15 ».\n"
        "- Dans le chat : **maximum 10 lignes** `#id` (les plus prioritaires selon Score Mandat™ / pipeline).\n"
        "- Ensuite une phrase du type « … et **{max(0, len(sorted_leads) - 10)} autres** dans Prospects ».\n"
        "- Puis **obligatoirement** la ligne seule `[[VOIR_TOUT_PROSPECTS]]` (l'interface affiche le bouton Voir tout).\n"
        "- Ne liste pas 50 puces : l'agent utilisera le bouton pour le tableau complet."
    )
    lines.append("")
    lines.append("## Indicateurs clés (prospects / annonces crawlées)")
    lines.append(
        f"- Prospects (annonces) totaux : {_fmt_int(stats.get('total'))} "
        f"(particuliers : {_fmt_int(stats.get('particuliers'))}, "
        f"sans agence : {_fmt_int(stats.get('sans_agence'))})"
    )
    lines.append(
        f"- Prospects nouveaux à contacter : {_fmt_int(stats.get('nouveaux'))} · "
        f"Prospects en mandat : {_fmt_int(stats.get('mandats'))}"
    )
    if actifs:
        lines.append(f"- Clients acheteurs/locataires actifs : {len(actifs)}")
    if pipeline_counts:
        pipeline_str = ", ".join(f"{k}={v}" for k, v in sorted(pipeline_counts.items(), key=lambda x: -x[1])[:6])
        lines.append(f"- Pipeline détaillé : {pipeline_str}")
    if target_cities:
        lines.append("- Villes cibles : " + ", ".join(target_cities[:8]))

    if top_leads:
        lines.append("")
        lines.append(
            f"## Top {len(top_leads)} prospects / annonces (Score Mandat™ — vendeurs à mandater)"
        )
        for lead in top_leads:
            lines.append("- " + _short_lead(lead))

    # Index compact : toutes les fiches (pas de « top 15 » dans le prompt).
    index_leads = sorted_leads[len(top_leads) : LEAD_INDEX_CAP]
    if index_leads:
        lines.append("")
        lines.append(
            f"## Répertoire prospects ({len(sorted_leads)} au total — index compact, pas un extrait « top »)"
        )
        lines.append(
            "Chaque ligne = **prospect** crawlé (pas un client acheteur). "
            "Référence : #id · titre · ville · prix · SM. Pour une liste dans le chat : max 10 lignes + `[[VOIR_TOUT_PROSPECTS]]`."
        )
        for lead in index_leads:
            lines.append("- " + _lead_index_line(lead))
        if len(sorted_leads) > LEAD_INDEX_CAP:
            lines.append(
                f"- … +{len(sorted_leads) - LEAD_INDEX_CAP} prospects non indexés ici "
                "(filtre par ville, prix ou Score Mandat™)."
            )

    if top_clients:
        lines.append("")
        lines.append(
            f"## Clients — acheteurs & locataires ({len(top_clients)} affichés, hors veille)"
        )
        lines.append(
            "Personnes en recherche d'un bien. **Ne pas** confondre avec les prospects/annonces ci-dessus."
        )
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
    lines.append("## Format des actions modifiantes (prospects uniquement)")
    lines.append(
        "Les actions JSON ne modifient **que les fiches prospect/annonce** (`lead_id` entier), "
        "**jamais** un client acheteur/locataire (UUID, module Clients).\n"
        "Si on te demande de modifier un **client** acheteur/locataire : explique que ce n'est pas "
        "automatisé via l'assistant — l'agent doit le faire dans le menu **Clients**.\n\n"
        "Pour un **prospect**, termine par :\n\n"
        "ACTION_JSON ```json\n"
        '{"action": "update_pipeline", "lead_id": 123, "pipeline": "contacte", "note": "Premier appel OK"}\n'
        "```\n\n"
        "Actions reconnues : `update_pipeline`, `add_note`, `set_followup`, `remember`.\n"
        "**Obligatoire** pour `update_pipeline`, `add_note`, `set_followup` : champ entier `lead_id` "
        "(même id que `#123` dans ton texte). Sans `lead_id`, le bouton CTA échoue.\n"
        "Si une seule fiche est concernée dans le message, reprends son `#id` dans `lead_id`.\n"
        "Pipeline prospect uniquement : `nouveau`, `a_contacter`, `contacte`, `rdv`, `mandat`, `perdu`."
    )
    return "\n".join(lines)


def trim_messages_for_model(messages: list[dict], max_messages: int) -> list[dict]:
    """Garde le system prompt en tête + les N derniers échanges."""
    system = [m for m in messages if m.get("role") == "system"]
    others = [m for m in messages if m.get("role") != "system"]
    return system + others[-max_messages:]
