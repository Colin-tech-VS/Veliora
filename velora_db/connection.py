"""Connexions SQLite et Supabase PostgreSQL (interface compatible storage.py)."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from velora_db.config import backend_name, database_url, is_postgres, sqlite_path


class DatabaseBusyError(RuntimeError):
    """Pool Postgres saturé (crawl + pics de requêtes)."""
from velora_db.sql_adapt import adapt_sql

logger = logging.getLogger(__name__)

_pg_pool = None
_pg_pool_failed = False

# Cache de résolution IPv4 par hôte — getaddrinfo n'est appelé qu'une fois.
_ipv4_cache: dict[str, str] = {}


def _dns_a_lookup(host: str, dns_server: str = "1.1.1.1") -> list[str]:
    """Recherche le ou les enregistrements A via un DNS public.

    Ce fallback est utile quand la résolution IPv4 locale échoue mais que
    l’IP A existe réellement dans le DNS Supabase.
    """
    try:
        import random
        import struct

        transaction_id = random.randrange(0, 65536)
        qname = b"".join(
            len(label).to_bytes(1, "big") + label.encode("ascii")
            for label in host.split(".")
        ) + b"\x00"
        packet = struct.pack(
            ">HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0
        ) + qname + struct.pack(
            ">HH", 1, 1
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.sendto(packet, (dns_server, 53))
            data, _ = sock.recvfrom(512)
        if len(data) < 12:
            return []
        resp_id, _, qdcount, ancount, _, _ = struct.unpack(
            ">HHHHHH", data[:12]
        )
        if resp_id != transaction_id or ancount == 0:
            return []
        offset = 12
        for _ in range(qdcount):
            while True:
                length = data[offset]
                offset += 1
                if length == 0:
                    break
                offset += length
            offset += 4
        addrs: list[str] = []
        for _ in range(ancount):
            if offset + 12 > len(data):
                break
            if data[offset] & 0xC0 == 0xC0:
                offset += 2
            else:
                while True:
                    length = data[offset]
                    offset += 1
                    if length == 0:
                        break
                    offset += length
            atype, aclass, _, rdlength = struct.unpack(
                ">HHIH", data[offset : offset + 10]
            )
            offset += 10
            if offset + rdlength > len(data):
                break
            if atype == 1 and aclass == 1 and rdlength == 4:
                addrs.append(socket.inet_ntoa(data[offset : offset + 4]))
            offset += rdlength
        return addrs
    except Exception:
        return []


def _resolve_ipv4_hostaddr(url: str | None) -> dict[str, str]:
    """Renvoie {"hostaddr": "1.2.3.4"} si l'hôte du DATABASE_URL résout en IPv4.

    Scalingo (et beaucoup d'hébergeurs) n'ont pas de connectivité IPv6
    sortante. Supabase publie des DNS qui renvoient IPv6 en premier ;
    psycopg essaie IPv6 et plante avec "Network is unreachable" sans
    fallback IPv4. On force la résolution IPv4 via socket.getaddrinfo
    et on passe l'IP directement à psycopg via `hostaddr` (le `host`
    d'origine reste utilisé pour la vérification TLS / SNI).

    Renvoie un dict vide si on ne peut pas résoudre — psycopg essaiera
    son flot normal.
    """
    if not url:
        return {}
    try:
        parsed = urlparse(url)
    except ValueError:
        return {}
    host = parsed.hostname
    if not host:
        return {}
    cached = _ipv4_cache.get(host)
    if cached:
        return {"hostaddr": cached}
    try:
        # Essaye d'abord la résolution IPv4 classique.
        addrs = socket.gethostbyname_ex(host)[2]
        if addrs:
            ipv4 = addrs[0]
            _ipv4_cache[host] = ipv4
            logger.info(
                "DB host %s résolu en IPv4 %s (contournement IPv6 Scalingo)",
                host,
                ipv4,
            )
            return {"hostaddr": ipv4}
    except OSError:
        pass

    port = parsed.port or 5432
    try:
        infos = socket.getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ipv4_infos = [info for info in infos if info[0] == socket.AF_INET]
            if ipv4_infos:
                infos = ipv4_infos
            else:
                raise
        except socket.gaierror as exc:
            logger.warning(
                "Résolution IPv4 impossible pour %s : %s (tentative DNS public)",
                host,
                exc,
            )
            public_addrs = _dns_a_lookup(host)
            if public_addrs:
                ipv4 = public_addrs[0]
                _ipv4_cache[host] = ipv4
                logger.info(
                    "DB host %s résolu en IPv4 via DNS public %s",
                    host,
                    ipv4,
                )
                return {"hostaddr": ipv4}
            return {}
    if not infos:
        return {}
    ipv4 = infos[0][4][0]
    _ipv4_cache[host] = ipv4
    logger.info("DB host %s résolu en IPv4 %s (contournement IPv6 Scalingo)", host, ipv4)
    return {"hostaddr": ipv4}


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
        # Détection du mode pooler Supabase :
        #   - port 5432 = session mode (limite stricte ~15 connexions sur free tier)
        #   - port 6543 = transaction mode (centaines de connexions OK)
        # En session mode on serre la ceinture pour ne PAS saturer le pooler.
        is_session_mode = ":5432" in url and "pooler" in url
        if is_session_mode:
            logger.warning(
                "DATABASE_URL pointe sur le pooler Supabase en mode session "
                "(port 5432) — limité à ~15 connexions. Passez sur le port "
                "6543 (transaction mode) pour scaler proprement."
            )
        scalingo = bool(os.getenv("SCALINGO_APP", "").strip())
        if is_session_mode:
            default_pool_max = "3"
        elif scalingo:
            default_pool_max = "6"
        else:
            default_pool_max = "8"
        pool_max = int(os.getenv("DATABASE_POOL_MAX", default_pool_max))
        pool_timeout = float(os.getenv("DATABASE_POOL_TIMEOUT", "8"))
        pool_waiting = int(os.getenv("DATABASE_POOL_MAX_WAITING", "40"))
        # `hostaddr` injecté pour court-circuiter la résolution DNS IPv6 sur
        # les hébergeurs sans IPv6 sortant (Scalingo, Heroku, certains conteneurs).
        ipv4_kwargs = _resolve_ipv4_hostaddr(url)
        _pg_pool = ConnectionPool(
            url,
            min_size=0,
            max_size=pool_max,
            timeout=pool_timeout,
            max_waiting=pool_waiting,
            kwargs={"row_factory": dict_row, **ipv4_kwargs},
            open=True,
        )
        logger.info(
            "Pool PostgreSQL Veliora actif (max=%s, timeout=%ss)",
            pool_max,
            pool_timeout,
        )
        return _pg_pool
    except ImportError as exc:
        logger.warning("psycopg_pool absent — connexion directe : %s", exc)
        _pg_pool_failed = True
        return None
    except Exception as exc:
        logger.warning(
            "Pool PostgreSQL indisponible (nouvel essai au prochain appel) : %s", exc
        )
        return None


def _reset_pool_after_fork() -> None:
    """Repart d'un pool neuf dans le worker enfant après un fork.

    Avec gunicorn ``--preload``, ``init_db()`` ouvre le pool dans le process
    MAÎTRE (ses threads de maintenance y tournent). Au fork, l'enfant hérite de
    la référence ``_pg_pool`` mais PAS de ces threads : ``pool.connection()``
    attendrait alors jusqu'au PoolTimeout → ``DatabaseBusyError`` (« base
    saturée »), et plus aucune donnée ne charge. On oublie le pool hérité pour
    qu'un pool propre au worker soit recréé à la demande.
    """
    global _pg_pool, _pg_pool_failed
    _pg_pool = None
    _pg_pool_failed = False


try:
    os.register_at_fork(after_in_child=_reset_pool_after_fork)
except (AttributeError, ValueError):  # plateformes sans fork (Windows)
    pass


def _postgres_connection() -> DbConnection:
    import psycopg
    from psycopg.rows import dict_row

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL manquant pour Supabase")
    ipv4_kwargs = _resolve_ipv4_hostaddr(url)
    raw = psycopg.connect(url, row_factory=dict_row, autocommit=False, **ipv4_kwargs)
    return DbConnection(raw, postgres=True)


@contextmanager
def get_connection():
    if is_postgres():
        pool = _get_postgres_pool()
        if pool is not None:
            try:
                with pool.connection() as raw:
                    conn = DbConnection(raw, postgres=True)
                    try:
                        yield conn
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
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
    ipv4_kwargs = _resolve_ipv4_hostaddr(url)
    with psycopg.connect(url, row_factory=dict_row, **ipv4_kwargs) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    logger.info("Schéma PostgreSQL Veliora (%d statements) appliqué", len(statements))
