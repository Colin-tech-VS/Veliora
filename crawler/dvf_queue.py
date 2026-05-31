"""Comparatif DVF en parallèle pendant le crawl (I/O réseau, ne bloque pas Playwright)."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from crawler.config import (
    DVF_PARALLEL_DURING_CRAWL,
    DVF_PARALLEL_WORKERS,
    DVF_QUEUE_DRAIN_TIMEOUT_SEC,
)

logger = logging.getLogger(__name__)


class DvfParallelQueue:
    """File de comparatifs DVF exécutés en arrière-plan pendant un job de crawl."""

    def __init__(self, agency_id: str, workers: int | None = None) -> None:
        self.agency_id = agency_id
        self._workers = workers or DVF_PARALLEL_WORKERS
        self._executor: ThreadPoolExecutor | None = None
        self._futures: list[Future] = []
        self._lock = threading.Lock()
        self.stats = {"submitted": 0, "completed": 0, "ok": 0, "unavailable": 0, "errors": 0}

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers,
                thread_name_prefix="veliora-dvf",
            )
        return self._executor

    def submit_lead(self, lead_id: int, *, is_update: bool = False) -> None:
        if not DVF_PARALLEL_DURING_CRAWL or not lead_id:
            return
        from crawler.storage import compare_and_enrich_lead_dvf

        def _task() -> dict:
            return compare_and_enrich_lead_dvf(
                int(lead_id),
                self.agency_id,
                force_recompare=is_update,
            )

        fut = self._ensure_executor().submit(_task)
        with self._lock:
            self._futures.append(fut)
            self.stats["submitted"] += 1

    def drain(self, timeout: float | None = None) -> dict:
        """Attend la fin des tâches DVF (appelé en fin de crawl)."""
        timeout = timeout if timeout is not None else DVF_QUEUE_DRAIN_TIMEOUT_SEC
        with self._lock:
            pending = list(self._futures)
            self._futures.clear()

        if not pending:
            return dict(self.stats)

        done = 0
        for fut in pending:
            try:
                comp = fut.result(timeout=timeout)
                done += 1
                with self._lock:
                    self.stats["completed"] += 1
                    if comp.get("error"):
                        self.stats["errors"] += 1
                    elif comp.get("available"):
                        self.stats["ok"] += 1
                    else:
                        self.stats["unavailable"] += 1
            except Exception as exc:
                logger.warning("DVF parallèle — échec lead: %s", exc)
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
        return (
            f"DVF : {s['ok']} comparatif(s) OK"
            f"{f', {s['unavailable']} sans données' if s['unavailable'] else ''}"
            f"{f', {s['errors']} erreur(s)' if s['errors'] else ''}"
        )
