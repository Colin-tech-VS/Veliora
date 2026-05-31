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

## Volume persistant (SQLite)

Sans volume, la base est **effacée** à chaque redéploiement.

```bash
scalingo --app veliora addons-add scalingo-fs-standard
scalingo --app veliora volume-add veliora-data --size 10 --path /var/lib/data
```

La variable `VELIORA_DB_PATH=/var/lib/data/propscout.db` est déjà définie dans `scalingo.json`.

## Variables d’environnement (dashboard ou CLI)

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `APP_PUBLIC_URL` | oui | URL publique, ex. `https://veliora.osc-fr1.scalingo.io` |
| `FLASK_SECRET_KEY` | oui | Chaîne aléatoire longue |
| `STRIPE_SECRET_KEY` | prod | Clé secrète Stripe |
| `STRIPE_WEBHOOK_SECRET` | prod | Webhook → `https://…/api/billing/webhook` |
| `STRIPE_PRICE_ID` | recommandé | Prix 500 €/mois |
| `SMTP_HOST` | recommandé | Envoi e-mails |
| `VELIORA_DB_PATH` | auto | `/var/lib/data/propscout.db` |

Exemple :

```bash
scalingo --app veliora env-set APP_PUBLIC_URL=https://veliora.osc-fr1.scalingo.io
scalingo --app veliora env-set FLASK_SECRET_KEY="$(openssl rand -hex 32)"
```

## Crawler en production

Sur Scalingo, **Playwright / Chrome** n’est pas activé par défaut (`CRAWL_PLAYWRIGHT_ENABLED=false`) : le crawl utilise **curl_cffi** et **requests** (plus léger, adapté au PaaS).

Pour un crawl navigateur complet, utilisez un worker dédié (VPS) ou activez Playwright avec un buildpack `apt` (image lourde, non recommandé sur un seul conteneur web `M`).

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
