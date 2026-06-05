"""Recrawl : les mises à jour des leads du pool (agency_id NULL) doivent persister.

Régression : les UPDATE de save_lead étaient scopés par `agency_id = NULL`, ce qui
ne matche jamais en SQL, donc aucune correction de champ ne persistait au recrawl.
"""

from __future__ import annotations

import os
import tempfile
import unittest


class RecrawlPersistTests(unittest.TestCase):
    def setUp(self):
        os.environ["VELIORA_DB_PATH"] = tempfile.mktemp(suffix=".db")
        from crawler.storage import init_db

        init_db()

    def _save(self, **kwargs):
        from crawler.extractors import LeadData
        from crawler.storage import save_lead

        return save_lead(
            LeadData(**kwargs),
            source_id="test",
            agency_id="ag-1",
            require_verification=False,
        )

    def test_recrawl_fills_missing_fields_for_pool_lead(self):
        from crawler.storage import get_lead_by_source_url

        url = "https://exemple.fr/annonce/1"
        # 1er crawl : fiche incomplète (ni contact, ni surface, ni prix).
        self._save(
            source_url=url,
            source="Test",
            city="Paris",
            postcode="75001",
            transaction_type="vente",
            type="particulier",
        )
        before = get_lead_by_source_url(url, None)
        self.assertIn(before.get("phone"), (None, "", "—"))
        self.assertFalse(before.get("price"))

        # Recrawl : données complètes → doivent remplir les champs manquants.
        self._save(
            source_url=url,
            source="Test",
            city="Paris",
            postcode="75001",
            phone="06 51 23 89 47",
            email="a@b.fr",
            surface=42,
            price=240000,
            transaction_type="vente",
            type="particulier",
        )
        after = get_lead_by_source_url(url, None)
        self.assertEqual(after.get("price"), 240000)
        self.assertEqual(after.get("surface"), 42)
        self.assertIn("51 23 89 47", after.get("phone") or "")
        self.assertEqual(after.get("email"), "a@b.fr")

    def test_recrawl_price_change_does_not_crash_for_pool_lead(self):
        from crawler.storage import get_lead_by_source_url

        url = "https://exemple.fr/annonce/2"
        self._save(
            source_url=url, source="Test", city="Lyon", postcode="69001",
            phone="04 11 22 33 44", surface=50, price=0, transaction_type="vente",
            type="particulier",
        )
        # Passage d'un prix invalide (0) à un prix valide → historise sans planter.
        saved = self._save(
            source_url=url, source="Test", city="Lyon", postcode="69001",
            phone="04 11 22 33 44", surface=50, price=300000, transaction_type="vente",
            type="particulier",
        )
        self.assertTrue(saved and saved.get("id"))
        after = get_lead_by_source_url(url, None)
        self.assertEqual(after.get("price"), 300000)


if __name__ == "__main__":
    unittest.main()
