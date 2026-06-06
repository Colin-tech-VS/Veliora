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
    except Exception:
        logging.exception("Impossible d'initialiser la base au démarrage")


# IMPORTANT — ne PAS appeler _bootstrap_db() ici (au niveau module).
#
# Avec ``--preload``, Gunicorn importe ce module dans le master pendant
# ``Arbiter.setup()``, AVANT de créer/binder le socket sur ``$PORT``. Or
# ``init_db()`` ouvre une connexion Supabase (handshake pooler), crée/migre les
# tables et exécute ``secure_public_schema_rls()`` qui parcourt et verrouille
# TOUTES les tables ``public`` — un travail qui s'allonge avec le schéma. Le
# faire ici bloquait le bind du port et provoquait un « timeout at boot » sur
# Scalingo.
#
# L'init est désormais : (1) faite en phase ``release`` (scripts/release.py),
# (2) relancée en arrière-plan dans ``gunicorn.conf.py`` post_fork (sans
# bloquer le worker), et (3) garantie paresseusement par ``ensure_db()``
# (@before_request) avant toute requête qui touche la base.

# Workers Gunicorn (--preload) : une seule init DB partagée
application.config["PRELOADED"] = True
