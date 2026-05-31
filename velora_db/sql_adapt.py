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


def _adapt_datetime(s: str) -> str:
    """Traduit les fonctions de date SQLite vers PostgreSQL.

    datetime('now', '-15 minutes') -> (NOW() - INTERVAL '15 minutes')
    datetime('now')                -> NOW()
    datetime(col)                  -> (col)::timestamptz
    """

    def _modifier(m: re.Match) -> str:
        sign, amount, unit = m.group(1), m.group(2), m.group(3)
        op = "-" if sign == "-" else "+"
        return f"(NOW() {op} INTERVAL '{amount} {unit}')"

    # datetime('now', '±N unit') — doit passer avant datetime('now')
    s = re.sub(
        r"datetime\(\s*'now'\s*,\s*'([+-])\s*(\d+)\s+(\w+)'\s*\)",
        _modifier,
        s,
        flags=re.IGNORECASE,
    )
    # datetime('now')
    s = re.sub(r"datetime\(\s*'now'\s*\)", "NOW()", s, flags=re.IGNORECASE)
    # datetime(<colonne>) — cast d'une colonne/expression en timestamptz
    s = re.sub(
        r"datetime\(\s*([A-Za-z_][\w.]*)\s*\)",
        r"(\1)::timestamptz",
        s,
        flags=re.IGNORECASE,
    )
    return s


def _adapt_substr(s: str) -> str:
    """substr(<expr>, …) → substr((<expr>)::text, …) pour Postgres.

    En SQLite les dates sont stockées en TEXT et substr(created_at, 1, 10)
    extrait 'YYYY-MM-DD'. En Postgres les colonnes sont TIMESTAMPTZ : on caste
    le 1ᵉʳ argument en text (inoffensif s'il est déjà textuel). Gère les
    parenthèses imbriquées (ex. COALESCE(a, b)).
    """
    low = s.lower()
    out: list[str] = []
    i = 0
    while True:
        idx = low.find("substr(", i)
        if idx == -1:
            out.append(s[i:])
            return "".join(out)
        start = idx + len("substr(")
        out.append(s[i:start])
        depth = 0
        j = start
        while j < len(s):
            ch = s[j]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                break
            j += 1
        out.append(s[start:j] + "::text")
        i = j


def _adapt_insert_or_replace(s: str) -> str:
    """INSERT OR REPLACE INTO t (c1, c2, …) → INSERT … ON CONFLICT (c1) DO UPDATE …

    La première colonne est supposée être la clé de conflit (PRIMARY KEY).
    """
    m = re.match(
        r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]*)\)",
        s,
        re.IGNORECASE,
    )
    body = re.sub(r"\bINSERT\s+OR\s+REPLACE\b", "INSERT", s, count=1, flags=re.IGNORECASE)
    if not m:
        return body
    cols = [c.strip() for c in m.group(2).split(",") if c.strip()]
    key = cols[0]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
    body = body.rstrip().rstrip(";")
    if updates:
        return f"{body} ON CONFLICT ({key}) DO UPDATE SET {updates}"
    return f"{body} ON CONFLICT ({key}) DO NOTHING"


def adapt_sql(sql: str, *, postgres: bool) -> str:
    if not postgres:
        return sql
    s = sql.replace("?", "%s")
    s = re.sub(r"\bAUTOINCREMENT\b", "", s, flags=re.IGNORECASE)
    s = s.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
    s = _adapt_datetime(s)
    if "substr(" in s.lower():
        s = _adapt_substr(s)
    if re.search(r"\bINSERT\s+OR\s+REPLACE\b", s, re.IGNORECASE):
        s = _adapt_insert_or_replace(s)
    upper = s.upper()
    if (
        upper.strip().startswith("INSERT")
        and "RETURNING" not in upper
        and "ON CONFLICT" not in upper
    ):
        for table in _INSERT_RETURNING:
            if re.search(rf"\bINTO\s+{table}\b", s, re.IGNORECASE):
                s = s.rstrip().rstrip(";") + " RETURNING id"
                break
    return s


def adapt_script_block(sql: str) -> str:
    """Transforme un bloc DDL SQLite minimal pour Postgres."""
    return re.sub(
        r"id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
        "id BIGSERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )
