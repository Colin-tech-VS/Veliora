#!/usr/bin/env python3
"""Phase release Scalingo — initialise / migre la base avant déploiement."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from crawler.storage import init_db  # noqa: E402


def main() -> None:
    init_db()
    print("Veliora release: base initialisée.", flush=True)


if __name__ == "__main__":
    main()
