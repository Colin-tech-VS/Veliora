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

### Crawler — profil « gratuit + rapide + données sûres » (`scalingo.json`)

Ce profil est calibré pour **Scalingo sans coût supplémentaire** (pas de proxies payants, pas de Chrome, pas de crédits StreamEstate en veille). La qualité des fiches repose sur les filtres Veliora (cohérence prix/surface, commune INSEE, DVF, rapprochement adresse) — **indépendants** du profil de vitesse.

**Appliquer en une fois** (hors secrets) :

```bash
grep -v '^\s*#' scripts/scalingo-env-apply.env | xargs -I{} scalingo --app veliora env-set {}
```

#### Ce qui crawl bien en gratuit

| Source | Statut |
|--------|--------|
| ParuVendu, Ouest-France, LeSiteImmo, Superimmo… | OK (HTTP) |
| Réseaux agences + catalogue (`CRAWL_INCLUDE_CATALOG_IN_AUTO`) | OK |
| Sites perso / Netty / WordPress (`CRAWL_INCLUDE_CUSTOM_IN_AUTO`) | OK |
| LeBonCoin, PAP, SeLoger, Bien’ici | **Désactivés** (`CRAWL_ANTIBOT_PORTALS_ENABLED=false`) — DataDome exige navigateur + proxies résidentiels |

Activez dans le CRM les portails **sans badge « Navigateur requis »** pour votre ville.

#### Qualité des données (strict)

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `SAVE_ACTIONABLE_LEADS` | `true` | Tél. ou email + adresse + prix/surface cohérents |
| `SAVE_MINIMAL_LEADS` | `false` | Pas de fiches partielles / bruit |
| `SAVE_CRAWL_SNAPSHOT` | `false` | Pas de sauvegarde « URL seule » |
| `ADDRESS_MATCH_DURING_CRAWL` | `true` | BAN / DPE / cadastre en parallèle |
| `DVF_PARALLEL_WORKERS` | `1` | Comparatif marché (adapté dyno M) |
| `SURFACE_MIN_SALE_M2` / `SURFACE_MAX_SALE_M2` | `5` / `500` | Rejette parkings / terrains confondus |

#### Vitesse

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `CRAWL_SPEED_PROFILE` | `fast` | Délais courts ; validation inchangée |
| `MAX_LISTING_LINKS` | `150` | Liens découverte par page |
| `MAX_SEARCH_PAGES` | `14` | Pagination résultats |
| `CRAWL_HTTP_TIMEOUT_SEC` | `12` | Timeout HTTP |
| `ANTIBOT_CHALLENGE_WAIT_MS` | `10000` | Abandon rapide si page bloquée |
| `CRAWL_VEILLE_SOURCE_MAX_SEC` | `480` | Max 8 min / portail en veille |

#### Infra crawl (PaaS)

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `CRAWL_PLAYWRIGHT_ENABLED` | `false` | Pas de Chrome sur le conteneur web |
| `CRAWL_ANTIBOT_PORTALS_ENABLED` | `false` | LBC/PAP/SeLoger exclus sans navigateur |
| `AUTO_WARMUP_ANTIBOT` | `false` | Pas de warmup automatique lourd |
| `DOMAIN_WARMUP` | `false` | Pas de navigation préalable par domaine |
| `CRAWL_SKIP_CITY_PROBE` | `true` | Pas de test HTTP des URLs ville |
| `CRAWL_HEADED_FALLBACK` | `false` | Pas de fenêtre Chrome visible |
| `CRAWL_HEADFUL` | `false` | Idem |
| `CITY_CRAWL_MAX_LISTINGS` | `90` | Plafond annonces par ville |
| `CITY_DISCOVERY_STOP_LINKS` | `28` | Arrêt découverte liens |
| `CRAWL_PROXY_ROTATE_EACH_CRAWL` | `true` | Nouvelle IP en début de job / portail |
| `CRAWL_PROXY_ROTATE_ON_BLOCK` | `true` | Nouvelle IP si rate-limit |
| `CRAWL_AUTO_FREE_PROXIES` | `true` | Pool public gratuit (rate-limit uniquement) |
| `CRAWL_INCLUDE_CATALOG_IN_AUTO` | `true` | ORPI, Nestenn, Acheter-Louer… en veille |
| `CRAWL_INCLUDE_CUSTOM_IN_AUTO` | `true` | URLs perso en veille |
| `SITE_WIDE_CRAWL_ENABLED` | `true` | Exploration BFS sites agences |
| `CRAWL_AI_DISCOVERY` | `auto` | IA liens si `AI_API_KEY` (Groq gratuit) configurée |
| `MAX_SOURCES_PER_AGENCY` | `30` | Limite portails par agence |

