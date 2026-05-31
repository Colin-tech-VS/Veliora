#!/usr/bin/env python3
"""Point d'entrée — lance Veliora via Flask (API + POST /api/sources)."""

from app import main

if __name__ == "__main__":
    print("Veliora — utilisez ce script (Flask), pas python -m http.server", flush=True)
    main()
