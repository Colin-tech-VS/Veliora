"""Briefing email quotidien + alertes (nouveaux particuliers / baisses de prix).

Tout est en dégradation gracieuse : sans SMTP configuré, l'envoi est un no-op
journalisé ; la boucle de fond ne démarre que si les notifications sont activées.
Mono-worker (``--workers 1``) → pas de double envoi.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_NOTIF_ENABLED = os.getenv("NOTIFICATIONS_ENABLED", "true").strip().lower() not in (
    "0", "false", "no", "off", ""
)
_BRIEFING_HOUR = int(os.getenv("BRIEFING_EMAIL_HOUR", "7"))  # heure UTC d'envoi
_ALERT_MIN_SCORE = int(os.getenv("ALERT_MIN_SCORE", "80"))
_CHECK_INTERVAL_SEC = int(os.getenv("NOTIFICATIONS_CHECK_INTERVAL_SEC", "600"))

_scheduler_started = False
_scheduler_lock = threading.Lock()


def _app_url() -> str:
    return (
        os.getenv("APP_PUBLIC_URL") or os.getenv("SITE_URL") or "https://veliora.fr"
    ).rstrip("/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_notifications_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agency_notifications (
            agency_id          TEXT PRIMARY KEY,
            daily_briefing     INTEGER NOT NULL DEFAULT 1,
            alerts             INTEGER NOT NULL DEFAULT 1,
            min_score          INTEGER NOT NULL DEFAULT 80,
            last_briefing_date TEXT,
            last_alert_at      TEXT,
            updated_at         TEXT
        )
        """
    )


def get_notification_prefs(agency_id: str) -> dict:
    from crawler.storage import get_connection

    with get_connection() as conn:
        ensure_notifications_table(conn)
        row = conn.execute(
            "SELECT * FROM agency_notifications WHERE agency_id = ?", (agency_id,)
        ).fetchone()
    if not row:
        return {
            "daily_briefing": True,
            "alerts": True,
            "min_score": _ALERT_MIN_SCORE,
            "last_briefing_date": None,
            "last_alert_at": None,
        }
    keys = row.keys()
    return {
        "daily_briefing": bool(row["daily_briefing"]),
        "alerts": bool(row["alerts"]),
        "min_score": int(row["min_score"] or _ALERT_MIN_SCORE),
        "last_briefing_date": row["last_briefing_date"] if "last_briefing_date" in keys else None,
        "last_alert_at": row["last_alert_at"] if "last_alert_at" in keys else None,
    }


def set_notification_prefs(agency_id: str, **fields) -> dict:
    from crawler.storage import get_connection

    current = get_notification_prefs(agency_id)
    daily = int(bool(fields.get("daily_briefing", current["daily_briefing"])))
    alerts = int(bool(fields.get("alerts", current["alerts"])))
    min_score = int(fields.get("min_score", current["min_score"]) or 0)
    with get_connection() as conn:
        ensure_notifications_table(conn)
        conn.execute(
            """INSERT INTO agency_notifications
               (agency_id, daily_briefing, alerts, min_score, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
                 daily_briefing = excluded.daily_briefing,
                 alerts = excluded.alerts,
                 min_score = excluded.min_score,
                 updated_at = excluded.updated_at""",
            (agency_id, daily, alerts, min_score, _now()),
        )
        conn.commit()
    return get_notification_prefs(agency_id)


def _mark_briefing_sent(agency_id: str, day: str) -> None:
    from crawler.storage import get_connection

    with get_connection() as conn:
        ensure_notifications_table(conn)
        conn.execute(
            """INSERT INTO agency_notifications (agency_id, last_briefing_date, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
                 last_briefing_date = excluded.last_briefing_date,
                 updated_at = excluded.updated_at""",
            (agency_id, day, _now()),
        )
        conn.commit()


def _mark_alert_sent(agency_id: str, at: str) -> None:
    from crawler.storage import get_connection

    with get_connection() as conn:
        ensure_notifications_table(conn)
        conn.execute(
            """INSERT INTO agency_notifications (agency_id, last_alert_at, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agency_id) DO UPDATE SET
                 last_alert_at = excluded.last_alert_at,
                 updated_at = excluded.updated_at""",
            (agency_id, at, _now()),
        )
        conn.commit()


