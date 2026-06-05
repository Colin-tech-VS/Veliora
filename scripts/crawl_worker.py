#!/usr/bin/env python3
"""Worker crawl dédié — Playwright + proxies résidentiels (PC / VPS).

Le CRM peut rester sur Scalingo (CRAWL_AUTO_START=false) ; ce process
exécute la veille et le recrawl via la même DATABASE_URL Supabase.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
load_dotenv(ROOT / "scripts" / "crawl-worker-local.env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("crawl_worker")


def _check_readiness() -> None:
    from crawler.config import CRAWL_PLAYWRIGHT_ENABLED, antibot_portals_readiness, proxies_enabled

    if not CRAWL_PLAYWRIGHT_ENABLED:
        logger.warning("CRAWL_PLAYWRIGHT_ENABLED=false — LBC/PAP/SeLoger resteront bloqués.")
    if not proxies_enabled():
        logger.warning("CRAWL_PROXIES vide — risque élevé de blocage anti-bot.")
    readiness = antibot_portals_readiness()
    logger.info("Préparation anti-bot : %s", readiness)


def main() -> None:
    from crawler.engine import bootstrap_background_services, engine
    from crawler.storage import init_db, mark_crawl_jobs_interrupted_on_startup

    init_db()
    mark_crawl_jobs_interrupted_on_startup()
    _check_readiness()
    bootstrap_background_services()
    logger.info(
        "Worker crawl actif (veille=%s, interval=%ss). Ctrl+C pour arrêter.",
        engine.running,
        engine._bg_interval_sec,
    )

    def _stop(*_args) -> None:
        logger.info("Arrêt du worker…")
        engine.stop_background()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
