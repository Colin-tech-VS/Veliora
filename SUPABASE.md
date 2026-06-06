# Veliora sur Supabase

Base **PostgreSQL** partagée (Scalingo, PC, équipe) — projet Supabase **Veliora**.

Veliora utilise **uniquement** `DATABASE_URL` (connexion Postgres directe). Les clés `SUPABASE_ANON_KEY` / API REST ne sont **pas** nécessaires au fonctionnement actuel de l'app.

---

## 1. Créer le projet

1. [supabase.com](https://supabase.com) → **New project** → nom `Veliora`
2. Région : **West EU (Paris)** si possible
3. Mot de passe base : à conserver dans un gestionnaire de mots de passe

---

## 2. Schéma SQL (tout en un clic)

1. **SQL Editor** → New query
2. Coller **tout** le fichier [`scripts/supabase_setup_complet.sql`](scripts/supabase_setup_complet.sql) → **Run**

Ce script unique fait : tables Veliora + IA + RLS + purge `crawl_logs` + rapport de taille.

Alternative (3 fichiers séparés) : `postgres_schema.sql` puis `supabase_enable_rls.sql` puis `supabase_maintenance.sql`.

Sans la RLS, le dashboard affiche **Unrestricted** sur toutes les tables.

**Deadlock `40P01` pendant le script ?** Arrêtez Veliora sur Scalingo 2 min, puis lancez uniquement [`scripts/supabase_rls_seul.sql`](scripts/supabase_rls_seul.sql), puis [`scripts/supabase_maintenance_seul.sql`](scripts/supabase_maintenance_seul.sql). Ne relancez pas tout `supabase_setup_complet.sql` si le schéma est déjà créé.

---

## 3. Connexion Veliora

```env
DATABASE_URL=postgresql://postgres.xxxx:PASSWORD@....pooler.supabase.com:6543/postgres
```

- Mode **Transaction** pooler, port **6543** (pas 5432 direct en prod Scalingo)
- Host **Shared Pooler** : `….pooler.supabase.com` (IPv4 Scalingo) — pas le Dedicated `db….supabase.co` si « Not IPv4 compatible »
- Veliora désactive les prepared statements psycopg (`prepare_threshold=None`) — requis avec ce pooler (sinon erreur `_pg3_0 already exists`)
- **Sans** `DATABASE_URL` → SQLite local `data/propscout.db`

Scalingo :

```bash
scalingo --app veliora env-set DATABASE_URL="postgresql://..."
```

Vérifier : `GET /api/health` → `"database": { "backend": "supabase", ... }`

---

## 4. Tables « Unrestricted » — correctif

| Symptôme | Cause | Action |
|----------|--------|--------|
| Badge **Unrestricted** | RLS désactivée | Run `scripts/supabase_enable_rls.sql` |
| Données exposées via API | Clé `anon` + PostgREST | Même script (RLS + `REVOKE` anon/authenticated) |

**L’app Veliora n’est pas impactée** : elle passe par le pooler avec le rôle propriétaire (`postgres`), qui contourne la RLS.

**Auto-réparation (depuis cette version)** : au démarrage, l’app active RLS + verrouille l’API (`anon`/`authenticated`) sur **toutes** les tables `public`, y compris celles créées plus tard par un module (transactions, portail, IA…). Une table « RLS Disabled in Public » (Critical) qui réapparaît est donc re-sécurisée au prochain déploiement. Pilotable par `VELIORA_AUTO_RLS` (défaut activé). Pour corriger **immédiatement** sans redéployer, exécutez `scripts/supabase_enable_rls.sql` dans le SQL Editor.

**« RLS Enabled No Policy »** (entrées sans badge *Critical*) = état **sain** ici, pas une faille : RLS activée + aucune policy ⇒ la table est fermée à l’API publique et n’est accessible qu’à l’app (rôle propriétaire). N’ajoutez **pas** de policy permissive `authenticated`/`anon` : cela ré-ouvrirait l’API. Seules les entrées **Critical** (RLS Disabled) doivent disparaître — ce que fait le script / l’auto-réparation.

**Ne pas** mettre `SUPABASE_ANON_KEY` dans le front-end Veliora tant que vous n’avez pas de politiques RLS métier par agence.

---

## 5. « Exceeding image usage » / quota dépassé

Deux métriques différentes dans le dashboard :

### A) Storage (fichiers / images dans Supabase Storage)

- Quota **Free** : **1 Go** de fichiers dans les **buckets** Storage
- Veliora **ne stocke pas** les photos d’annonces dans Supabase Storage  
  → fichiers `data/lead_images/*.webp` sur le serveur (Scalingo : disque éphémère)
- Si l’alerte concerne **Storage** :
  1. **Storage** → vérifier chaque bucket → supprimer les fichiers via l’UI (pas via SQL seul)
  2. Si vous avez vidé les buckets mais le quota ne bouge pas : fichiers **orphelins** (suppression SQL) → [support Supabase](https://supabase.com/dashboard/support/new)
  3. Ne créez pas de bucket pour les images Veliora tant que Scalingo/S3 n’est pas configuré

### B) Database size (PostgreSQL — 500 Mo en Free)

- C’est la **taille des tables** (leads, logs, cache JSON…), pas les images WebP
- Diagnostic : [`scripts/supabase_maintenance.sql`](scripts/supabase_maintenance.sql)
- Purge automatique côté app : `crawl_logs` plafonné à **5000** lignes (`CRAWL_LOG_KEEP`)

Tables qui grossissent le plus :

| Table | Pourquoi | Action |
|-------|----------|--------|
| `crawl_logs` | 1 ligne par URL visitée | Purge SQL + variable `CRAWL_LOG_KEEP=3000` |
| `leads` | Portefeuille | Normal ; éviter champs JSON énormes |
| `dvf_commune_cache` | Cache médianes DVF | Utile ; purger si > 50 Mo |
| `lead_price_history` | Historique prix | Purger > 12 mois (SQL maintenance) |
| `facts_audit` / payloads JSON | Détail crawl | Limiter rétention si besoin |

Commande utile :

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```

---

## 6. Quel plan Supabase choisir ?

| Profil | Plan | Prix indicatif | Quand |
|--------|------|----------------|--------|
| Test / 1 agence, < 5k leads | **Free** | 0 € | OK si DB < **400 Mo** et peu de crawls |
| Production 1–3 agences, crawls réguliers | **Pro** | ~25 $/mo | DB > 400 Mo, besoin de non-pause, plus d’egress |
| Plusieurs agences / gros crawls | **Pro** + surveillance | ~25 $ + | `CRAWL_LOG_KEEP=2000`, maintenance mensuelle |
| Équipe / SLA | **Team** | ~599 $/mo | Rarement nécessaire au stade Veliora |

**Free — limites à surveiller**

- **500 Mo** base Postgres → pause projet si dépassé longtemps
- **5 Go** egress / mois (API + pooler)
- **1 Go** Storage (si vous utilisez des buckets)
- Inactivité → projet mis en pause

**Pro — intérêt principal**

- **8 Go** base
- **100 Go** Storage
- **250 Go** egress
- Pas de pause pour inactivité
- Sauvegardes / support

**Recommandation Veliora aujourd’hui**

1. Rester en **Free** si `pg_database_size` < **350 Mo** après purge `crawl_logs`
2. Passer en **Pro** si :
   - alerte **Database size** récurrente
   - plus de **2–3 agences** avec crawls quotidiens
   - besoin de dispo 24/7 sans pause Supabase
3. **Ne pas** utiliser Supabase Storage pour les images → garder proxy Veliora `/api/leads/.../image` (évite le quota Storage)

Variables Scalingo utiles :

```bash
scalingo --app veliora env-set CRAWL_LOG_KEEP=3000
```

---

## 7. Maintenance (mensuelle)

1. SQL Editor → [`scripts/supabase_maintenance.sql`](scripts/supabase_maintenance.sql)
2. Noter les 3 plus grosses tables
3. Si `crawl_logs` > 100 Mo → `DELETE FROM crawl_logs;` ou réduire `CRAWL_LOG_KEEP`
4. **Settings → Database** → vérifier Usage

L’app exécute aussi `prune_crawl_logs()` à chaque démarrage worker (Postgres).

---

## 8. Migration SQLite → Supabase

```bash
pip install "psycopg[binary]"
python scripts/migrate_sqlite_to_supabase.py --dry-run
python scripts/migrate_sqlite_to_supabase.py
```

---

## 9. Dépannage

| Problème | Action |
|----------|--------|
| Tables Unrestricted | `scripts/supabase_enable_rls.sql` |
| Quota Storage / « image » | Vider buckets Storage ; pas SQL seul |
| DB > 500 Mo | `supabase_maintenance.sql` + Pro ou purge |
| `connection refused` | URI pooler 6543, mot de passe |
| « base de données indisponible » à la connexion / par intermittence | Connexions zombies servies par le pool (PgBouncer coupe l'inactif). Corrigé : `check` valide chaque connexion avant de la prêter + recyclage (`max_lifetime`/`max_idle`) + keepalives TCP. Réglages : voir `.env.example` (`DATABASE_POOL_CHECK`, `DATABASE_POOL_MAX_LIFETIME`, `DATABASE_KEEPALIVES_*`). |
| `DuplicatePreparedStatement` / `_pg3_0` | Pooler 6543 + code récent (`prepare_threshold=None` dans `velora_db/connection.py`) |
| `relation does not exist` | `postgres_schema.sql` |
| Retour SQLite | Retirer `DATABASE_URL` |

---

## Sécurité

- Ne jamais committer `.env` ni `DATABASE_URL`
- Rotation mot de passe : Settings → Database
- Ne pas exposer `SUPABASE_SERVICE_ROLE_KEY` côté navigateur
