"""Tri priorité mandat — partagé crawlers HTML et API."""

from __future__ import annotations

import unittest

from crawler.extractors import LeadData
from crawler.lead_priority import lead_importance_key


class LeadPriorityTests(unittest.TestCase):
    def test_particulier_before_agence(self):
        pro = LeadData(type="agence", phone="01 23 45 67 89", price=500_000, surface=50)
        part = LeadData(type="particulier", phone="01 23 45 67 89", price=500_000, surface=50)
        self.assertLess(lead_importance_key(part), lead_importance_key(pro))

    def test_contact_before_no_contact(self):
        with_contact = LeadData(type="particulier", phone="01 23 45 67 89", price=300_000, surface=60)
        bare = LeadData(type="particulier", price=300_000, surface=60)
        self.assertLess(lead_importance_key(with_contact), lead_importance_key(bare))


if __name__ == "__main__":
    unittest.main()
