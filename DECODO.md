# Decodo (ex-Smartproxy) — crawl complet Veliora

## Quel forfait choisir ?

| Forfait | Prix/mois | Usage Veliora |
|---------|-----------|---------------|
| **3 Go** | ~11,25 $ | **Essai 2–3 semaines** — 1 agence, veille modérée |
| **10 Go** | **35 $** | **Recommandé prod** — 1 agence, tous portails + catalogue |
| 25 Go | ~81 $ | Multi-villes agressif ou plusieurs agences |
| 50 Go+ | 150 $+ | Réseau / très haute fréquence |
| Pay-as-you-go 4 $/Go | variable | Sans engagement, plus cher au Go |

**Notre conseil :** commencez par **3 Go** (trial), passez à **10 Go** dès que la veille tourne 24/7 sans alerte quota.

Consommation indicative (1 agence, `CRAWL_SPEED_PROFILE=quality`) :
- ~0,5–1,5 Go / semaine avec LBC + PAP + SeLoger + catalogue + veille 5 min
- **10 Go** = marge confortable

---

## Architecture

```
Scalingo (CRM)          Worker PC (vous)
CRAWL_AUTO_START=false  CRAWL_PLAYWRIGHT_ENABLED=true
                        CRAWL_PROXIES=Decodo France
                        CRAWL_SKIP_STREAMESTATE=true
         └──── Supabase (même DATABASE_URL) ────┘
```

StreamEstate : **code conservé**, ignoré via `CRAWL_SKIP_STREAMESTATE=true`.

---

## Setup en 5 minutes

### 1. Acheter Decodo Residential

[decodo.com](https://decodo.com/) → **Residential Proxies** → forfait **10 Go** (ou 3 Go trial).

Dashboard → **Authentication** :
- Session : **Rotating**
- Location : **France**
- Endpoint : **`fr.decodo.com:40000`** (port 40000 = rotation FR)
- Username / password : tels qu’affichés (`spXXXXXXXX` + mot de passe)

### 2. Fichiers locaux

```powershell
cd Veliora
.\scripts\apply-decodo-crawl.ps1
```

Éditez `scripts/crawl-worker-local.env` :
- `DATABASE_URL` = même URI Supabase que Scalingo (pooler 6543)
- `CRAWL_PROXIES=http://spXXXX:MOT_DE_PASSE@fr.decodo.com:40000`

### 3. Tester le proxy

```powershell
# France rotating (dashboard) — espace avant --test obligatoire
python scripts/configure_proxy_rotation.py --proxy "http://spXXXX:PASS@fr.decodo.com:40000" --test

# Alternative gateway Decodo
python scripts/configure_proxy_rotation.py --proxy "http://user-spXXXX-country-fr:PASS@gate.decodo.com:7000" --test
```

Réponse attendue : JSON avec une IP française (`ip.decodo.com` ou ipify).

Si le message mentionne **FortiGate** : le réseau d'entreprise bloque les proxies — testez en **4G / partage mobile**, pas sur le Wi‑Fi ANRH.

### 4. Scalingo (désactiver veille locale)

```powershell
.\scripts\apply-decodo-crawl.ps1 -Target scalingo
```

### 5. Lancer le worker

```powershell
pip install -r requirements.txt
playwright install chromium
.\scripts\run-crawl-worker.ps1
```

### 6. CRM

- Activez **tous** les portails (LBC, PAP, SeLoger, ParuVendu, catalogue…)
- Laissez **Analyse approfondie** OFF (ou ON — ignorée si `CRAWL_SKIP_STREAMESTATE=true`)
- Ville / territoire agence renseigné

---

## Variables clés

| Variable | Worker | Scalingo |
|----------|--------|----------|
| `CRAWL_PROXIES` | URL Decodo | vide ou même URL |
| `CRAWL_PLAYWRIGHT_ENABLED` | `true` | `false` |
| `CRAWL_ANTIBOT_PORTALS_ENABLED` | `true` | `false` |
| `CRAWL_AUTO_START` | `true` | `false` |
| `CRAWL_SKIP_STREAMESTATE` | `true` | `true` |
| `CRAWL_AUTO_FREE_PROXIES` | `false` | `false` |

---

## Dépannage

| Problème | Solution |
|----------|----------|
| Quota Decodo dépassé | Passer 10→25 Go ou `CRAWL_BACKGROUND_INTERVAL_SEC=600` |
| Encore « anti-bot » | Vérifier `user-XXX-country-fr` ; worker bien lancé |
| Double veille | `CRAWL_AUTO_START=false` sur Scalingo |
| StreamEstate consomme | `CRAWL_SKIP_STREAMESTATE=true` |

Voir aussi : [PROXIES_RESIDENTIELS.md](PROXIES_RESIDENTIELS.md)