#### Veille automatique

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `CRAWL_AUTO_START` | `true` | Veille au boot Gunicorn |
| `CRAWL_BACKGROUND_INTERVAL_SEC` | `300` | Tous portails toutes les 5 min |
| `CRAWL_LEAD_REFRESH_ENABLED` | `true` | Recrawl fiches prospects |
| `CRAWL_LEAD_REFRESH_INTERVAL_SEC` | `3600` | Cycle recrawl toutes les heures |
| `CRAWL_LEAD_REFRESH_STALE_HOURS` | `24` | Fiches > 24 h candidates |
| `CRAWL_LEAD_REFRESH_MAX_PER_RUN` | `20` | Max recrawls / cycle |
| `CRAWL_VEILLE_DISCOVERY_MAX_LISTINGS` | `90` | Nouvelles URLs / portail / veille |

#### StreamEstate (option payante — désactivé en veille)

| Variable | Défaut Scalingo | Rôle |
|----------|-----------------|------|
| `STREAMESTATE_INCLUDE_IN_VEILLE` | `false` | Pas de crédits consommés en boucle |
| `STREAMESTATE_PARTICULIER_ONLY` | `true` | Particuliers seulement |
| `STREAMESTATE_WITH_COHERENT_PRICE` | `true` | Prix cohérents |
| `STREAMESTATE_MAX_LISTINGS` | `60` | Plafond crawl manuel |

Clé API (secret, hors repo) : `scalingo --app veliora env-set STREAMESTATE_API_KEY=...`

### Proxies (rotation IP — fortement recommandé en prod)

**Sans configuration**, Veliora active déjà une rotation d'IP gratuite : avec `CRAWL_AUTO_FREE_PROXIES=true` (défaut Scalingo), dès qu'un portail bloque, l'app récupère et teste un pool de proxies HTTP publics puis change d'IP automatiquement. C'est suffisant contre les blocages par rate-limit d'IP, **mais pas** contre DataDome / Cloudflare avancés.

Pour une fiabilité maximale (Cloudflare, DataDome), utilisez des **proxies résidentiels rotatifs** (IPRoyal, Bright Data, Oxylabs, Smartproxy, etc.) — ils prennent automatiquement le dessus sur le pool gratuit :

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

Sur Scalingo, **Playwright** est désactivé : le crawl passe par **curl_cffi** et **requests**, avec rotation IP gratuite (`CRAWL_AUTO_FREE_PROXIES`). Les portails DataDome (LBC, PAP, SeLoger) sont **volontairement exclus** — les activer sans navigateur + proxies résidentiels produit « 0 annonce ».

**Checklist agence** (CRM) :

1. Ville / territoire renseigné → filtre INSEE actif
2. Portails ON : ParuVendu, Ouest-France, réseaux, catalogue (pas « Navigateur requis »)
3. Sites concurrents locaux ajoutés en URL perso

Pour LBC/PAP/SeLoger : worker VPS avec `CRAWL_PLAYWRIGHT_ENABLED=true` + proxies résidentiels, ou clé `STREAMESTATE_API_KEY` en crawl manuel uniquement.

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
