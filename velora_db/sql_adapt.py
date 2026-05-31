"""Adaptation SQL SQLite → PostgreSQL."""

from __future__ import annotations

import re

_INSERT_RETURNING = (
    "leads",
    "activities",
    "crawl_logs",
    "lead_price_history",
    "lead_outcomes",
)


def adapt_sql(sql: str, *, postgres: bool) -> str:
    if not postgres:
        return sql
    s = sql.replace("?", "%s")
    s = re.sub(r"\bAUTOINCREMENT\b", "", s, flags=re.IGNORECASE)
    s = s.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
    upper = s.upper()
    if upper.strip().startswith("INSERT") and "RETURNING" not in upper:
        for table in _INSERT_RETURNING:
            if re.search(rf"\bINTO\s+{table}\b", s, re.IGNORECASE):
                s = s.rstrip().rstrip(";") + " RETURNING id"
                break
    return s


def adapt_script_block(sql: str) -> str:
    """Transforme un bloc DDL SQLite minimal pour Postgres."""
    s = sql
    s = re.sub(
        r"id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "id BIGSERIAL PRIMARY KEY",
        s,
        flags=re.IGNORECASE,
    )
    s = s.replace("INTEGER DEFAULT 1", "BOOLEAN DEFAULT TRUE")
    s = s.replace("INTEGER DEFAULT 0", "BOOLEAN DEFAULT FALSE")
    s = s.replace("enabled INTEGER DEFAULT 1", "enabled BOOLEAN DEFAULT TRUE")
    s = s.replace("is_custom INTEGER DEFAULT 0", "is_custom BOOLEAN DEFAULT FALSE")
    s = s.replace("active INTEGER NOT NULL DEFAULT 1", "active BOOLEAN NOT NULL DEFAULT TRUE")
    s = s.replace("used INTEGER NOT NULL DEFAULT 0", "used BOOLEAN NOT NULL DEFAULT FALSE")
    s = s.replace("onboarding_completed INTEGER NOT NULL DEFAULT 0", "onboarding_completed BOOLEAN NOT NULL DEFAULT FALSE")
    return s
