"""Tests portail annonces — slug et demandes visiteurs."""

from __future__ import annotations

import pytest

from crm.portal.inquiry import submit_listing_inquiry
from crm.portal.slug import make_public_slug, slugify
from crm.portal.storage import create_listing, ensure_listing_public_slug, get_listing_by_slug


def test_slugify_ascii():
    assert slugify("Lyon — T3 Centre") == "lyon-t3-centre"


def test_make_public_slug_includes_id_suffix():
    item = {"city": "Paris", "title": "Appartement 3 pièces", "id": "abc123def456"}
    slug = make_public_slug(item)
    assert "paris" in slug
    assert "abc123de" in slug


@pytest.fixture
def published_listing(tmp_path, monkeypatch):
    monkeypatch.setenv("VELIORA_DB", str(tmp_path / "test.db"))
    from crawler.storage import init_db

    init_db()
    item = create_listing(
        {
            "title": "Maison familiale",
            "city": "Lorient",
            "price": 350000,
            "surface": 120,
            "status": "published",
            "transaction_type": "vente",
        },
        agency_id="ag-test",
        publisher_type="agency",
    )
    return item


def test_public_slug_and_inquiry(published_listing):
    lid = published_listing["id"]
    slug = ensure_listing_public_slug(lid)
    assert slug
    found = get_listing_by_slug(slug, public=True)
    assert found and found["id"] == lid

    out = submit_listing_inquiry(
        lid,
        {
            "kind": "contact_agency",
            "name": "Jean Dupont",
            "email": "jean@example.com",
            "phone": "0601020304",
        },
    )
    assert out.get("ok") is True

    from crm.portal.inquiry import agency_listing_inquiries

    res = agency_listing_inquiries("ag-test", lid)
    assert res.get("ok")
    assert len(res.get("inquiries") or []) >= 1
