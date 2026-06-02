"""Point d'entrée WSGI — Gunicorn / Scalingo."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from app import app as application  # noqa: E402


def _bootstrap_db() -> None:
    from crawler.storage import init_db, mark_crawl_jobs_interrupted_on_startup

    try:
        init_db()
        mark_crawl_jobs_interrupted_on_startup()
    except Exception as exc:
        logging.exception("Impossible d'initialiser la base au démarrage")


_bootstrap_db()

# Workers Gunicorn (--preload) : une seule init DB partagée
application.config["PRELOADED"] = True
