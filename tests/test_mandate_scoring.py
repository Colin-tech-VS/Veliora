"""Score Mandat — signaux ajoutés : motivation texte, fraîcheur baisse, surévalué PRIME."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from crm.scoring.mandate import compute_mandate_score


def _ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


def _keys(result) -> set[str]:
    return {c.key for c in result.contributions}


class MotivationTextTests(unittest.TestCase):
    def test_strong_keyword_in_title_adds_motivation(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 250_000, "transaction_type": "vente",
            "listing_title": "Maison à vendre cause mutation professionnelle",
        })
        self.assertIn("motivation_texte", _keys(r))
        self.assertIn("motivation_vendeur", r.tags)

    def test_no_keyword_no_motivation(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 250_000, "transaction_type": "vente",
            "listing_title": "Bel appartement T3 lumineux",
        })
        self.assertNotIn("motivation_texte", _keys(r))

    def test_accents_are_ignored(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 250_000, "transaction_type": "vente",
            "listing_title": "Cause décès, prix négociable",
        })
        self.assertIn("motivation_texte", _keys(r))

    def test_motivation_is_capped(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 250_000, "transaction_type": "vente",
            "listing_title": "Succession urgent divorce mutation, prix négociable à débattre",
        })
        motif = next(c for c in r.contributions if c.key == "motivation_texte")
        self.assertLessEqual(motif.points, 16)


class PriceDropFreshnessTests(unittest.TestCase):
    def test_recent_drop_adds_bonus(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 200_000, "previous_price": 220_000,
            "price_change_count": 1, "last_price_change_at": _ago(5),
            "transaction_type": "vente", "listing_title": "Maison",
        })
        self.assertIn("baisse_recente", _keys(r))

    def test_old_drop_no_freshness_bonus(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 200_000, "previous_price": 220_000,
            "price_change_count": 1, "last_price_change_at": _ago(120),
            "transaction_type": "vente", "listing_title": "Maison",
        })
        self.assertNotIn("baisse_recente", _keys(r))

    def test_no_drop_no_freshness_even_if_recent_change_date(self):
        r = compute_mandate_score({
            "type": "particulier", "price": 200_000, "last_price_change_at": _ago(3),
            "transaction_type": "vente", "listing_title": "Maison",
        })
        self.assertNotIn("baisse_recente", _keys(r))


class OverpricedFsboTests(unittest.TestCase):
    def test_particulier_overpriced_installed_is_bonus(self):
        r = compute_mandate_score({
            "type": "particulier", "published_at": _ago(95), "price": 300_000,
            "dvf_verdict": "sur_marche", "dvf_delta_pct": 18,
            "transaction_type": "vente", "listing_title": "Maison",
        })
        keys = _keys(r)
        self.assertIn("sureval_opportunite", keys)
        self.assertNotIn("malus_sur_marche", keys)
        bonus = next(c for c in r.contributions if c.key == "sureval_opportunite")
        self.assertGreater(bonus.points, 0)

    def test_agence_overpriced_keeps_malus(self):
        r = compute_mandate_score({
            "type": "agence", "agency": "X", "published_at": _ago(60), "price": 300_000,
            "dvf_verdict": "sur_marche", "dvf_delta_pct": 18,
            "transaction_type": "vente", "listing_title": "Maison",
        })
        keys = _keys(r)
        self.assertIn("malus_sur_marche", keys)
        self.assertNotIn("sureval_opportunite", keys)

    def test_particulier_overpriced_recent_keeps_malus(self):
        # Surévalué mais en ligne depuis peu : pas encore une cible mandat → malus.
        r = compute_mandate_score({
            "type": "particulier", "published_at": _ago(10), "price": 300_000,
            "dvf_verdict": "sur_marche", "dvf_delta_pct": 18,
            "transaction_type": "vente", "listing_title": "Maison",
        })
        keys = _keys(r)
        self.assertIn("malus_sur_marche", keys)
        self.assertNotIn("sureval_opportunite", keys)


if __name__ == "__main__":
    unittest.main()
