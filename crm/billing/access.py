"""Contrôle d'accès selon l'abonnement."""

from __future__ import annotations

from crm.billing.config import ACTIVE_SUBSCRIPTION_STATUSES, billing_required
from crm.billing.storage import get_agency_billing


def agency_has_active_subscription(agency_id: str | None) -> bool:
    if not agency_id:
        return False
    if not billing_required():
        return True

    row = get_agency_billing(agency_id)
    if not row:
        return False
    status = (row.get("subscription_status") or "").lower()
    return status in ACTIVE_SUBSCRIPTION_STATUSES


def billing_status_payload(agency_id: str) -> dict:
    row = get_agency_billing(agency_id) or {}
    status = (row.get("subscription_status") or "unknown").lower()
    active = agency_has_active_subscription(agency_id)
    return {
        "status": status,
        "active": active,
        "requires_payment": billing_required(),
        "plan": row.get("subscription_plan") or "veliora_pro",
        "current_period_end": row.get("subscription_current_period_end"),
        "amount_eur": __import__("crm.billing.config", fromlist=["SUBSCRIPTION_AMOUNT_EUR"]).SUBSCRIPTION_AMOUNT_EUR,
        "has_stripe_customer": bool(row.get("stripe_customer_id")),
        "stripe_customer_id": row.get("stripe_customer_id") or "",
    }
