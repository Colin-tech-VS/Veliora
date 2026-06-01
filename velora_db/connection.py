"""Connexions SQLite et Supabase PostgreSQL (interface compatible storage.py)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from velora_db.config import backend_name, database_url, is_postgres, sqlite_path


class DatabaseBusyError(RuntimeError):
    """Pool Postgres saturé (crawl + pics de requêtes)."""
from velora_db.sql_adapt import adapt_sql

logger = logging.getLogger(__name__)

_pg_pool = None
_pg_pool_failed = False


class DbCursor:
    """Curseur unifié (fetchone / fetchall / lastrowid / rowcount)."""

    def __init__(self, raw, *, postgres: bool, returning: bool) -> None:
        self._raw = raw
        self._postgres = postgres
        self._returning = returning
        self.lastrowid: int | None = None
        if postgres and returning:
            row = raw.fetchone()
            if row is not None:
                self.lastrowid = row[0] if isinstance(row, (tuple, list)) else row.get("id")

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()


class DbConnection:
    """API proche sqlite3.Connection pour le code existant."""

    def __init__(self, raw, *, postgres: bool) -> None:
        self._raw = raw
        self._postgres = postgres
        self.row_factory = None

    def execute(self, sql: str, params: tuple | list | None = None):
        adapted = adapt_sql(sql, postgres=self._postgres)
        returning = self._postgres and "RETURNING" in adapted.upper()
        if self._postgres:
            cur = self._raw.cursor()
            cur.execute(adapted, params or ())
            return DbCursor(cur, postgres=True, returning=returning)
        return self._raw.execute(adapted, params or [])

    def executescript(self, script: str) -> None:
        if self._postgres:
            for stmt in script.split(";"):
                part = stmt.strip()
                if part:
                    self.execute(part)
            return
        self._raw.executescript(script)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def _sqlite_connection() -> DbConnection:
    path = sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return DbConnection(conn, postgres=False)


def _get_postgres_pool():
    """Pool partagé Supabase — évite une poignée TCP par requête API."""
    global _pg_pool, _pg_pool_failed
    if _pg_pool_failed:
        return None
    if _pg_pool is not None:
        return _pg_pool
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        url = database_url()
        if not url:
            _pg_pool_failed = True
            return None
        pool_max = int(os.getenv("DATABASE_POOL_MAX", "8"))
        pool_timeout = float(os.getenv("DATABASE_POOL_TIMEOUT", "12"))
        pool_waiting = int(os.getenv("DATABASE_POOL_MAX_WAITING", "40"))
        _pg_pool = ConnectionPool(
            url,
            min_size=0,
            max_size=pool_max,
            timeout=pool_timeout,
            max_waiting=pool_waiting,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        logger.info(
            "Pool PostgreSQL Veliora actif (max=%s, timeout=%ss)",
            pool_max,
            pool_timeout,
        )
        return _pg_pool
    except Exception as exc:
        logger.warning("Pool PostgreSQL indisponible, connexion directe : %s", exc)
        _pg_pool_failed = True
        return None


def _postgres_connection() -> DbConnection:
    import psycopg
    from psycopg.rows import dict_row

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL manquant pour Supabase")
    raw = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    return DbConnection(raw, postgres=True)


@contextmanager
def get_connection():
    if is_postgres():
        pool = _get_postgres_pool()
        if pool is not None:
            try:
                with pool.connection() as raw:
                    yield DbConnection(raw, postgres=True)
            except Exception as exc:
                name = type(exc).__name__
                if name == "PoolTimeout" or "PoolTimeout" in name:
                    raise DatabaseBusyError(
                        "Connexions base saturées — réessayez dans quelques secondes"
                    ) from exc
                raise
            return
        conn = _postgres_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    conn = _sqlite_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_status() -> dict:
    if is_postgres():
        url = database_url() or ""
        masked = url.split("@")[-1] if "@" in url else "postgresql"
        return {
            "backend": "supabase",
            "path": masked,
            "exists": True,
            "size_bytes": 0,
            "writable": True,
        }
    path = sqlite_path()
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    return {
        "backend": "sqlite",
        "path": str(path),
        "exists": exists,
        "size_bytes": size,
        "writable": path.parent.exists() and path.parent.is_dir(),
    }


def row_scalar(row) -> int:
    """Première colonne d'un fetchone() — SQLite (index 0) ou PostgreSQL (dict)."""
    if row is None:
        return 0
    if isinstance(row, dict):
        if not row:
            return 0
        for key in ("count", "c", "cnt", "n"):
            if key in row and row[key] is not None:
                return int(row[key])
        return int(next(iter(row.values())) or 0)
    try:
        return int(row[0] or 0)
    except (KeyError, TypeError, IndexError):
        return int(list(row)[0] or 0) if row else 0


def checkpoint_database() -> None:
    if is_postgres():
        return
    try:
        with get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


def backup_database(max_backups: int = 14, min_hours_between: int = 6) -> Path | None:
    if is_postgres():
        return None
    import shutil

    path = sqlite_path()
    if not path.is_file():
        return None
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(backup_dir.glob("propscout_*.db"), reverse=True)
    if existing and min_hours_between > 0:
        try:
            age_h = (
                datetime.now(timezone.utc)
                - datetime.fromtimestamp(existing[0].stat().st_mtime, tz=timezone.utc)
            ).total_seconds() / 3600
            if age_h < min_hours_between:
                return None
        except OSError:
            pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"propscout_{stamp}.db"
    shutil.copy2(path, dest)
    for old in sorted(backup_dir.glob("propscout_*.db"), reverse=True)[max_backups:]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def _split_sql_script(script: str) -> list[str]:
    """Découpe un script DDL en statements (ignore commentaires ligne --)."""
    chunks: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buf.append(line)
        if stripped.endswith(";"):
            chunks.append("\n".join(buf))
            buf = []
    if buf:
        chunks.append("\n".join(buf))
    return [c.strip().rstrip(";").strip() for c in chunks if c.strip()]


def init_postgres_schema() -> None:
    schema_path = Path(__file__).resolve().parent / "postgres_schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    import psycopg
    from psycopg.rows import dict_row

    url = database_url()
    statements = _split_sql_script(sql)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    logger.info("Schéma PostgreSQL Veliora (%d statements) appliqué", len(statements))
