"""Filtre commune INSEE (aligné API agrégée)."""

from crawler.commune_filter import crawl_commune_row, lead_matches_commune
from crawler.extractors import LeadData


def test_crawl_commune_row_nantes():
    row = crawl_commune_row("Nantes", "44000")
    assert row is not None
    assert row["code"] == "44109"
    assert row["postcode"] == "44000"


def test_lead_matches_same_insee_postcode():
    row = crawl_commune_row("Nantes", "44000")
    lead = LeadData(
        city="Nantes",
        postcode="44000",
        address="12 rue de la Paix, 44000 Nantes",
    )
    assert lead_matches_commune(lead, "Nantes", commune_row=row)


def test_lead_rejects_wrong_commune_postcode():
    row = crawl_commune_row("Nantes", "44000")
    lead = LeadData(
        city="Rennes",
        postcode="35000",
        address="10 rue de la Paix, 35000 Rennes",
    )
    assert not lead_matches_commune(lead, "Nantes", commune_row=row)


def test_lead_fuzzy_fallback_without_postcode():
    lead = LeadData(
        city="",
        postcode="",
        address="Appartement centre-ville Nantes",
    )
    assert lead_matches_commune(lead, "Nantes")
