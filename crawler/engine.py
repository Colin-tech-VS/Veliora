"""Moteur de crawl Veliora — Playwright + jobs async."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from crawler.adapters import BaseAdapter, build_adapters, resolve_adapter
from crawler.browser import close_browser_session, fetch_page
from crawler.browser import warmup_domain
from crawler.city_urls import (
    apply_city_to_search_url,
    listing_url_likely_in_city,
    build_city_seed_urls,
    pick_working_city_search_url,
)
from crawler.listing_guard import (
    filter_listing_urls,
    should_withdraw_incoherent,
    validate_listing_coherence,
    validate_listing_coherence_crawl,
    validate_listing_coherence_import,
    validate_listing_url,
    validate_listing_url_import,
)
from crawler.config import (
    CRAWL_SIMILAR_LISTINGS,
    CRAWL_SPEED_PROFILE,
    DOMAIN_WARMUP_ENABLED,
    DOMAIN_WARMUP_SEC,
    DVF_PARALLEL_DURING_CRAWL,
    MAX_LISTING_LINKS,
    MAX_LISTINGS_PER_SCAN,
    MAX_SEARCH_PAGES,
    MAX_SITE_DISCOVERY_PAGES,
    SITE_WIDE_CRAWL_ENABLED,
)
from crawler.dvf_queue import DvfParallelQueue
from crawler.human import (
    estimate_crawl_seconds,
    format_eta,
    human_sleep,
    listing_delay,
    micro_pause,
    search_page_delay,
    source_switch_delay,
    warmup_sleep,
)
from crawler.errors import CrawlError, format_missing_fields
from crawler.extractors import (
    LeadData,
    find_pagination_links,
    find_related_listing_links,
    is_excluded_listing_url,
    normalize_listing_url,
)
from crawler.portals import url_needs_browser
from crawler.storage import (
    add_activity,
    add_crawl_log,
    create_crawl_job,
    get_lead_by_source_url,
    get_source_lead_urls,
    get_sources,
    mark_source_scanned,
    repair_source_leads_in_db,
    save_lead,
    update_crawl_job,
    withdraw_lead_incoherent,
    reactivate_lead_after_repair,
)

logger = logging.getLogger(__name__)


@dataclass
class CrawlResult:
    url: str = ""
    leads_found: int = 0
    leads_saved: int = 0
    leads_updated: int = 0
    listings_processed: int = 0
    out_of_city: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    partial: list[dict] = field(default_factory=list)

    def can_process_more_listings(self) -> bool:
        if MAX_LISTINGS_PER_SCAN <= 0:
            return True
        return self.listings_processed < MAX_LISTINGS_PER_SCAN


class CrawlerEngine:
    def __init__(self):
        self.adapters: dict[str, BaseAdapter] = {}
        self._agency_id: str | None = None
        self._crawl_city: str | None = None
        self._dvf_queue: DvfParallelQueue | None = None
        self._address_queue = None  # AddressMatchQueue | None
        self.running = False
        self._thread: threading.Thread | None = None
        self._lead_refresh_thread: threading.Thread | None = None
        self._bg_interval_sec = 300
        self._source_deadline: float | None = None
        self._veille_mode = False
        self._veille_recrawl_urls: set[str] = set()
        self._lock = threading.Lock()
        self._job_lock = threading.Lock()

    @staticmethod
    def _city_slug(s: str | None) -> str:
        import re

        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    def _lead_in_target_city(self, lead) -> bool:
        """Crawl local : la fiche doit concerner la ville cible (sinon rejetée)."""
        target = (self._crawl_city or "").strip()
        if not target:
            return True
        from crm.dvf import extract_listing_location

        loc = extract_listing_location(
            getattr(lead, "address", None),
            (getattr(lead, "raw_extras", None) or {}).get("listing_title"),
            getattr(lead, "city", None),
        )
        hay = self._city_slug(
            " ".join(
                p
                for p in (
                    loc.get("city"),
                    getattr(lead, "address", None),
                    getattr(lead, "city", None),
                    (getattr(lead, "raw_extras", None) or {}).get("listing_title"),
                )
                if p
            )
        )
        tslug = self._city_slug(target)
        if not tslug:
            return True
        if not hay or len(hay) < 4:
            return False
        padded = f" {hay} "
        if f" {tslug} " in padded or hay.endswith(tslug) or hay.startswith(tslug):
            return True
        # Correspondance partielle (ex. « lyon » dans « lyon 3eme »).
        for part in tslug.split():
            if len(part) >= 4 and part in hay:
                return True
        loc_city = self._city_slug(loc.get("city"))
        if loc_city:
            for part in tslug.split():
                if len(part) >= 4 and part in loc_city:
                    return True
            for part in loc_city.split():
                if len(part) >= 4 and part in tslug:
                    return True
            if loc_city != tslug and len(loc_city) >= 4:
                return False
        return False

    def _load_adapters(self, agency_id: str) -> dict[str, BaseAdapter]:
        return build_adapters(get_sources(agency_id, sync=True, live_counts=False))

    def refresh_adapters(self, agency_id: str | None = None) -> None:
        aid = agency_id or self._agency_id
        if aid:
            self.adapters = self._load_adapters(aid)
        else:
            self.adapters = {}

    def _normalize_url(self, url: str) -> str:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def enqueue_job(
        self,
        job_type: str,
        target_url: str = "",
        source_id: str | None = None,
        city: str | None = None,
        *,
        agency_id: str | None = None,
        eta_seconds: int | None = None,
        listings_total: int | None = None,
        lane: str | None = None,
    ) -> dict:
        if not agency_id:
            raise ValueError("agency_id requis pour lancer un crawl")
        city = (city or "").strip() or None
        if eta_seconds is None:
            eta = estimate_crawl_seconds(MAX_LISTING_LINKS, MAX_SEARCH_PAGES)
        else:
            eta = eta_seconds
        if listings_total is None:
            listings_total = MAX_LISTING_LINKS
        from crawler.storage import crawl_job_lane, get_pending_or_running_crawl_job

        job_lane = lane or crawl_job_lane(job_type)
        with self._job_lock:
            existing = get_pending_or_running_crawl_job(agency_id, lane=job_lane)
            if existing:
                logger.info(
                    "Crawl %s déjà actif pour l'agence %s — job %s",
                    job_lane,
                    agency_id,
                    existing.get("id"),
                )
                return existing
            job = create_crawl_job(
                job_type,
                target_url or "multi",
                source_id,
                agency_id=agency_id,
                city=city,
                eta_seconds=eta,
                listings_total=listings_total,
            )
            thread = threading.Thread(
                target=self._run_job,
                args=(job["id"], job_type, target_url, source_id, city, agency_id),
                daemon=True,
                name="veliora-crawl",
            )
            thread.start()
            return job

    def _run_job(
        self,
        job_id: str,
        job_type: str,
        target_url: str,
        source_id: str | None,
        city: str | None = None,
        agency_id: str | None = None,
    ) -> None:
        self._agency_id = agency_id
        self._dvf_queue = (
            DvfParallelQueue(agency_id) if DVF_PARALLEL_DURING_CRAWL else None
        )
        from crawler.config import ADDRESS_MATCH_DURING_CRAWL

        if ADDRESS_MATCH_DURING_CRAWL:
            from crawler.address_match import AddressMatchQueue

            self._address_queue = AddressMatchQueue(agency_id)
        else:
            self._address_queue = None
        profile_label = {
            "quality": "qualité max",
            "balanced": "équilibré (défaut)",
            "fast": "rapide",
            "turbo": "turbo",
        }.get(CRAWL_SPEED_PROFILE, CRAWL_SPEED_PROFILE)
        dvf_hint = " · DVF en parallèle" if self._dvf_queue else ""
        update_crawl_job(
            job_id,
            status="running",
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            message=f"Démarrage crawl ({profile_label}{dvf_hint})…",
            progress=5,
        )

        try:
            from crawler.proxy_manager import begin_crawl_session, reset_block_rotation_counter

            # Tous les types de job : nouvelle IP si pool dispo (payant ou auto-gratuit).
            begin_crawl_session(force_new=True)
            reset_block_rotation_counter()
            self.refresh_adapters(agency_id)
            self._veille_mode = job_type == "veille_auto"
            self._source_deadline = None
            if job_type in ("all_sources", "veille_auto"):
                self._job_scan_all(job_id, city=city, veille_mode=self._veille_mode)
            elif job_type == "single_source" and source_id:
                self._job_scan_source(job_id, source_id, city=city)
            elif job_type == "url" and target_url:
                self._job_crawl_url(job_id, target_url)
            elif job_type == "listing_import" and target_url:
                self._job_import_listing(job_id, target_url)
            elif job_type == "lead_refresh" and target_url:
                self._job_refresh_lead(job_id, target_url, source_id)
            else:
                update_crawl_job(
                    job_id,
                    status="failed",
                    errors=[CrawlError.issue(CrawlError.SOURCE_UNKNOWN)],
                    message="Type de job invalide",
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            update_crawl_job(
                job_id,
                status="failed",
                errors=[CrawlError.issue(CrawlError.FETCH_FAILED, str(exc)[:200])],
                message=f"Erreur interne : {exc}",
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
        finally:
            if self._dvf_queue and self._dvf_queue.stats["submitted"] > self._dvf_queue.stats["completed"]:
                self._dvf_queue.drain()
            self._dvf_queue = None
            if (
                self._address_queue
                and self._address_queue.stats["submitted"] > self._address_queue.stats["completed"]
            ):
                self._address_queue.drain()
            self._address_queue = None
            self._agency_id = None
            from crawler.proxy_manager import end_crawl_session, reset_block_rotation_counter

            reset_block_rotation_counter()
            end_crawl_session()
            close_browser_session()

    def _crawl_stopped(self, job_id: str | None) -> bool:
        from crawler.storage import crawl_job_should_stop

        if not job_id or not crawl_job_should_stop(job_id):
            return False
        close_browser_session()
        return True

    def _finish_job(
        self, job_id: str, result: CrawlResult, label: str, *, veille_soft: bool = False
    ) -> None:
        if self._crawl_stopped(job_id):
            return
        dvf_line = ""
        if self._dvf_queue:
            if job_id:
                update_crawl_job(
                    job_id,
                    progress=96,
                    message="Comparatifs DVF en cours (données Etalab)…",
                )
            self._dvf_queue.drain()
            dvf_line = self._dvf_queue.summary_line()

        addr_line = ""
        if self._address_queue:
            if job_id:
                update_crawl_job(
                    job_id,
                    progress=98,
                    message="Rapprochement d'adresses en cours (DPE / BAN / cadastre)…",
                )
            self._address_queue.drain()
            addr_line = self._address_queue.summary_line()

        status = "completed"
        if result.errors and not result.leads_saved and not result.leads_updated:
            if result.leads_found > 0 or result.warnings or result.partial:
                status = "completed"
            elif veille_soft:
                status = "completed"
            else:
                status = "failed" if not result.leads_found else "completed"

        parts = []
        if result.leads_saved:
            parts.append(f"{result.leads_saved} nouveau(x)")
        if result.leads_updated:
            parts.append(f"{result.leads_updated} mis à jour")

        if parts:
            msg = f"{label} — " + ", ".join(parts)
        elif result.leads_found:
            msg = (
                f"{label} — {result.leads_found} annonce(s) analysée(s), "
                f"0 enregistrée(s) (données insuffisantes ou pages bloquées)"
            )
        elif result.errors:
            msg = result.errors[0]["message"]
        else:
            msg = f"{label} — aucun prospect"

        if dvf_line:
            msg = f"{msg} · {dvf_line}"
        if addr_line:
            msg = f"{msg} · {addr_line}"

        update_crawl_job(
            job_id,
            status=status,
            progress=100,
            leads_found=result.leads_found,
            leads_saved=result.leads_saved,
            leads_updated=result.leads_updated,
            listings_done=result.listings_processed,
            errors=result.errors,
            warnings=result.warnings,
            message=msg,
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        add_crawl_log(
            None,
            "",
            "completed" if status == "completed" else "error",
            msg,
            job_id,
        )

    def _source_timed_out(self) -> bool:
        d = self._source_deadline
        return d is not None and time.monotonic() >= d

    def _job_scan_all(
        self, job_id: str, city: str | None = None, *, veille_mode: bool = False
    ) -> None:
        from crawler.storage import seed_default_sources_for_agency, get_sources_for_full_crawl

        added = seed_default_sources_for_agency(self._agency_id)
        self.refresh_adapters(self._agency_id)
        sources = get_sources_for_full_crawl(self._agency_id)
        if veille_mode:
            from crawler.config import antibot_portals_crawl_enabled

            if not antibot_portals_crawl_enabled():
                from crawler.storage import is_antibot_source

                sources = [s for s in sources if not is_antibot_source(s)]
        if not sources:
            update_crawl_job(
                job_id,
                status="failed",
                errors=[CrawlError.issue(CrawlError.NO_LISTINGS, "Aucun portail recommandé activé")],
                message="Aucun portail accessible pour la veille (activez ParuVendu, Ouest-France Immo… — LBC/PAP exclus)",
                finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            return

        if not city:
            from crawler.storage import get_crawl_job as _gcj
            job_row = _gcj(job_id)
            city = (job_row or {}).get("city")

        # Crawl par ville : ne garder que les portails capables de cibler une ville.
        # Les national-only (etreproprio, lefigaro, superimmo) ne ramèneraient que du
        # hors-zone filtré (→ 0) en explorant 22 pages chacun : on les saute pour que
        # les résultats arrivent vite (paruvendu, ouest-france, lesiteimmo d'abord).
        if city:
            from crawler.portals import portal_supports_city_search

            city_capable = [s for s in sources if portal_supports_city_search(s["id"])]
            if city_capable:
                skipped = [s["name"] for s in sources if s not in city_capable]
                sources = city_capable
                if skipped:
                    add_crawl_log(
                        None,
                        "",
                        "skip_source",
                        f"Recherche {city} — portails sans recherche ville ignorés : {', '.join(skipped)}",
                        job_id,
                    )

        names_preview = ", ".join(s["name"] for s in sources[:4])
        if len(sources) > 4:
            names_preview += f" +{len(sources) - 4}"
        extra = f" ({added} site(s) ajouté(s))" if added else ""
        if city:
            scope = f"Veille {city}" if veille_mode else f"Crawl {city}"
        else:
            scope = "Veille nationale" if veille_mode else "Crawl national"
        update_crawl_job(
            job_id,
            progress=10,
            message=f"{scope} — {len(sources)} portail(s) accessible(s) : {names_preview}{extra}…",
        )

        from crawler.config import antibot_portals_crawl_enabled

        if not veille_mode or antibot_portals_crawl_enabled():
            self._prime_protected_sources(sources, job_id, city=city)

        total = CrawlResult()
        finish_label = "Veille automatique" if veille_mode else "Crawler tout"
        for i, src in enumerate(sources):
            if self._crawl_stopped(job_id):
                return
            if self._source_timed_out():
                add_crawl_log(
                    None,
                    "",
                    "skip_source",
                    "Délai veille atteint — portail suivant au prochain passage",
                    job_id,
                )
                break
            from crawler.proxy_manager import begin_crawl_session, reset_block_rotation_counter

            begin_crawl_session(force_new=True)
            reset_block_rotation_counter()
            pct = int(10 + (i / len(sources)) * 80)
            update_crawl_job(
                job_id,
                progress=pct,
                message=f"Site {i + 1}/{len(sources)} — {src['name']}…",
            )
            if veille_mode:
                from crawler.config import veille_source_budget_sec

                n_existing = len(
                    self._existing_lead_urls_for_source(src["id"])
                )
                self._source_deadline = time.monotonic() + veille_source_budget_sec(
                    n_existing
                )
                if job_id and n_existing:
                    update_crawl_job(
                        job_id,
                        message=(
                            f"Recrawl obligatoire de {n_existing} fiche(s) en base "
                            f"— puis découverte de nouvelles annonces…"
                        ),
                    )
            try:
                r = self._crawl_source(
                    src["id"], job_id, city=city, veille_mode=veille_mode
                )
            except Exception as exc:
                logger.warning("Veille — %s ignoré : %s", src.get("name"), exc)
                r = CrawlResult(
                    warnings=[
                        CrawlError.issue(
                            CrawlError.FETCH_FAILED,
                            f"{src.get('name')} — passage suivant ({str(exc)[:80]})",
                        )
                    ],
                )
            finally:
                if veille_mode:
                    self._source_deadline = None
                    self._veille_recrawl_urls = set()
            if self._crawl_stopped(job_id):
                return
            total.leads_found += r.leads_found
            total.leads_saved += r.leads_saved
            total.leads_updated += r.leads_updated
            total.errors.extend(r.errors)
            total.warnings.extend(r.warnings)
            total.partial.extend(r.partial)
            source_switch_delay()

        if job_id:
            update_crawl_job(job_id, progress=95, message="Finalisation et enregistrement des résultats…")
        if (
            total.leads_saved + total.leads_updated == 0
            and total.leads_found == 0
            and sources
        ):
            self._global_rescue_pass(sources, total, job_id, city=city)
        self._finish_job(job_id, total, finish_label, veille_soft=veille_mode)

    def _prime_protected_sources(
        self, sources: list[dict], job_id: str, city: str | None = None
    ) -> None:
        """Préchauffe la session (Chrome visible) sur les portails à anti-bot fort
        avant le crawl, pour poser les cookies DataDome en une fois."""
        from crawler.config import AUTO_WARMUP_ANTIBOT

        if not AUTO_WARMUP_ANTIBOT:
            return
        from crawler.browser import prime_protected_sites
        from crawler.storage import HARD_ANTIBOT_HOSTS

        targets: list[tuple[str, str]] = []
        for s in sources:
            blob = f"{s.get('id', '')} {s.get('base_url', '')} {s.get('search_url', '')}".lower()
            if not any(h in blob for h in HARD_ANTIBOT_HOSTS):
                continue
            adapter = self.adapters.get(s["id"])
            raw = (adapter.config.search_url if adapter else None) or s.get("search_url") or s.get("base_url")
            if raw:
                url = apply_city_to_search_url(raw, s["id"], city) if city else raw
                targets.append((s["name"], url))

        if not targets:
            return

        if job_id:
            update_crawl_job(
                job_id,
                progress=8,
                message="Préparation de la session sécurisée (Chrome) pour les portails protégés…",
            )

        def _status(name: str, url: str) -> None:
            if job_id:
                update_crawl_job(
                    job_id,
                    message=f"Session sécurisée — {name} (résolvez le captcha si une fenêtre Chrome s’ouvre)…",
                )

        try:
            prime_protected_sites(targets, on_status=_status)
        except Exception as exc:
            logger.warning("Préchauffage anti-bot: %s", exc)

    def _job_scan_source(self, job_id: str, source_id: str, city: str | None = None) -> None:
        src = next((s for s in get_sources(self._agency_id) if s["id"] == source_id), None)
        label = src["name"] if src else source_id
        if city:
            label = f"{label} — {city}"
            update_crawl_job(job_id, message=f"Préparation crawl {city}…")
        else:
            update_crawl_job(
                job_id,
                progress=15,
                message=f"Préparation du crawl sur {label}…",
            )
        result = self._crawl_source(source_id, job_id, city=city)
        self._finish_job(job_id, result, label)

    def _job_crawl_url(self, job_id: str, url: str) -> None:
        domain = urlparse(self._normalize_url(url)).netloc or "le site"
        update_crawl_job(
            job_id,
            progress=15,
            message=f"Analyse de l’URL — {domain}…",
        )
        result = self._crawl_url_sync(self._normalize_url(url), job_id=job_id)
        self._finish_job(job_id, result, domain)

    def _job_import_listing(self, job_id: str, url: str) -> None:
        url = self._normalize_url(url)
        domain = urlparse(url).netloc.replace("www.", "") or "annonce"
        if not self._looks_like_listing(url):
            update_crawl_job(
                job_id,
                progress=12,
                message=f"Page liste détectée — exploration des annonces sur {domain}…",
            )
            result = self._crawl_url_sync(url, job_id=job_id)
            self._finish_job(job_id, result, f"Import — {domain}")
            return
        update_crawl_job(
            job_id,
            progress=12,
            message=f"Import fiche — {domain} (extraction complète)…",
            listings_total=1,
        )
        adapter = resolve_adapter(url, self.adapters)
        result = CrawlResult(url=url)
        self._process_listing(
            url,
            adapter,
            result,
            adapter.source_id,
            job_id,
            index=0,
            total=1,
            crawl_related=False,
            deep_refresh=True,
            skip_city_check=True,
            import_mode=True,
        )
        self._finish_job(job_id, result, f"Import — {domain}")

    def _job_refresh_lead(
        self,
        job_id: str,
        url: str,
        source_id: str | None,
    ) -> None:
        from crawler.storage import get_lead_by_source_url

        url = self._normalize_url(url)
        row = get_lead_by_source_url(url, None)
        label = (row or {}).get("owner") or "Prospect"
        update_crawl_job(
            job_id,
            progress=20,
            message=f"Mise à jour approfondie — {label}…",
            listings_total=1,
        )
        adapter = self.adapters.get(source_id) if source_id else None
        if not adapter:
            adapter = resolve_adapter(url, self.adapters)
        result = CrawlResult(url=url)
        self._process_listing(
            url,
            adapter,
            result,
            source_id or adapter.source_id,
            job_id,
            deep_refresh=True,
            skip_city_check=True,
            crawl_related=False,
        )
        self._finish_job(job_id, result, label)

    def _crawl_source(
        self,
        source_id: str,
        job_id: str | None = None,
        city: str | None = None,
        *,
        veille_mode: bool = False,
    ) -> CrawlResult:
        if self._crawl_stopped(job_id) or self._source_timed_out():
            return CrawlResult()
        adapter = self.adapters.get(source_id)
        if not adapter:
            return CrawlResult(
                errors=[CrawlError.issue(CrawlError.SOURCE_UNKNOWN, source_id)],
            )

        base_search = adapter.config.search_url
        if city:
            from crawler.city_urls import pick_best_city_search_url, pick_working_city_search_url
            from crawler.config import CRAWL_SKIP_CITY_PROBE

            if CRAWL_SKIP_CITY_PROBE:
                search_url = pick_best_city_search_url(base_search, source_id, city)
            else:

                def _probe_city_list_url(u: str) -> bool:
                    fetched = fetch_page(
                        u,
                        referer=adapter.config.base_url,
                        prefer_browser=url_needs_browser(u),
                        fast_mode=True,
                    )
                    if not fetched.ok:
                        return False
                    listings = adapter.find_listings(fetched.html or "", u, limit=8)
                    if not listings:
                        return False
                    in_city = sum(
                        1 for link in listings if listing_url_likely_in_city(link, city)
                    )
                    return in_city >= max(1, len(listings) // 3)

                search_url = pick_working_city_search_url(
                    base_search, source_id, city, _probe_city_list_url
                )
        else:
            search_url = apply_city_to_search_url(base_search, source_id, city)

        from crawler.site_discovery import get_portal_discover_urls

        discover_url = search_url
        if self._looks_like_listing(search_url) and adapter.config.base_url:
            discover_url = adapter.config.base_url
        # Seeds prioritaires filtrés sur la ville de l'agence (crawl local) — tapés
        # en premier pour trouver vite des annonces de la ville.
        city_seeds = build_city_seed_urls(
            adapter.config.base_url or search_url, search_url, source_id, city
        )
        portal_seeds = get_portal_discover_urls(source_id, adapter, search_url)
        from crawler.immobilier_catalog import resolve_catalog_id
        from crawler.portals import resolve_base_portal_id

        if city:
            # Pas de seeds nationaux bruts : pages vides / hors-zone. Uniquement URLs filtrées ville.
            extra_portal: list[str] = []
            if resolve_base_portal_id(source_id) or resolve_catalog_id(source_id):
                extra_portal = [
                    apply_city_to_search_url(u, source_id, city)
                    for u in portal_seeds[:5]
                ]
            discover_seeds = list(
                dict.fromkeys(
                    city_seeds
                    + [search_url, discover_url, adapter.config.base_url or ""]
                    + extra_portal
                    # Repli national : si l'URL ville échoue (404/anti-bot), on explore
                    # quand même la recherche nationale ; les annonces sont ensuite
                    # filtrées par ville en aval (listing_url_likely_in_city /
                    # _lead_in_target_city), donc pas d'évasion hors-zone.
                    + [base_search]
                )
            )
        else:
            discover_seeds = list(dict.fromkeys(city_seeds + portal_seeds))

        mark_source_scanned(source_id)
        if self._agency_id and job_id:
            repaired = repair_source_leads_in_db(source_id, self._agency_id)
            if repaired:
                update_crawl_job(
                    job_id,
                    message=f"{repaired} prospect(s) corrigé(s) en base — lancement du crawl…",
                )
        from crawler.config import CRAWL_SPEED_PROFILE

        if (
            DOMAIN_WARMUP_ENABLED
            and adapter.config.base_url
            and CRAWL_SPEED_PROFILE == "quality"
        ):
            if job_id:
                update_crawl_job(
                    job_id,
                    message=f"Échauffement session — {adapter.source_name}…",
                )
            warmup_domain(adapter.config.base_url, search_url)
            warmup_sleep()

        result = self._crawl_url_sync(
            discover_url,
            adapter=adapter,
            source_id=source_id,
            job_id=job_id,
            discover_seeds=discover_seeds,
            city=city,
        )
        if result.errors and not result.leads_saved and not result.leads_updated and not result.leads_found:
            mark_source_scanned(source_id, error=result.errors[0]["message"][:200])
        else:
            mark_source_scanned(source_id, error=None)
        if self._agency_id:
            from crawler.storage import recalc_source_found_counts

            recalc_source_found_counts(self._agency_id)
        return result

    def _crawl_url_sync(
        self,
        url: str,
        adapter: BaseAdapter | None = None,
        source_id: str | None = None,
        job_id: str | None = None,
        discover_seeds: list[str] | None = None,
        city: str | None = None,
    ) -> CrawlResult:
        if self._crawl_stopped(job_id):
            return CrawlResult()
        url = self._normalize_url(url)
        if not adapter:
            adapter = resolve_adapter(url, self.adapters)

        self._crawl_city = (city or "").strip() or None
        result = CrawlResult(url=url)

        domain = urlparse(url).netloc.replace("www.", "") or url[:40]
        if job_id:
            update_crawl_job(
                job_id,
                progress=25,
                message=f"Ouverture de {domain} (mode furtif)…",
            )

        is_listing = self._looks_like_listing(url)

        if is_listing:
            if job_id:
                update_crawl_job(
                    job_id,
                    progress=55,
                    message="Page annonce détectée — extraction des coordonnées…",
                )
            self._process_listing(url, adapter, result, source_id, job_id)
            return result

        listing_urls, fetch_err = self._collect_listing_urls(
            url, adapter, job_id, source_id, discover_seeds=discover_seeds, city=city
        )

        targets, self._veille_recrawl_urls = self._prepare_listing_targets(
            listing_urls or [], source_id
        )
        if fetch_err and not targets:
            result.errors.append(fetch_err)
            return result
        if fetch_err and targets and self._veille_recrawl_urls:
            result.warnings.append(
                CrawlError.issue(
                    CrawlError.FETCH_FAILED,
                    "Exploration limitée — recrawl des fiches déjà en base",
                )
            )

        if targets:
            if job_id:
                pages_est = max(1, min(MAX_SEARCH_PAGES, (len(targets) // 25) + 1))
                eta = estimate_crawl_seconds(len(targets), pages_est)
                n_recrawl = len(self._veille_recrawl_urls)
                if self._veille_mode and n_recrawl:
                    n_new = max(0, len(targets) - n_recrawl)
                    limit_msg = (
                        f"Veille — {n_recrawl} fiche(s) en base (mise à jour) "
                        f"+ {n_new} nouvelle(s) — {format_eta(eta)}"
                    )
                else:
                    limit_msg = f"{len(targets)} annonce(s) — durée estimée {format_eta(eta)}"
                update_crawl_job(
                    job_id,
                    progress=50,
                    message=limit_msg,
                    eta_seconds=eta,
                    listings_total=len(targets),
                    listings_done=0,
                )
            add_activity(
                "crawl",
                f"Scan de {len(targets)} annonces — {adapter.source_name}",
                self._agency_id,
            )
            for i, listing_url in enumerate(targets):
                if self._crawl_stopped(job_id) or self._source_timed_out():
                    if self._source_timed_out() and job_id:
                        update_crawl_job(
                            job_id,
                            message="Délai portail atteint — suite au prochain passage de veille",
                        )
                    break
                if not result.can_process_more_listings():
                    break
                norm_url = normalize_listing_url(listing_url)
                is_veille_recrawl = norm_url in self._veille_recrawl_urls
                if (
                    self._crawl_city
                    and not is_veille_recrawl
                    and not listing_url_likely_in_city(listing_url, self._crawl_city)
                ):
                    result.out_of_city += 1
                    add_crawl_log(
                        source_id or adapter.source_id,
                        listing_url,
                        "skip_city",
                        f"Hors {self._crawl_city} (URL) — ignorée",
                        job_id,
                    )
                    continue
                if job_id:
                    pct = 50 + int((i / max(len(targets), 1)) * 40)
                    path_hint = urlparse(listing_url).path.rstrip("/").split("/")[-1][:40] or "annonce"
                    city_tag = f"{self._crawl_city} · " if self._crawl_city else ""
                    update_crawl_job(
                        job_id,
                        progress=pct,
                        message=f"Annonce {i + 1}/{len(targets)} — {city_tag}{path_hint}…",
                    )
                self._process_listing(
                    listing_url, adapter, result, source_id, job_id, index=i, total=len(targets)
                )
                # Source non locale : si presque tout est hors-zone et rien d'enregistré,
                # on abandonne vite (inutile de fetch 500 annonces nationales).
                if (
                    self._crawl_city
                    and result.listings_processed >= 80
                    and result.out_of_city >= 70
                    and (result.leads_saved + result.leads_updated) == 0
                    and result.out_of_city >= int(0.98 * max(1, result.listings_processed))
                ):
                    if job_id:
                        update_crawl_job(
                            job_id,
                            message=f"Source sans annonce à {self._crawl_city} — on passe à la suivante",
                        )
                    add_crawl_log(
                        source_id or adapter.source_id,
                        "",
                        "skip_source",
                        f"{result.out_of_city} annonces hors {self._crawl_city} — source non locale, abandon",
                        job_id,
                    )
                    break
                if job_id:
                    update_crawl_job(
                        job_id,
                        listings_done=result.listings_processed,
                        progress=min(95, 50 + int((result.listings_processed / max(len(targets), 1)) * 45)),
                    )
                if i < len(targets) - 1 and result.can_process_more_listings():
                    listing_delay(
                        is_recrawl=get_lead_by_source_url(listing_url, None) is not None,
                    )
        else:
            rescue = self._last_resort_listing_discovery(
                url,
                adapter,
                source_id,
                job_id,
                city=city,
                portal_seeds=discover_seeds or [],
                seen=set(),
            )
            if rescue:
                targets = rescue[: max(15, len(rescue))]
                if job_id:
                    update_crawl_job(
                        job_id,
                        progress=50,
                        message=f"Repli — {len(targets)} annonce(s) repérée(s), extraction…",
                        listings_total=len(targets),
                    )
                for i, listing_url in enumerate(targets):
                    if self._crawl_stopped(job_id) or not result.can_process_more_listings():
                        break
                    self._process_listing(
                        listing_url,
                        adapter,
                        result,
                        source_id,
                        job_id,
                        index=i,
                        total=len(targets),
                    )
            if result.leads_saved or result.leads_updated or result.leads_found:
                return result
            issue = CrawlError.issue(CrawlError.NO_LISTINGS, url=url)
            result.errors.append(issue)
            add_crawl_log(source_id or adapter.source_id, url, "error", issue["message"], job_id)
            if job_id:
                update_crawl_job(
                    job_id,
                    progress=60,
                    message="Aucune annonce repérée sur cette page",
                )

        return result

    def _global_rescue_pass(
        self,
        sources: list[dict],
        total: CrawlResult,
        job_id: str | None,
        *,
        city: str | None = None,
    ) -> None:
        """Dernière passe si le crawl multi-portails n'a rien remonté."""
        if job_id:
            update_crawl_job(
                job_id,
                progress=92,
                message="Repli global — nouvelle passe sur les portails prioritaires…",
            )
        for src in sources[:5]:
            if self._crawl_stopped(job_id):
                return
            adapter = self.adapters.get(src["id"])
            if not adapter:
                continue
            start = (
                apply_city_to_search_url(
                    adapter.config.search_url or adapter.config.base_url,
                    src["id"],
                    city,
                )
                if city
                else (adapter.config.search_url or adapter.config.base_url)
            )
            if not start or not start.startswith("http"):
                continue
            seen: set[str] = set()
            rescue = self._last_resort_listing_discovery(
                start,
                adapter,
                src["id"],
                job_id,
                city=city,
                portal_seeds=[],
                seen=seen,
            )
            for i, listing_url in enumerate(rescue[:20]):
                if self._crawl_stopped(job_id) or not total.can_process_more_listings():
                    return
                self._process_listing(
                    listing_url,
                    adapter,
                    total,
                    src["id"],
                    job_id,
                    index=i,
                    total=min(20, len(rescue)),
                )
            if total.leads_saved or total.leads_updated:
                return

    def _collect_listing_urls(
        self,
        start_url: str,
        adapter: BaseAdapter,
        job_id: str | None,
        source_id: str | None,
        discover_seeds: list[str] | None = None,
        city: str | None = None,
    ) -> tuple[list[str], dict | None]:
        """Explore le site (multi-seeds, pagination, catégories) jusqu'à MAX_LISTING_LINKS.

        Si une ville est fixée (crawl local), on ne suit que les pages de cette ville :
        les catégories renvoyant vers d'autres communes sont ignorées (sinon le crawl
        s'évade vers toute la France).
        """
        from crawler.site_discovery import (
            build_site_seed_urls,
            extend_adapter_patterns,
            find_category_links,
            get_portal_discover_urls,
        )

        import re as _re

        portal_seeds = get_portal_discover_urls(source_id, adapter, start_url)
        city_slug = _re.sub(r"[^a-z0-9]+", "-", (city or "").lower().strip()).strip("-")

        def _category_in_city(cat_url: str) -> bool:
            if not city_slug:
                return True
            return city_slug in cat_url.lower()

        all_links: list[str] = []
        seen: set[str] = set()
        base_url = adapter.config.base_url or start_url
        if city_slug:
            # Crawl local : seeds ville d'abord ; chemins catalogue en repli si vide.
            seed_urls = list(dict.fromkeys(discover_seeds or [start_url]))
            if len(seed_urls) < 4:
                seed_urls = list(
                    dict.fromkeys(
                        seed_urls + build_site_seed_urls(base_url, start_url)[:14]
                    )
                )
        elif SITE_WIDE_CRAWL_ENABLED:
            seed_urls = list(
                dict.fromkeys(
                    (discover_seeds or [])
                    + build_site_seed_urls(base_url, start_url)
                )
            )
        else:
            seed_urls = list(dict.fromkeys(discover_seeds or [start_url]))
        pages_to_visit: list[str] = []
        for s in seed_urls:
            if s not in pages_to_visit:
                pages_to_visit.append(s)
        visited_pages: set[str] = set()
        last_method = "playwright"
        search_referer = adapter.config.base_url or None
        from crawler.config import CRAWL_SPEED_PROFILE
        from crawler.human import discovery_scroll_lazy

        if CRAWL_SPEED_PROFILE in ("fast", "turbo"):
            max_browse_pages = min(32, MAX_SITE_DISCOVERY_PAGES)
        elif CRAWL_SPEED_PROFILE == "balanced":
            max_browse_pages = min(36, MAX_SITE_DISCOVERY_PAGES)
        else:
            max_browse_pages = max(MAX_SEARCH_PAGES, MAX_SITE_DISCOVERY_PAGES)

        discovery_scroll = discovery_scroll_lazy()
        zero_yield_pages = 0
        fallback_seeds_done = False
        ai_discovery_attempts = 0
        from crawler.discovery_pipeline import extract_listing_urls_from_page

        if DOMAIN_WARMUP_ENABLED and CRAWL_SPEED_PROFILE == "quality" and adapter.config.base_url and start_url != adapter.config.base_url:
            warmup_domain(adapter.config.base_url, start_url)
            from crawler.config import active_speed_preset

            human_sleep(float(active_speed_preset().get("warmup_sec", 2.0)) * 0.5)
            search_referer = adapter.config.base_url

        while pages_to_visit and len(all_links) < MAX_LISTING_LINKS and len(visited_pages) < max_browse_pages:
            page_url = pages_to_visit.pop(0)
            norm_page = page_url.split("#")[0].rstrip("/")
            if norm_page in visited_pages:
                continue
            visited_pages.add(norm_page)

            if job_id:
                update_crawl_job(
                    job_id,
                    message=(
                        f"Exploration du site ({len(visited_pages)}/{max_browse_pages}) — "
                        f"{len(all_links)} annonce(s) repérée(s)…"
                    ),
                )

            use_browser = url_needs_browser(page_url)
            fast_discover = not discovery_scroll
            fetched = fetch_page(
                page_url,
                scroll_lazy=discovery_scroll,
                referer=search_referer,
                prefer_browser=use_browser,
                fast_mode=fast_discover,
            )
            if not fetched.ok:
                fetched = fetch_page(
                    page_url,
                    scroll_lazy=discovery_scroll,
                    referer=search_referer,
                    prefer_browser=True,
                    fast_mode=fast_discover,
                )
            elif use_browser is False and not fast_discover:
                batch_probe = adapter.find_listings(fetched.html or "", page_url, limit=5)
                if not batch_probe:
                    fetched = fetch_page(
                        page_url,
                        scroll_lazy=discovery_scroll,
                        referer=search_referer,
                        prefer_browser=True,
                        fast_mode=False,
                    )
            if not fetched.ok:
                if not all_links and not pages_to_visit:
                    detail = fetched.error_detail or ""
                    hint = (
                        " Anti-bot actif — relancez le crawl ; une fenêtre Chrome peut s’ouvrir."
                        if fetched.error_code == CrawlError.SITE_BLOCKED
                        else ""
                    )
                    return [], CrawlError.issue(
                        fetched.error_code or CrawlError.FETCH_FAILED,
                        (detail + hint).strip(),
                        page_url,
                    )
                continue

            last_method = fetched.method
            links_before = len(all_links)
            batch = extract_listing_urls_from_page(
                adapter,
                fetched.html,
                page_url,
                limit=MAX_LISTING_LINKS,
                use_ai=ai_discovery_attempts < 4,
                ai_attempt=ai_discovery_attempts < 4,
            )
            if batch and ai_discovery_attempts < 4:
                ai_discovery_attempts += 1
            for link in batch:
                if city and not listing_url_likely_in_city(link, city):
                    continue
                if link not in seen:
                    seen.add(link)
                    all_links.append(link)
            page_yielded = len(all_links) - links_before
            if page_yielded > 0:
                zero_yield_pages = 0
            else:
                zero_yield_pages += 1

            if len(all_links) >= 25 and zero_yield_pages >= 4:
                break

            if (
                not fallback_seeds_done
                and zero_yield_pages >= 3
                and len(all_links) < 3
                and pages_to_visit
            ):
                fallback_seeds_done = True
                extra = list(
                    dict.fromkeys(
                        portal_seeds
                        + build_site_seed_urls(base_url, start_url)[:12]
                    )
                )
                added = 0
                for u in extra:
                    n = u.split("#")[0].rstrip("/")
                    if n not in visited_pages and n not in pages_to_visit:
                        pages_to_visit.append(n)
                        added += 1
                if job_id and added:
                    update_crawl_job(
                        job_id,
                        message=(
                            f"Exploration élargie — {added} page(s) catalogue "
                            f"({len(all_links)} annonce(s) pour l'instant)…"
                        ),
                    )

            if len(all_links) >= 10:
                extend_adapter_patterns(adapter, all_links)

            if MAX_LISTINGS_PER_SCAN > 0 and len(all_links) >= MAX_LISTINGS_PER_SCAN:
                break

            if city_slug:
                from crawler.config import CITY_DISCOVERY_STOP_LINKS

                if CITY_DISCOVERY_STOP_LINKS > 0 and len(all_links) >= CITY_DISCOVERY_STOP_LINKS:
                    break

            pagination = [
                n
                for u in find_pagination_links(fetched.html, page_url, page_url)
                if (n := u.split("#")[0].rstrip("/")) not in visited_pages and n not in pages_to_visit
            ]
            categories = []
            if SITE_WIDE_CRAWL_ENABLED:
                categories = [
                    c
                    for u in find_category_links(fetched.html, page_url, base_url, visited_pages, limit=20)
                    if (c := u.split("#")[0].rstrip("/")) not in visited_pages
                    and c not in pages_to_visit
                    and _category_in_city(c)
                ]

            # Si la page a donné des annonces → on continue sa pagination en priorité.
            # Sinon (page d'accueil/rubrique sans fiche) → on plonge d'abord dans les
            # catégories pour atteindre vite les pages qui contiennent des annonces.
            if page_yielded > 0:
                pages_to_visit[:0] = pagination
                pages_to_visit.extend(categories)
            else:
                pages_to_visit[:0] = categories
                pages_to_visit.extend(pagination)

            if pages_to_visit and CRAWL_SPEED_PROFILE != "turbo":
                search_page_delay()
            elif pages_to_visit and CRAWL_SPEED_PROFILE == "turbo":
                from crawler.human import human_sleep

                human_sleep(0.15)

        if job_id and all_links:
            method_labels = {
                "playwright": "navigateur furtif",
                "curl_cffi": "TLS Chrome (curl_cffi)",
                "requests": "HTTP",
            }
            method_label = method_labels.get(last_method, last_method)
            update_crawl_job(
                job_id,
                progress=45,
                message=(
                    f"{len(all_links)} annonce(s) sur {len(visited_pages)} page(s) "
                    f"({method_label})"
                ),
            )
        elif job_id and visited_pages:
            update_crawl_job(
                job_id,
                progress=40,
                message=f"Exploration terminée — {len(visited_pages)} page(s), aucune annonce trouvée",
            )

        if len(all_links) < 5:
            rescue = self._last_resort_listing_discovery(
                start_url,
                adapter,
                source_id,
                job_id,
                city=city,
                portal_seeds=portal_seeds,
                seen=seen,
            )
            for link in rescue:
                if city and not listing_url_likely_in_city(link, city):
                    continue
                if link not in seen:
                    seen.add(link)
                    all_links.append(link)

        return all_links, None

    def _last_resort_listing_discovery(
        self,
        start_url: str,
        adapter: BaseAdapter,
        source_id: str | None,
        job_id: str | None,
        *,
        city: str | None,
        portal_seeds: list[str],
        seen: set[str],
    ) -> list[str]:
        """Repli : navigateur complet + pipeline unifié + IA si clé API."""
        from crawler.discovery_pipeline import extract_listing_urls_from_page
        from crawler.site_discovery import build_site_seed_urls

        base_url = adapter.config.base_url or start_url
        candidates = list(
            dict.fromkeys(
                [start_url, adapter.config.search_url or "", base_url]
                + (portal_seeds or [])[:8]
                + build_site_seed_urls(base_url, start_url)[:10]
            )
        )
        found: list[str] = []
        for page_url in candidates:
            if not page_url or not page_url.startswith("http"):
                continue
            if job_id:
                update_crawl_job(
                    job_id,
                    message=f"Repli découverte — nouvelle tentative sur {urlparse(page_url).netloc}…",
                )
            fetched = fetch_page(
                page_url,
                scroll_lazy=True,
                referer=base_url,
                prefer_browser=True,
                fast_mode=False,
            )
            if not fetched.ok or not (fetched.html or "").strip():
                continue
            batch = extract_listing_urls_from_page(
                adapter,
                fetched.html,
                page_url,
                limit=MAX_LISTING_LINKS,
                use_ai=True,
                ai_attempt=True,
            )
            for link in batch:
                if link in seen:
                    continue
                found.append(link)
            if len(found) >= 8:
                break
        if found and job_id:
            add_crawl_log(
                source_id or adapter.source_id,
                start_url,
                "discovery_rescue",
                f"Repli — {len(found)} annonce(s) repérée(s)",
                job_id,
            )
        return found

    def _existing_lead_urls_for_source(self, source_id: str | None) -> list[str]:
        """Toutes les URLs de fiches actives pour ce portail (recrawl veille)."""
        if not source_id or not self._agency_id:
            return []
        existing_valid: list[str] = []
        seen: set[str] = set()
        for raw in get_source_lead_urls(source_id, self._agency_id):
            u = normalize_listing_url(raw)
            if u in seen:
                continue
            ok, _ = validate_listing_url_import(u)
            if not ok:
                ok, _ = validate_listing_url(u)
            if ok:
                seen.add(u)
                existing_valid.append(u)
        return existing_valid

    def _prepare_listing_targets(
        self,
        discovered: list[str],
        source_id: str | None,
    ) -> tuple[list[str], set[str]]:
        """
        Veille : toutes les fiches en base d'abord (sans plafond), puis nouvelles URLs (plafonnées).
        Crawl manuel : recrawl prioritaire + plafond global inchangé.
        """
        if self._veille_mode:
            existing_valid = self._existing_lead_urls_for_source(source_id)
            existing_set = set(existing_valid)
            seen = set(existing_set)
            new_urls: list[str] = []
            for raw in discovered:
                u = normalize_listing_url(raw)
                if u in seen:
                    continue
                seen.add(u)
                new_urls.append(u)
            from crawler.config import CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS

            if CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS > 0:
                new_urls = new_urls[:CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS]
            return list(existing_valid) + new_urls, existing_set

        ordered = self._prioritize_recrawl_urls(discovered, source_id)
        cap = MAX_LISTINGS_PER_SCAN if MAX_LISTINGS_PER_SCAN > 0 else len(ordered)
        if self._crawl_city:
            from crawler.config import CITY_CRAWL_MAX_LISTINGS

            if CITY_CRAWL_MAX_LISTINGS > 0:
                cap = min(cap, CITY_CRAWL_MAX_LISTINGS) if cap else CITY_CRAWL_MAX_LISTINGS
        return ordered[:cap], set()

    def _prioritize_recrawl_urls(
        self,
        discovered: list[str],
        source_id: str | None,
    ) -> list[str]:
        """Recrawl d'abord les annonces déjà en base, puis les nouvelles découvertes."""
        if not source_id or not self._agency_id:
            return discovered

        existing_valid = self._existing_lead_urls_for_source(source_id)
        seen: set[str] = set(existing_valid)
        ordered: list[str] = list(existing_valid)
        for u in discovered:
            u = normalize_listing_url(u)
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered

    def _looks_like_listing(self, url: str) -> bool:
        """True pour une fiche annonce (validation souple en priorité)."""
        if is_excluded_listing_url(url):
            return False
        ok, _ = validate_listing_url_import(url)
        if not ok:
            ok, _ = validate_listing_url(url)
        return ok

    def _coherence_check(
        self,
        url: str,
        html: str | None,
        lead: LeadData,
        *,
        import_mode: bool,
    ) -> tuple[bool, str]:
        if import_mode:
            return validate_listing_coherence_import(url, html, lead)
        return validate_listing_coherence_crawl(url, html, lead)

    def _coherence_check_strict(
        self,
        url: str,
        html: str | None,
        lead: LeadData,
    ) -> tuple[bool, str]:
        return validate_listing_coherence(url, html, lead)

    def _retry_listing_verification(
        self,
        url: str,
        adapter: BaseAdapter,
        lead: LeadData,
        fetched,
        *,
        import_mode: bool,
        deep_refresh: bool,
        job_id: str | None,
    ) -> tuple[LeadData, object, bool, str]:
        """2e lecture avant retrait — confirme l’incohérence."""
        if job_id:
            update_crawl_job(
                job_id,
                progress=62,
                message="Vérification — 2e lecture de l’annonce…",
            )
        fetched2 = fetch_page(url, click_contacts=True, prefer_browser=True)
        if not fetched2.ok:
            return lead, fetched, False, fetched2.error_detail or "échec 2e lecture"
        lead2 = adapter.parse_listing(fetched2.html, url)
        if deep_refresh:
            from crawler.extractors import deep_enhance_listing_contacts

            lead2 = deep_enhance_listing_contacts(fetched2.html, url, lead2)
        ok, reason = self._coherence_check(
            url, fetched2.html, lead2, import_mode=import_mode
        )
        return lead2, fetched2, ok, reason

    def _try_repair_incoherent_listing(
        self,
        url: str,
        lead: LeadData,
        fetched,
        existing_row: dict | None,
        *,
        adapter: BaseAdapter,
        deep_refresh: bool,
        import_mode: bool,
        job_id: str | None,
        coh_reason: str,
    ) -> tuple[LeadData, object, bool, str]:
        """Tente de réparer une fiche (mix annonces, hub…) avant retrait."""
        from crawler.validation import (
            lead_from_db_row,
            merge_lead_for_update,
            repair_mixed_listing,
        )

        if job_id:
            update_crawl_job(
                job_id,
                progress=64,
                message="Réparation — consensus prix, surface, titre…",
            )

        repaired = repair_mixed_listing(
            lead, fetched.html, url, coherence_hint=coh_reason
        )
        if existing_row:
            existing_lead = lead_from_db_row(existing_row)
            repaired = merge_lead_for_update(
                existing_lead, repaired, deep_refresh=deep_refresh
            )

        coherent, reason = self._coherence_check(
            url, fetched.html, repaired, import_mode=import_mode
        )
        if coherent:
            return repaired, fetched, True, reason

        lead2, fetched2, ok_fetch, reason2 = self._retry_listing_verification(
            url,
            adapter,
            lead,
            fetched,
            import_mode=import_mode,
            deep_refresh=deep_refresh,
            job_id=job_id,
        )
        repaired2 = repair_mixed_listing(
            lead2, fetched2.html, url, coherence_hint=reason2 or coh_reason
        )
        if existing_row:
            existing_lead = lead_from_db_row(existing_row)
            repaired2 = merge_lead_for_update(
                existing_lead, repaired2, deep_refresh=deep_refresh
            )

        coherent2, reason2 = self._coherence_check(
            url, fetched2.html, repaired2, import_mode=import_mode
        )
        return repaired2, fetched2, coherent2, reason2 or coh_reason

    def _submit_address_match(self, saved: dict, lead: LeadData) -> None:
        """Soumet le lead au rapprochement d'adresse DPE/BAN/cadastre (carte).

        Standardisé pour TOUTES les sources et TOUS les chemins d'enregistrement
        (vérifié, minimal, réparé) : chaque prospect reçoit la meilleure adresse
        réelle approximative — idéalement exacte — pour la carte.
        """
        if not self._address_queue or not saved or not saved.get("id"):
            return
        lead_id = int(saved["id"])
        try:
            from crawler.address_match.storage import save_lead_features

            feats = (getattr(lead, "raw_extras", None) or {}).get("listing_features")
            if feats:
                save_lead_features(lead_id, self._agency_id or "", feats)
        except Exception:
            logger.debug("save_lead_features ignoré", exc_info=True)
        self._address_queue.submit_lead(lead_id)

    def _save_repaired_lead(
        self,
        lead: LeadData,
        url: str,
        result: CrawlResult,
        source_id: str | None,
        adapter: BaseAdapter,
        job_id: str | None,
        *,
        repair_note: str,
        was_retired: bool = False,
    ) -> bool:
        saved = save_lead(
            lead,
            source_id=source_id or adapter.source_id,
            job_id=job_id,
            agency_id=self._agency_id,
            deep_refresh=True,
        )
        if not saved or not saved.get("id"):
            return False
        self._submit_address_match(saved, lead)
        if was_retired:
            reactivate_lead_after_repair(int(saved["id"]), self._agency_id or "")
        result.leads_found += 1
        if saved.get("created"):
            result.leads_saved += 1
        else:
            result.leads_updated += 1
        add_crawl_log(
            source_id or adapter.source_id,
            url,
            "repaired",
            repair_note,
            job_id,
        )
        if job_id:
            update_crawl_job(
                job_id,
                message=f"Fiche réparée — {lead.owner or lead.address or 'annonce'}",
                leads_saved=result.leads_saved,
                leads_updated=result.leads_updated,
                leads_found=result.leads_found,
            )
        return True

    def _fetch_listing_page(
        self,
        url: str,
        *,
        referer: str | None,
        deep_refresh: bool,
        scroll_lazy: bool,
    ):
        """curl_cffi d'abord (rapide) ; Playwright + contacts si données manquantes."""
        from crawler.browser import _html_has_contact_hints
        from crawler.portals import url_needs_browser

        if deep_refresh:
            return fetch_page(
                url,
                click_contacts=True,
                referer=referer,
                prefer_browser=True,
                scroll_lazy=True,
            )
        quick = fetch_page(url, referer=referer, prefer_browser=False, fast_mode=True)
        if quick.ok:
            html = quick.html or ""
            if _html_has_contact_hints(html):
                return quick
            if not url_needs_browser(url) and len(html) > 10_000:
                low = html.lower()
                if any(
                    tok in low
                    for tok in ("surface", "m²", "m2", "prix", "€", "adresse", "detail", "annonce")
                ):
                    return quick
        return fetch_page(
            url,
            click_contacts=True,
            referer=referer,
            prefer_browser=True,
            scroll_lazy=scroll_lazy,
        )

    def _boost_listing_address(self, lead, html: str, url: str) -> None:
        """Ré-extrait une adresse rue (JSON-LD, DOM) si la fiche n'a que la ville."""
        from crawler.address_quality import is_street_level_address

        if is_street_level_address(
            lead.address,
            getattr(lead, "city", None),
            getattr(lead, "postcode", None),
        ):
            return
        if not (html or "").strip():
            return
        from bs4 import BeautifulSoup
        from crawler.extractors import extract_from_json_ld, extract_listing_address

        soup = BeautifulSoup(html, "lxml")
        extract_from_json_ld(soup, lead, page_url=url)
        if is_street_level_address(
            lead.address,
            getattr(lead, "city", None),
            getattr(lead, "postcode", None),
        ):
            return
        dom_addr = extract_listing_address(soup, url)
        if dom_addr:
            from crawler.hub_detection import is_hub_listing_address

            if not is_hub_listing_address(dom_addr):
                lead.address = dom_addr

    def _clean_unreliable_fields(self, lead) -> None:
        """Zéro donnée faussée : on n'enregistre jamais un nom/adresse douteux.

        - Nom = fragment de phrase / CTA / champ d'annonce → vidé.
        - Adresse = titre/descriptif d'annonce → remplacée par la localité réelle
          (ville + code postal, extraits du titre/URL) ou vidée si inconnue.
        """
        from crawler.validation import _address_ok, _name_ok

        try:
            from crm.dvf import apply_lead_location_fields

            apply_lead_location_fields(lead)  # remplit city/postcode depuis titre/adresse
        except Exception:
            pass

        if not _name_ok(lead.first_name, lead.last_name):
            lead.first_name = None
            lead.last_name = None

        # Nom de ville manquant mais code postal connu → résoudre (56100 → Lorient).
        if not getattr(lead, "city", None) and getattr(lead, "postcode", None):
            try:
                from crawler.fr_communes import city_for_postcode

                lead.city = city_for_postcode(lead.postcode) or lead.city
            except Exception:
                pass

        if lead.address and not _address_ok(lead.address):
            lead.address = None
        else:
            from crawler.address_quality import is_city_only_address, scrub_lead_address_for_storage

            scrub_lead_address_for_storage(lead)

    def _save_listing_snapshot(
        self,
        lead: LeadData,
        url: str,
        adapter: BaseAdapter,
        result: CrawlResult,
        source_id: str | None,
        job_id: str | None,
        *,
        import_mode: bool,
        deep_refresh: bool,
        is_recrawl: bool,
    ) -> bool:
        """Dernier recours : enregistrer au minimum l'URL de fiche (tous types de crawl)."""
        stub = lead
        if not (getattr(stub, "source_url", None) or "").startswith("http"):
            stub = LeadData(source_url=url, source=adapter.source_name)
        stub.source_url = url
        if not (stub.owner or "").strip() or stub.owner == "—":
            path_hint = urlparse(url).path.rstrip("/").split("/")[-1][:80]
            stub.owner = path_hint or "Annonce"
        if not (stub.raw_extras or {}).get("listing_title"):
            stub.raw_extras = dict(stub.raw_extras or {})
            stub.raw_extras.setdefault("listing_title", stub.owner)
        saved = save_lead(
            stub,
            source_id=source_id or adapter.source_id,
            job_id=job_id,
            agency_id=self._agency_id,
            require_verification=False,
            deep_refresh=deep_refresh or import_mode,
            veille_recrawl=is_recrawl and self._veille_mode,
        )
        if not saved or not saved.get("id"):
            return False
        result.leads_found += 1
        if saved.get("created"):
            result.leads_saved += 1
        else:
            result.leads_updated += 1
        self._submit_address_match(saved, stub)
        add_crawl_log(
            source_id or adapter.source_id,
            url,
            "saved_snapshot",
            "Fiche enregistrée (URL / données minimales)",
            job_id,
        )
        if job_id:
            update_crawl_job(
                job_id,
                message=f"Prospect enregistré (minimal) — {stub.owner}",
                leads_saved=result.leads_saved,
                leads_updated=result.leads_updated,
                leads_found=result.leads_found,
            )
        return True

    def _process_listing(
        self,
        url: str,
        adapter: BaseAdapter,
        result: CrawlResult,
        source_id: str | None,
        job_id: str | None,
        index: int | None = None,
        total: int | None = None,
        crawl_related: bool = True,
        *,
        deep_refresh: bool = False,
        skip_city_check: bool = False,
        import_mode: bool = False,
    ) -> None:
        if not result.can_process_more_listings():
            return
        url = normalize_listing_url(url)
        if is_excluded_listing_url(url):
            return
        if import_mode:
            url_ok, url_reason = validate_listing_url_import(url)
        else:
            url_ok, url_reason = validate_listing_url(url)
            if not url_ok:
                url_ok, url_reason = validate_listing_url_import(url)
        if not url_ok:
            add_crawl_log(
                source_id or adapter.source_id,
                url,
                "skip_url",
                url_reason,
                job_id,
            )
            return
        result.listings_processed += 1

        if job_id:
            prefix = f"Annonce {index + 1}/{total} — " if index is not None and total else ""
            load_msg = (
                "Relecture complète — type agence/particulier, téléphone, email…"
                if deep_refresh
                else f"{prefix}Chargement de l’annonce (téléphone, email, adresse)…"
            )
            update_crawl_job(job_id, message=load_msg)

        existing_row = (
            get_lead_by_source_url(url, None)
        )
        is_recrawl = existing_row is not None
        if not skip_city_check and self._veille_mode:
            if normalize_listing_url(url) in self._veille_recrawl_urls:
                skip_city_check = True
        if job_id:
            if deep_refresh:
                msg = "Mise à jour poussée — type, téléphone, email…"
            else:
                msg = "Vérification des données (recrawl)…" if is_recrawl else "Chargement de l’annonce…"
            update_crawl_job(job_id, message=msg)

        listing_referer = adapter.config.search_url or adapter.config.base_url
        scroll = deep_refresh
        fetched = self._fetch_listing_page(
            url,
            referer=listing_referer,
            deep_refresh=deep_refresh,
            scroll_lazy=scroll,
        )
        if not fetched.ok:
            fetched = fetch_page(
                url,
                click_contacts=True,
                prefer_browser=True,
                scroll_lazy=scroll,
            )
        if deep_refresh and fetched.ok:
            if job_id:
                update_crawl_job(job_id, progress=42, message="2e passage — contacts et prix…")
            micro_pause()
            fetched2 = fetch_page(
                url,
                click_contacts=True,
                prefer_browser=True,
                scroll_lazy=True,
            )
            if fetched2.ok:
                fetched = fetched2
        if not fetched.ok:
            if import_mode or deep_refresh or self._veille_mode:
                stub = LeadData(source_url=url, source=adapter.source_name)
                if self._save_listing_snapshot(
                    stub,
                    url,
                    adapter,
                    result,
                    source_id,
                    job_id,
                    import_mode=import_mode,
                    deep_refresh=deep_refresh,
                    is_recrawl=is_recrawl,
                ):
                    return
            issue = CrawlError.issue(
                fetched.error_code or CrawlError.FETCH_FAILED,
                fetched.error_detail,
                url,
            )
            result.errors.append(issue)
            add_crawl_log(source_id or adapter.source_id, url, "error", issue["message"], job_id)
            return

        if job_id:
            prog = 58 if deep_refresh else None
            fields = {
                "message": (
                    "Extraction détaillée — type, téléphone, email…"
                    if deep_refresh
                    else "Extraction et contrôle qualité des champs…"
                ),
            }
            if prog is not None:
                fields["progress"] = prog
            update_crawl_job(job_id, **fields)

        lead = adapter.parse_listing(fetched.html, url)
        from crawler.extractors import deep_enhance_listing_contacts
        from crawler.validation import _name_ok, missing_core_fields

        def _type_uncertain(ld) -> bool:
            # Détection agence/particulier peu fiable : on veut trancher avant
            # d'enregistrer plutôt que d'affirmer un type à tort.
            audit = ld.raw_extras.get("publisher_audit") or {}
            return audit.get("confidence") == "low"

        def _needs_contact_pass(ld) -> bool:
            # On veut récupérer obligatoirement contact + nom ET un type fiable :
            # on relance l'extraction profonde dès qu'il manque téléphone, email,
            # le nom du vendeur, ou que le type agence/particulier est incertain
            # (le passage « révéler le contact » fait souvent apparaître nom,
            # numéro et indices agence/particulier en même temps).
            miss = missing_core_fields(ld)
            if any(f in miss for f in ("phone", "email")):
                return True
            if not _name_ok(ld.first_name, ld.last_name):
                return True
            return _type_uncertain(ld)

        def _needs_contact_refetch(ld) -> bool:
            # Le 2e passage navigateur (click « révéler le contact ») est coûteux :
            # on ne le déclenche QUE s'il manque vraiment tout moyen de contact
            # (ni téléphone ni email) ou le nom du vendeur. Une simple incertitude
            # de type, ou un seul canal manquant, ne justifie pas un re-fetch complet.
            if not _name_ok(ld.first_name, ld.last_name):
                return True
            return not ld.phone and not ld.email

        core_miss = missing_core_fields(lead)
        if _needs_contact_pass(lead):
            lead = deep_enhance_listing_contacts(fetched.html, url, lead)
            core_miss = missing_core_fields(lead)
        if _needs_contact_refetch(lead) and not deep_refresh:
            if job_id:
                update_crawl_job(
                    job_id,
                    message="2e passage — révéler téléphone / email / nom sur la fiche…",
                )
            micro_pause()
            fetched_contacts = fetch_page(
                url,
                click_contacts=True,
                prefer_browser=True,
                scroll_lazy=True,
            )
            if fetched_contacts.ok:
                lead = adapter.parse_listing(fetched_contacts.html, url)
                lead = deep_enhance_listing_contacts(fetched_contacts.html, url, lead)
        if deep_refresh:
            lead = deep_enhance_listing_contacts(fetched.html, url, lead)
            if job_id:
                type_lbl = "agence" if lead.type == "agence" else "particulier"
                contact_bits = []
                if lead.phone:
                    contact_bits.append("tél.")
                if lead.email:
                    contact_bits.append("email")
                extra = f" — {', '.join(contact_bits)}" if contact_bits else ""
                update_crawl_job(
                    job_id,
                    progress=68,
                    message=f"Type : {type_lbl}{extra} — enregistrement…",
                )

        from crawler.validation import repair_mixed_listing

        lead = repair_mixed_listing(lead, fetched.html, url)
        self._boost_listing_address(lead, fetched.html, url)
        self._clean_unreliable_fields(lead)

        # Crawl strictement local : on rejette toute annonce hors de la ville cible.
        if not skip_city_check and not self._lead_in_target_city(lead):
            result.out_of_city += 1
            add_crawl_log(
                source_id or adapter.source_id,
                url,
                "skip_city",
                f"Hors zone — annonce ignorée (≠ {self._crawl_city})",
                job_id,
            )
            return

        coherent, coh_reason = self._coherence_check(
            url, fetched.html, lead, import_mode=import_mode
        )
        if not coherent:
            lead, fetched, coherent, coh_reason = self._retry_listing_verification(
                url,
                adapter,
                lead,
                fetched,
                import_mode=import_mode,
                deep_refresh=deep_refresh,
                job_id=job_id,
            )
        if not coherent:
            existing_row = get_lead_by_source_url(url, None)
            was_retired = (existing_row or {}).get("status") == "retire"
            lead, fetched, coherent, coh_reason = self._try_repair_incoherent_listing(
                url,
                lead,
                fetched,
                existing_row,
                adapter=adapter,
                deep_refresh=deep_refresh,
                import_mode=import_mode,
                job_id=job_id,
                coh_reason=coh_reason,
            )
            if coherent:
                note = f"Fiche réparée (mix/hub corrigé) — {coh_reason[:80]}"
                if self._save_repaired_lead(
                    lead,
                    url,
                    result,
                    source_id,
                    adapter,
                    job_id,
                    repair_note=note,
                    was_retired=was_retired,
                ):
                    return

            if existing_row and not import_mode:
                _, strict_reason = self._coherence_check_strict(url, fetched.html, lead)
                if not should_withdraw_incoherent(coh_reason) and not should_withdraw_incoherent(
                    strict_reason
                ):
                    from crawler.config import SAVE_MINIMAL_LEADS

                    if SAVE_MINIMAL_LEADS:
                        saved_try = save_lead(
                            lead,
                            source_id=source_id or adapter.source_id,
                            job_id=job_id,
                            agency_id=self._agency_id,
                            deep_refresh=deep_refresh,
                        )
                        if saved_try and saved_try.get("id"):
                            result.leads_found += 1
                            if saved_try.get("created"):
                                result.leads_saved += 1
                            else:
                                result.leads_updated += 1
                            add_crawl_log(
                                source_id or adapter.source_id,
                                url,
                                "saved_partial",
                                f"Fiche conservée (données partielles) — {coh_reason[:60]}",
                                job_id,
                            )
                            return
                    add_crawl_log(
                        source_id or adapter.source_id,
                        url,
                        "skipped_withdraw",
                        f"Non retirée (filtre léger) — {coh_reason[:80]}",
                        job_id,
                    )
                    return
                lid = int(existing_row["id"])
                if withdraw_lead_incoherent(
                    lid,
                    self._agency_id or "",
                    reason=coh_reason,
                    source_id=source_id or adapter.source_id,
                    job_id=job_id,
                ):
                    result.leads_found += 1
                    result.leads_updated += 1
                    if job_id:
                        update_crawl_job(
                            job_id,
                            message=f"Fiche retirée (vérifiée) — {coh_reason[:55]}",
                            leads_updated=result.leads_updated,
                        )
                else:
                    add_crawl_log(
                        source_id or adapter.source_id,
                        url,
                        "withdrawn",
                        f"Retrait échoué — {coh_reason[:120]}",
                        job_id,
                    )
                return

            from crawler.config import SAVE_MINIMAL_LEADS

            if import_mode or SAVE_MINIMAL_LEADS:
                saved_try = save_lead(
                    lead,
                    source_id=source_id or adapter.source_id,
                    job_id=job_id,
                    agency_id=self._agency_id,
                    deep_refresh=deep_refresh or import_mode,
                )
                if saved_try and saved_try.get("id"):
                    result.leads_found += 1
                    if saved_try.get("created"):
                        result.leads_saved += 1
                    else:
                        result.leads_updated += 1
                    self._submit_address_match(saved_try, lead)
                    add_crawl_log(
                        source_id or adapter.source_id,
                        url,
                        "import_partial" if import_mode else "saved_minimal",
                        "Fiche enregistrée (données partielles)"
                        if import_mode
                        else f"Enregistrement minimal — {coh_reason[:80]}",
                        job_id,
                    )
                    if job_id:
                        update_crawl_job(
                            job_id,
                            message=f"Prospect importé — {lead.owner or 'compléter la fiche'}",
                            leads_saved=result.leads_saved,
                            leads_updated=result.leads_updated,
                            leads_found=result.leads_found,
                        )
                    return

            issue = CrawlError.issue(CrawlError.INCOMPLETE_DATA, coh_reason, url)
            result.warnings.append(issue)
            add_crawl_log(
                source_id or adapter.source_id,
                url,
                "rejected",
                f"Annonce incohérente (non enregistrée) — {coh_reason}",
                job_id,
            )
            if job_id:
                update_crawl_job(
                    job_id,
                    message=f"Annonce ignorée — {coh_reason[:60]}",
                )
            return

        result.leads_found += 1

        if job_id and deep_refresh:
            update_crawl_job(job_id, progress=78, message="Enregistrement et fusion des données…")

        saved = save_lead(
            lead,
            source_id=source_id or adapter.source_id,
            job_id=job_id,
            agency_id=self._agency_id,
            deep_refresh=deep_refresh,
            veille_recrawl=is_recrawl and self._veille_mode,
        )

        if saved and not saved.get("verified"):
            if job_id:
                update_crawl_job(job_id, message="Rechargement anti-bot + nouvelle vérification…")
            fetched2 = fetch_page(url, click_contacts=True, prefer_browser=True)
            if fetched2.ok:
                lead = adapter.parse_listing(fetched2.html, url)
                if deep_refresh:
                    from crawler.extractors import deep_enhance_listing_contacts

                    lead = deep_enhance_listing_contacts(fetched2.html, url, lead)
                coherent, coh_reason = self._coherence_check(
                    url, fetched2.html, lead, import_mode=import_mode
                )
                if coherent:
                    saved = save_lead(
                        lead,
                        source_id=source_id or adapter.source_id,
                        job_id=job_id,
                        agency_id=self._agency_id,
                        deep_refresh=deep_refresh,
                        veille_recrawl=is_recrawl and self._veille_mode,
                    )

        if saved and saved.get("id") and saved.get("verified"):
            if self._dvf_queue:
                self._dvf_queue.submit_lead(
                    int(saved["id"]),
                    is_update=not saved.get("created"),
                )
            self._submit_address_match(saved, lead)
            summary = f"{lead.owner} — {lead.address or 'adresse OK'}"
            verif = saved.get("verification", "")
            if saved.get("created"):
                result.leads_saved += 1
                from crawler.lead_changes import diff_lead_fields, record_lead_change

                details = diff_lead_fields(None, lead)
                detail_txt = " · ".join(details[:3])
                add_activity(
                    "new",
                    f"Nouveau prospect — {lead.owner} — {detail_txt}",
                    self._agency_id,
                )
                record_lead_change(
                    job_id=job_id,
                    agency_id=self._agency_id,
                    lead_id=int(saved["id"]),
                    change_type="created",
                    summary=f"Nouveau — {lead.owner or 'Vendeur'}",
                    details=details,
                    source_name=adapter.source_name,
                    listing_url=url,
                    owner_label=lead.owner,
                )
                add_crawl_log(
                    source_id or adapter.source_id,
                    url,
                    "ok",
                    f"Nouveau — {summary} — {detail_txt}",
                    job_id,
                )
                if job_id:
                    update_crawl_job(
                        job_id,
                        message=f"Prospect enregistré : {lead.owner}",
                        leads_saved=result.leads_saved,
                        leads_found=result.leads_found,
                        leads_updated=result.leads_updated,
                        listings_done=result.listings_processed,
                    )
            else:
                result.leads_updated += 1
                from crawler.lead_changes import diff_lead_fields, record_lead_change

                details = diff_lead_fields(existing_row, lead)
                detail_txt = " · ".join(details[:3])
                add_activity(
                    "crawl",
                    f"Mis à jour — {lead.owner} — {detail_txt}",
                    self._agency_id,
                )
                record_lead_change(
                    job_id=job_id,
                    agency_id=self._agency_id,
                    lead_id=int(saved["id"]),
                    change_type="updated",
                    summary=f"Mise à jour — {lead.owner or 'Vendeur'}",
                    details=details,
                    source_name=adapter.source_name,
                    listing_url=url,
                    owner_label=lead.owner,
                )
                add_crawl_log(
                    source_id or adapter.source_id,
                    url,
                    "updated",
                    f"Màj — {summary} — {detail_txt}",
                    job_id,
                )
                if job_id:
                    update_crawl_job(
                        job_id,
                        message=f"Prospect mis à jour : {lead.owner}",
                        leads_saved=result.leads_saved,
                        leads_found=result.leads_found,
                        leads_updated=result.leads_updated,
                        listings_done=result.listings_processed,
                    )
        else:
            if saved and saved.get("id") and not saved.get("verified"):
                # On remonte TOUJOURS l'annonce, même incomplète : la fiche est
                # enregistrée (verified=0), étiquetée « à vérifier », et reste
                # triée plus bas grâce à son score (champs manquants). Mieux vaut
                # une piste à compléter qu'une veille qui ne ramène rien.
                detail = saved.get("verification") or format_missing_fields(lead.missing_fields())
                errs = saved.get("errors") or []
                if errs:
                    detail = f"{detail} — {', '.join(errs)}"
                self._submit_address_match(saved, lead)
                from crawler.lead_changes import diff_lead_fields, record_lead_change

                created = bool(saved.get("created"))
                details = diff_lead_fields(None, lead) if created else []
                if created:
                    result.leads_saved += 1
                else:
                    result.leads_updated += 1
                add_activity(
                    "new" if created else "crawl",
                    f"{'Nouveau prospect' if created else 'Mise à jour'} à vérifier — "
                    f"{lead.owner or 'Vendeur'} — {detail[:60]}",
                    self._agency_id,
                )
                record_lead_change(
                    job_id=job_id,
                    agency_id=self._agency_id,
                    lead_id=int(saved["id"]),
                    change_type="created" if created else "updated",
                    summary=f"À vérifier — {lead.owner or 'Vendeur'}",
                    details=details,
                    source_name=adapter.source_name,
                    listing_url=url,
                    owner_label=lead.owner,
                )
                issue = CrawlError.issue(CrawlError.INCOMPLETE_DATA, detail, url)
                result.warnings.append(issue)
                add_crawl_log(
                    source_id or adapter.source_id,
                    url,
                    "saved_unverified",
                    f"Fiche remontée à vérifier — {detail[:80]}",
                    job_id,
                )
                if job_id:
                    update_crawl_job(
                        job_id,
                        message=f"Prospect à vérifier — {lead.owner or 'compléter la fiche'}",
                        leads_saved=result.leads_saved,
                        leads_updated=result.leads_updated,
                        leads_found=result.leads_found,
                    )
            elif saved and not saved.get("verified"):
                detail = saved.get("verification") or format_missing_fields(lead.missing_fields())
                errs = saved.get("errors") or []
                if errs:
                    detail = f"{detail} — {', '.join(errs)}"
                issue = CrawlError.issue(CrawlError.INCOMPLETE_DATA, detail, url)
                result.warnings.append(issue)
                add_crawl_log(
                    source_id or adapter.source_id,
                    url,
                    "verify_failed",
                    detail,
                    job_id,
                )
                if job_id:
                    update_crawl_job(job_id, message=f"Vérification échouée — {detail[:80]}")
            else:
                snap_saved = self._save_listing_snapshot(
                    lead,
                    url,
                    adapter,
                    result,
                    source_id,
                    job_id,
                    import_mode=import_mode,
                    deep_refresh=deep_refresh,
                    is_recrawl=is_recrawl,
                )
                if snap_saved:
                    return
                missing = lead.missing_fields()
                detail = format_missing_fields(missing)
                issue = CrawlError.issue(
                    CrawlError.INCOMPLETE_DATA,
                    f"Manquant : {detail}",
                    url,
                )
                result.errors.append(issue)
                result.partial.append(lead.to_dict())
                add_crawl_log(
                    source_id or adapter.source_id,
                    url,
                    "incomplete",
                    f"Champs manquants : {detail}",
                    job_id,
                )
                if job_id:
                    update_crawl_job(
                        job_id,
                        message=f"Annonce incomplète — manque : {detail}",
                    )

        if CRAWL_SIMILAR_LISTINGS and crawl_related and fetched.html:
            patterns = adapter.config.listing_patterns
            related = find_related_listing_links(
                fetched.html,
                url,
                patterns,
                limit=MAX_LISTING_LINKS,
            )
            for rel_url in related:
                if not result.can_process_more_listings():
                    break
                if rel_url.rstrip("/") == url.rstrip("/"):
                    continue
                if job_id:
                    update_crawl_job(
                        job_id,
                        message="Annonce proposée / similaire — analyse…",
                    )
                self._process_listing(
                    rel_url,
                    adapter,
                    result,
                    source_id,
                    job_id,
                    crawl_related=False,
                )
                if result.can_process_more_listings():
                    listing_delay(is_recrawl=get_lead_by_source_url(rel_url, None) is not None)

    def scan_source(
        self, source_id: str, city: str | None = None, *, agency_id: str | None = None
    ) -> dict:
        return self.enqueue_job(
            "single_source", source_id=source_id, city=city, agency_id=agency_id
        )

    def scan_all_enabled(self, city: str | None = None, *, agency_id: str | None = None) -> dict:
        return self.enqueue_job("all_sources", city=city, agency_id=agency_id, lane="portal")

    def crawl_url(self, url: str, *, agency_id: str | None = None) -> dict:
        return self.enqueue_job("url", target_url=url, agency_id=agency_id)

    def import_listing_url(self, url: str, *, agency_id: str | None = None) -> dict:
        """Import manuel d'une seule annonce (tous sites, extraction poussée)."""
        return self.enqueue_job(
            "listing_import",
            target_url=url,
            agency_id=agency_id,
            eta_seconds=90,
            listings_total=1,
        )

    def refresh_lead(self, lead_id: int, *, agency_id: str | None = None) -> dict:
        """Recrawl approfondi d'une fiche prospect existante (lien source_url)."""
        from crawler.storage import get_lead

        if not agency_id:
            raise ValueError("agency_id requis")
        row = get_lead(lead_id, agency_id)
        if not row:
            raise ValueError("Prospect introuvable")
        url = (row.get("source_url") or "").strip()
        if not url:
            raise ValueError("Ce prospect n'a pas de lien d'annonce")
        source_id = row.get("source_id")
        eta = estimate_crawl_seconds(1, 0)
        return self.enqueue_job(
            "lead_refresh",
            target_url=url,
            source_id=source_id,
            agency_id=agency_id,
            eta_seconds=eta,
            listings_total=1,
            lane="refresh",
        )

    def start_background(self, interval: int | None = None) -> None:
        from crawler.config import (
            CRAWL_BACKGROUND_INTERVAL_SEC,
            CRAWL_LEAD_REFRESH_ENABLED,
        )

        interval = max(60, int(interval or CRAWL_BACKGROUND_INTERVAL_SEC))
        with self._lock:
            thread_alive = self._thread is not None and self._thread.is_alive()
            if self.running and thread_alive:
                self._bg_interval_sec = interval
                return
            if self.running and not thread_alive:
                logger.warning("Veille auto — thread mort, redémarrage")
                self.running = False
            self.running = True
            self._bg_interval_sec = interval
            self._thread = threading.Thread(
                target=self._background_loop,
                args=(interval,),
                daemon=True,
                name="veliora-crawl-bg",
            )
            self._thread.start()
            if CRAWL_LEAD_REFRESH_ENABLED:
                self._lead_refresh_thread = threading.Thread(
                    target=self._lead_refresh_loop,
                    daemon=True,
                    name="veliora-lead-refresh-bg",
                )
                self._lead_refresh_thread.start()
            add_activity("crawl", f"Veille auto démarrée (toutes les {interval // 60} min)")

    def stop_background(self) -> None:
        with self._lock:
            self.running = False
            add_activity("crawl", "Veille automatique en pause")

    def _background_loop(self, interval: int) -> None:
        from crawler.storage import (
            get_agency_primary_city,
            get_pending_or_running_crawl_job,
            list_agency_ids,
        )

        while self.running:
            try:
                self._run_lead_refresh_pass()
                for agency_id in list_agency_ids():
                    if not self.running:
                        break
                    city = (get_agency_primary_city(agency_id) or "").strip() or None
                    if get_pending_or_running_crawl_job(agency_id, lane="portal"):
                        logger.debug(
                            "Veille portails — job déjà actif pour %s",
                            agency_id,
                        )
                    else:
                        self.enqueue_job(
                            "veille_auto",
                            city=city,
                            agency_id=agency_id,
                            lane="portal",
                        )
            except Exception as exc:
                logger.exception("Background crawl: %s", exc)
            wait = self._bg_interval_sec if self.running else interval
            for _ in range(wait):
                if not self.running:
                    break
                time.sleep(1)

    def _lead_refresh_loop(self) -> None:
        from crawler.config import CRAWL_LEAD_REFRESH_INTERVAL_SEC

        while self.running:
            try:
                self._run_lead_refresh_pass()
            except Exception as exc:
                logger.exception("Lead refresh background: %s", exc)
            for _ in range(CRAWL_LEAD_REFRESH_INTERVAL_SEC):
                if not self.running:
                    break
                time.sleep(1)

    def _run_lead_refresh_pass(self) -> None:
        from crawler.config import (
            CRAWL_LEAD_REFRESH_MAX_PER_RUN,
            CRAWL_LEAD_REFRESH_STALE_HOURS,
        )
        from crawler.storage import (
            get_crawl_job,
            get_leads_stale_for_refresh,
            get_pending_or_running_crawl_job,
            list_agency_ids,
        )

        for agency_id in list_agency_ids():
            if not self.running:
                break
            if get_pending_or_running_crawl_job(agency_id, lane="refresh"):
                continue
            stale = get_leads_stale_for_refresh(
                agency_id,
                limit=CRAWL_LEAD_REFRESH_MAX_PER_RUN,
                stale_hours=CRAWL_LEAD_REFRESH_STALE_HOURS,
            )
            for row in stale:
                if not self.running:
                    break
                if get_pending_or_running_crawl_job(agency_id, lane="refresh"):
                    break
                lead_id = row.get("id")
                if not lead_id:
                    continue
                try:
                    job = self.refresh_lead(int(lead_id), agency_id=agency_id)
                except Exception as exc:
                    logger.warning("refresh_lead %s: %s", lead_id, exc)
                    continue
                job_id = (job or {}).get("id")
                if not job_id:
                    continue
                for _ in range(180):
                    if not self.running:
                        break
                    j = get_crawl_job(job_id, agency_id)
                    if not j:
                        break
                    st = (j.get("status") or "").lower()
                    if st in ("completed", "failed", "cancelled"):
                        break
                    time.sleep(2)

    def status(self) -> dict:
        from crawler.config import background_crawl_config
        from crawler.storage import get_active_crawl_job

        active = get_active_crawl_job()
        bg_alive = self._thread is not None and self._thread.is_alive()
        lead_alive = (
            self._lead_refresh_thread is not None and self._lead_refresh_thread.is_alive()
        )
        return {
            "running": self.running,
            "background_thread_alive": bg_alive,
            "lead_refresh_thread_alive": lead_alive,
            "veille_effective": self.running and bg_alive,
            "active_job": active,
            "is_crawling": active is not None,
            "background_interval_sec": self._bg_interval_sec,
            **background_crawl_config(),
        }


def bootstrap_background_services() -> None:
    """Démarre la veille auto au boot si CRAWL_AUTO_START=true (idempotent)."""
    from crawler.config import CRAWL_AUTO_START
    from crawler.proxy_manager import warm_proxy_pool_async

    warm_proxy_pool_async()

    if not CRAWL_AUTO_START:
        logger.info("Veille auto au boot désactivée (CRAWL_AUTO_START=false)")
        return
    if engine.running:
        return
    engine.start_background()
    logger.info("Veille auto démarrée au boot")


engine = CrawlerEngine()
