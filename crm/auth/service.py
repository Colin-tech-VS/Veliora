"""Inscription agence, collaborateurs, sessions (SQLite local)."""

from __future__ import annotations

import os
import re
import secrets
import uuid
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from crawler.storage import get_connection

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return s[:48] or "agence"


def register_agency(
    agency_name: str,
    admin_email: str,
    password: str,
    admin_first_name: str = "",
    admin_last_name: str = "",
    city: str = "",
) -> dict:
    agency_name = (agency_name or "").strip()
    admin_email = (admin_email or "").strip().lower()
    password = password or ""
    city = (city or "").strip()

    if len(agency_name) < 2:
        raise ValueError("Nom d'agence requis (2 caractères minimum)")
    if not EMAIL_RE.match(admin_email):
        raise ValueError("Email invalide")
    if len(password) < 8:
        raise ValueError("Mot de passe : 8 caractères minimum")
    if len(city) < 2:
        raise ValueError("Ville de l'agence requise (le crawl se fait sur votre ville)")

    agency_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    slug = _slugify(agency_name)
    pwd_hash = generate_password_hash(password)

    with get_connection() as conn:
        if conn.execute(
            "SELECT id FROM agencies WHERE email = ? OR slug = ?",
            (admin_email, slug),
        ).fetchone():
            raise ValueError("Cette agence ou cet email existe déjà")

        from crm.billing.stripe_service import initial_subscription_status

        sub_status = initial_subscription_status()
        conn.execute(
            """INSERT INTO agencies (
                   id, name, slug, email, created_at,
                   subscription_status, subscription_plan
               ) VALUES (?, ?, ?, ?, ?, ?, 'veliora_pro')""",
            (agency_id, agency_name, slug, admin_email, _now(), sub_status),
        )
        conn.execute(
            """INSERT INTO agency_users
               (id, agency_id, email, password_hash, role, first_name, last_name, active, created_at)
               VALUES (?, ?, ?, ?, 'admin', ?, ?, 1, ?)""",
            (
                user_id,
                agency_id,
                admin_email,
                pwd_hash,
                admin_first_name.strip(),
                admin_last_name.strip(),
                _now(),
            ),
        )
        conn.commit()

    from crawler.storage import seed_default_sources_for_agency, upsert_agency_settings

    seed_default_sources_for_agency(agency_id)
    # Ville de l'agence : sert de filtre par défaut à tous les crawls (crawl local).
    upsert_agency_settings(agency_id, {"target_cities": [city]})

    from crm.email.service import send_welcome_email

    app_url = os.getenv("APP_PUBLIC_URL", "http://localhost:8000")
    send_welcome_email(admin_email, agency_name, app_url)

    login = login_user(admin_email, password)
    if not login:
        return {
            "agency_id": agency_id,
            "user_id": user_id,
            "agency_name": agency_name,
            "email": admin_email,
            "role": "admin",
        }

    return {
        "agency_id": agency_id,
        "user_id": user_id,
        "agency_name": agency_name,
        "email": admin_email,
        "role": "admin",
        "token": login["token"],
        "user": login["user"],
    }


def login_user(email: str, password: str) -> dict | None:
    email = (email or "").strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            """SELECT u.*, a.name AS agency_name, a.slug AS agency_slug
               FROM agency_users u
               JOIN agencies a ON a.id = u.agency_id
               WHERE u.email = ? AND u.active = 1""",
            (email,),
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None

        token = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE agency_users SET last_login_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        conn.execute(
            """INSERT INTO auth_sessions (token, user_id, agency_id, created_at, expires_at)
               VALUES (?, ?, ?, ?, datetime('now', '+30 days'))""",
            (token, row["id"], row["agency_id"], _now()),
        )
        conn.commit()

    return {
        "token": token,
        "user": {
            "id": row["id"],
            "email": row["email"],
            "role": row["role"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "agency_id": row["agency_id"],
            "agency_name": row["agency_name"],
            "agency_slug": row["agency_slug"],
        },
    }


def invite_collaborator(
    agency_id: str,
    email: str,
    password: str,
    *,
    first_name: str = "",
    last_name: str = "",
    role: str = "collaborator",
) -> dict:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Email invalide")
    if len(password) < 8:
        raise ValueError("Mot de passe : 8 caractères minimum")
    if role not in ("collaborator", "admin"):
        role = "collaborator"

    user_id = str(uuid.uuid4())
    with get_connection() as conn:
        agency = conn.execute("SELECT id FROM agencies WHERE id = ?", (agency_id,)).fetchone()
        if not agency:
            raise ValueError("Agence introuvable")
        if conn.execute("SELECT id FROM agency_users WHERE email = ?", (email,)).fetchone():
            raise ValueError("Cet email est déjà utilisé")

        conn.execute(
            """INSERT INTO agency_users
               (id, agency_id, email, password_hash, role, first_name, last_name, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                user_id,
                agency_id,
                email,
                generate_password_hash(password),
                role,
                first_name.strip(),
                last_name.strip(),
                _now(),
            ),
        )
        conn.commit()

    return {"id": user_id, "email": email, "role": role, "agency_id": agency_id}


def request_password_reset(email: str, *, app_url: str) -> dict:
    """Crée un token reset (toujours OK côté API pour ne pas révéler les emails)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Email invalide")

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta

    expires_at = (expires + timedelta(hours=1)).isoformat()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, email FROM agency_users WHERE email = ? AND active = 1",
            (email,),
        ).fetchone()
        if row:
            conn.execute(
                """INSERT INTO password_reset_tokens (token, user_id, email, expires_at, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (token, row["id"], email, expires_at, _now()),
            )
            conn.commit()
            from crm.email.service import send_password_reset_email

            send_password_reset_email(
                email,
                f"{app_url.rstrip('/')}/crm/auth?reset={token}",
            )
    return {"ok": True, "message": "Si cet email existe, un lien de réinitialisation a été envoyé."}


def reset_password_with_token(token: str, new_password: str) -> dict:
    token = (token or "").strip()
    if len(new_password or "") < 8:
        raise ValueError("Mot de passe : 8 caractères minimum")
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM password_reset_tokens
               WHERE token = ? AND used = 0 AND datetime(expires_at) > datetime('now')""",
            (token,),
        ).fetchone()
        if not row:
            raise ValueError("Lien expiré ou invalide")
        conn.execute(
            "UPDATE agency_users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password), row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE token = ?",
            (token,),
        )
        conn.commit()
    return {"ok": True, "message": "Mot de passe mis à jour — vous pouvez vous connecter."}


def get_session_user(token: str) -> dict | None:
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT u.*, a.name AS agency_name
               FROM auth_sessions s
               JOIN agency_users u ON u.id = s.user_id
               JOIN agencies a ON a.id = s.agency_id
               WHERE s.token = ? AND datetime(s.expires_at) > datetime('now')""",
            (token,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "email": row["email"],
            "role": row["role"],
            "agency_id": row["agency_id"],
            "agency_name": row["agency_name"],
        }
