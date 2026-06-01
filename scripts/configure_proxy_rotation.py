#!/usr/bin/env python3
"""
Configure la rotation de proxies pour le crawler Veliora.

Usage :
  python scripts/configure_proxy_rotation.py --file proxies.txt --test --write-env
  python scripts/configure_proxy_rotation.py --proxy "http://user:pass@host:8000" --write-env

Fichier proxies.txt : une URL par ligne (http://user:pass@host:port).
Les proxies résidentiels rotatifs (Bright Data, Oxylabs, IPRoyal, etc.) sont recommandés.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROXY_RE = re.compile(r"^https?://", re.I)


def load_proxy_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not PROXY_RE.match(line):
            raise ValueError(f"Proxy invalide (http:// requis) : {line[:80]}")
        lines.append(line)
    if not lines:
        raise ValueError(f"Aucun proxy dans {path}")
    return lines


def test_proxy(url: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        import requests

        resp = requests.get(
            "https://api.ipify.org?format=json",
            proxies={"http": url, "https": url},
            timeout=timeout,
        )
        if resp.ok:
            return True, resp.text.strip()[:120]
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:120]


def write_env_proxies(proxies: list[str], env_path: Path) -> None:
    value = ",".join(proxies)
    lines: list[str] = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    replaced_proxies = False
    replaced_single = False
    for line in lines:
        if line.startswith("CRAWL_PROXIES="):
            out.append(f"CRAWL_PROXIES={value}")
            replaced_proxies = True
            continue
        if line.startswith("CRAWL_PROXY=") and not replaced_single:
            out.append(f"# {line}  # remplacé par CRAWL_PROXIES")
            replaced_single = True
            continue
        out.append(line)
    if not replaced_proxies:
        if out and out[-1].strip():
            out.append("")
        out.append("# Proxies crawl — rotation automatique (scripts/configure_proxy_rotation.py)")
        out.append(f"CRAWL_PROXIES={value}")
        out.append("CRAWL_PROXY_ROTATE_EACH_CRAWL=true")
        out.append("CRAWL_SKIP_CITY_PROBE=true")
        out.append("CRAWL_HEADED_FALLBACK=false")
        out.append("DOMAIN_WARMUP=false")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Configuration proxies crawl Veliora")
    parser.add_argument("--file", type=Path, help="Fichier texte (un proxy par ligne)")
    parser.add_argument("--proxy", action="append", default=[], help="Proxy http://… (répétable)")
    parser.add_argument("--test", action="store_true", help="Tester chaque proxy (ipify)")
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Écrire CRAWL_PROXIES dans .env à la racine du projet",
    )
    args = parser.parse_args()

    proxies: list[str] = []
    if args.file:
        proxies.extend(load_proxy_lines(args.file))
    proxies.extend(p.strip() for p in args.proxy if p.strip())
    if not proxies:
        parser.error("Indiquez --file proxies.txt et/ou --proxy http://…")

    print(f"{len(proxies)} proxy(s) chargé(s).")
    ok_list: list[str] = []
    for i, px in enumerate(proxies, 1):
        host = px.split("@")[-1] if "@" in px else px
        if args.test:
            ok, detail = test_proxy(px)
            status = "OK" if ok else "ÉCHEC"
            print(f"  [{i}] {status} {host} — {detail}")
            if ok:
                ok_list.append(px)
        else:
            print(f"  [{i}] {host}")
            ok_list.append(px)

    if args.test and not ok_list:
        print("Aucun proxy fonctionnel — corrigez la liste avant --write-env.", file=sys.stderr)
        return 1

    target = ok_list if args.test else proxies
    if args.write_env:
        env_path = ROOT / ".env"
        write_env_proxies(target, env_path)
        print(f"\n.env mis à jour ({len(target)} proxy(s)) : {env_path}")
        print("Relancez python app.py ou demarrer.bat pour appliquer.")
    else:
        print("\nCRAWL_PROXIES (copiez dans .env) :")
        print("CRAWL_PROXIES=" + ",".join(target))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
