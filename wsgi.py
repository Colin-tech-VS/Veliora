"""Point d'entrée WSGI — Gunicorn / Scalingo."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from app import app as application  # noqa: E402


def _bootstrap_db() -> None:
    from crawler.storage import init_db, mark_crawl_jobs_interrupted_on_startup

    init_db()
    mark_crawl_jobs_interrupted_on_startup()


_bootstrap_db()

# Workers Gunicorn (--preload) : une seule init DB partagée
application.config["PRELOADED"] = True
