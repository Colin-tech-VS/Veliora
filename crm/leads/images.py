"""Images annonces — crawl, WebP, remplacement CRM."""

from __future__ import annotations

import io
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from crawler.storage import get_connection
from velora_db.config import is_postgres

logger = logging.getLogger(__name__)

_IMAGE_ROOT = Path(__file__).resolve().parents[2] / "data" / "lead_images"
_MAX_DOWNLOAD = 6 * 1024 * 1024
_MAX_EDGE = 1400
_WEBP_QUALITY = 82
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


#: Nombre maximum d'images de galerie conservées par annonce.
MAX_GALLERY_IMAGES = 10


def ensure_lead_image_schema() -> None:
    with get_connection() as conn:
        _ensure_lead_images_table(conn)
        if is_postgres():
            cur = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'leads'
                  AND column_name IN ('listing_image_url', 'image_custom', 'image_updated_at')
                """
            )
            cols = set()
            for r in cur.fetchall():
                if isinstance(r, dict):
                    cols.add(r.get("column_name") or next(iter(r.values()), ""))
                elif isinstance(r, (tuple, list)):
                    cols.add(r[0])
            if "listing_image_url" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN listing_image_url TEXT")
            if "image_custom" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN image_custom SMALLINT NOT NULL DEFAULT 0")
            if "image_updated_at" not in cols:
                conn.execute("ALTER TABLE leads ADD COLUMN image_updated_at TEXT")
        else:
            lcols = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
            if lcols:
                if "listing_image_url" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN listing_image_url TEXT")
                if "image_custom" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN image_custom INTEGER NOT NULL DEFAULT 0")
                if "image_updated_at" not in lcols:
                    conn.execute("ALTER TABLE leads ADD COLUMN image_updated_at TEXT")
        conn.commit()


def _ensure_lead_images_table(conn) -> None:
    """Table de métadonnées galerie (1 ligne par image, fichiers sur disque)."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_images (
            agency_id         TEXT NOT NULL,
            lead_id           INTEGER NOT NULL,
            position          INTEGER NOT NULL,
            source_url        TEXT,
            watermark_removed INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT,
            PRIMARY KEY (agency_id, lead_id, position)
        )
        """
    )


def _agency_dir(agency_id: str) -> Path:
    d = _IMAGE_ROOT / agency_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _paths(agency_id: str, lead_id: int) -> tuple[Path, Path]:
    base = _agency_dir(agency_id)
    return base / f"{lead_id}_crawl.webp", base / f"{lead_id}.webp"


def _gallery_dir(agency_id: str, lead_id: int) -> Path:
    d = _agency_dir(agency_id) / f"{lead_id}_gallery"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gallery_path(agency_id: str, lead_id: int, position: int) -> Path:
    return _gallery_dir(agency_id, lead_id) / f"{position:03d}.webp"


def lead_has_display_image(agency_id: str, lead_id: int) -> bool:
    _, active = _paths(agency_id, lead_id)
    return active.is_file() and active.stat().st_size > 80


def _to_webp(raw: bytes) -> bytes:
    try:
        from PIL import Image
    except ImportError:
        return raw
    img = Image.open(io.BytesIO(raw))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > _MAX_EDGE:
        ratio = _MAX_EDGE / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=_WEBP_QUALITY, method=4)
    return out.getvalue()


def process_image_for_storage(raw: bytes) -> tuple[bytes, bool]:
    """Retire les marquages (IA) puis convertit en WebP. Renvoie (webp, nettoyé?)."""
    cleaned = raw
    removed = False
    try:
        from crm.leads.watermark import remove_watermark

        cleaned, removed = remove_watermark(raw)
    except Exception:
        logger.exception("remove_watermark")
        cleaned, removed = raw, False
    return _to_webp(cleaned), removed


def _download_bytes(url: str, referer: str | None = None) -> bytes | None:
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer

    try:
        import requests

        resp = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
        if resp.status_code == 200 and resp.content:
            data = resp.content[: _MAX_DOWNLOAD + 1]
            if len(data) <= _MAX_DOWNLOAD:
                return data
    except Exception as exc:
        logger.debug("requests image download %s: %s", url[:80], exc)

    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.get(
            url,
            headers=headers,
            timeout=25,
            allow_redirects=True,
            impersonate="chrome",
        )
        if resp.status_code == 200 and resp.content:
            data = resp.content[: _MAX_DOWNLOAD + 1]
            if len(data) <= _MAX_DOWNLOAD:
                return data
    except Exception:
        pass

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read(_MAX_DOWNLOAD + 1)
            if len(data) > _MAX_DOWNLOAD:
                return None
            return data
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.info("lead image download failed %s: %s", url[:100], exc)
        return None


def _write_webp(path: Path, raw: bytes) -> bool:
    try:
        webp = _to_webp(raw)
        path.write_bytes(webp)
        return True
    except Exception:
        logger.exception("write webp %s", path)
        return False


def _touch_lead_image_meta(
    conn,
    lead_id: int,
    agency_id: str,
    *,
    listing_image_url: str | None = None,
    image_custom: int | None = None,
) -> None:
    from crawler.storage import _now

    now = _now()
    sets = ["image_updated_at = ?"]
    vals: list[object] = [now]
    if listing_image_url is not None:
        sets.append("listing_image_url = ?")
        vals.append(listing_image_url)
    if image_custom is not None:
        sets.append("image_custom = ?")
        vals.append(int(image_custom))
    vals.extend([lead_id, agency_id])
    conn.execute(
        f"UPDATE leads SET {', '.join(sets)} WHERE id = ? AND agency_id = ?",
        tuple(vals),
    )


def sync_lead_image_from_url(
    lead_id: int,
    agency_id: str,
    image_url: str,
    *,
    respect_custom: bool = True,
    referer: str | None = None,
) -> bool:
    """Télécharge l'image portail, convertit en WebP (_crawl + affichage si non personnalisée)."""
    if not image_url or not agency_id or not lead_id:
        return False
    ensure_lead_image_schema()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT image_custom, listing_image_url, source_url FROM leads WHERE id = ? AND agency_id = ?",
            (lead_id, agency_id),
        ).fetchone()
    if not row:
        return False

    ref = referer or (row["source_url"] if "source_url" in row.keys() else None) or image_url
    custom = int(row["image_custom"] or 0)

    raw = _download_bytes(image_url, referer=ref)
    if not raw:
        return False

    # Nettoyage IA des marquages (iad, Orpi…) une seule fois, puis WebP.
    try:
        webp, _removed = process_image_for_storage(raw)
    except Exception:
        logger.exception("process primary image %s", lead_id)
        return False

    crawl_path, active_path = _paths(agency_id, lead_id)
    try:
        crawl_path.write_bytes(webp)
    except OSError:
        logger.exception("write crawl webp %s", crawl_path)
        return False

    if respect_custom and custom:
        with get_connection() as conn:
            _touch_lead_image_meta(conn, lead_id, agency_id, listing_image_url=image_url)
            conn.commit()
        return True

    try:
        active_path.write_bytes(webp)
    except OSError:
        logger.exception("write active webp %s", active_path)
        return False

    with get_connection() as conn:
        _touch_lead_image_meta(
            conn,
            lead_id,
            agency_id,
            listing_image_url=image_url,
            image_custom=0,
        )
        conn.commit()
    return True


