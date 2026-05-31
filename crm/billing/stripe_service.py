"""Stripe Checkout, Customer Portal et webhooks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from crm.billing.config import (
    APP_PUBLIC_URL,
    STRIPE_ENABLED,
    STRIPE_PRICE_ID,
    STRIPE_PRODUCT_NAME,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    SUBSCRIPTION_AMOUNT_EUR,
    SUBSCRIPTION_CURRENCY,
    SUBSCRIPTION_INTERVAL,
    billing_required,
)
from crm.billing.storage import (
    find_agency_by_stripe_customer,
    find_agency_by_stripe_subscription,
    get_agency_billing,
    update_agency_billing,
)

logger = logging.getLogger(__name__)


def _stripe():
    if not STRIPE_ENABLED:
        raise RuntimeError(
            "Stripe non configuré. Renseignez STRIPE_SECRET_KEY dans le fichier .env "
            "(voir .env.example)."
        )
    import stripe

    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


def _ts_iso(unix_ts: int | None) -> str | None:
    if not unix_ts:
        return None
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()


def _line_items() -> list[dict[str, Any]]:
    if STRIPE_PRICE_ID:
        return [{"price": STRIPE_PRICE_ID, "quantity": 1}]
    amount_cents = SUBSCRIPTION_AMOUNT_EUR * 100
    return [
        {
            "price_data": {
                "currency": SUBSCRIPTION_CURRENCY,
                "unit_amount": amount_cents,
                "recurring": {"interval": SUBSCRIPTION_INTERVAL},
                "product_data": {"name": STRIPE_PRODUCT_NAME},
            },
            "quantity": 1,
        }
    ]


def initial_subscription_status() -> str:
    return "pending" if billing_required() else "active"


def create_checkout_session(agency_id: str, *, customer_email: str | None = None) -> dict:
    stripe = _stripe()
    agency = get_agency_billing(agency_id)
    if not agency:
        raise ValueError("Agence introuvable")

    email = customer_email or agency.get("email")
    from crm.config import STRIPE_TRIAL_DAYS

    sub_data: dict[str, Any] = {"metadata": {"agency_id": agency_id}}
    if STRIPE_TRIAL_DAYS > 0:
        sub_data["trial_period_days"] = STRIPE_TRIAL_DAYS

    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": _line_items(),
        "client_reference_id": agency_id,
        "metadata": {"agency_id": agency_id},
        "subscription_data": sub_data,
        "success_url": (
            f"{APP_PUBLIC_URL}/crm/auth?checkout=success"
            "&session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": f"{APP_PUBLIC_URL}/crm/auth?checkout=cancel",
        "allow_promotion_codes": True,
        "billing_address_collection": "required",
        "tax_id_collection": {"enabled": True},
    }

    if agency.get("stripe_customer_id"):
        params["customer"] = agency["stripe_customer_id"]
    elif email:
        params["customer_email"] = email

    session = stripe.checkout.Session.create(**params)
    update_agency_billing(agency_id, subscription_status="checkout_pending")
    return {"checkout_url": session.url, "session_id": session.id}


def create_portal_session(agency_id: str) -> dict:
    stripe = _stripe()
    agency = get_agency_billing(agency_id)
    if not agency or not agency.get("stripe_customer_id"):
        raise ValueError("Aucun abonnement Stripe lié à cette agence")

    session = stripe.billing_portal.Session.create(
        customer=agency["stripe_customer_id"],
        return_url=f"{APP_PUBLIC_URL}/crm",
    )
    return {"portal_url": session.url}


def sync_subscription_from_stripe(subscription: dict | Any) -> None:
    """Met à jour l'agence depuis un objet Subscription Stripe."""
    sub_id = subscription.get("id") if isinstance(subscription, dict) else subscription.id
    customer_id = (
        subscription.get("customer")
        if isinstance(subscription, dict)
        else subscription.customer
    )
    status = (
        subscription.get("status")
        if isinstance(subscription, dict)
        else subscription.status
    )
    period_end = (
        subscription.get("current_period_end")
        if isinstance(subscription, dict)
        else subscription.current_period_end
    )
    metadata = (
        subscription.get("metadata") or {}
        if isinstance(subscription, dict)
        else dict(subscription.metadata or {})
    )

    agency_id = metadata.get("agency_id")
    agency = None
    if agency_id:
        agency = get_agency_billing(agency_id)
    if not agency:
        agency = find_agency_by_stripe_subscription(sub_id)
    if not agency:
        agency = find_agency_by_stripe_customer(customer_id)
    if not agency:
        logger.warning("Subscription Stripe sans agence locale : %s", sub_id)
        return

    update_agency_billing(
        agency["id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=sub_id,
        subscription_status=status,
        subscription_current_period_end=_ts_iso(period_end),
        subscription_plan="veliora_pro",
    )
    logger.info(
        "Abonnement agence %s → %s (%s)",
        agency["id"],
        status,
        sub_id,
    )


def handle_checkout_completed(session: dict | Any, *, send_emails: bool = False) -> None:
    agency_id = (
        session.get("client_reference_id")
        or (session.get("metadata") or {}).get("agency_id")
    )
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if agency_id and customer_id:
        update_agency_billing(
            agency_id,
            stripe_customer_id=customer_id,
            stripe_subscription_id=subscription_id,
            subscription_status="active",
        )

    if subscription_id:
        stripe = _stripe()
        sub = stripe.Subscription.retrieve(subscription_id)
        sync_subscription_from_stripe(sub)

    if send_emails and agency_id:
        agency = get_agency_billing(agency_id)
        if agency:
            from crm.billing.config import SUBSCRIPTION_AMOUNT_EUR
            from crm.email.service import send_payment_confirmed_email, send_welcome_email

            email = agency.get("email") or session.get("customer_email")
            if email:
                send_payment_confirmed_email(email, agency.get("name") or "Votre agence", SUBSCRIPTION_AMOUNT_EUR)
                send_welcome_email(
                    email,
                    agency.get("name") or "Votre agence",
                    APP_PUBLIC_URL,
                )


def handle_webhook(payload: bytes, signature: str | None) -> dict:
    if not STRIPE_WEBHOOK_SECRET:
        raise RuntimeError(
            "STRIPE_WEBHOOK_SECRET manquant. Configurez le webhook dans le Dashboard Stripe."
        )
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except ValueError as exc:
        raise ValueError(f"Payload webhook invalide : {exc}") from exc
    except Exception as exc:
        raise ValueError(f"Signature webhook invalide : {exc}") from exc

    etype = event["type"]
    data = event["data"]["object"]

    if etype == "checkout.session.completed":
        handle_checkout_completed(data, send_emails=True)
    elif etype in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        sync_subscription_from_stripe(data)
    elif etype == "invoice.paid":
        sub_id = data.get("subscription")
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
            sync_subscription_from_stripe(sub)
    elif etype == "invoice.payment_failed":
        sub_id = data.get("subscription")
        if sub_id:
            agency = find_agency_by_stripe_subscription(sub_id)
            if agency:
                update_agency_billing(agency["id"], subscription_status="past_due")

    return {"received": True, "type": etype}


def verify_checkout_session(session_id: str, agency_id: str) -> dict:
    """Vérifie une session Checkout après retour navigateur (secours si webhook retardé)."""
    stripe = _stripe()
    session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    ref = session.get("client_reference_id") or (session.get("metadata") or {}).get(
        "agency_id"
    )
    if ref and ref != agency_id:
        raise ValueError("Cette session de paiement ne correspond pas à votre agence")

    if session.get("payment_status") == "paid" or session.get("status") == "complete":
        handle_checkout_completed(session, send_emails=True)
        sub = session.get("subscription")
        if sub and not isinstance(sub, str):
            sync_subscription_from_stripe(sub)
        elif isinstance(sub, str) and sub:
            sync_subscription_from_stripe(stripe.Subscription.retrieve(sub))

    agency = get_agency_billing(agency_id)
    return {
        "ok": True,
        "subscription_status": (agency or {}).get("subscription_status"),
    }
