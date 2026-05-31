"""Configuration base Veliora — SQLite ou Supabase (DATABASE_URL)."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_SQLITE = Path(__file__).resolve().parent.parent / "data" / "propscout.db"


def database_url() -> str | None:
    raw = (
        os.getenv("DATABASE_URL")
        or os.getenv("SUPABASE_DB_URL")
        or ""
    ).strip()
    return raw or None


def sqlite_path() -> Path:
    return Path(os.getenv("VELIORA_DB_PATH", str(_DEFAULT_SQLITE))).expanduser().resolve()


def is_postgres() -> bool:
    url = database_url()
    if not url:
        return False
    low = url.lower()
    return low.startswith("postgres://") or low.startswith("postgresql://")


def backend_name() -> str:
    return "supabase" if is_postgres() else "sqlite"


def database_url_configured() -> bool:
    return bool(database_url())
