# Veliora sur OVH VPS (site + veille tout-en-un)

Architecture : **CRM + crawl Playwright + Decodo** sur un seul serveur. Supabase reste la base de donnees.

## Quelle offre OVH ?

| Offre | Usage |
|-------|-------|
| VPS-1 (8 Go) | 1 agence |
| **VPS-2 (12 Go)** | **Recommande** — confortable, marge pour plusieurs agences |
| VPS-3 (24 Go) | Reseau / IA locale lourde |

A la commande : **Ubuntu 26.04 LTS** (propose par OVH VPS 2026) ou **24.04 LTS**, region **France** (Gravelines ou Roubaix).

## Avant de commencer

1. Domaine pointe vers l'IP du VPS (enregistrement **A**).
2. Recuperez les secrets Scalingo :
   ```bash
   scalingo --app veliora env > scalingo-backup.env
   ```
3. Decodo : user/pass + `fr.decodo.com:40000`.

## Installation automatique (15 min)

Connectez-vous en SSH au VPS :

```bash
ssh root@IP_DU_VPS

git clone https://github.com/Colin-tech-VS/Veliora.git
cd Veliora
sudo bash scripts/deploy-ovh-vps.sh --domain veliora.votredomaine.fr
```

Puis configurez les secrets :

```bash
nano /opt/veliora/.env
# DATABASE_URL, CRAWL_PROXIES, FLASK_SECRET_KEY, Stripe, SMTP...
```

Demarrez et activez HTTPS :

```bash
systemctl start veliora
certbot --nginx -d veliora.votredomaine.fr
systemctl restart veliora
```

## Verification

```bash
curl -s https://veliora.votredomaine.fr/api/health
journalctl -u veliora -f
```

Logs crawl : messages `crawler.engine`, `crawler.proxy_manager`.

## Variables cles (.env)

| Variable | Valeur VPS |
|----------|------------|
| `APP_PUBLIC_URL` | `https://votredomaine.fr` |
| `CRAWL_AUTO_START` | `true` |
| `CRAWL_PLAYWRIGHT_ENABLED` | `true` |
| `CRAWL_PROXIES` | Decodo `fr.decodo.com:40000` |
| `CRAWL_SKIP_STREAMESTATE` | `true` |

Modele complet : [`scripts/ovh-vps.env.example`](scripts/ovh-vps.env.example).

## Migration depuis Scalingo

1. Deployez le VPS (ci-dessus).
2. Testez CRM + crawl 24 h.
3. Basculez le domaine public vers le VPS.
4. Mettez a jour le webhook Stripe vers `https://nouveau-domaine/api/billing/webhook`.
5. Arretez l'app Scalingo (economie ~15 EUR/mois).

Le worker PC (`run-crawl-worker.ps1`) n'est **plus necessaire** sur le VPS.

## Mise a jour du code

```bash
cd /opt/veliora
sudo -u veliora git pull
sudo -u veliora bash -lc 'source .venv/bin/activate && pip install -r requirements.txt'
sudo systemctl restart veliora
```

## Depannage

| Probleme | Action |
|----------|--------|
| Playwright echoue | `sudo -u veliora bash -lc 'cd /opt/veliora && source .venv/bin/activate && playwright install-deps chromium'` |
| 502 Bad Gateway | `systemctl status veliora` — verifier `.env` et `DATABASE_URL` |
| Crawl 0 annonce LBC | Verifier `CRAWL_PROXIES` et quota Decodo |
| RAM saturee | `htop` — passer VPS-2 ou reduire `CITY_CRAWL_MAX_LISTINGS` |
