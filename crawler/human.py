"""Délais et gestes « humains » pour limiter la détection anti-bot."""

from __future__ import annotations

import random
import time

from crawler.config import (
    CRAWL_SPEED_PROFILE,
    active_speed_preset,
)


def _speed_preset() -> dict[str, float]:
    return active_speed_preset()

# Temps moyen chargement page + scroll (secondes)
AVG_PAGE_FETCH_SEC = 12.0
AVG_LISTING_FETCH_SEC = 18.0
WARMUP_SEC = 4.0


def human_sleep(seconds: float) -> None:
    time.sleep(max(0.05, seconds))


def micro_pause() -> None:
    human_sleep(random.uniform(0.35, 1.4))


def thinking_pause() -> None:
    human_sleep(random.uniform(1.2, 3.8))


def listing_delay(*, is_recrawl: bool = False) -> float:
    """Pause entre deux annonces — irrégulière (profil CRAWL_SPEED_PROFILE)."""
    p = _speed_preset()
    chance = p["extra_pause_chance"]
    if random.random() < chance:
        d = random.uniform(p["extra_pause_min"], p["extra_pause_max"])
    else:
        d = random.uniform(p["listing_min"], p["listing_max"])
    if is_recrawl:
        d *= p.get("recrawl_delay_factor", 1.0)
        d = max(0.25, d)
    human_sleep(d)
    return d


def search_page_delay() -> float:
    p = _speed_preset()
    d = random.uniform(p["search_min"], p["search_max"])
    if random.random() < 0.12 and CRAWL_SPEED_PROFILE == "quality":
        d += random.uniform(2, 8)
    human_sleep(d)
    return d


def discovery_scroll_lazy() -> bool:
    """Scroll long uniquement en profil qualité (exploration plus rapide sinon)."""
    return CRAWL_SPEED_PROFILE == "quality"


def warmup_sleep() -> float:
    """Pause après échauffement domaine (profil vitesse)."""
    from crawler.config import DOMAIN_WARMUP_ENABLED

    if not DOMAIN_WARMUP_ENABLED:
        return 0.0
    p = _speed_preset()
    d = float(p.get("warmup_sec", 2.0))
    human_sleep(d)
    return d


def source_switch_delay() -> float:
    """Entre deux portails lors d'un crawl global."""
    p = _speed_preset()
    d = random.uniform(p.get("source_gap_min", 0.3), p.get("source_gap_max", 0.8))
    human_sleep(d)
    return d


def estimate_crawl_seconds(
    listings_count: int,
    search_pages: int = 1,
    *,
    include_warmup: bool = True,
) -> int:
    """Estimation du temps total (fourchette réaliste, crawl humain)."""
    listings_count = max(0, listings_count)
    search_pages = max(1, search_pages)
    p = _speed_preset()
    avg_listing = (p["listing_min"] + p["listing_max"]) / 2 + AVG_LISTING_FETCH_SEC
    avg_page = (p["search_min"] + p["search_max"]) / 2 + AVG_PAGE_FETCH_SEC
    base = search_pages * avg_page + listings_count * avg_listing
    if include_warmup:
        base += float(p.get("warmup_sec", WARMUP_SEC))
    # Marge imprévu (challenges, lenteur réseau)
    return int(base * 1.15)


def format_eta(seconds: int) -> str:
    if seconds < 60:
        return f"~{seconds} s"
    mins = seconds // 60
    if mins < 60:
        return f"~{mins} min"
    h, m = divmod(mins, 60)
    return f"~{h} h {m} min" if m else f"~{h} h"
