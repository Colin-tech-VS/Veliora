"""Veliora — base de données SQLite (local) ou Supabase PostgreSQL (en ligne)."""

from velora_db.config import backend_name, is_postgres, database_url_configured
from velora_db.connection import (
    backup_database,
    checkpoint_database,
    db_status,
    get_connection,
)

__all__ = [
    "get_connection",
    "db_status",
    "checkpoint_database",
    "backup_database",
    "is_postgres",
    "backend_name",
    "database_url_configured",
]
