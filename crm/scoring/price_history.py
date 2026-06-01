"""Historique des prix — comptage des baisses pour le scoring."""

from __future__ import annotations


def count_price_drops_from_history(
    rows: list[dict],
    *,
    current_price: int | None,
    previous_price: int | None,
) -> tuple[int, int | None]:
    """
    Retourne (nombre_de_baisses, pct_dernière_baisse).
    Inclut previous_price → current_price si applicable.
    """
    prices: list[tuple[int, str]] = []
    for r in rows:
        p = r.get("price")
        if p and int(p) > 0:
            prices.append((int(p), r.get("recorded_at") or ""))
    prices.sort(key=lambda x: x[1])

    drops = 0
    last_drop_pct: int | None = None

    if len(prices) >= 2:
        for i in range(1, len(prices)):
            prev_p, cur_p = prices[i - 1][0], prices[i][0]
            if cur_p < prev_p:
                drops += 1
                last_drop_pct = int((prev_p - cur_p) / prev_p * 100)

    if previous_price and current_price and int(previous_price) > int(current_price):
        pct = int((int(previous_price) - int(current_price)) / int(previous_price) * 100)
        if not prices or prices[-1][0] != int(current_price):
            drops += 1
            last_drop_pct = pct
        elif last_drop_pct is None:
            last_drop_pct = pct

    return drops, last_drop_pct


def fetch_price_history_rows(conn, lead_id: int, agency_id: str) -> list[dict]:
    cur = conn.execute(
        """SELECT price, recorded_at FROM lead_price_history
           WHERE lead_id = ? AND agency_id = ?
           ORDER BY recorded_at ASC""",
        (lead_id, agency_id),
    )
    return [{"price": r["price"], "recorded_at": r["recorded_at"]} for r in cur.fetchall()]


def fetch_price_history_map(
    conn,
    agency_id: str,
    lead_ids: list[int],
    *,
    chunk_size: int = 400,
) -> dict[int, list[dict]]:
    """Historique prix pour plusieurs leads — requêtes SQL par lots."""
    if not lead_ids:
        return {}
    ids = [int(i) for i in lead_ids if i is not None]
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    for start in range(0, len(ids), chunk_size):
        batch = ids[start : start + chunk_size]
        placeholders = ",".join("?" * len(batch))
        cur = conn.execute(
            f"""SELECT lead_id, price, recorded_at FROM lead_price_history
                WHERE agency_id = ? AND lead_id IN ({placeholders})
                ORDER BY lead_id, recorded_at ASC""",
            [agency_id, *batch],
        )
        for r in cur.fetchall():
            lid = int(r["lead_id"])
            out.setdefault(lid, []).append(
                {"price": r["price"], "recorded_at": r["recorded_at"]}
            )
    return out
