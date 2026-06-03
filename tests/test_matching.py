"""Tests du rapprochement annonces ↔ acheteurs/locataires."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crm.matching.service import (  # noqa: E402
    build_client_matches,
    build_lead_matches,
    eligible_clients_for_lead,
    score_client_for_lead,
)


def _client(**kw):
    base = {
        "id": "c1",
        "segment": "acheteur",
        "status": "actif",
        "full_name": "Jean Dupont",
        "cities": ["Lyon"],
        "budget_min": 200_000,
        "budget_max": 280_000,
        "property_type": "appartement",
        "surface_min": 60,
        "rooms_min": 3,
    }
    base.update(kw)
    return base


def _lead(**kw):
    base = {
        "id": 1,
        "transaction_type": "vente",
        "city": "Lyon",
        "postcode": "69003",
        "price": 245_000,
        "surface": 72,
        "listing_title": "Appartement T3 Lyon 3e",
        "status": "nouveau",
    }
    base.update(kw)
    return base


class MatchingTests(unittest.TestCase):
    def test_city_name_match(self):
        s = score_client_for_lead(_lead(city="Lyon 3e"), _client(cities=["Lyon"]))
        self.assertIsNotNone(s)
        self.assertGreaterEqual(s["score"], 45)

    def test_postcode_prefix_match(self):
        s = score_client_for_lead(
            _lead(city="", postcode="69007"),
            _client(cities=["69003"]),
        )
        self.assertIsNotNone(s)

    def test_department_token_match(self):
        s = score_client_for_lead(
            _lead(city="", postcode="69007"),
            _client(cities=["69"]),
        )
        self.assertIsNotNone(s)

    def test_address_extracts_city_and_cp(self):
        s = score_client_for_lead(
            {
                "id": 2,
                "transaction_type": "vente",
                "address": "12 rue de la République 69003 Lyon",
                "price": 250_000,
                "surface": 65,
                "listing_title": "T3 centre",
            },
            _client(cities=["Lyon"]),
        )
        self.assertIsNotNone(s)
        self.assertGreaterEqual(s["score"], 45)

    def test_wrong_city_eliminated(self):
        s = score_client_for_lead(_lead(city="Marseille"), _client(cities=["Lyon"]))
        self.assertIsNone(s)

    def test_acheteur_ignores_location_leads_in_client_view(self):
        r = build_client_matches(
            _client(segment="acheteur"),
            [_lead(transaction_type="location", price=900)],
        )
        self.assertEqual(r["counts"]["total"], 0)
        self.assertIn("hints", r.get("diagnostics", {}))

    def test_build_lead_matches_finds_buyer(self):
        r = build_lead_matches(_lead(), [_client()])
        self.assertGreater(r["counts"]["total"], 0)
        self.assertTrue(r["top_matches"])

    def test_locataire_ignored_on_vente_lead(self):
        s = score_client_for_lead(_lead(transaction_type="vente"), _client(segment="locataire"))
        self.assertIsNone(s)

    def test_eligible_clients_for_lead_filters_segment(self):
        rows = eligible_clients_for_lead(
            _lead(transaction_type="vente"),
            [_client(), _client(id="c2", segment="locataire", cities=["Lyon"], budget_max=900)],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_id"], "c1")

    def test_surface_unknown_not_eliminated(self):
        s = score_client_for_lead(_lead(surface=None), _client(surface_min=80))
        self.assertIsNotNone(s)

    def test_surface_too_small_eliminated(self):
        s = score_client_for_lead(_lead(surface=50), _client(surface_min=80))
        self.assertIsNone(s)

    def test_locataire_matches_rent(self):
        r = build_client_matches(
            _client(
                segment="locataire",
                cities=["Paris"],
                budget_max=1200,
                surface_min=None,
                rooms_min=None,
            ),
            [
                {
                    "id": 3,
                    "transaction_type": "location",
                    "city": "Paris",
                    "postcode": "75011",
                    "price": 1100,
                    "surface": 45,
                    "listing_title": "Studio Paris 11",
                }
            ],
        )
        self.assertGreaterEqual(r["counts"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
