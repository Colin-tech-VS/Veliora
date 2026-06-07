"""Tolérance au code postal embarqué « Ville (CP) » de l'autocomplete."""

from crawler.city_urls import (
    apply_city_to_search_url,
    city_search_url_candidates,
)
from crawler.commune_filter import crawl_commune_row
from crawler.fr_communes import (
    path_slug_for_city,
    resolve_commune,
    split_city_postcode,
)


def test_split_city_postcode():
    assert split_city_postcode("Lorient (56100)") == ("Lorient", "56100")
    assert split_city_postcode("Lorient") == ("Lorient", None)
    assert split_city_postcode("Lorient (56100), ") == ("Lorient", "56100")
    assert split_city_postcode("") == ("", None)
    assert split_city_postcode(None) == ("", None)


def test_resolve_commune_with_embedded_postcode():
    row = resolve_commune("Nantes (44000)")
    assert row is not None
    assert row["code"] == "44109"
    assert row["postcode"] == "44000"


def test_path_slug_strips_embedded_postcode():
    # Le slug doit rester « nantes-44 », pas « nantes-44000 ».
    assert path_slug_for_city("Nantes (44000)") == "nantes-44"


def test_crawl_commune_row_with_embedded_postcode():
    row = crawl_commune_row("Nantes (44000)")
    assert row is not None
    assert row["code"] == "44109"


def test_seloger_url_uses_dept_not_postcode():
    cands = city_search_url_candidates(
        "https://www.seloger.com/", "seloger", "Nantes (44000)"
    )
    assert cands
    assert cands[0] == "https://www.seloger.com/immobilier/achat/immo-nantes-44"
    # Aucune URL ne doit contenir le code postal complet ni le vieux list.htm.
    assert not any("44000" in u for u in cands)
    assert not any("list.htm" in u for u in cands)


def test_paruvendu_slug_has_no_postcode_artifact():
    url = apply_city_to_search_url(
        "https://www.paruvendu.fr/immobilier/", "paruvendu", "Nantes (44000)"
    )
    assert "ville=Nantes" in url
    assert "56100" not in url and "44000" not in url
