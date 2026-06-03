"""Garantie : le champ `address` est toujours une voie (jamais vide ni ville seule)."""

from __future__ import annotations

import unittest

from crawler.address_quality import (
    ensure_street_address_from_data,
    has_approximate_address_marker,
    is_city_only_address,
    is_street_level_address,
    pick_best_address,
    real_street_or_none,
    synthesize_approx_street,
)


class _Lead:
    def __init__(self, **kw):
        self.source_url = kw.get("source_url")
        self.city = kw.get("city")
        self.postcode = kw.get("postcode")
        self.address = kw.get("address")
        self.latitude = kw.get("latitude")
        self.longitude = kw.get("longitude")


class AddressGuaranteeTests(unittest.TestCase):
    def test_synthesize_is_street_level_and_marked(self):
        addr = synthesize_approx_street("https://leboncoin.fr/ad/123")
        self.assertTrue(is_street_level_address(addr, "Lorient", "56100"))
        self.assertFalse(is_city_only_address(addr, "Lorient", "56100"))
        self.assertTrue(has_approximate_address_marker(addr))

    def test_synthesize_is_deterministic(self):
        self.assertEqual(
            synthesize_approx_street("seed-x"), synthesize_approx_street("seed-x")
        )
        self.assertNotEqual(
            synthesize_approx_street("seed-x"), synthesize_approx_street("seed-y")
        )

    def test_real_street_or_none_drops_approx(self):
        self.assertIsNone(real_street_or_none(synthesize_approx_street("z")))
        self.assertEqual(real_street_or_none("12 rue de la Paix"), "12 rue de la Paix")

    def test_ensure_never_empty_without_geo(self):
        # Sans ville/CP/coords : aucune voie réelle possible -> repli synthétique.
        lead = _Lead(source_url="https://x.fr/ad/77")
        found_real = ensure_street_address_from_data(lead, run_full_match=False)
        self.assertFalse(found_real)
        self.assertTrue(lead.address)
        self.assertTrue(is_street_level_address(lead.address))
        self.assertFalse(is_city_only_address(lead.address))

    def test_ensure_keeps_existing_real_street(self):
        lead = _Lead(source_url="https://x.fr/ad/1", address="8 rue de la Ronce", city="Lyon")
        ensure_street_address_from_data(lead, run_full_match=False)
        self.assertEqual(lead.address, "8 rue de la Ronce")

    def test_pick_best_prefers_real_over_approx(self):
        approx = synthesize_approx_street("long-seed-to-make-it-lengthy")
        real = "5 rue Neuve"
        # Même quand le repli est plus long, la vraie voie l'emporte.
        self.assertEqual(
            pick_best_address(real, approx, fresh_city="Brest", existing_city="Brest"),
            real,
        )
        self.assertEqual(
            pick_best_address(approx, real, fresh_city="Brest", existing_city="Brest"),
            real,
        )


if __name__ == "__main__":
    unittest.main()
