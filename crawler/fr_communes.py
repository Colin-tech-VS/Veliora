"""Référentiel des communes françaises (geo.api.gouv.fr → data/fr_communes.json)."""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "fr_communes.json"
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DEPT_TAIL_RE = re.compile(r"-((?:\d{2,3}|2[ab]))$", re.I)


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").lower().strip()).strip("-")


def _fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()


@lru_cache(maxsize=1)
def _indexes() -> tuple[list[dict], dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = []
    by_slug: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    by_postcode: dict[str, list[dict]] = {}

    for item in raw:
        nom = (item.get("n") or "").strip()
        if not nom:
            continue
        dept = (item.get("d") or "").strip()
        row = {
            "name": nom,
            "code": item.get("c") or "",
            "dept": dept,
            "postcode": (item.get("p") or "").strip(),
            "slug": slugify(nom),
            "path_slug": f"{slugify(nom)}-{dept.lower()}",
        }
        rows.append(row)
        by_slug.setdefault(row["slug"], []).append(row)
        by_name.setdefault(nom.lower(), []).append(row)
        if row["postcode"]:
            by_postcode.setdefault(row["postcode"], []).append(row)

    return rows, by_slug, by_name, by_postcode


def all_communes() -> list[dict]:
    return _indexes()[0]


def city_for_postcode(postcode: str | None) -> str | None:
    """Nom de commune le plus probable pour un code postal (ex. 56100 → Lorient)."""
    if not postcode:
        return None
    _, _, _, by_postcode = _indexes()
    best = _pick_best(by_postcode.get(postcode.strip(), []), postcode)
    return best["name"] if best else None


def _pick_best(candidates: list[dict], postcode: str | None = None) -> dict | None:
    if not candidates:
        return None
    if postcode:
        pc = postcode.strip()
        for c in candidates:
            if c["postcode"] == pc:
                return c
        for c in candidates:
            if c["postcode"].startswith(pc[:2]):
                return c
    if len(candidates) == 1:
        return candidates[0]
    return sorted(candidates, key=lambda x: (len(x["name"]), x["code"]))[0]


def resolve_commune(city: str, postcode: str | None = None) -> dict | None:
    """Résout une commune par nom (et optionnellement code postal)."""
    city = (city or "").strip()
    if not city:
        return None
    _, by_slug, by_name, by_postcode = _indexes()

    if postcode:
        hit = _pick_best(by_postcode.get(postcode.strip(), []), postcode)
        if hit and _fold(hit["name"]) == _fold(city):
            return hit

    by_exact = by_name.get(city.lower())
    if by_exact:
        return _pick_best(by_exact, postcode)

    sl = slugify(city)
    if sl in by_slug:
        return _pick_best(by_slug[sl], postcode)

    folded = _fold(city)
    for name_key, group in by_name.items():
        if _fold(name_key) == folded:
            return _pick_best(group, postcode)

    return None


def path_slug_for_city(city: str, postcode: str | None = None) -> str:
    """Slug URL type SeLoger : lorient-56, ajaccio-2a, basse-terre-971."""
    row = resolve_commune(city, postcode)
    if row:
        return row["path_slug"]
    sl = slugify(city)
    if postcode and len(postcode) >= 2:
        dept = postcode[:3] if postcode.startswith(("97", "98")) else postcode[:2]
        return f"{sl}-{dept.lower()}" if sl else sl
    return sl


def department_code_from_path_slug(path_slug: str) -> str | None:
    m = _DEPT_TAIL_RE.search(path_slug or "")
    if not m:
        return None
    return m.group(1).upper()


def search_communes(query: str, *, limit: int = 25, postcode: str | None = None) -> list[dict]:
    """Recherche autocomplete (préfixe sur le nom, accents ignorés)."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    limit = max(1, min(50, limit))
    folded_q = _fold(q)
    rows = all_communes()
    prefix_hits: list[dict] = []
    contains_hits: list[dict] = []

    for row in rows:
        if postcode and row["postcode"] != postcode.strip():
            continue
        fn = _fold(row["name"])
        if fn.startswith(folded_q):
            prefix_hits.append(row)
        elif folded_q in fn and len(contains_hits) < limit * 2:
            contains_hits.append(row)
        if len(prefix_hits) >= limit:
            break

    out = prefix_hits[:limit]
    if len(out) < limit:
        seen = {r["code"] for r in out}
        for row in contains_hits:
            if row["code"] in seen:
                continue
            out.append(row)
            seen.add(row["code"])
            if len(out) >= limit:
                break
    return [
        {
            "name": r["name"],
            "dept": r["dept"],
            "postcode": r["postcode"],
            "code": r["code"],
            "path_slug": r["path_slug"],
            "label": f"{r['name']} ({r['dept']})",
        }
        for r in out
    ]
