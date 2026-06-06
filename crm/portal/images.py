"""Images publiques du portail — cache local WebP, marquages retirés.

Les annonces portail référencent des URLs d'images sources (``images_json``).
À la première requête publique, l'image est téléchargée, nettoyée (retrait IA
des marquages) puis convertie en WebP et mise en cache sur disque. Les requêtes
suivantes servent directement le fichier.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_PORTAL_IMAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "portal_images"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _listing_dir(listing_id: str) -> Path:
    d = _PORTAL_IMAGE_ROOT / str(listing_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(listing_id: str, idx: int) -> Path:
    return _listing_dir(listing_id) / f"{idx:03d}.webp"


def _lock_for(listing_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(listing_id)
        if lock is None:
            lock = threading.Lock()
            _locks[listing_id] = lock
        return lock


def resolve_portal_image_path(listing_id: str, idx: int) -> Path | None:
    """Renvoie le WebP en cache, le générant depuis l'URL source si besoin."""
    listing_id = str(listing_id)
    if idx < 0:
        return None
    cached = _path(listing_id, idx)
    if cached.is_file() and cached.stat().st_size > 80:
        return cached

    from crm.portal.storage import get_listing

    item = get_listing(listing_id, public=True)
    if not item:
        return None
    sources = item.get("source_images") or (
        [item["image_url"]] if item.get("image_url") else []
    )
    if idx >= len(sources):
        return None
    src = (sources[idx] or "").strip()
    if not src:
        return None

    lock = _lock_for(f"{listing_id}:{idx}")
    with lock:
        if cached.is_file() and cached.stat().st_size > 80:
            return cached
        try:
            from crm.leads.images import _download_bytes, process_image_for_storage

            raw = _download_bytes(src, referer=src)
            if not raw:
                return None
            webp, _removed = process_image_for_storage(raw)
            cached.write_bytes(webp)
            return cached
        except Exception:
            logger.exception("portal image cache %s[%s]", listing_id, idx)
            return None


def delete_portal_images(listing_id: str) -> None:
    d = _PORTAL_IMAGE_ROOT / str(listing_id)
    if d.is_dir():
        for f in d.glob("*.webp"):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            d.rmdir()
        except OSError:
            pass
