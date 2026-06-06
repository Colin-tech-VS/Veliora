"""Retrait IA des marquages/logos (iad, Orpi, etc.) sur les photos d'annonces.

Le retrait passe par un service d'inpainting externe, choisi via la variable
``WATERMARK_AI_PROVIDER`` :

- ``""`` / ``none``  → désactivé, l'image est renvoyée telle quelle (la galerie
  fonctionne quand même, simplement sans nettoyage).
- ``heuristic``      → 100% local & gratuit (Pillow) : rogne la bande où se
  trouvent la plupart des marquages portails (bas / haut), sans IA ni réseau.
  Imparfait (ne retire pas un logo posé au centre) mais immédiat et sans clé.
- ``http``           → POST multipart générique vers ``WATERMARK_AI_URL`` ; la
  réponse doit être l'image nettoyée (``Content-Type: image/*``).
- ``replicate``      → API Replicate (``WATERMARK_AI_MODEL`` = version du modèle),
  l'image est envoyée en data-URI puis le résultat est téléchargé.

Toute erreur (réseau, quota, provider indisponible) est avalée : on retourne
l'image d'origine pour ne jamais bloquer le crawl ni l'affichage.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import time

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = float(os.getenv("WATERMARK_AI_TIMEOUT", "45"))
_POLL_MAX_SEC = float(os.getenv("WATERMARK_AI_POLL_MAX", "55"))


def watermark_provider() -> str:
    return (os.getenv("WATERMARK_AI_PROVIDER") or "").strip().lower()


def watermark_removal_enabled() -> bool:
    return watermark_provider() not in ("", "none", "off", "0", "false")


def _data_uri(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _looks_like_image(content_type: str | None, body: bytes) -> bool:
    if content_type and content_type.lower().startswith("image/"):
        return True
    # Signatures JPEG / PNG / WebP / GIF.
    return body[:3] == b"\xff\xd8\xff" or body[:8] == b"\x89PNG\r\n\x1a\n" or body[:4] in (
        b"RIFF",
        b"GIF8",
    )


def _env_fraction(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(max(v, 0.0), 0.4)


def is_probably_logo(raw: bytes) -> bool:
    """Heuristique conservatrice : True si l'image est vraisemblablement un logo
    ou une bannière (à exclure de la galerie). Volontairement stricte pour ne
    jamais écarter une vraie photo de bien."""
    if not raw:
        return True
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        # Très petite image → pictogramme.
        if max(w, h) < 150:
            return True
        # Format extrême (bannière / barre).
        ratio = max(w, h) / max(1, min(w, h))
        if ratio > 4:
            return True
        # PNG très majoritairement transparent → logo détouré.
        if img.mode in ("RGBA", "LA"):
            alpha = img.getchannel("A")
            hist = alpha.histogram()
            transparent = sum(hist[:16])
            total = w * h
            if total and transparent / total > 0.5:
                return True
    except Exception:
        return False
    return False


def _remove_heuristic(raw: bytes) -> bytes | None:
    """Rogne la bande haute/basse où vivent la plupart des marquages portails."""
    try:
        from PIL import Image
    except ImportError:
        return None
    bottom = _env_fraction("WATERMARK_CROP_BOTTOM", 0.07)
    top = _env_fraction("WATERMARK_CROP_TOP", 0.0)
    if bottom <= 0 and top <= 0:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        img = img.convert("RGB")
        w, h = img.size
        top_px = int(h * top)
        bottom_px = int(h * bottom)
        if h - top_px - bottom_px < h * 0.5:
            return None  # garde-fou : ne jamais retirer plus de la moitié
        cropped = img.crop((0, top_px, w, h - bottom_px))
        out = io.BytesIO()
        cropped.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:
        logger.exception("heuristic crop")
        return None


def _remove_http(raw: bytes) -> bytes | None:
    url = (os.getenv("WATERMARK_AI_URL") or "").strip()
    if not url:
        return None
    import requests

    headers = {}
    api_key = (os.getenv("WATERMARK_AI_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    field = (os.getenv("WATERMARK_AI_INPUT_KEY") or "image_file").strip()
    resp = requests.post(
        url,
        files={field: ("image.jpg", raw, "image/jpeg")},
        headers=headers,
        timeout=_REQUEST_TIMEOUT,
    )
    if resp.status_code == 200 and resp.content and _looks_like_image(
        resp.headers.get("Content-Type"), resp.content
    ):
        return resp.content
    logger.info("watermark http provider statut %s (%s o)", resp.status_code, len(resp.content or b""))
    return None


def _remove_replicate(raw: bytes) -> bytes | None:
    version = (os.getenv("WATERMARK_AI_MODEL") or "").strip()
    api_key = (os.getenv("WATERMARK_AI_API_KEY") or "").strip()
    if not version or not api_key:
        return None
    import requests

    input_key = (os.getenv("WATERMARK_AI_INPUT_KEY") or "image").strip()
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }
    create = requests.post(
        "https://api.replicate.com/v1/predictions",
        json={"version": version, "input": {input_key: _data_uri(raw)}},
        headers=headers,
        timeout=_REQUEST_TIMEOUT,
    )
    if create.status_code not in (200, 201):
        logger.info("watermark replicate create statut %s", create.status_code)
        return None
    pred = create.json()
    get_url = (pred.get("urls") or {}).get("get")
    deadline = time.monotonic() + _POLL_MAX_SEC
    while pred.get("status") in ("starting", "processing") and get_url:
        if time.monotonic() > deadline:
            logger.info("watermark replicate timeout")
            return None
        time.sleep(1.5)
        pred = requests.get(get_url, headers=headers, timeout=_REQUEST_TIMEOUT).json()
    if pred.get("status") != "succeeded":
        logger.info("watermark replicate statut final %s", pred.get("status"))
        return None
    out = pred.get("output")
    out_url = out[0] if isinstance(out, list) and out else out if isinstance(out, str) else None
    if not out_url:
        return None
    dl = requests.get(out_url, timeout=_REQUEST_TIMEOUT)
    if dl.status_code == 200 and dl.content and _looks_like_image(
        dl.headers.get("Content-Type"), dl.content
    ):
        return dl.content
    return None


def remove_watermark(raw: bytes) -> tuple[bytes, bool]:
    """Retire les marquages de l'image. Renvoie (octets, nettoyé?).

    En cas d'échec ou de provider désactivé, renvoie l'image d'origine inchangée
    afin de ne jamais casser la chaîne galerie.
    """
    if not raw:
        return raw, False
    provider = watermark_provider()
    if provider in ("", "none", "off", "0", "false"):
        return raw, False
    try:
        if provider == "heuristic":
            cleaned = _remove_heuristic(raw)
        elif provider == "http":
            cleaned = _remove_http(raw)
        elif provider == "replicate":
            cleaned = _remove_replicate(raw)
        else:
            logger.warning("WATERMARK_AI_PROVIDER inconnu : %s", provider)
            cleaned = None
    except Exception:
        logger.exception("retrait watermark (%s) échoué", provider)
        cleaned = None
    if cleaned:
        return cleaned, True
    return raw, False
