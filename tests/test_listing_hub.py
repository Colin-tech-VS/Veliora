"""Pages liste / mix annonces — Belles Demeures et garde-fous transverses."""

from crawler.extractors import LeadData, extract_listing_price, is_excluded_listing_url
from crawler.hub_detection import (
    is_hub_page_title,
    is_multi_listing_html_page,
    is_taxonomy_or_list_hub_url,
)
from crawler.listing_guard import (
    filter_listing_urls,
    validate_listing_coherence_crawl,
    validate_listing_url,
)

BD_LIST_URL = (
    "https://www.bellesdemeures.com/vente/france/ile-de-france/paris/"
    "paris-16eme/appartement-luxe/tt-2-tb-1-pl-32596/"
)
BD_DETAIL_URL = (
    "https://www.bellesdemeures.com/vente/france/ile-de-france/paris/"
    "paris-16eme/appartement-luxe/search/visitonline_a_2000028918038"
)

def _belles_demeures_list_html() -> str:
    """Page résultats type Belles Demeures (plusieurs cartes, prix parking en sus)."""
    return """
    <html><head>
    <title>1619 Appartements de luxe à vendre à Paris 16ème - Belles Demeures</title>
    </head><body>
    <h1>1619 biens d'exception</h1>
    Message envoyé le Appartement 5 Pièces•130 m² Muette Nord, Paris 16ème 1 290 000 €
    Signaler cette annonce SQUARE PRIVE haussmannien...
    Message envoyé le Appartement 4 Pièces•192,25 m² Porte Dauphine, Paris 16ème 2 750 000 €
    Signaler cette annonce Flandrin – Avenue Henri Martin...
    En supplément, possibilité d'acquérir un double emplacement de stationnement
    au sous-sol au prix de 220 000 €.
    </body></html>
    """


def test_belles_demeures_taxonomy_url_rejected():
    ok, reason = validate_listing_url(BD_LIST_URL)
    assert not ok
    assert "liste" in reason.lower() or "belles" in reason.lower() or "exclue" in reason.lower()
    assert is_taxonomy_or_list_hub_url(BD_LIST_URL)
    assert is_excluded_listing_url(BD_LIST_URL)


def test_belles_demeures_detail_url_accepted():
    ok, _ = validate_listing_url(BD_DETAIL_URL)
    assert ok
    assert not is_taxonomy_or_list_hub_url(BD_DETAIL_URL)


def test_multi_listing_html_detected_from_fixture():
    html = _belles_demeures_list_html()
    assert is_hub_page_title("1619 Appartements de luxe à vendre à Paris 16ème")
    assert is_multi_listing_html_page(html, BD_LIST_URL)


def test_crawl_coherence_rejects_list_page_with_mixed_price():
    html = _belles_demeures_list_html()
    lead = LeadData(
        source="Belles Demeures",
        source_url=BD_LIST_URL,
        price=220_000,
        surface=130.0,
        address="18 Rue Gros 75016 Paris",
        transaction_type="vente",
    )
    ok, reason = validate_listing_coherence_crawl(BD_LIST_URL, html, lead)
    assert not ok
    assert "liste" in reason.lower() or "plusieurs" in reason.lower()


def test_filter_listing_urls_drops_taxonomy():
    urls = [
        BD_LIST_URL,
        BD_DETAIL_URL,
        "https://www.bellesdemeures.com/vente/france/ile-de-france/paris/"
        "paris-16eme/appartement-luxe/tt-1-tb-2-pl-99/",
    ]
    kept = filter_listing_urls(urls)
    assert BD_LIST_URL not in kept
    assert BD_DETAIL_URL in kept


def test_ancillary_parking_price_ignored_in_extraction():
    from bs4 import BeautifulSoup

    html = """
    <html><body>
    <h1>Appartement 130 m²</h1>
    <div class="price">1 290 000 €</div>
    <p>En supplément, double emplacement de stationnement au sous-sol au prix de 220 000 €.</p>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    info = extract_listing_price(soup, "https://example.com/annonce/123456")
    assert info is not None
    assert info.amount == 1_290_000