def _clear_gallery_files(agency_id: str, lead_id: int) -> None:
    d = _agency_dir(agency_id) / f"{lead_id}_gallery"
    if d.is_dir():
        for f in d.glob("*.webp"):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass


def sync_lead_gallery_from_urls(
    lead_id: int,
    agency_id: str,
    image_urls: list[str],
    *,
    referer: str | None = None,
    max_images: int = MAX_GALLERY_IMAGES,
) -> int:
    """Télécharge la galerie complète (≤ max), nettoie les marquages, écrit en WebP.

    Renvoie le nombre d'images réellement enregistrées. Remplace l'ancienne galerie.
    """
    if not agency_id or not lead_id:
        return 0
    urls: list[str] = []
    seen: set[str] = set()
    for u in image_urls or []:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    urls = urls[: max(1, max_images)]
    if not urls:
        return 0

    ensure_lead_image_schema()
    from crawler.storage import _now

    now = _now()
    saved = 0
    rows: list[tuple] = []
    _clear_gallery_files(agency_id, lead_id)
    try:
        from crm.leads.watermark import is_probably_logo
    except Exception:
        is_probably_logo = lambda _b: False  # noqa: E731
    for url in urls:
        raw = _download_bytes(url, referer=referer or url)
        if not raw:
            continue
        # Écarte les logos/bannières évidents (jamais une vraie photo de bien).
        if is_probably_logo(raw):
            continue
        try:
            webp, removed = process_image_for_storage(raw)
        except Exception:
            logger.exception("process gallery image %s", url[:80])
            continue
        path = _gallery_path(agency_id, lead_id, saved)
        try:
            path.write_bytes(webp)
        except OSError:
            logger.exception("write gallery webp %s", path)
            continue
        rows.append((agency_id, lead_id, saved, url, 1 if removed else 0, now))
        saved += 1

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM lead_images WHERE agency_id = ? AND lead_id = ?",
            (agency_id, lead_id),
        )
        for r in rows:
            conn.execute(
                """INSERT INTO lead_images
                   (agency_id, lead_id, position, source_url, watermark_removed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                r,
            )
        conn.commit()
    return saved


def resolve_lead_gallery_path(agency_id: str, lead_id: int, position: int) -> Path | None:
    p = _IMAGE_ROOT / str(agency_id) / f"{lead_id}_gallery" / f"{position:03d}.webp"
    if p.is_file() and p.stat().st_size > 80:
        return p
    # Repli : la position 0 correspond à l'image principale historique.
    if position == 0:
        _, active = _paths(agency_id, lead_id)
        if active.is_file():
            return active
    return None


def lead_gallery_count(agency_id: str, lead_id: int) -> int:
    """Nombre d'images de galerie. Stat séquentiel (s'arrête au 1er trou) pour
    rester léger quand appelé par lead dans la liste CRM."""
    base = _IMAGE_ROOT / str(agency_id) / f"{lead_id}_gallery"
    n = 0
    while n < MAX_GALLERY_IMAGES:
        p = base / f"{n:03d}.webp"
        try:
            if p.stat().st_size <= 80:
                break
        except OSError:
            break
        n += 1
    return n


def lead_gallery_source_urls(agency_id: str, lead_id: int) -> list[str]:
    ensure_lead_image_schema()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT source_url FROM lead_images
               WHERE agency_id = ? AND lead_id = ? ORDER BY position ASC""",
            (agency_id, lead_id),
        ).fetchall()
    return [r["source_url"] for r in rows if r and r["source_url"]]


