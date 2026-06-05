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
    streamestate_display_name,
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
        self.assertEqual(lead.type, "agence")
        self.assertIsNone(lead.address)
        self.assertEqual(lead.raw_extras.get("data_provider"), "streamestate")
        self.assertEqual(lead.raw_extras.get("listing_title"), "Appartement 2 pieces")

    def test_aggregates_fields_across_adverts_same_seller(self):
        # Même bien, même agence sur 2 portails : tél sur l'un, email + photos sur l'autre.
        doc = {
            "uuid": "u-agg",
            "transactionType": 0,
            "propertyType": 0,
            "surface": None,
            "price": None,
            "city": {"originalName": "Nantes", "zipcode": "44000"},
            "adverts": [
                {
                    "url": "https://portail-a.fr/1",
                    "surface": 72,
                    "priceExcludingFees": 285000,
                    "contact": {"name": "Paul Durand", "phone": "0240112233", "agency": "Immo Ouest"},
                    "publisher": {"name": "Immo Ouest", "type": 1},
                },
                {
                    "url": "https://portail-b.fr/2",
                    "contact": {"email": "contact@immo-ouest.fr", "agency": "Immo Ouest"},
                    "publisher": {"name": "Immo Ouest", "type": 1},
                    "pictures": ["https://img.fr/x.jpg"],
                },
            ],
        }
        lead = property_to_lead(doc)
        assert lead is not None
        # Téléphone (advert A) ET email (advert B) réunis sur la même fiche.
        self.assertIn("40 11 22 33", lead.phone or "")
        self.assertEqual(lead.email, "contact@immo-ouest.fr")
        self.assertEqual(lead.first_name, "Paul")
        # Surface + prix comblés depuis l'advert qui les porte (property vide).
        self.assertEqual(lead.surface, 72.0)
        self.assertEqual(lead.price, 285000)
        # Photo récupérée sur l'autre advert.
        self.assertEqual(lead.raw_extras.get("listing_image_url"), "https://img.fr/x.jpg")

    def test_does_not_mix_contacts_across_competing_agencies(self):
        # Deux agences DIFFÉRENTES : on ne mélange pas leurs contacts.
        doc = {
            "uuid": "u-mix",
            "transactionType": 0,
            "propertyType": 0,
            "city": {"originalName": "Nantes", "zipcode": "44000"},
            "adverts": [
                {
                    "url": "https://a.fr/1",
                    "contact": {"phone": "0240112233", "agency": "Agence A"},
                    "publisher": {"name": "Agence A", "type": 1},
                },
                {
                    "url": "https://b.fr/2",
                    "contact": {"email": "x@agence-b.fr", "agency": "Agence B"},
                    "publisher": {"name": "Agence B", "type": 1},
                },
            ],
        }
        lead = property_to_lead(doc)
        assert lead is not None
        # L'advert retenu (Agence A, avec tél) ne doit pas hériter de l'email d'Agence B.
        self.assertIn("40 11 22 33", lead.phone or "")
        self.assertIsNone(lead.email)

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

    def test_display_name_default(self):
        self.assertEqual(streamestate_display_name(), "Analyse approfondie")


