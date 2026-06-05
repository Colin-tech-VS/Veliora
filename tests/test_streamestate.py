"""StreamEstate API → LeadData Veliora."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from crawler.extractors import LeadData
from crawler.streamestate import (
    build_query_params,
    lead_importance_key,
    property_to_lead,
    streamestate_configured,
)


SAMPLE_PROPERTY = {
    "@id": "/documents/properties/a2fe8869-bbd8-4d92-ad22-5ca5511a5bc7",
    "uuid": "38cb65b9-2965-4bd0-bc9b-2b8a8be7c457",
    "title": "Appartement 2 pieces",
    "transactionType": 0,
    "propertyType": 0,
    "surface": 30.0,
    "price": 1800000,
    "publisherTypes": [0],
    "createdAt": "2023-05-23T23:48:47+02:00",
    "city": {
        "name": "Paris 18e",
        "originalName": "Paris 18e",
        "zipcode": "75018",
        "insee": "75118",
        "department": {"code": "75", "name": "Paris"},
    },
    "adverts": [
        {
            "url": "https://www.century21.fr/trouver_logement/detail/2424196788/",
            "price": 1800000,
            "priceExcludingFees": 1800000,
            "surface": 30,
            "title": "Appartement 2 pieces",
            "createdAt": "2023-05-23T23:48:47+02:00",
            "contact": {
                "name": "Olivie Olivier",
                "phone": "0120304050",
                "email": "contact@agenceimmo2000.fr",
                "agency": "Agence Immo 2000",
            },
            "publisher": {"name": "Century21", "type": 1},
        }
    ],
}


class StreamEstateMappingTests(unittest.TestCase):
    def test_property_to_lead_maps_core_fields(self):
        lead = property_to_lead(SAMPLE_PROPERTY)
        self.assertIsNotNone(lead)
        assert lead is not None
        self.assertEqual(lead.source_url, "https://www.century21.fr/trouver_logement/detail/2424196788/")
        self.assertEqual(lead.transaction_type, "vente")
        self.assertEqual(lead.price, 1800000)
        self.assertEqual(lead.surface, 30.0)
        self.assertEqual(lead.city, "Paris 18e")
        self.assertEqual(lead.postcode, "75018")
        self.assertIn("20 30 40 50", lead.phone or "")
        self.assertEqual(lead.email, "contact@agenceimmo2000.fr")
        self.assertEqual(lead.raw_extras.get("data_provider"), "streamestate")

    def test_build_query_params_insee_filter(self):
        with patch("crawler.fr_communes.resolve_commune") as mock_resolve:
            mock_resolve.return_value = {"code": "75118", "postcode": "75018", "name": "Paris 18e"}
            params = build_query_params("Paris 18e", page=2)
        self.assertEqual(params["page"], 2)
        self.assertEqual(params["transactionType"], 0)
        self.assertEqual(params["includedInseeCodes[]"], "75118")

    def test_lead_importance_key_prioritizes_particulier(self):
        pro = LeadData(type="agence", phone="01 23 45 67 89", email="a@b.fr", price=500_000, surface=50)
        part = LeadData(type="particulier", phone="01 23 45 67 89", price=500_000, surface=50)
        self.assertLess(lead_importance_key(part), lead_importance_key(pro))

    def test_configured_when_key_set(self):
        with patch.dict(os.environ, {"STREAMESTATE_API_KEY": "test-key"}):
            self.assertTrue(streamestate_configured())


class StreamEstateFetchTests(unittest.TestCase):
    @patch("crawler.streamestate.requests.get")
    def test_iter_properties_pagination(self, mock_get):
        page1 = MagicMock()
        page1.status_code = 200
        page1.json.return_value = {
            "hydra:member": [{"uuid": "a", "adverts": [{"url": "https://exemple.fr/1"}]}],
            "hydra:view": {"hydra:next": "/documents/properties?page=2"},
        }
        page2 = MagicMock()
        page2.status_code = 200
        page2.json.return_value = {
            "hydra:member": [{"uuid": "b", "adverts": [{"url": "https://exemple.fr/2"}]}],
            "hydra:view": {},
        }
        mock_get.side_effect = [page1, page2]

        from crawler.streamestate import iter_properties

        with patch.dict(os.environ, {"STREAMESTATE_API_KEY": "k"}):
            docs = list(iter_properties(max_pages=5, max_listings=10))
        self.assertEqual(len(docs), 2)
        self.assertEqual(mock_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