def save_custom_lead_image(lead_id: int, agency_id: str, raw: bytes) -> bool:
    ensure_lead_image_schema()
    _, active_path = _paths(agency_id, lead_id)
    if not _write_webp(active_path, raw):
        return False
    with get_connection() as conn:
        _touch_lead_image_meta(conn, lead_id, agency_id, image_custom=1)
        conn.commit()
    return True


def revert_lead_image_to_crawl(lead_id: int, agency_id: str) -> bool:
    ensure_lead_image_schema()
    crawl_path, active_path = _paths(agency_id, lead_id)
    if not crawl_path.is_file():
        with get_connection() as conn:
            row = conn.execute(
                "SELECT listing_image_url FROM leads WHERE id = ? AND agency_id = ?",
                (lead_id, agency_id),
            ).fetchone()
        url = (row["listing_image_url"] or "").strip() if row else ""
        if url:
            return sync_lead_image_from_url(lead_id, agency_id, url, respect_custom=False)
        return False
    active_path.write_bytes(crawl_path.read_bytes())
    with get_connection() as conn:
        _touch_lead_image_meta(conn, lead_id, agency_id, image_custom=0)
        conn.commit()
    return True


def resolve_lead_image_path(agency_id: str, lead_id: int) -> Path | None:
    _, active = _paths(agency_id, lead_id)
    if active.is_file():
        return active
    return None


