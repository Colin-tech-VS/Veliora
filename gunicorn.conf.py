"""Config Gunicorn — Scalingo / prod.

Les threads de veille auto ne doivent PAS démarrer dans le master ``--preload``
(sinon ``engine.running=True`` dans le worker mais aucun thread actif).

Boot Scalingo : le worker doit binder ``$PORT`` au plus vite. L'init base
(connexion Supabase, migrations, RLS) et le démarrage des services de fond se
font donc dans un thread daemon — jamais en synchrone dans post_fork, qui
retarderait l'acceptation des connexions et provoquerait un « timeout at boot ».
"""

from __future__ import annotations

import threading


def post_fork(server, worker):  # noqa: ARG001
    from crawler.engine import engine

    # État hérité du master preload : running=True sans thread vivant.
    engine.running = False
    engine._thread = None
    engine._lead_refresh_thread = None

    def _boot() -> None:
        # Init base d'abord (idempotente avec la phase release et ensure_db),
        # puis services de fond (veille auto, notifications, pool proxies).
        #
        # ``ensure_db_ready`` pose le drapeau ``app._db_ready`` partagé : une fois
        # l'init faite ici en arrière-plan, la première requête ``/api`` la trouve
        # déjà prête au lieu de tout ré-exécuter en synchrone (cause d'« application
        # timeout » sur Scalingo). En cas d'échec ici, le repli paresseux de
        # ``ensure_db`` (@before_request) retentera proprement à la première requête.
        import logging

        from crawler.engine import bootstrap_background_services
        from app import ensure_db_ready

        try:
            ensure_db_ready()
        except Exception:
            logging.exception("Init DB au boot — repli paresseux à la 1re requête")
        bootstrap_background_services()

    threading.Thread(target=_boot, name="veliora-boot", daemon=True).start()
