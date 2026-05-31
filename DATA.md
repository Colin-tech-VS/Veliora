# Persistance des données Veliora

## Où sont stockées les données ?

| Mode | Configuration | Usage |
|------|---------------|--------|
| **Supabase** (en ligne) | `DATABASE_URL` dans `.env` | Production Scalingo, équipe, données partagées |
| **SQLite** (local) | pas de `DATABASE_URL` | Développement sur PC |

| Fichier / service | Contenu |
|-------------------|---------|
| Supabase projet **Veliora** | Prospects, sources, crawls, agences, mandats… |
| `data/propscout.db` | Même schéma en local si pas de `DATABASE_URL` |
| `data/backups/` | Sauvegardes SQLite au démarrage (local uniquement) |

Guide Supabase : **[SUPABASE.md](SUPABASE.md)**

## Rechargement navigateur (F5)

- Le CRM charge les données via l’API (`/api/leads`, etc.) depuis SQLite.
- Seuls le **token de connexion** et le profil utilisateur sont en `localStorage` (pas les prospects).
- Un rechargement de page **ne supprime pas** vos leads.

## Re-crawl et mises à jour

Pour une annonce déjà connue (même URL + même agence) :

1. **Fusion intelligente** : téléphone, email, adresse déjà valides ne sont pas effacés par un crawl incomplet.
2. **Prix / surface** : mis à jour si le portail affiche une nouvelle valeur.
3. **Baisse de prix** : enregistrée dans `previous_price` + historique.
4. **Pipeline CRM** : statut (contacté, RDV, mandat), notes — **conservés**.
5. **Score mandat** : recalculé à chaque mise à jour validée.
6. **DVF** : recalculé en parallèle si le prix ou la surface change.

## Git push / déploiement

- `data/propscout.db` est dans **`.gitignore`** : il ne part **pas** sur GitHub avec le code.
- Un `git push` ne supprime **pas** votre base sur votre PC.
- Sur un **nouveau serveur**, la base est vide tant que vous n’avez pas copié `propscout.db` ou restauré un backup.

### Production

1. Monter un volume persistant (ex. `/var/veliora/data/propscout.db`).
2. Définir `VELIORA_DB_PATH=/var/veliora/data/propscout.db`.
3. Sauvegarder régulièrement `data/backups/` ou le fichier `.db`.

## Vérifier que tout est bien enregistré

```http
GET http://localhost:8000/api/health
```

Regardez `database.path`, `database.exists`, `database.size_bytes`.

## En cas de perte apparente

1. Vérifiez que vous lancez toujours le serveur depuis le même dossier **Web Agency**.
2. Vérifiez `data/propscout.db` (taille > 0).
3. Restaurez depuis `data/backups/propscout_YYYYMMDD_HHMMSS.db` (renommez en `propscout.db` après arrêt du serveur).
