"""Point d'entrée WSGI — Gunicorn / Scalingo."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from app import app as application  # noqa: E402


# IMPORTANT — ne PAS initialiser la base ici (au niveau module).
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
# (2) relancée en arrière-plan dans ``gunicorn.conf.py`` post_fork via
# ``app.ensure_db_ready()`` (sans bloquer le worker), et (3) garantie
# paresseusement par ce même ``ensure_db_ready`` depuis ``ensure_db``
# (@before_request). Ces deux chemins partagent un verrou et le drapeau
# ``app._db_ready`` : l'init n'a donc lieu qu'UNE fois, et la première requête
# ``/api`` ne la ré-exécute jamais en synchrone.

# Workers Gunicorn (--preload) : une seule init DB partagée
application.config["PRELOADED"] = True
