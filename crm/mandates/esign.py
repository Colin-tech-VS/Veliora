"""Signature électronique des mandats — scaffold opt-in (Yousign).

Activation par variables d'environnement (sinon no-op explicite) :
- ``ESIGN_PROVIDER=yousign``
- ``YOUSIGN_API_KEY=...``
- ``YOUSIGN_API_BASE`` (défaut https://api.yousign.com — sandbox :
  https://api-sandbox.yousign.com)

La mise en production nécessite : un compte Yousign + un générateur PDF
(WeasyPrint/pdfkit installé) pour transformer le mandat HTML en document
signable. Sans ces prérequis, l'initiation renvoie une erreur claire et rien
n'est cassé dans le flux mandats existant (validation manuelle inchangée).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TIMEOUT = float(os.getenv("ESIGN_TIMEOUT", "30"))


def esign_provider() -> str:
    return (os.getenv("ESIGN_PROVIDER") or "").strip().lower()


def esign_enabled() -> bool:
    provider = esign_provider()
    if provider in ("", "none", "off", "0", "false"):
        return False
    if provider == "yousign":
        return bool((os.getenv("YOUSIGN_API_KEY") or "").strip())
    return False


def _mandate_pdf_bytes(mandate: dict) -> bytes | None:
    """Convertit le HTML du mandat en PDF si un moteur est disponible."""
    html = mandate.get("body_html") or ""
    if not html:
        return None
    try:
        from weasyprint import HTML  # type: ignore

        return HTML(string=html).write_pdf()
    except Exception:
        logger.info("WeasyPrint indisponible — PDF mandat non généré")
    try:
        import pdfkit  # type: ignore

        return pdfkit.from_string(html, False)
    except Exception:
        logger.info("pdfkit indisponible — PDF mandat non généré")
    return None


def _yousign_send(mandate: dict, signer_name: str, signer_email: str) -> dict:
    import requests

    api_key = (os.getenv("YOUSIGN_API_KEY") or "").strip()
    base = (os.getenv("YOUSIGN_API_BASE") or "https://api.yousign.com").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}

    pdf = _mandate_pdf_bytes(mandate)
    if not pdf:
        return {
            "ok": False,
            "error": "Génération PDF indisponible (installez WeasyPrint) — requis pour Yousign.",
        }

    # 1) Créer la demande de signature.
    sr = requests.post(
        f"{base}/v3/signature_requests",
        json={"name": mandate.get("title") or "Mandat", "delivery_mode": "email"},
        headers=headers,
        timeout=_TIMEOUT,
    )
    if sr.status_code not in (200, 201):
        return {"ok": False, "error": f"Yousign create {sr.status_code}: {sr.text[:200]}"}
    sr_id = sr.json().get("id")

    # 2) Téléverser le document.
    doc = requests.post(
        f"{base}/v3/signature_requests/{sr_id}/documents",
        files={"file": ("mandat.pdf", pdf, "application/pdf")},
        data={"nature": "signable_document"},
        headers=headers,
        timeout=_TIMEOUT,
    )
    if doc.status_code not in (200, 201):
        return {"ok": False, "error": f"Yousign document {doc.status_code}: {doc.text[:200]}"}

    # 3) Ajouter le signataire (vendeur).
    first, _, last = (signer_name or "").partition(" ")
    signer = requests.post(
        f"{base}/v3/signature_requests/{sr_id}/signers",
        json={
            "info": {
                "first_name": first or "Vendeur",
                "last_name": last or "—",
                "email": signer_email,
                "locale": "fr",
            },
            "signature_level": "electronic_signature",
            "signature_authentication_mode": "no_otp",
        },
        headers=headers,
        timeout=_TIMEOUT,
    )
    if signer.status_code not in (200, 201):
        return {"ok": False, "error": f"Yousign signer {signer.status_code}: {signer.text[:200]}"}

    # 4) Activer la demande (déclenche l'email Yousign au signataire).
    act = requests.post(
        f"{base}/v3/signature_requests/{sr_id}/activate",
        headers=headers,
        timeout=_TIMEOUT,
    )
    if act.status_code not in (200, 201):
        return {"ok": False, "error": f"Yousign activate {act.status_code}: {act.text[:200]}"}
    signer_url = (signer.json().get("signature_link") or "")
    return {"ok": True, "request_id": sr_id, "status": "pending", "signer_url": signer_url}


def send_for_signature(mandate: dict, signer_name: str, signer_email: str) -> dict:
    """Lance une demande de signature électronique. No-op explicite si désactivé."""
    if not esign_enabled():
        return {
            "ok": False,
            "error": "Signature électronique non configurée (ESIGN_PROVIDER/YOUSIGN_API_KEY).",
            "code": "esign_disabled",
        }
    if not (signer_email or "").strip():
        return {"ok": False, "error": "Email du signataire requis."}
    provider = esign_provider()
    try:
        if provider == "yousign":
            out = _yousign_send(mandate, signer_name, signer_email)
        else:
            out = {"ok": False, "error": f"Provider e-sign inconnu : {provider}"}
    except Exception as exc:
        logger.exception("send_for_signature")
        out = {"ok": False, "error": f"Erreur signature : {exc}"[:200]}
    if out.get("ok"):
        out["provider"] = provider
    return out


def parse_completion_webhook(payload: dict) -> dict | None:
    """Extrait (request_id, completed?) d'un webhook Yousign. None si non pertinent."""
    if not isinstance(payload, dict):
        return None
    event = payload.get("event_name") or payload.get("event") or ""
    data = payload.get("data") or {}
    sr = data.get("signature_request") or data.get("signatureRequest") or {}
    request_id = sr.get("id") or data.get("signature_request_id")
    if not request_id:
        return None
    completed = "done" in str(event).lower() or str(sr.get("status") or "").lower() in (
        "done",
        "completed",
        "signed",
    )
    return {"request_id": request_id, "completed": completed, "status": sr.get("status")}
