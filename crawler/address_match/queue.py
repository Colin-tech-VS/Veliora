"""Rapprochement d'adresse en parallèle pendant le crawl (I/O réseau).

Même conception que `DvfParallelQueue` : les appels aux API publiques (DPE, BAN,
cadastre) sont lents et indépendants ; on les exécute dans un pool de threads
pendant que Playwright continue de scraper. Standardisé pour TOUTES les sources.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from crawler.config import (
    ADDRESS_MATCH_DRAIN_TIMEOUT_SEC,
    ADDRESS_MATCH_DURING_CRAWL,
    ADDRESS_MATCH_WORKERS,
)

logger = logging.getLogger(__name__)


class AddressMatchQueue:
    """File de rapprochements d'adresse exécutés en arrière-plan pendant un crawl."""

    def __init__(self, agency_id: str, workers: int | None = None) -> None:
        self.agency_id = agency_id
        self._workers = workers or ADDRESS_MATCH_WORKERS
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list[Future] = []
        self._lock = threading.Lock()
        self.stats = {"submitted": 0, "completed": 0, "resolved": 0, "unresolved": 0, "errors": 0}

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers, thread_name_prefix="veliora-addr"
            )
        return self._executor

    def submit_lead(self, lead_id: int) -> None:
        if not ADDRESS_MATCH_DURING_CRAWL or not lead_id:
            return

        def _task() -> dict:
            return resolve_and_store_lead_address(int(lead_id), self.agency_id)

        fut = self._ensure_executor().submit(_task)
        with self._lock:
            self._futures.append(fut)
            self.stats["submitted"] += 1

    def drain(self, timeout: float | None = None) -> dict:
        timeout = timeout if timeout is not None else ADDRESS_MATCH_DRAIN_TIMEOUT_SEC
        with self._lock:
            pending = list(self._futures)
            self._futures.clear()
        if not pending:
            return dict(self.stats)

        per_task = min(45.0, max(8.0, timeout / max(len(pending), 1)))
        deadline = time.monotonic() + timeout
        for fut in pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fut.cancel()
                with self._lock:
                    self.stats["completed"] += 1
                    self.stats["errors"] += 1
                continue
            try:
                res = fut.result(timeout=min(per_task, remaining))
                with self._lock:
                    self.stats["completed"] += 1
                    if res.get("error"):
                        self.stats["errors"] += 1
                    elif res.get("adresse_probable"):
                        self.stats["resolved"] += 1
                    else:
                        self.stats["unresolved"] += 1
            except Exception as exc:
                logger.warning("Address-match parallèle — échec lead: %s", str(exc)[:160])
                with self._lock:
                    self.stats["completed"] += 1
                    self.stats["errors"] += 1

        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=False)
            self._executor = None
        return dict(self.stats)

    def summary_line(self) -> str:
        s = self.stats
        if s["submitted"] == 0:
            return ""
        line = f"Adresses : {s['resolved']} estimée(s)"
        if s["unresolved"]:
            line += f", {s['unresolved']} sans candidat fiable"
        if s["errors"]:
            line += f", {s['errors']} erreur(s)"
        return line


def resolve_and_store_lead_address(lead_id: int, agency_id: str) -> dict:
    """Résout l'adresse d'un lead et persiste le résultat. Idempotent."""
    from crawler.address_match.resolver import resolve_address_for_lead
    from crawler.address_match.storage import get_lead_features, save_address_match
    from crawler.storage import get_lead

    lead = get_lead(lead_id, agency_id)
    if not lead:
        return {"error": True, "reason": "Prospect introuvable", "lead_id": lead_id}
    # Caractéristiques fines persistées au crawl (DPE, pièces, année…).
    stored_feats = get_lead_features(lead_id, agency_id)
    if stored_feats:
        lead["listing_features"] = stored_feats
    try:
        resolution = resolve_address_for_lead(lead)
    except Exception as exc:
        logger.warning("resolve_address lead %s: %s", lead_id, str(exc)[:160])
        return {"error": True, "reason": str(exc)[:200], "lead_id": lead_id}
    save_address_match(lead_id, agency_id, resolution)
    resolution["lead_id"] = lead_id
    return resolution
