"""Persistance abonnement agence (SQLite)."""

from __future__ import annotations

import os
import time

from crawler.storage import get_connection

_BILLING_CACHE_TTL_SEC = float(os.getenv("BILLING_CACHE_TTL", "60"))
_billing_cache: dict[str, tuple[float, dict | None]] = {}


def invalidate_agency_billing_cache(agency_id: str | None = None) -> None:
    if agency_id:
        _billing_cache.pop(agency_id, None)
    else:
        _billing_cache.clear()


def get_agency_billing(agency_id: str) -> dict | None:
    now = time.monotonic()
    hit = _billing_cache.get(agency_id)
    if hit and now - hit[0] < _BILLING_CACHE_TTL_SEC:
        return dict(hit[1]) if hit[1] else None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, name, email, stripe_customer_id, stripe_subscription_id,
                      subscription_status, subscription_current_period_end, subscription_plan
               FROM agencies WHERE id = ?""",
            (agency_id,),
        ).fetchone()
    payload = dict(row) if row else None
    _billing_cache[agency_id] = (time.monotonic(), payload)
    return payload


def update_agency_billing(agency_id: str, **fields) -> None:
    allowed = {
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
        "subscription_current_period_end",
        "subscription_plan",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    vals = list(updates.values()) + [agency_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE agencies SET {cols} WHERE id = ?", vals)
        conn.commit()
    invalidate_agency_billing_cache(agency_id)


def find_agency_by_stripe_customer(customer_id: str) -> dict | None:
    if not customer_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, name, email, stripe_customer_id, stripe_subscription_id,
                      subscription_status, subscription_current_period_end
               FROM agencies WHERE stripe_customer_id = ?""",
            (customer_id,),
        ).fetchone()
    return dict(row) if row else None


def find_agency_by_stripe_subscription(subscription_id: str) -> dict | None:
    if not subscription_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT id, name, email, stripe_customer_id, stripe_subscription_id,
                      subscription_status, subscription_current_period_end
               FROM agencies WHERE stripe_subscription_id = ?""",
            (subscription_id,),
        ).fetchone()
    return dict(row) if row else None
