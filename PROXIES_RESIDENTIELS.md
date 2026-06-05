# Proxies résidentiels — débloquer le crawl (LBC, PAP, SeLoger…)

## Réponse courte

| Configuration | Résultat |
|---------------|----------|
| **Proxies résidentiels seuls** sur Scalingo (HTTP, sans Chrome) | Améliore ParuVendu / catalogue / sites agences — **pas** LBC/PAP/SeLoger |
| **Proxies résidentiels + Playwright** (PC ou VPS dédié) | **Débloque** LBC, PAP, SeLoger, Bien’ici, Logic-Immo |
| **StreamEstate** (API payante) | LBC+PAP+SeLoger agrégés sans gérer proxies/navigateur |

Les portails DataDome exigent **deux couches** : IP résidentielle **et** vrai navigateur (Playwright).

---

## Fournisseur recommandé : IPRoyal Residential

**Pourquoi IPRoyal** (immobilier FR) :
- Pool résidentiel avec IPs **France** (`_country-fr`)
- Rotation automatique (`_session-rotate`)
- Format HTTP compatible Veliora
- Pay-as-you-go (~7 €/Go) ou forfait mensuel — pas d’engagement long

**Inscription** : [dashboard.iproyal.com](https://dashboard.iproyal.com/) → **Residential Proxies** → acheter du trafic → **Proxy Generator**.

### Générer le proxy (dashboard IPRoyal)

1. **Type** : Rotating residential  
2. **Location** : France (`fr`) — ou votre ville si dispo  
3. **Rotation** : New IP each request (`session-rotate`)  
4. **Protocol** : HTTP/HTTPS  
5. **Host** : `geo.iproyal.com` (auto région EU)  
6. **Port** : `12321`

Vous obtenez :
- **Username** : ex. `votre_user`  
- **Password** : ex. `votre_pass_country-fr_session-rotate`

### URL pour Veliora

```
http://VOTRE_USER:votre_pass_country-fr_session-rotate@geo.iproyal.com:12321
```

⚠️ Le mot de passe contient des `_` — ne pas les encoder à la main. Copiez-collez depuis le dashboard.

**Test local** :

```bash
python scripts/configure_proxy_rotation.py --proxy "http://USER:PASS@geo.iproyal.com:12321" --test
```

---

## Architecture recommandée (tout débloquer)

```
┌─────────────────────┐     ┌──────────────────────────────┐
│  Scalingo (CRM web) │     │  Worker crawl (PC ou VPS)    │
│  CRAWL_AUTO_START=  │     │  CRAWL_PLAYWRIGHT_ENABLED=   │
│  false              │     │  true                        │
│  Pas de Chrome      │     │  CRAWL_PROXIES= IPRoyal      │
└──────────┬──────────┘     │  CRAWL_AUTO_START=true       │
           │                └──────────────┬───────────────┘
           │         même DATABASE_URL     │
           └────────────── Supabase ───────┘
```

Le CRM reste sur Scalingo ; **un seul** worker (votre PC allumé ou VPS Oracle gratuit) fait la veille avec Chrome + IPRoyal.

---

## Mise en place rapide

### 1. Copier les modèles de config

```powershell
cd Veliora
copy scripts\scalingo-env-residential.env.example scripts\scalingo-env-residential.env
copy scripts\crawl-worker-local.env.example scripts\crawl-worker-local.env
```

Remplissez `CRAWL_PROXIES` dans les deux fichiers (même URL IPRoyal).

### 2. Scalingo — désactiver la veille locale (évite double crawl)

```powershell
.\scripts\apply-residential-crawl.ps1 -Target scalingo
```

Puis secrets (une fois) :

```powershell
scalingo --app veliora env-set CRAWL_PROXIES="http://USER:PASS_country-fr_session-rotate@geo.iproyal.com:12321"
scalingo --app veliora restart
```

### 3. Worker local Windows (Playwright + LBC/PAP)

```powershell
pip install -r requirements.txt
playwright install chromium
.\scripts\run-crawl-worker.ps1
```

Laissez la fenêtre ouverte (veille 24/7 tant que le PC tourne).

### 4. CRM

- Activez **LeBonCoin, PAP, SeLoger** (badge « Navigateur requis » disparaît côté worker)
- Ville / territoire agence renseigné

---

## Variables clés

| Variable | Worker (tout débloquer) | Scalingo seul (HTTP+) |
|----------|-------------------------|------------------------|
| `CRAWL_PROXIES` | URL IPRoyal | URL IPRoyal |
| `CRAWL_AUTO_FREE_PROXIES` | `false` | `false` |
| `CRAWL_PLAYWRIGHT_ENABLED` | `true` | `false` |
| `CRAWL_ANTIBOT_PORTALS_ENABLED` | `true` (ou auto) | `false` |
| `CRAWL_AUTO_START` | `true` | `false` |
| `CRAWL_SPEED_PROFILE` | `quality` | `balanced` |

---

## Budget indicatif IPRoyal

| Usage | Trafic estimé | Coût |
|-------|---------------|------|
| Veille 5 portails, 1 ville, 24/7 | ~2–5 Go/mois | ~15–35 €/mois |
| Crawl manuel ponctuel | < 500 Mo | < 5 € |

Réduisez le coût : `CRAWL_BACKGROUND_INTERVAL_SEC=600`, moins de portails actifs, `CITY_CRAWL_MAX_LISTINGS=60`.

---

## Dépannage

| Symptôme | Action |
|----------|--------|
| Toujours « anti-bot » avec IPRoyal | Vérifiez Playwright (`playwright install chromium`) sur le **worker**, pas Scalingo |
| Proxy test OK mais crawl vide | Ajoutez `_country-fr` au mot de passe IPRoyal |
| Double veille / jobs en double | `CRAWL_AUTO_START=false` sur Scalingo, `true` sur worker uniquement |
| Trop cher | Désactivez catalogue peu utile ; gardez LBC+PAP+ParuVendu |

---

## Alternatives IPRoyal

| Fournisseur | Note |
|-------------|------|
| **Decodo** (ex-Smartproxy) | Bon pool EU, un peu plus cher |
| **Bright Data** | Très fiable, prix entreprise |
| **Oxylabs** | Idem, gros volumes |

Veliora accepte tout proxy HTTP au format `http://user:pass@host:port`.
