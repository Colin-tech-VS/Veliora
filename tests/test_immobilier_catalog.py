"""Catalogue réseaux / petites annonces."""

from crawler.immobilier_catalog import (
    CATALOG_BY_ID,
    _valid_catalog_agency_id,
    catalog_city_search_candidates,
    sync_immobilier_catalog_for_agency,
)


def test_new_network_sites_registered():
    for cid in (
        "nestenn",
        "ladresse",
        "citya",
        "megagence",
        "drhouse_immo",
        "moteurs_agence",
    ):
        site = CATALOG_BY_ID[cid]
        assert site.enabled
        assert site.search_url.startswith("http")


def test_new_classified_sites_registered():
    for cid in (
        "annoncesjaunes",
        "acheter_louer",
        "pro_a_part",
        "achat_terrain",
        "immoxia",
        "citadimmo",
        "refleximmo",
    ):
        site = CATALOG_BY_ID[cid]
        assert site.enabled
        assert site.search_url.startswith("http")


def test_bienveo_uses_fr_domain():
    site = CATALOG_BY_ID["bienveo"]
    assert "bienveo.fr" in site.base_url


def test_catalog_city_urls_nestenn():
    urls = catalog_city_search_candidates("nestenn", "", "Nantes", "44000")
    assert any("nestenn.com" in u and "nantes" in u.lower() for u in urls)


def test_catalog_sync_rejects_invalid_agency_id():
    assert _valid_catalog_agency_id(None) is None
    assert _valid_catalog_agency_id("") is None
    assert _valid_catalog_agency_id("__shared__") is None
    assert _valid_catalog_agency_id("None") is None
    assert _valid_catalog_agency_id("agence-abc") == "agence-abc"
    assert sync_immobilier_catalog_for_agency(None) == 0