def delete_lead_images(agency_id: str, lead_id: int) -> None:
    crawl_path, active_path = _paths(agency_id, lead_id)
    for p in (crawl_path, active_path):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _clear_gallery_files(agency_id, lead_id)
    gdir = _agency_dir(agency_id) / f"{lead_id}_gallery"
    try:
        gdir.rmdir()
    except OSError:
        pass
    try:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM lead_images WHERE agency_id = ? AND lead_id = ?",
                (agency_id, lead_id),
            )
            conn.commit()
    except Exception:
        logger.debug("delete lead_images rows %s", lead_id, exc_info=True)


def lead_image_meta_from_row(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    custom = int(row["image_custom"] or 0) if "image_custom" in keys else 0
    lead_id = int(row["id"])
    agency_id = row["agency_id"] if "agency_id" in keys else ""
    has_file = lead_has_display_image(str(agency_id), lead_id) if agency_id else False
    listing_url = (
        (row["listing_image_url"] or "").strip()
        if "listing_image_url" in keys and row["listing_image_url"]
        else ""
    )
    gallery_n = lead_gallery_count(str(agency_id), lead_id) if agency_id else 0
    has = has_file or bool(listing_url) or gallery_n > 0
    updated = row["image_updated_at"] if "image_updated_at" in keys else None
    v = str(updated or lead_id) if has else None
    images = [
        f"/api/leads/{lead_id}/image/{i}?v={v}" for i in range(gallery_n)
    ]
    return {
        "has_image": has,
        "image_custom": bool(custom),
        "listing_image_url": listing_url or None,
        "image_url": f"/api/leads/{lead_id}/image?v={v}" if has else None,
        "image_count": gallery_n,
        "images": images,
    }


_image_jobs_lock = threading.Lock()
_image_jobs_pending: set[tuple[int, str]] = set()


def schedule_lead_image_sync(
    lead_id: int,
    agency_id: str,
    image_url: str,
    *,
    respect_custom: bool = True,
    referer: str | None = None,
    force: bool = False,
) -> None:
    key = (lead_id, agency_id)
    with _image_jobs_lock:
        if not force and key in _image_jobs_pending:
            return
        _image_jobs_pending.add(key)

    def _run() -> None:
        try:
            sync_lead_image_from_url(
                lead_id,
                agency_id,
                image_url,
                respect_custom=respect_custom,
                referer=referer,
            )
        finally:
            with _image_jobs_lock:
                _image_jobs_pending.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"lead-img-{lead_id}").start()


_gallery_jobs_lock = threading.Lock()
_gallery_jobs_pending: set[tuple[int, str]] = set()


def schedule_lead_gallery_sync(
    lead_id: int,
    agency_id: str,
    image_urls: list[str],
    *,
    referer: str | None = None,
    force: bool = False,
) -> None:
    if not image_urls:
        return
    key = (lead_id, agency_id)
    with _gallery_jobs_lock:
        if not force and key in _gallery_jobs_pending:
            return
        _gallery_jobs_pending.add(key)

    def _run() -> None:
        try:
            sync_lead_gallery_from_urls(
                lead_id, agency_id, image_urls, referer=referer
            )
        except Exception:
            logger.exception("gallery sync %s", lead_id)
        finally:
            with _gallery_jobs_lock:
                _gallery_jobs_pending.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"lead-gal-{lead_id}").start()
