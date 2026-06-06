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
        from crawler.engine import bootstrap_background_services
        from wsgi import _bootstrap_db

        _bootstrap_db()
        bootstrap_background_services()

    threading.Thread(target=_boot, name="veliora-boot", daemon=True).start()
