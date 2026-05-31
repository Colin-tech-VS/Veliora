"""Réinitialise le schéma PostgreSQL/Supabase (one-off Scalingo).

Recrée toutes les tables à partir de velora_db/postgres_schema.sql — utile
après une correction de types (ex. BOOLEAN -> SMALLINT). Refuse d'agir si la
base contient déjà des données, sauf option --force.

Usage (Scalingo) :
    scalingo --app veliora run --detached python scripts/pg_reset_schema.py --yes
"""

from __future__ import annotations

import sys

import psycopg

from velora_db.config import database_url

_GUARD_TABLES = ("agencies", "agency_users", "leads", "sources", "seller_mandates")
_FLAG_COLUMNS = ("enabled", "is_custom", "active", "used", "onboarding_completed")


def main() -> int:
    confirm = "--yes" in sys.argv
    force = "--force" in sys.argv

    url = database_url()
    if not url:
        print("ERREUR: DATABASE_URL absent — rien à faire.")
        return 1

    conn = psycopg.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    counts: dict[str, int | None] = {}
    for t in _GUARD_TABLES:
        try:
            cur.execute("SELECT count(*) FROM " + t)
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = None  # table absente
    print("COUNTS:", counts)

    total = sum(v for v in counts.values() if isinstance(v, int))
    if total > 0 and not force:
        print("ABORT: %d lignes présentes. Utilisez --force pour écraser." % total)
        return 2
    if not confirm:
        print("DRY-RUN: ajoutez --yes pour exécuter le reset.")
        return 0

    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute('DROP TABLE IF EXISTS public."%s" CASCADE' % t)
    print("DROPPED:", len(tables), "tables")
    conn.close()

    from velora_db.connection import init_postgres_schema
    init_postgres_schema()

    from crm.mandates.storage import ensure_mandate_tables
    from crawler.storage import get_connection
    with get_connection() as c2:
        ensure_mandate_tables(c2)
    print("SCHEMA RECREATED")

    conn = psycopg.connect(url)
    cur = conn.cursor()
    cur.execute(
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='public' AND column_name = ANY(%s) "
        "ORDER BY table_name, column_name",
        (list(_FLAG_COLUMNS),),
    )
    for r in cur.fetchall():
        print("TYPE:", r[0], r[1], "=>", r[2])
    conn.close()

    print("MAINT DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