def agency_recipients(agency_id: str) -> list[str]:
    """Emails destinataires : utilisateurs actifs, repli sur l'email de l'agence."""
    from crawler.storage import get_connection

    emails: list[str] = []
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT email, role FROM agency_users WHERE agency_id = ? AND active = 1",
                (agency_id,),
            ).fetchall()
            admins = [r["email"] for r in rows if (r["role"] or "") == "admin" and r["email"]]
            others = [r["email"] for r in rows if (r["role"] or "") != "admin" and r["email"]]
            emails = admins or others
            if not emails:
                arow = conn.execute(
                    "SELECT email FROM agencies WHERE id = ?", (agency_id,)
                ).fetchone()
                if arow and arow["email"]:
                    emails = [arow["email"]]
    except Exception:
        logger.debug("agency_recipients %s", agency_id, exc_info=True)
    # Dédoublonnage en conservant l'ordre.
    seen: set[str] = set()
    out: list[str] = []
    for e in emails:
        e = (e or "").strip().lower()
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def _fmt_price(lead: dict) -> str:
    p = lead.get("price")
    if not p:
        return "—"
    try:
        return f"{int(p):,}".replace(",", " ") + " €"
    except (TypeError, ValueError):
        return "—"


def _lead_line(lead: dict, app_url: str) -> str:
    import html as _html

    score = int(lead.get("mandate_score") or 0)
    title = _html.escape(
        (lead.get("listing_title") or lead.get("address") or lead.get("city") or "Annonce")[:80]
    )
    city = _html.escape(lead.get("city") or "")
    reason = _html.escape((lead.get("mandate_score_reason") or "")[:120])
    link = f"{app_url}/crm#lead-{lead.get('id')}"
    return (
        f'<tr>'
        f'<td style="padding:6px 10px;font-weight:700;color:#152a36;">{score}</td>'
        f'<td style="padding:6px 10px;"><a href="{link}" style="color:#1b6ec2;text-decoration:none;">{title}</a>'
        f'<div style="color:#667;font-size:12px;">{city} · {_fmt_price(lead)} · {reason}</div></td>'
        f'</tr>'
    )


def render_briefing_email(briefing: dict, agency_name: str, app_url: str) -> tuple[str, str]:
    counts = briefing.get("counts") or {}
    priorities = (briefing.get("priorities") or [])[:8]
    day = briefing.get("date") or ""
    subject = (
        f"Veliora — {counts.get('hot_mandate', 0)} vendeurs prioritaires à appeler aujourd'hui"
    )
    rows = "".join(_lead_line(l, app_url) for l in priorities) or (
        '<tr><td style="padding:10px;color:#667;">Aucune priorité forte aujourd\'hui — '
        'lancez une veille ou élargissez votre secteur.</td></tr>'
    )
    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:auto;">
  <h2 style="color:#152a36;">Briefing du {day}</h2>
  <p style="color:#445;">Bonjour, voici vos priorités d'appel pour <strong>{agency_name}</strong>.</p>
  <table style="border-collapse:collapse;margin:12px 0;">
    <tr>
      <td style="padding:8px 14px;background:#f3f6f8;border-radius:8px;">
        <strong>{counts.get('new_without_agency', 0)}</strong><br><span style="font-size:12px;color:#667;">nouveaux particuliers</span>
      </td>
      <td style="padding:8px 14px;background:#f3f6f8;border-radius:8px;">
        <strong>{counts.get('price_drops', 0)}</strong><br><span style="font-size:12px;color:#667;">baisses de prix</span>
      </td>
      <td style="padding:8px 14px;background:#f3f6f8;border-radius:8px;">
        <strong>{counts.get('hot_mandate', 0)}</strong><br><span style="font-size:12px;color:#667;">score ≥ 85</span>
      </td>
    </tr>
  </table>
  <h3 style="color:#152a36;">À appeler en premier</h3>
  <table style="border-collapse:collapse;width:100%;">{rows}</table>
  <p style="margin-top:18px;"><a href="{app_url}/crm" style="background:#1b6ec2;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Ouvrir le CRM</a></p>
  <p style="color:#99a;font-size:12px;margin-top:18px;">Vous recevez ce briefing car il est activé pour votre agence. Réglages dans le CRM.</p>
</div>"""
    return subject, html


def send_daily_briefing(agency_id: str, *, force: bool = False) -> bool:
    """Construit et envoie le briefing du jour à l'agence. Idempotent par jour."""
    from crawler.storage import get_agency_name, get_agency_settings, get_leads
    from crm.email.service import send_email
    from crm.radar import build_briefing

    prefs = get_notification_prefs(agency_id)
    if not force and not prefs["daily_briefing"]:
        return False
    today = datetime.now(timezone.utc).date().isoformat()
    if not force and prefs.get("last_briefing_date") == today:
        return False

    recipients = agency_recipients(agency_id)
    if not recipients:
        return False

    name = get_agency_name(agency_id) or "votre agence"
    settings = get_agency_settings(agency_id)
    leads = get_leads(agency_id)
    briefing = build_briefing(
        leads, name, target_cities=settings.get("target_cities") or []
    )
    subject, html = render_briefing_email(briefing, name, _app_url())
    sent_any = False
    for to in recipients:
        if send_email(to, subject, html, from_name="Veliora"):
            sent_any = True
    # On marque la date même si SMTP off (no-op) seulement si réellement envoyé,
    # afin de réessayer quand l'email sera configuré.
    if sent_any:
        _mark_briefing_sent(agency_id, today)
    return sent_any


