"""Connexions SQLite et Supabase PostgreSQL (interface compatible storage.py)."""

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import urllib.request
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

# Pics transitoires : un crawl en arrière-plan relâche ses connexions en
# continu, donc une file pleine (`TooManyRequests`) se vide souvent en
# quelques centaines de ms. On réessaie quelques fois avant d'abandonner.
_POOL_ACQUIRE_RETRIES = int(os.getenv("DATABASE_POOL_ACQUIRE_RETRIES", "3"))
_POOL_ACQUIRE_BACKOFF = float(os.getenv("DATABASE_POOL_ACQUIRE_BACKOFF", "0.35"))

# --- Réservation de connexions pour le web pendant un crawl ----------------
# Un crawl lance plusieurs threads de fond (moteur + DVF + rapprochement
# d'adresse) qui martèlent la base. Sans garde-fou, ils consomment TOUT le
# pool et les requêtes web échouent en « base saturée ». On borne donc le
# nombre de connexions que ces threads de fond peuvent détenir EN MÊME TEMPS,
# pour toujours laisser des créneaux libres au web.
_BG_THREAD_PREFIXES = ("veliora-crawl", "veliora-dvf", "veliora-addr", "veliora-lead")
_pg_pool_max = 0
_bg_db_sem: threading.Semaphore | None = None
_bg_db_lock = threading.Lock()
_bg_db_local = threading.local()


def _is_bg_db_thread() -> bool:
    """Vrai si le thread courant est un thread de crawl en arrière-plan."""
    return (threading.current_thread().name or "").startswith(_BG_THREAD_PREFIXES)


def _get_bg_db_semaphore() -> threading.Semaphore | None:
    """Sémaphore bornant les connexions détenues par les threads de crawl.

    Capacité = pool_max − réservation web (≈40 %, min 2). Ainsi le web garde
    toujours des connexions disponibles, même crawl à fond.
    """
    global _bg_db_sem
    if _bg_db_sem is not None:
        return _bg_db_sem
    if _pg_pool_max <= 0:
        return None
    with _bg_db_lock:
        if _bg_db_sem is None:
            # Réserve web ≈ threads Gunicorn + marge (évite que le crawl vide le pool).
            try:
                gunicorn_threads = int(os.getenv("GUNICORN_THREADS", "8"))
            except ValueError:
                gunicorn_threads = 8
            reserve_default = max(3, min(_pg_pool_max - 2, gunicorn_threads + 2))
            reserve = int(os.getenv("DATABASE_WEB_RESERVE", str(reserve_default)))
            cap = max(1, _pg_pool_max - max(0, reserve))
            _bg_db_sem = threading.BoundedSemaphore(cap)
            logger.info(
                "Garde DB crawl active — %s connexion(s) max pour les threads de "
                "fond (pool=%s, réserve web=%s)",
                cap,
                _pg_pool_max,
                reserve,
            )
    return _bg_db_sem


@contextmanager
def _bg_db_slot():
    """Réserve un créneau DB pour un thread de crawl (no-op sinon).

    Ré-entrant par thread : un même thread qui imbrique deux ``get_connection``
    ne prend qu'un seul jeton (évite l'auto-blocage).
    """
    sem = _get_bg_db_semaphore() if _is_bg_db_thread() else None
    if sem is None:
        yield
        return
    depth = getattr(_bg_db_local, "depth", 0)
    if depth > 0:
        _bg_db_local.depth = depth + 1
        try:
            yield
        finally:
            _bg_db_local.depth -= 1
        return
    sem.acquire()
    _bg_db_local.depth = 1
    try:
        yield
    finally:
        _bg_db_local.depth = 0
        sem.release()


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


def _dns_a_lookup_doh(host: str) -> list[str]:
    """Fallback DNS-over-HTTPS (TCP) pour environnements bloquant l'UDP/53."""
    urls = (
        f"https://cloudflare-dns.com/dns-query?name={host}&type=A",
        f"https://dns.google/resolve?name={host}&type=A",
    )
    headers = {"accept": "application/dns-json", "user-agent": "veliora-db/1.0"}
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                payload = resp.read().decode("utf-8", errors="ignore")
            data = json.loads(payload)
            answers = data.get("Answer") or []
            addrs = [
                entry.get("data", "")
                for entry in answers
                if isinstance(entry, dict) and entry.get("type") == 1
            ]
            addrs = [a for a in addrs if a and "." in a]
            if addrs:
                return addrs
        except Exception:
            continue
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
    manual = os.getenv("DATABASE_HOSTADDR", "").strip()
    if manual:
        _ipv4_cache[host] = manual
        logger.info("DB hostaddr forcé via DATABASE_HOSTADDR=%s", manual)
        return {"hostaddr": manual}
    cached = _ipv4_cache.get(host)
    if cached:
        return {"hostaddr": cached}
    for attempt in range(1, 4):
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
            if attempt < 3:
                # Évite un faux négatif DNS transitoire au démarrage du conteneur.
                time.sleep(0.2 * attempt)

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
            if not public_addrs:
                public_addrs = _dns_a_lookup_doh(host)
            if public_addrs:
                ipv4 = public_addrs[0]
                _ipv4_cache[host] = ipv4
                logger.info(
                    "DB host %s résolu en IPv4 via DNS fallback %s",
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


def _postgres_driver_kwargs(url: str | None = None) -> dict:
    """Kwargs psycopg3 pour Veliora (pool + connexions directes).

    Le pooler Supabase en mode *transaction* (port 6543 / PgBouncer) ne gère pas
    les prepared statements nommés (_pg3_0, …) réutilisés entre transactions :
    sans ``prepare_threshold=None`` → DuplicatePreparedStatement.

    On active aussi les *keepalives* TCP et un ``connect_timeout`` court : sans
    eux, une connexion silencieusement coupée (Supabase/PgBouncer ferme les
    connexions inactives, NAT/box qui expire le flux) n'est détectée qu'au
    moment où l'on s'en sert → « base de données indisponible ». Les keepalives
    sondent la connexion en continu et la déclarent morte tôt, pour qu'elle soit
    recyclée plutôt que servie à une requête web.
    """
    from psycopg.rows import dict_row

    u = url or database_url() or ""
    return {
        "row_factory": dict_row,
        "prepare_threshold": None,
        "connect_timeout": int(os.getenv("DATABASE_CONNECT_TIMEOUT", "10")),
        # Keepalives TCP (paramètres libpq) — sondent la connexion au repos.
        "keepalives": 1,
        "keepalives_idle": int(os.getenv("DATABASE_KEEPALIVES_IDLE", "30")),
        "keepalives_interval": int(os.getenv("DATABASE_KEEPALIVES_INTERVAL", "10")),
        "keepalives_count": int(os.getenv("DATABASE_KEEPALIVES_COUNT", "5")),
        **_resolve_ipv4_hostaddr(u),
    }


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
        from psycopg_pool import ConnectionPool

        url = database_url()
        if not url:
            _pg_pool_failed = True
            return None
        # Supabase : port 6543 = pooler transaction (recommandé prod) ;
        # port 5432 = session ou direct — plafond connexions très bas.
        parsed = urlparse(url)
        port = parsed.port or 5432
        is_tx_pooler = port == 6543 or ":6543/" in url or ":6543?" in url
        is_session_pooler = "pooler" in url and port == 5432
        if is_session_pooler or (not is_tx_pooler and port == 5432):
            logger.warning(
                "DATABASE_URL en port 5432 (session ou direct) — connexions "
                "limitées. Utilisez le pooler Supabase port 6543 (transaction) "
                "sur Scalingo (voir SUPABASE.md / .env.example)."
            )
        scalingo = bool(os.getenv("SCALINGO_APP", "").strip())
        if is_tx_pooler:
            default_pool_max = "16" if scalingo else "10"
        elif is_session_pooler or port == 5432:
            default_pool_max = "4" if scalingo else "3"
        else:
            default_pool_max = "12" if scalingo else "8"
        pool_max = int(os.getenv("DATABASE_POOL_MAX", default_pool_max))
        global _pg_pool_max
        _pg_pool_max = pool_max
        pool_timeout = float(os.getenv("DATABASE_POOL_TIMEOUT", "10"))
        # File courte : 503 + retry client plutôt que 20 requêtes bloquées.
        pool_waiting = int(os.getenv("DATABASE_POOL_MAX_WAITING", "8"))
        # Recyclage : on ne garde pas une connexion éternellement (PgBouncer en
        # mode transaction peut la couper côté serveur) ni au repos trop
        # longtemps. Au-delà, le pool la referme et en rouvre une saine.
        max_lifetime = float(os.getenv("DATABASE_POOL_MAX_LIFETIME", "1800"))
        max_idle = float(os.getenv("DATABASE_POOL_MAX_IDLE", "300"))
        # check : valide chaque connexion (SELECT 1) AVANT de la prêter. Une
        # connexion morte est alors recyclée de façon transparente au lieu de
        # faire échouer la requête en « base de données indisponible ». C'est le
        # garde-fou central contre les connexions zombies du pooler Supabase.
        use_check = os.getenv("DATABASE_POOL_CHECK", "1").strip() not in ("0", "false", "")
        pool_check = ConnectionPool.check_connection if use_check else None
        _pg_pool = ConnectionPool(
            url,
            min_size=0,
            max_size=pool_max,
            timeout=pool_timeout,
            max_waiting=pool_waiting,
            max_lifetime=max_lifetime,
            max_idle=max_idle,
            check=pool_check,
            kwargs=_postgres_driver_kwargs(url),
            open=True,
        )
        logger.info(
            "Pool PostgreSQL Veliora actif (max=%s, timeout=%ss, check=%s, "
            "max_lifetime=%ss, max_idle=%ss)",
            pool_max,
            pool_timeout,
            "on" if use_check else "off",
            max_lifetime,
            max_idle,
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
    global _pg_pool, _pg_pool_failed, _bg_db_sem
    _pg_pool = None
    _pg_pool_failed = False
    # Le sémaphore vit dans le process maître : on le recrée dans l'enfant.
    _bg_db_sem = None


try:
    os.register_at_fork(after_in_child=_reset_pool_after_fork)
except (AttributeError, ValueError):  # plateformes sans fork (Windows)
    pass


def _postgres_connection() -> DbConnection:
    import psycopg

    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL manquant pour Supabase")
    raw = psycopg.connect(url, autocommit=False, **_postgres_driver_kwargs(url))
    return DbConnection(raw, postgres=True)


def _is_pool_saturation(exc: BaseException) -> bool:
    """Vrai si l'exception traduit un pool saturé (file pleine ou attente expirée)."""
    name = type(exc).__name__
    return name in ("TooManyRequests", "PoolTimeout") or "PoolTimeout" in name


def _enter_pool_connection(pool):
    """Entre dans ``pool.connection()`` en absorbant les pics transitoires.

    Deux saturations *temporaires* peuvent survenir quand un crawl tourne en
    arrière-plan et monopolise des connexions :

    * ``TooManyRequests`` — la file d'attente du pool est pleine (échec
      immédiat) ; on patiente brièvement puis on réessaie, la file se
      vidant en continu.
    * ``PoolTimeout`` — l'attente a déjà expiré (on a donc déjà bloqué
      ``timeout`` secondes) ; inutile de re-bloquer, on abandonne
      proprement en ``DatabaseBusyError``.

    Renvoie ``(context_manager, raw_connection)``. L'appelant DOIT fermer le
    context_manager (``__exit__``) pour rendre la connexion au pool.
    """
    last_exc: BaseException | None = None
    for attempt in range(max(1, _POOL_ACQUIRE_RETRIES)):
        cm = pool.connection()
        try:
            raw = cm.__enter__()
            return cm, raw
        except BaseException as exc:
            if not _is_pool_saturation(exc):
                raise
            last_exc = exc
            # PoolTimeout : on a déjà attendu, on ne re-bloque pas.
            if type(exc).__name__ != "TooManyRequests":
                break
            if attempt < _POOL_ACQUIRE_RETRIES - 1:
                time.sleep(_POOL_ACQUIRE_BACKOFF * (attempt + 1))
    raise DatabaseBusyError(
        "Connexions base saturées — réessayez dans quelques secondes"
    ) from last_exc


@contextmanager
def get_connection():
    if is_postgres():
        pool = _get_postgres_pool()
        if pool is not None:
            # Garde de concurrence : un thread de crawl attend ici qu'un créneau
            # se libère plutôt que de vider le pool au détriment du web.
            with _bg_db_slot():
                cm, raw = _enter_pool_connection(pool)
                conn = DbConnection(raw, postgres=True)
                try:
                    yield conn
                    conn.commit()
                except BaseException as exc:
                    conn.rollback()
                    cm.__exit__(type(exc), exc, exc.__traceback__)
                    raise
                cm.__exit__(None, None, None)
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


def secure_public_schema_rls() -> int:
    """Active RLS + révoque l'accès API sur toutes les tables ``public``.

    Pourquoi : Supabase expose le schéma ``public`` via l'API PostgREST aux
    rôles ``anon`` / ``authenticated``. Une table sans RLS y est lisible /
    modifiable par quiconque possède la clé anon → fuite de données. Le
    dashboard les signale « Unrestricted » / « RLS Disabled in Public ».

    Veliora se connecte en Postgres avec le rôle **propriétaire** (``postgres``)
    qui *contourne* RLS (ownership ⇒ BYPASSRLS) : activer RLS n'a donc AUCUN
    impact sur l'app, mais ferme la porte à l'API publique.

    Idempotent, appelé à l'init : toute table — y compris créée plus tard par un
    module (transactions, portail, IA…) — est verrouillée automatiquement, ce
    qui évite la réapparition de tables « Critical ». Renvoie le nombre de
    tables traitées.
    """
    if not is_postgres():
        return 0
    import psycopg
    from psycopg import sql as _sql

    url = database_url()
    if not url:
        return 0

    secured = 0
    try:
        with psycopg.connect(url, **_postgres_driver_kwargs(url)) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
                rows = cur.fetchall()
                tables = [
                    (r["tablename"] if isinstance(r, dict) else r[0]) for r in rows
                ]
                for table in tables:
                    cur.execute(
                        _sql.SQL("ALTER TABLE public.{} ENABLE ROW LEVEL SECURITY").format(
                            _sql.Identifier(table)
                        )
                    )
                    secured += 1

                # Révoque l'accès API (présent ET futur) pour les rôles Supabase.
                # Sur un Postgres générique ces rôles peuvent ne pas exister.
                for role in ("anon", "authenticated"):
                    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                    if cur.fetchone() is None:
                        continue
                    rid = _sql.Identifier(role)
                    cur.execute(
                        _sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(rid)
                    )
                    cur.execute(
                        _sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(rid)
                    )
                    cur.execute(
                        _sql.SQL(
                            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                            "REVOKE ALL ON TABLES FROM {}"
                        ).format(rid)
                    )
                    cur.execute(
                        _sql.SQL(
                            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                            "REVOKE ALL ON SEQUENCES FROM {}"
                        ).format(rid)
                    )
            conn.commit()
        logger.info("RLS Supabase appliqué — %d table(s) public verrouillée(s)", secured)
    except Exception:
        logger.warning(
            "secure_public_schema_rls ignoré (droits insuffisants ?) — "
            "appliquez scripts/supabase_enable_rls.sql manuellement",
            exc_info=True,
        )
    return secured


def init_postgres_schema() -> None:
    schema_path = Path(__file__).resolve().parent / "postgres_schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    import psycopg

    url = database_url()
    statements = _split_sql_script(sql)
    with psycopg.connect(url, **_postgres_driver_kwargs(url)) as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()
    logger.info("Schéma PostgreSQL Veliora (%d statements) appliqué", len(statements))
