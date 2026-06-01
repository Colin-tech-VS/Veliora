# Déploiement Veliora sur Scalingo

## Prérequis

- Compte [Scalingo](https://scalingo.com)
- CLI : `scalingo login`
- Dépôt GitHub **Veliora** (branche `main`)

## Créer l’application

```bash
cd Veliora
scalingo create Veliora --region osc-fr1
# ou lier un repo GitHub depuis le dashboard Scalingo → Veliora → main
```

## Base Supabase (recommandé)

Voir **[SUPABASE.md](SUPABASE.md)** — créez le projet **Veliora**, exécutez `velora_db/postgres_schema.sql`, puis :

```bash
scalingo --app veliora env-set DATABASE_URL="postgresql://postgres.xxx:PASSWORD@....pooler.supabase.com:6543/postgres"
scalingo --app veliora env-set VELIORA_AUTO_SCHEMA=true
```

Sans `DATABASE_URL`, Veliora retombe sur SQLite (`VELIORA_DB_PATH`, éphémère sans volume persistant).

## Variables d’environnement

Les **valeurs par défaut** du crawl et du PaaS sont dans [`scalingo.json`](scalingo.json). Les **secrets** se configurent dans le dashboard ou via `scalingo env-set` (ne pas les commiter).

### Obligatoires (production)

| Variable | Description |
|----------|-------------|
| `APP_PUBLIC_URL` | URL publique, ex. `https://veliora.osc-fr1.scalingo.io` |
| `FLASK_SECRET_KEY` | Chaîne aléatoire longue (sessions) |
| `DATABASE_URL` | URI PostgreSQL Supabase (pooler port 6543) |

```bash
scalingo --app veliora env-set APP_PUBLIC_URL=https://veliora.osc-fr1.scalingo.io
scalingo --app veliora env-set FLASK_SECRET_KEY="$(openssl rand -hex 32)"
```

### Stripe (facturation agences)

| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | Clé secrète `sk_live_…` |
| `STRIPE_PUBLISHABLE_KEY` | Clé publique `pk_live_…` |
| `STRIPE_WEBHOOK_SECRET` | `whsec_…` → endpoint `https://…/api/billing/webhook` |
| `STRIPE_PRICE_ID` | Prix récurrent 500 €/mois |
| `STRIPE_REQUIRE_PAYMENT` | `true` en prod (`scalingo.json`) |
| `STRIPE_TRIAL_DAYS` | Jours d’essai (0 = facturation immédiate) |

### Email (SMTP)

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | Serveur SMTP |
| `SMTP_PORT` | `587` par défaut |
| `SMTP_USER` / `SMTP_PASSWORD` | Identifiants |
| `SMTP_FROM` | Ex. `Veliora <noreply@votredomaine.fr>` |
| `SMTP_USE_TLS` | `true` |

### Crawler (défauts Scalingo dans `scalingo.json`)

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `CRAWL_PLAYWRIGHT_ENABLED` | `false` | Pas de Chrome sur le conteneur web |
| `AUTO_WARMUP_ANTIBOT` | `false` | Pas de warmup automatique lourd |
| `DOMAIN_WARMUP` | `false` | Pas de navigation préalable par domaine |
| `CRAWL_SKIP_CITY_PROBE` | `true` | Pas de test HTTP des URLs ville |
| `CRAWL_HEADED_FALLBACK` | `false` | Pas de fenêtre Chrome visible |
| `CRAWL_HEADFUL` | `false` | Idem |
| `CRAWL_SPEED_PROFILE` | `balanced` | `quality` \| `balanced` \| `fast` \| `turbo` |
| `CITY_CRAWL_MAX_LISTINGS` | `90` | Plafond annonces par ville |
| `CITY_DISCOVERY_STOP_LINKS` | `28` | Arrêt découverte liens |
| `SAVE_ACTIONABLE_LEADS` | `true` | Enregistrer leads exploitables |
| `CRAWL_PROXY_ROTATE_EACH_CRAWL` | `true` | Nouvelle IP en début de job / portail |
| `CRAWL_PROXY_ROTATE_ON_BLOCK` | `true` | Nouvelle IP si anti-bot / Cloudflare |

### Proxies (rotation IP — fortement recommandé en prod)

Pour limiter les blocages (Cloudflare, DataDome), utilisez des **proxies résidentiels rotatifs** (IPRoyal, Bright Data, Oxylabs, Smartproxy, etc.) :

```bash
# Un ou plusieurs proxies (séparés par des virgules)
scalingo --app veliora env-set CRAWL_PROXIES="http://user:pass@gw.fournisseur.com:8000"

# Ou plusieurs :
scalingo --app veliora env-set CRAWL_PROXIES="http://u:p@gw1:8000,http://u:p@gw2:8000"
```

En local, générer / tester :

```bash
python scripts/configure_proxy_rotation.py --file proxies.txt --test --write-env
```

Alias accepté : `CRAWL_PROXY` (un seul).

### Support & légal (optionnel)

| Variable | Description |
|----------|-------------|
| `SUPPORT_EMAIL` | Contact support |
| `DEMO_BOOKING_URL` | Lien Calendly démo |
| `LEGAL_*` / `SITE_URL` | Pages légales |
| `MAX_SOURCES_PER_AGENCY` | Limite portails par agence (`25`) |

## Crawler en production

Sur Scalingo, **Playwright** est désactivé : le crawl passe par **curl_cffi** et **requests**. Avec `CRAWL_PROXIES` + `CRAWL_PROXY_ROTATE_ON_BLOCK`, Veliora change d’IP automatiquement au blocage.

Pour un crawl navigateur complet, prévoyez un worker VPS ou n’activez Playwright que sur une machine dédiée (image lourde).

## Déployer

```bash
git push scalingo main
# ou déploiement auto depuis GitHub (branche main)
```

## Vérification

- `https://<app>.osc-fr1.scalingo.io/api/health` → `api_version: 7`
- Vitrine : `/`
- CRM : `/crm`

## Logs

```bash
scalingo --app veliora logs -f
```
