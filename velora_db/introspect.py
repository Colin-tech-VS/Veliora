"""Introspection schéma SQLite (PRAGMA) / PostgreSQL (information_schema)."""

from __future__ import annotations

from velora_db.config import is_postgres


def _row_name(row) -> str:
    if isinstance(row, dict):
        return str(row.get("column_name") or next(iter(row.values()), ""))
    if isinstance(row, (tuple, list)):
        return str(row[0] if len(row) == 1 else row[1])
    return str(row)


def table_column_names(conn, table: str) -> set[str]:
    """Noms de colonnes d'une table (schéma public sous Postgres)."""
    if is_postgres():
        cur = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table,),
        )
        return {_row_name(r) for r in cur.fetchall()}
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def ensure_columns(conn, table: str, columns: dict[str, str]) -> None:
    """Ajoute les colonnes manquantes (nom → type SQL ALTER)."""
    existing = table_column_names(conn, table)
    for col, col_type in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
