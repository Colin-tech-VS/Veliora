"""Persistance des conversations et de la mémoire de l'assistant IA.

Tables créées paresseusement (SQLite local + Postgres prod). Les UUID sont
générés Python — on évite ainsi `gen_random_uuid()` côté Postgres qui demande
pgcrypto. Idem pour les timestamps : ISO8601 UTC, identiques aux autres tables.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from crawler.storage import get_connection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_ai_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_conversations (
            id          TEXT PRIMARY KEY,
            agency_id   TEXT NOT NULL,
            user_id     TEXT,
            title       TEXT NOT NULL DEFAULT 'Nouvelle conversation',
            model       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            agency_id       TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL DEFAULT '',
            meta_json       TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_memories (
            id          TEXT PRIMARY KEY,
            agency_id   TEXT NOT NULL,
            scope       TEXT NOT NULL DEFAULT 'general',
            content     TEXT NOT NULL,
            source      TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_conversations_agency ON ai_conversations(agency_id, updated_at)"
    )


def create_conversation(
    agency_id: str,
    *,
    user_id: str | None = None,
    title: str | None = None,
    model: str | None = None,
) -> dict:
    cid = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        ensure_ai_tables(conn)
        conn.execute(
            """INSERT INTO ai_conversations
               (id, agency_id, user_id, title, model, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cid, agency_id, user_id, title or "Nouvelle conversation", model, now, now),
        )
        conn.commit()
    return {
        "id": cid,
        "agency_id": agency_id,
        "user_id": user_id,
        "title": title or "Nouvelle conversation",
        "model": model,
        "created_at": now,
        "updated_at": now,
    }


def list_conversations(agency_id: str, *, limit: int = 30) -> list[dict]:
    with get_connection() as conn:
        ensure_ai_tables(conn)
        rows = conn.execute(
            """SELECT id, title, model, created_at, updated_at
                 FROM ai_conversations
                WHERE agency_id = ?
                ORDER BY updated_at DESC
                LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conv_id: str, agency_id: str) -> dict | None:
    with get_connection() as conn:
        ensure_ai_tables(conn)
        row = conn.execute(
            "SELECT * FROM ai_conversations WHERE id = ? AND agency_id = ?",
            (conv_id, agency_id),
        ).fetchone()
    return dict(row) if row else None


def delete_conversation(conv_id: str, agency_id: str) -> bool:
    with get_connection() as conn:
        ensure_ai_tables(conn)
        cur = conn.execute(
            "DELETE FROM ai_conversations WHERE id = ? AND agency_id = ?",
            (conv_id, agency_id),
        )
        conn.execute(
            "DELETE FROM ai_messages WHERE conversation_id = ? AND agency_id = ?",
            (conv_id, agency_id),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def rename_conversation(conv_id: str, agency_id: str, title: str) -> bool:
    title = (title or "").strip()[:140] or "Conversation"
    with get_connection() as conn:
        ensure_ai_tables(conn)
        cur = conn.execute(
            """UPDATE ai_conversations SET title = ?, updated_at = ?
                WHERE id = ? AND agency_id = ?""",
            (title, _now(), conv_id, agency_id),
        )
        conn.commit()
        return (cur.rowcount or 0) > 0


def append_message(
    conversation_id: str,
    agency_id: str,
    role: str,
    content: str,
    *,
    meta: dict | None = None,
) -> dict:
    mid = str(uuid.uuid4())
    now = _now()
    meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
    with get_connection() as conn:
        ensure_ai_tables(conn)
        conn.execute(
            """INSERT INTO ai_messages
               (id, conversation_id, agency_id, role, content, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, conversation_id, agency_id, role, content, meta_json, now),
        )
        conn.execute(
            "UPDATE ai_conversations SET updated_at = ? WHERE id = ? AND agency_id = ?",
            (now, conversation_id, agency_id),
        )
        conn.commit()
    return {
        "id": mid,
        "role": role,
        "content": content,
        "meta": meta,
        "created_at": now,
    }


def get_messages(conversation_id: str, agency_id: str, *, limit: int | None = None) -> list[dict]:
    sql = (
        "SELECT id, role, content, meta_json, created_at "
        "FROM ai_messages WHERE conversation_id = ? AND agency_id = ? "
        "ORDER BY created_at ASC"
    )
    params: list = [conversation_id, agency_id]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with get_connection() as conn:
        ensure_ai_tables(conn)
        rows = conn.execute(sql, tuple(params)).fetchall()
    out: list[dict] = []
    for r in rows:
        meta = None
        if r["meta_json"]:
            try:
                meta = json.loads(r["meta_json"])
            except json.JSONDecodeError:
                meta = None
        out.append({
            "id": r["id"],
            "role": r["role"],
            "content": r["content"] or "",
            "meta": meta,
            "created_at": r["created_at"],
        })
    return out


def add_memory(agency_id: str, content: str, *, scope: str = "general", source: str | None = None) -> dict:
    content = (content or "").strip()
    if not content:
        return {}
    mid = str(uuid.uuid4())
    now = _now()
    with get_connection() as conn:
        ensure_ai_tables(conn)
        conn.execute(
            """INSERT INTO ai_memories
               (id, agency_id, scope, content, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (mid, agency_id, scope, content[:1200], source, now),
        )
        conn.commit()
    return {"id": mid, "scope": scope, "content": content[:1200], "source": source, "created_at": now}


def list_memories(agency_id: str, *, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        ensure_ai_tables(conn)
        rows = conn.execute(
            """SELECT id, scope, content, source, created_at
                 FROM ai_memories WHERE agency_id = ?
                ORDER BY created_at DESC LIMIT ?""",
            (agency_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(memory_id: str, agency_id: str) -> bool:
    with get_connection() as conn:
        ensure_ai_tables(conn)
        cur = conn.execute(
            "DELETE FROM ai_memories WHERE id = ? AND agency_id = ?",
            (memory_id, agency_id),
        )
        conn.commit()
    return (cur.rowcount or 0) > 0
