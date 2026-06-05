#!/usr/bin/env bash
# Deploiement Veliora sur OVH VPS (Ubuntu 24.04 / 26.04 LTS).
# Usage (en root sur le VPS) :
#   curl -fsSL ... ou git clone puis :
#   sudo bash scripts/deploy-ovh-vps.sh --domain veliora.votredomaine.fr
set -euo pipefail

DOMAIN=""
REPO_URL="https://github.com/Colin-tech-VS/Veliora.git"
APP_DIR="/opt/veliora"
APP_USER="veliora"

usage() {
  echo "Usage: sudo bash $0 --domain veliora.example.com"
  echo "       sudo bash $0 --domain 51.210.12.34   # sans nom de domaine (HTTP)"
  echo "Options: [--repo URL]"
  exit 1
}

is_ip() {
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Option inconnue: $1"; usage ;;
  esac
done

[[ -n "$DOMAIN" ]] || usage
[[ "$(id -u)" -eq 0 ]] || { echo "Lancez en root (sudo)."; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx \
  build-essential libpq-dev curl

if ! id "$APP_USER" &>/dev/null; then
  useradd --system --create-home --home-dir "/home/$APP_USER" --shell /bin/bash "$APP_USER"
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"
else
  echo "Repo deja present dans $APP_DIR (git pull manuel si mise a jour)."
fi

sudo -u "$APP_USER" bash -lc "
  cd '$APP_DIR'
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  playwright install chromium
  playwright install-deps chromium
"

if is_ip "$DOMAIN"; then
  PUBLIC_URL="http://$DOMAIN"
  USE_HTTPS=false
else
  PUBLIC_URL="https://$DOMAIN"
  USE_HTTPS=true
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/scripts/ovh-vps.env.example" "$APP_DIR/.env"
  sed -i "s|APP_PUBLIC_URL=.*|APP_PUBLIC_URL=$PUBLIC_URL|" "$APP_DIR/.env"
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo ""
  echo "IMPORTANT: editez $APP_DIR/.env (DATABASE_URL, CRAWL_PROXIES, Stripe, SMTP...)"
  echo "  nano $APP_DIR/.env"
fi

sudo -u "$APP_USER" bash -lc "
  cd '$APP_DIR'
  source .venv/bin/activate
  python scripts/release.py
"

sed "s/DOMAIN_PLACEHOLDER/$DOMAIN/g" "$APP_DIR/infra/ovh/nginx-veliora.conf" \
  > "/etc/nginx/sites-available/veliora"
ln -sf /etc/nginx/sites-available/veliora /etc/nginx/sites-enabled/veliora
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl reload nginx

cp "$APP_DIR/infra/ovh/veliora.service" /etc/systemd/system/veliora.service
systemctl daemon-reload
systemctl enable veliora

echo ""
echo "=== Veliora OVH VPS ==="
echo "1. Editez les secrets : nano $APP_DIR/.env"
echo "2. Demarrez l'app    : systemctl start veliora"
if [[ "$USE_HTTPS" == true ]]; then
  echo "3. HTTPS             : certbot --nginx -d $DOMAIN"
  echo "4. Stripe webhook    : https://$DOMAIN/api/billing/webhook"
  echo "5. Verifiez          : bash $APP_DIR/scripts/verify-ovh-vps.sh"
else
  echo "3. Pas de domaine    : acces http://$DOMAIN/ et http://$DOMAIN/crm"
  echo "4. Verifiez          : bash $APP_DIR/scripts/verify-ovh-vps.sh"
  echo "5. HTTPS plus tard   : certbot apres achat d'un nom de domaine"
fi
echo ""
echo "Logs : journalctl -u veliora -f"
