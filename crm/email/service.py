"""Envoi d'emails SMTP (optionnel — sans config, log uniquement)."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = (os.getenv("SMTP_HOST") or "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = (os.getenv("SMTP_USER") or "").strip()
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").strip()
SMTP_FROM = (os.getenv("SMTP_FROM") or os.getenv("SUPPORT_EMAIL") or "noreply@veliora.fr").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")


def email_enabled() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to: str, subject: str, html_body: str, text_body: str | None = None) -> bool:
    to = (to or "").strip()
    if not to:
        return False
    text_body = text_body or _html_to_text(html_body)
    if not email_enabled():
        logger.info("Email (non envoyé — SMTP non configuré) → %s | %s", to, subject)
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if SMTP_USE_TLS:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.starttls()
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [to], msg.as_string())
        else:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [to], msg.as_string())
        logger.info("Email envoyé → %s (%s)", to, subject)
        return True
    except Exception as exc:
        logger.exception("Échec envoi email à %s : %s", to, exc)
        return False


def _html_to_text(html: str) -> str:
    import re

    t = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def send_welcome_email(to: str, agency_name: str, app_url: str) -> bool:
    subject = f"Bienvenue sur Veliora — {agency_name}"
    html = f"""
    <p>Bonjour,</p>
    <p>Votre agence <strong>{agency_name}</strong> est prête sur Veliora.</p>
    <p><strong>Prochaines étapes :</strong></p>
    <ol>
      <li>Ajoutez une source (portail immobilier)</li>
      <li>Lancez votre premier crawl</li>
      <li>Consultez le briefing priorités mandat</li>
    </ol>
    <p><a href="{app_url}/crm">Accéder au CRM</a></p>
    <p>— L'équipe Veliora</p>
    """
    return send_email(to, subject, html)


def send_payment_confirmed_email(to: str, agency_name: str, amount_eur: int) -> bool:
    subject = "Paiement confirmé — Veliora"
    html = f"""
    <p>Bonjour,</p>
    <p>Nous avons bien reçu votre abonnement Veliora pour <strong>{agency_name}</strong>
    ({amount_eur} €/mois).</p>
    <p>Vos factures sont disponibles dans l'espace Stripe (bouton « Gérer mon abonnement »).</p>
    <p>— Veliora</p>
    """
    return send_email(to, subject, html)


def send_password_reset_email(to: str, reset_url: str) -> bool:
    subject = "Réinitialisation de votre mot de passe Veliora"
    html = f"""
    <p>Bonjour,</p>
    <p>Pour choisir un nouveau mot de passe, cliquez sur le lien ci-dessous (valable 1 heure) :</p>
    <p><a href="{reset_url}">{reset_url}</a></p>
    <p>Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.</p>
  """
    return send_email(to, subject, html)