class StreamEstateVerifyTests(unittest.TestCase):
    @patch("crawler.storage.is_streamestate_enabled_for_agency", return_value=False)
    @patch.dict(os.environ, {"STREAMESTATE_API_KEY": "test-key"})
    def test_verify_rejects_when_source_disabled(self, _enabled):
        from crawler.streamestate import StreamEstateError, verify_existing_leads

        with self.assertRaises(StreamEstateError) as ctx:
            verify_existing_leads("agency-test")
        self.assertIn("désactivé", str(ctx.exception).lower())

    def test_lead_needs_verification(self):
        from crawler.streamestate import lead_needs_verification

        # Champs clés présents → rien à vérifier
        complete = {"phone": "01 23 45 67 89", "surface": 30, "price": 200000, "address": "1 rue X"}
        self.assertFalse(lead_needs_verification(complete))
        # Aucun contact → à vérifier
        self.assertTrue(lead_needs_verification({"surface": 30, "price": 200000, "address": "1 rue X"}))
        # Surface manquante → à vérifier
        self.assertTrue(lead_needs_verification({"email": "a@b.fr", "price": 200000, "address": "1 rue X"}))
        # Prix manquant / adresse vide → à vérifier
        self.assertTrue(lead_needs_verification({"email": "a@b.fr", "surface": 30, "address": ""}))

    @patch("crawler.streamestate.fetch_properties_page")
    def test_verify_existing_leads_matches_by_url_and_amortizes_credits(self, mock_fetch):
        # Une seule page (1 crédit) couvre 2 annonces existantes de la même ville.
        mock_fetch.return_value = {
            "hydra:member": [SAMPLE_PROPERTY],
            "hydra:view": {},
        }
        leads_db = [
            {  # incomplète (pas de prix) → candidate, URL présente dans la page
                "id": 1,
                "status": "nouveau",
                "city": "Paris 18e",
                "postcode": "75018",
                "source_url": "https://www.century21.fr/trouver_logement/detail/2424196788",
                "source_id": "century21",
                "phone": "01 23 45 67 89",
                "surface": 30,
                "price": None,
                "address": "1 rue de Test",
            },
            {  # incomplète, même ville, URL absente de la page → not_found
                "id": 2,
                "status": "nouveau",
                "city": "Paris 18e",
                "postcode": "75018",
                "source_url": "https://www.seloger.com/annonces/999",
                "source_id": "seloger",
                "phone": None,
                "email": None,
                "surface": 40,
                "price": 300000,
                "address": "2 rue X",
            },
            {  # complète → ignorée (ne consomme aucun crédit)
                "id": 3,
                "status": "nouveau",
                "city": "Lyon",
                "postcode": "69003",
                "source_url": "https://exemple.fr/lyon/1",
                "phone": "04 11 22 33 44",
                "surface": 50,
                "price": 250000,
                "address": "3 rue Y",
            },
        ]
        saved_calls = []

        def fake_save_lead(lead, **kwargs):
            saved_calls.append(lead)
            return {"id": 1, "updated": True}

        from crawler import streamestate as se

        with patch("crawler.storage.get_leads", return_value=leads_db), patch(
            "crawler.storage.save_lead", side_effect=fake_save_lead
        ), patch("crawler.storage.is_streamestate_enabled_for_agency", return_value=True), patch.dict(
            os.environ, {"STREAMESTATE_API_KEY": "k"}
        ):
            summary = se.verify_existing_leads("agency-1")

        # Seule Paris 18e (2 candidates) est scannée : 1 crédit, 1 match, 1 not_found.
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["cities_scanned"], 1)
        self.assertEqual(summary["credits_used"], 1)
        self.assertEqual(summary["matched"], 1)
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary["not_found"], 1)
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(len(saved_calls), 1)

    @patch("crawler.streamestate.fetch_properties_page")
    def test_verify_respects_credit_budget(self, mock_fetch):
        mock_fetch.return_value = {"hydra:member": [], "hydra:view": {}}
        leads_db = [
            {"id": i, "status": "nouveau", "city": f"Ville{i}", "postcode": f"7500{i}",
             "source_url": f"https://exemple.fr/{i}", "phone": None, "email": None,
             "surface": None, "price": None, "address": ""}
            for i in range(1, 6)
        ]
        from crawler import streamestate as se

        with patch("crawler.storage.get_leads", return_value=leads_db), patch(
            "crawler.storage.save_lead", return_value={"id": 1}
        ), patch("crawler.storage.is_streamestate_enabled_for_agency", return_value=True), patch.dict(
            os.environ, {"STREAMESTATE_API_KEY": "k"}
        ):
            summary = se.verify_existing_leads("agency-1", max_pages=2, max_pages_per_city=1)

        # Budget de 2 crédits → au plus 2 villes scannées, le reste reporté.
        self.assertEqual(summary["credits_used"], 2)
        self.assertEqual(summary["cities_scanned"], 2)
        self.assertTrue(summary["budget_exhausted"])


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


class TestCrawlSkipStreamEstate(unittest.TestCase):
    def test_skip_env_disables_streamestate_for_agency(self):
        with patch.dict(os.environ, {"CRAWL_SKIP_STREAMESTATE": "true"}, clear=False):
            import importlib

            import crawler.config as cfg
            import crawler.storage as st

            importlib.reload(cfg)
            importlib.reload(st)
            self.assertTrue(cfg.CRAWL_SKIP_STREAMESTATE)
            self.assertFalse(st.is_streamestate_enabled_for_agency("any-agency"))


if __name__ == "__main__":
    unittest.main()
