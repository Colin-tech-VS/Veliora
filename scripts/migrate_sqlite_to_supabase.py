#!/usr/bin/env python3
"""Copie les données SQLite locales vers Supabase (projet Veliora).

Prérequis :
  1. Projet Supabase « Veliora » créé + velora_db/postgres_schema.sql exécuté
  2. DATABASE_URL dans .env (URI PostgreSQL, mode Transaction pooler)
  3. pip install psycopg[binary]

Usage :
  python scripts/migrate_sqlite_to_supabase.py
  python scripts/migrate_sqlite_to_supabase.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

TABLES_ORDER = [
    "agencies",
    "agency_users",
    "agency_settings",
    "agency_scoring_weights",
    "agency_legal_profiles",
    "sources",
    "leads",
    "crawl_jobs",
    "crawl_logs",
    "activities",
    "auth_sessions",
    "password_reset_tokens",
    "lead_price_history",
    "lead_outcomes",
    "seller_mandates",
    "property_clients",
    "mandate_dossiers",
    "dvf_commune_cache",
]


def sqlite_db() -> Path:
    return Path(os.getenv("VELIORA_DB_PATH", str(ROOT / "data" / "propscout.db")))


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("DATABASE_URL manquant dans .env", file=sys.stderr)
        sys.exit(1)

    db_file = sqlite_db()
    if not db_file.is_file():
        print(f"SQLite introuvable : {db_file}", file=sys.stderr)
        sys.exit(1)

    import psycopg
    from psycopg.rows import dict_row

    sq = sqlite3.connect(str(db_file))
    sq.row_factory = sqlite3.Row

    url = os.environ["DATABASE_URL"]
    pg = psycopg.connect(url, row_factory=dict_row)

    for table in TABLES_ORDER:
        try:
            rows = sq.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        if not rows:
            print(f"  {table}: 0 ligne")
            continue
        cols = list(rows[0].keys())
        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(cols)
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        data = [tuple(r[c] for c in cols) for r in rows]
        print(f"  {table}: {len(data)} ligne(s)")
        if args.dry_run:
            continue
        with pg.cursor() as cur:
            for row in data:
                try:
                    cur.execute(sql, row)
                except Exception as exc:
                    print(f"    skip: {exc}")
            pg.commit()

    pg.close()
    sq.close()
    print("Migration terminée." if not args.dry_run else "Dry-run OK.")


if __name__ == "__main__":
    main()
