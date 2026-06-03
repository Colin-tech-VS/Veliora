"""Contrat API CRM — cohérence health / auth / portail / patch leads."""

from __future__ import annotations

import unittest

from crm.constants import API_VERSION, LEAD_PATCH_FIELDS


class CrmContractTests(unittest.TestCase):
    def test_api_version_constant(self):
        self.assertEqual(API_VERSION, 8)

    def test_health_endpoint(self):
        from app import app

        with app.test_client() as client:
            r = client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("api_version"), API_VERSION)
        self.assertTrue(data.get("clients"))
        self.assertTrue(data.get("mandates"))
        self.assertTrue(data.get("radar_analyze_url"))
        self.assertTrue(data.get("delete_leads"))

    def test_public_portal_listings_get(self):
        from app import app

        with app.test_client() as client:
            r = client.get("/api/public/portal/listings")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIsInstance(data.get("listings"), list)

    def test_geo_communes_public(self):
        from app import app

        with app.test_client() as client:
            r = client.get("/api/geo/communes?q=paris")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        communes = data if isinstance(data, list) else data.get("communes")
        self.assertIsInstance(communes, list)

    def test_bootstrap_requires_auth(self):
        from app import app

        with app.test_client() as client:
            r = client.get("/api/bootstrap")
        self.assertEqual(r.status_code, 401)

    def test_health_advertises_transactions(self):
        from app import app

        with app.test_client() as client:
            data = client.get("/api/health").get_json()
        self.assertTrue(data.get("transactions"))
        self.assertTrue(data.get("agents"))
        self.assertTrue(data.get("publish_requires_signed_mandate"))

    def test_transaction_endpoints_require_auth(self):
        from app import app

        with app.test_client() as client:
            for path in ("/api/transactions", "/api/commissions", "/api/agents"):
                self.assertEqual(client.get(path).status_code, 401, path)

    def test_transaction_stage_model(self):
        from crm.transactions.service import STAGES, PUBLISH_STAGE_KEY, derive_stage

        keys = [k for k, _l, _n in STAGES]
        self.assertEqual(keys[0], "prospect")
        self.assertEqual(keys[-1], "vendu")
        self.assertIn(PUBLISH_STAGE_KEY, keys)
        # Un mandat validé (2 parties) autorise la publication ; un brouillon non.
        self.assertEqual(
            derive_stage(assignment={"agent_id": "a"}, mandates=[{"status": "draft"}], listings=[], outcomes=set()),
            "mandat_cree",
        )
        self.assertEqual(
            derive_stage(
                assignment=None,
                mandates=[{"status": "signed", "owner_validated_at": "x", "agent_validated_at": "y"}],
                listings=[],
                outcomes=set(),
            ),
            "mandat_valide",
        )

    def test_patch_lead_fields_aligned_with_constants(self):
        import inspect

        from crawler.storage import patch_lead

        src = inspect.getsource(patch_lead)
        self.assertIn("LEAD_PATCH_FIELDS", src)
        self.assertIn("pipeline", LEAD_PATCH_FIELDS)
