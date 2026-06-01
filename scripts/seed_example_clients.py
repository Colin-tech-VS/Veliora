#!/usr/bin/env python3
"""Crée des profils acheteurs / locataires d'exemple (réalistes) pour une agence.

Usage (en local SQLite) :
    python scripts/seed_example_clients.py [agency_id]

Sur Scalingo (PostgreSQL / Supabase — DATABASE_URL déjà présent) :
    scalingo --app veliora run python scripts/seed_example_clients.py

Sans agency_id, cible l'agence qui a le plus d'annonces. Les profils utilisent
les vraies villes des annonces de l'agence pour que le matching soit visible.
Idempotent : un profil au même email n'est pas recréé.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.storage import get_connection
from crm.mandates.storage import (
    create_property_client,
    ensure_mandate_tables,
    list_property_clients,
)


def _resolve_agency(arg: str | None) -> str | None:
    if arg:
        return arg
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT agency_id, COUNT(*) AS n
            FROM leads
            WHERE agency_id IS NOT NULL AND agency_id != ''
            GROUP BY agency_id
            ORDER BY n DESC
            LIMIT 1
            """
        ).fetchone()
    if not row:
        return None
    return row["agency_id"] if not isinstance(row, (tuple, list)) else row[0]


def _agency_cities(agency_id: str) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT city FROM leads
            WHERE agency_id = ? AND city IS NOT NULL AND city != ''
            ORDER BY city
            """,
            (agency_id,),
        ).fetchall()
    out = []
    for r in rows:
        c = r["city"] if not isinstance(r, (tuple, list)) else r[0]
        if c:
            out.append(str(c))
    return out


def _profiles(cities: list[str]) -> list[dict]:
    # Replis si l'agence n'a pas (encore) de villes en base.
    c = cities or ["Lorient", "Lanester", "Hennebont", "Ploemeur"]

    def city(i):
        return c[i % len(c)]

    return [
        {
            "segment": "acheteur", "first_name": "Camille", "last_name": "Moreau",
            "phone": "0612345601", "email": "camille.moreau@exemple.fr",
            "budget_min": 180000, "budget_max": 250000, "property_type": "appartement",
            "rooms_min": 3, "surface_min": 60, "cities": [city(0)],
            "notes": "Primo-accédant, recherche T3 lumineux proche centre.",
        },
        {
            "segment": "acheteur", "first_name": "Thomas", "last_name": "Lefebvre",
            "phone": "0612345602", "email": "thomas.lefebvre@exemple.fr",
            "budget_min": 300000, "budget_max": 450000, "property_type": "maison",
            "rooms_min": 4, "surface_min": 90, "cities": [city(0), city(1)],
            "notes": "Famille, veut une maison avec jardin, budget flexible.",
        },
        {
            "segment": "acheteur", "first_name": "Sophie", "last_name": "Garnier",
            "phone": "0612345603", "email": "sophie.garnier@exemple.fr",
            "budget_min": 120000, "budget_max": 180000, "property_type": "appartement",
            "rooms_min": 2, "surface_min": 40, "cities": [city(1)],
            "notes": "Investisseuse locative, vise un T2 bien placé.",
        },
        {
            "segment": "acheteur", "first_name": "Julien", "last_name": "Roux",
            "phone": "0612345604", "email": "julien.roux@exemple.fr",
            "budget_min": 250000, "budget_max": 380000, "property_type": "maison",
            "rooms_min": 5, "surface_min": 100, "cities": [city(2)],
            "notes": "Recherche maison familiale, 4 chambres minimum.",
        },
        {
            "segment": "acheteur", "first_name": "Inès", "last_name": "Fontaine",
            "phone": "0612345605", "email": "ines.fontaine@exemple.fr",
            "budget_min": 200000, "budget_max": 300000, "property_type": "appartement",
            "rooms_min": 3, "surface_min": 65, "cities": [city(0), city(3)],
            "notes": "Couple, secteur recherché, prêt à se positionner vite.",
        },
        {
            "segment": "locataire", "first_name": "Lucas", "last_name": "Girard",
            "phone": "0612345606", "email": "lucas.girard@exemple.fr",
            "budget_min": 500, "budget_max": 750, "property_type": "appartement",
            "rooms_min": 2, "surface_min": 35, "cities": [city(0)],
            "notes": "Jeune actif, T2 meublé ou non, proche transports.",
        },
        {
            "segment": "locataire", "first_name": "Emma", "last_name": "Bonnet",
            "phone": "0612345607", "email": "emma.bonnet@exemple.fr",
            "budget_min": 700, "budget_max": 1000, "property_type": "appartement",
            "rooms_min": 3, "surface_min": 55, "cities": [city(1), city(0)],
            "notes": "Famille, T3 avec extérieur souhaité.",
        },
        {
            "segment": "locataire", "first_name": "Nathan", "last_name": "Mercier",
            "phone": "0612345608", "email": "nathan.mercier@exemple.fr",
            "budget_min": 900, "budget_max": 1300, "property_type": "maison",
            "rooms_min": 4, "surface_min": 80, "cities": [city(2)],
            "notes": "Mutation pro, maison en location longue durée.",
        },
    ]


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    agency_id = _resolve_agency(arg)
    if not agency_id:
        print("Aucune agence trouvée (pas d'annonces). Passez un agency_id en argument.")
        return 1

    with get_connection() as conn:
        ensure_mandate_tables(conn)

    cities = _agency_cities(agency_id)
    print(f"Agence : {agency_id}")
    print(f"Villes détectées : {', '.join(cities) if cities else '(aucune, replis par défaut)'}")

    existing_emails = {
        (c.get("email") or "").lower() for c in list_property_clients(agency_id)
    }
    created = 0
    for p in _profiles(cities):
        if p["email"].lower() in existing_emails:
            print(f"  = déjà présent : {p['first_name']} {p['last_name']} ({p['email']})")
            continue
        create_property_client(agency_id, p)
        created += 1
        print(f"  + {p['segment']:9} {p['first_name']} {p['last_name']} "
              f"— {p['budget_min']}/{p['budget_max']} — {', '.join(p['cities'])}")

    total = len(list_property_clients(agency_id))
    print(f"\n{created} profil(s) créé(s). Total acheteurs/locataires de l'agence : {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
