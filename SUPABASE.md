# Veliora sur Supabase (gratuit)

Base **en ligne** partagée entre Scalingo, votre PC et toute l’équipe — projet Supabase nommé **Veliora**.

## 1. Créer le projet Supabase

1. [https://supabase.com](https://supabase.com) → **New project**
2. **Name** : `Veliora`
3. **Database password** : notez-la (mot de passe fort)
4. Région : `West EU (Paris)` ou proche de vos utilisateurs
5. Plan **Free** (500 Mo DB, suffisant pour démarrer)

## 2. Appliquer le schéma SQL

1. Dashboard → **SQL Editor** → **New query**
2. Collez tout le fichier [`velora_db/postgres_schema.sql`](velora_db/postgres_schema.sql)
3. **Run** — toutes les tables Veliora sont créées

## 3. Récupérer l’URL de connexion

1. **Project Settings** → **Database**
2. **Connection string** → **URI** (mode **Transaction** pooler, port `6543`)
3. Remplacez `[YOUR-PASSWORD]` par le mot de passe du projet

Exemple :

```text
postgresql://postgres.xxxxxxxxxxxx:VOTRE_MDP@aws-0-eu-west-3.pooler.supabase.com:6543/postgres
```

## 4. Configurer Veliora

Dans `.env` (local) ou variables Scalingo :

```env
# Supabase — active PostgreSQL (sinon SQLite local data/propscout.db)
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@....pooler.supabase.com:6543/postgres

# Optionnel (API REST Supabase — futur)
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbG...
```

**Sans `DATABASE_URL`** : Veliora utilise **SQLite** (`data/propscout.db`) comme avant.

Optionnel : `VELIORA_AUTO_SCHEMA=true` pour ré-appliquer `postgres_schema.sql` au démarrage (sinon, exécutez le SQL une fois dans le dashboard Supabase, étape 2).

Redémarrez le serveur :

```bash
python app.py
```

Vérifiez :

```http
GET /api/health
```

→ `"database": { "backend": "supabase", ... }`

## 5. Migrer les données locales (optionnel)

Si vous avez déjà des prospects dans `data/propscout.db` :

```bash
pip install psycopg[binary]
python scripts/migrate_sqlite_to_supabase.py --dry-run
python scripts/migrate_sqlite_to_supabase.py
```

## 6. Scalingo

Dans `scalingo.json` ou le dashboard :

```bash
scalingo --app veliora env-set DATABASE_URL="postgresql://..."
```

Vous pouvez **retirer** le volume disque SQLite (`VELIORA_DB_PATH`) : tout est sur Supabase.

## Sécurité

- Ne commitez **jamais** `.env` ni le mot de passe DB
- Supabase : **Settings** → **Database** → rotate password si fuite

### Tables « Unrestricted » → activer la RLS

Dans le dashboard, les tables sans **Row Level Security** sont marquées
**« Unrestricted »**. Tant que la RLS est désactivée, toute personne disposant
de l'URL du projet + la clé `anon` peut lire/écrire **toutes** les données via
l'API REST auto-générée (PostgREST). L'app Veliora, elle, se connecte en direct
au Postgres avec le rôle privilégié du pooler (qui **contourne** la RLS) — donc
activer la RLS **ne casse pas l'app** et ferme l'accès anonyme.

**Correctif (1 min)** : Dashboard → **SQL Editor** → coller
[`scripts/supabase_enable_rls.sql`](scripts/supabase_enable_rls.sql) → **Run**.
Les tables ne sont alors plus « Unrestricted », et l'app continue de fonctionner.

## Usage / quota dépassé (« exceeding usage »)

Le plan Free Supabase plafonne surtout : **taille de base 500 Mo**, **egress
~5 Go/mois**, et la base est mise en pause après inactivité prolongée.

Où regarder : **Dashboard → Reports / Usage** (voir quelle métrique dépasse :
*Database size*, *Egress*, *Compute*).

Causes probables côté Veliora et leviers :

| Cause | Levier |
|-------|--------|
| Table `crawl_logs` qui gonfle (1 ligne par URL crawlée) | Purge automatique désormais en place (garde 5000 lignes, `CRAWL_LOG_KEEP`). Pour repartir propre : `DELETE FROM crawl_logs;` dans SQL Editor. |
| `lead_price_history`, `lead_outcomes`, `geocode_cache` volumineux | `geocode_cache` est réutilisable (à garder) ; purger l'historique ancien si besoin. |
| Egress : polling du crawl + listes rechargées | Le polling n'est actif que pendant un crawl ; éviter de laisser un crawl tourner en boucle. |
| Pool de connexions saturé / « base saturée » au démarrage | Corrigé : le pool est réinitialisé dans chaque worker après le fork gunicorn (`--preload` ouvrait le pool dans le maître, ses threads ne survivaient pas au fork). Utiliser le pooler **Transaction** port `6543`. |

> Les **images d'annonces ne sont PAS dans Supabase** (stockées en fichiers
> `data/lead_images/*.webp`) — elles ne comptent pas dans le quota DB. Note :
> sur Scalingo le disque est éphémère, ces images sont reperdues à chaque
> redéploiement (sujet distinct du quota Supabase).

> Astuce taille : `SELECT pg_size_pretty(pg_total_relation_size('crawl_logs'));`
> pour voir le poids d'une table dans le SQL Editor.

## Dépannage

| Problème | Action |
|----------|--------|
| `connection refused` | Vérifiez l’URI pooler (port 6543) et le mot de passe |
| `relation does not exist` | Relancez `postgres_schema.sql` dans SQL Editor |
| Données vides en prod | Lancez `migrate_sqlite_to_supabase.py` |
| Retour SQLite local | Supprimez `DATABASE_URL` du `.env` |
