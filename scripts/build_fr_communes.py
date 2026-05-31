#!/usr/bin/env python3
"""Regénère data/fr_communes.json depuis geo.api.gouv.fr (toutes les communes)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "fr_communes.json"


def main() -> None:
    depts: list[str] = [f"{i:02d}" for i in range(1, 96)]
    depts.extend(["2A", "2B"])
    depts.extend(str(i) for i in range(971, 976))

    rows: list[dict] = []
    for d in depts:
        url = (
            "https://geo.api.gouv.fr/communes"
            f"?codeDepartement={d}&fields=nom,code,codeDepartement,codesPostaux&format=json"
        )
        with urllib.request.urlopen(url, timeout=60) as resp:
            part = json.loads(resp.read().decode())
        rows.extend(part)
        print(f"{d}: {len(part)} (total {len(rows)})")

    compact = [
        {
            "n": c["nom"],
            "c": c["code"],
            "d": c["codeDepartement"],
            "p": (c.get("codesPostaux") or [""])[0],
        }
        for c in rows
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"Écrit {len(compact)} communes → {OUT} ({OUT.stat().st_size} octets)")


if __name__ == "__main__":
    main()
