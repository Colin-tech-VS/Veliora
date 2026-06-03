"""Config Gunicorn — Scalingo / prod.

Les threads de veille auto ne doivent PAS démarrer dans le master ``--preload``
(sinon ``engine.running=True`` dans le worker mais aucun thread actif).
"""

from __future__ import annotations


def post_fork(server, worker):  # noqa: ARG001
    from crawler.engine import engine, bootstrap_background_services

    # État hérité du master preload : running=True sans thread vivant.
    engine.running = False
    engine._thread = None
    engine._lead_refresh_thread = None
    bootstrap_background_services()
