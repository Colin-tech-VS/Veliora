# Rapprochement d'adresse standardisé — architecture Veliora

> Pipeline post-scraping, **uniforme à tous les crawlers** (actuels et futurs),
> qui estime l'adresse la plus probable d'une annonce en croisant des données
> publiques légales, avec un score de confiance et des justifications.
> **Le système n'invente jamais d'adresse** : il ne renvoie que des candidats
> issus de sources publiques, classés par score.

## 1. Audit de l'architecture existante

| Élément | Rôle | Réutilisé par le matching |
|---|---|---|
| `CrawlerEngine._process_listing` | Boucle de traitement par annonce (toutes sources) | Point d'injection du submit |
| `BaseAdapter.parse_listing` | **Chokepoint universel** — tous les adaptateurs y passent | Extraction des features |
| `listing_facts.verify_and_apply_listing_facts` | Consensus multi-sources prix/surface/titre/date | Source des champs fiables |
| `crm/dvf.py` | Géocodage BAN + DVF + résolution commune INSEE | Résolution commune + marché €/m² |
| `DvfParallelQueue` | Post-processing réseau **parallèle** pendant le crawl | Modèle de la file d'adresses |
| `lead_estimates` (table dédiée) | Persistance hors `leads` (anti-verrou) | Modèle des tables dédiées |

**Constat :** l'infrastructure « post-processing standardisé indépendant de la
source » existe déjà (file DVF). Le matching d'adresse s'y greffe sans toucher
la logique propre à chaque portail → parité garantie entre toutes les sources.

## 2. Refonte du pipeline (unifiée)

```
parse_listing (BaseAdapter)            ← TOUS les adaptateurs en héritent
  └─ apply_features_to_lead()          ← extraction features (HTML, sans réseau)
        → lead.raw_extras["listing_features"]

_process_listing → save_lead → submit  ← post-processing parallèle, par source-agnostique
  ├─ DvfParallelQueue.submit_lead()
  └─ AddressMatchQueue.submit_lead()    ← NOUVEAU
        └─ resolve_and_store_lead_address()
              1. features (table lead_features)
              2. _resolve_commune()      → INSEE/CP/ville (BAN + geo.api.gouv.fr)
              3. sources publiques       → candidats (DPE, BAN)
              4. scoring pondéré         → classement
              5. cadastre (enrich top)   → parcelle
              6. save_address_match()    → table lead_address_matches
```

Aucun crawler n'est traité différemment : le comportement est porté par la
**classe de base** + la **file parallèle**, donc tout nouveau crawler en hérite
automatiquement sans code spécifique.

## 3. Données extraites (module `features.py`)

Texte/localisation, caractéristiques physiques (surface, terrain, pièces,
chambres, étage, nb étages, ascenseur, parking, cave, balcon, terrasse, piscine,
année, exposition), DPE (classe énergie/climat, conso, CO₂), commercial (prix,
prix/m², date, agence, référence), métadonnées image (hook `image_meta`).

> **Données manquantes avant cette évolution :** toutes les caractéristiques
> fines (pièces, DPE, étage, équipements, année…) n'étaient pas collectées. Elles
> le sont désormais pour 100 % des sources via le chokepoint `parse_listing`.

## 4. Sources croisées (`sources.py`) — toutes en open data légal

| Source | API | Apport |
|---|---|---|
| **DPE ADEME** | `data.ademe.fr` (datasets DPE v2 existants/neufs) | Adresses réelles + surface + classe DPE/GES + année + type → source la plus discriminante |
| **BAN** | `api-adresse.data.gouv.fr` | Géocodage + candidats niveau rue/numéro |
| **DVF** | Etalab geo-DVF (via `crm/dvf.py`) | Médiane €/m² locale (cohérence prix) |
| **Cadastre** | `apicarto.ign.fr/api/cadastre` | Parcelle du meilleur candidat |

Architecture ouverte : ajouter une source = écrire une fonction renvoyant des
`AddressCandidate` et l'appeler dans `resolver.resolve_address`.

## 5. Tables de stockage

```sql
lead_features          (lead_id, agency_id, payload JSON, updated_at)   -- caractéristiques
lead_address_matches   (lead_id, agency_id, probable_address, confidence, payload JSON, updated_at)
```

Tables **dédiées** (pas d'`ALTER` sur `leads` → pas de verrou ACCESS EXCLUSIVE en
conflit avec les UPDATE du crawl). Index `(agency_id, confidence DESC)` pour le
tri. JSON pour le détail (candidats + raisons), colonnes dénormalisées pour
filtre/tri rapides.

## 6. Algorithme de matching (`scoring.py`)

Somme pondérée normalisée sur les critères **réellement évaluables** (pas de
pénalité pour une donnée absente de l'annonce) :

| Critère | Poids | Critère | Poids |
|---|---|---|---|
| Surface | 22 | Type de bien | 8 |
| DPE énergie | 14 | Période construction | 8 |
| Cohérence géo (GPS) | 14 | Quartier | 8 |
| Ville | 12 | DPE climat | 6 |
| Pièces | 8 | Prix/m² marché | 6 |
| Similarité photo | 6 | Équipements | 4 |

`score = ratio_critères × 100 × (0.55 + 0.45 × couverture)` → un candidat évalué
sur peu de critères voit sa confiance atténuée. Résultat **0–100**.

## 7. Sortie (format exact)

```json
{
  "adresse_probable": "12 Rue de la Paix, 56100 Lorient",
  "score_confiance": 92,
  "candidats": [
    {"adresse": "...", "score": 92, "raisons": ["Même classe énergie (D)", "..."]},
    {"adresse": "...", "score": 84, "raisons": ["..."]}
  ]
}
```

Si aucun candidat ne dépasse `PROBABLE_MIN_SCORE` (55) :
`adresse_probable = null`, `score_confiance = 0`, `note` explicative — jamais
d'adresse inventée.

## 8. Optimisation (centaines de milliers d'annonces)

- **Parallélisme** : `AddressMatchQueue` (ThreadPool) pendant le crawl, sans
  bloquer Playwright. Workers réglables (`ADDRESS_MATCH_WORKERS`).
- **Cache DVF** réutilisé (`dvf_commune_cache`) → médiane €/m² calculée une fois
  par commune.
- **Filtrage serveur** des candidats DPE (requête Elasticsearch ADEME bornée par
  commune + surface ± 12 m² + classe DPE) → volume réseau minimal.
- **Dédup** des candidats par adresse+coords.
- **Idempotence** : résultat persisté ; recalcul seulement à la demande (API
  POST) ou si features changent.
- **Tables dédiées indexées** : pas de verrou sur `leads`, tri O(log n) via
  index `(agency_id, confidence DESC)`.
- **Dégradation gracieuse** : chaque connecteur a timeout + repli `[]`.
- *Pistes scale-out* : déplacer la file vers un worker dédié (RQ/Celery),
  geocoding en lot BAN (`/search/csv`), cache DPE par commune.

## 9. API

| Méthode | Route | Effet |
|---|---|---|
| `GET` | `/api/leads/<id>/address` | Dernier rapprochement persisté (404 si jamais calculé) |
| `POST` | `/api/leads/<id>/address` | (Re)calcule à la demande et persiste |

Réponse : `adresse_probable`, `score_confiance`, `candidats[]`, `commune`,
`sources_interrogees`, `note`.

## Configuration

```
ADDRESS_MATCH_DURING_CRAWL=true     # activer le matching parallèle au crawl
ADDRESS_MATCH_WORKERS=3             # threads du pool
ADDRESS_MATCH_DRAIN_TIMEOUT_SEC=150 # budget de fin de crawl
```
