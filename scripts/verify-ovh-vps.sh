#!/usr/bin/env bash
# Verification rapide apres install OVH VPS.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/veliora}"
URL="${1:-}"

if [[ -z "$URL" && -f "$APP_DIR/.env" ]]; then
  URL="$(grep -E '^APP_PUBLIC_URL=' "$APP_DIR/.env" | head -1 | cut -d= -f2- | tr -d '\r')"
fi

echo "=== Veliora VPS verify ==="

if systemctl is-active --quiet veliora; then
  echo "[OK] service veliora actif"
else
  echo "[!!] service veliora inactif — systemctl status veliora"
fi

if systemctl is-active --quiet nginx; then
  echo "[OK] nginx actif"
else
  echo "[!!] nginx inactif"
fi

if [[ -n "$URL" ]]; then
  echo "Test $URL/api/health ..."
  if curl -fsS "$URL/api/health" | head -c 200; then
    echo ""
    echo "[OK] API health"
  else
    echo "[!!] API health echoue"
  fi
else
  echo "[--] APP_PUBLIC_URL inconnu — passez l'URL en argument"
fi

echo ""
echo "Logs recents :"
journalctl -u veliora -n 15 --no-pager 2>/dev/null || true