def _is_recent(iso: str | None, since_iso: str | None) -> bool:
    if not iso:
        return False
    if not since_iso:
        return True
    return str(iso) > str(since_iso)


def send_alert_digest(agency_id: str, *, force: bool = False) -> int:
    """Email digest des nouveaux particuliers / baisses depuis le dernier passage."""
    from crawler.storage import get_agency_name, get_leads
    from crm.email.service import send_email
    from crm.radar import is_active_lead, is_particulier_lead

    prefs = get_notification_prefs(agency_id)
    if not force and not prefs["alerts"]:
        return 0
    since = None if force else prefs.get("last_alert_at")
    min_score = int(prefs.get("min_score") or _ALERT_MIN_SCORE)

    leads = get_leads(agency_id)
    fresh: list[dict] = []
    for l in leads:
        if not is_active_lead(l) or not is_particulier_lead(l):
            continue
        if int(l.get("mandate_score") or 0) < min_score:
            continue
        is_new = _is_recent(l.get("created_at"), since)
        is_drop = "baisse_prix" in (l.get("alert_tags") or []) and _is_recent(
            l.get("last_price_change_at"), since
        )
        if is_new or is_drop:
            fresh.append(l)

    now = _now()
    if not fresh:
        if not force:
            _mark_alert_sent(agency_id, now)
        return 0

    recipients = agency_recipients(agency_id)
    if not recipients:
        return 0
    fresh.sort(key=lambda x: int(x.get("mandate_score") or 0), reverse=True)
    name = get_agency_name(agency_id) or "votre agence"
    app_url = _app_url()
    import html as _html

    rows = "".join(_lead_line(l, app_url) for l in fresh[:15])
    subject = f"Veliora — {len(fresh)} nouvelle(s) opportunité(s) à fort potentiel"
    body = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:auto;">
  <h2 style="color:#152a36;">{_html.escape(name)} — alertes mandat</h2>
  <p style="color:#445;">{len(fresh)} vendeur(s) particulier(s) à fort potentiel (score ≥ {min_score}) depuis votre dernière alerte :</p>
  <table style="border-collapse:collapse;width:100%;">{rows}</table>
  <p style="margin-top:18px;"><a href="{app_url}/crm" style="background:#1b6ec2;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Appeler maintenant</a></p>
</div>"""
    sent_any = False
    for to in recipients:
        if send_email(to, subject, body, from_name="Veliora"):
            sent_any = True
    if sent_any:
        _mark_alert_sent(agency_id, now)
    return len(fresh) if sent_any else 0


def run_alert_pass() -> int:
    from crawler.storage import list_agency_ids

    total = 0
    for aid in list_agency_ids():
        try:
            total += send_alert_digest(aid)
        except Exception:
            logger.exception("alert digest %s", aid)
    return total


def run_daily_briefings() -> int:
    from crawler.storage import list_agency_ids

    n = 0
    for aid in list_agency_ids():
        try:
            if send_daily_briefing(aid):
                n += 1
        except Exception:
            logger.exception("daily briefing %s", aid)
    return n


def _scheduler_loop() -> None:
    logger.info("Notifications : scheduler démarré (briefing %dh UTC)", _BRIEFING_HOUR)
    while True:
        try:
            run_alert_pass()
            if datetime.now(timezone.utc).hour >= _BRIEFING_HOUR:
                run_daily_briefings()
        except Exception:
            logger.exception("notifications scheduler")
        time.sleep(_CHECK_INTERVAL_SEC)


def start_notifications_scheduler() -> None:
    """Démarre la boucle de notifications (idempotent, opt-out, no-op sans SMTP)."""
    global _scheduler_started
    from crm.email.service import email_enabled

    if not _NOTIF_ENABLED:
        logger.info("Notifications désactivées (NOTIFICATIONS_ENABLED=false)")
        return
    if not email_enabled():
        logger.info("Notifications : SMTP non configuré — scheduler non démarré")
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        threading.Thread(
            target=_scheduler_loop, daemon=True, name="veliora-notifications"
        ).start()
