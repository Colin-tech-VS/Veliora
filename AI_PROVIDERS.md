# Choisir un fournisseur d'IA pour Veliora

Veliora supporte plusieurs fournisseurs interchangeables. Le choix se fait
via la variable d'environnement `AI_PROVIDER`. L'onglet « Assistant IA »
fonctionne strictement de la même façon, peu importe le provider derrière.

## Vue d'ensemble

| Provider     | Coût      | Setup     | Qualité   | Vitesse  | Confidentialité    |
| ------------ | --------- | --------- | --------- | -------- | ------------------ |
| **Groq** ⭐  | Gratuit   | 3 min     | ★★★★☆     | ★★★★★    | US (Equinix)       |
| Ollama local | Gratuit   | 10 min    | ★★★☆☆     | ★★☆☆☆    | 100 % local        |
| Ollama VPS   | 0–17 €/mo | 1 h       | ★★★☆☆     | ★★☆☆☆    | UE (auto-hébergé)  |
| Mistral      | Free tier | 3 min     | ★★★★☆     | ★★★★☆    | UE (FR/Suède)      |
| OpenAI       | Pay-go    | 3 min     | ★★★★★     | ★★★★☆    | US                 |
| OpenRouter   | Free tier | 3 min     | ★★★★★     | ★★★★☆    | Variable           |

## Recommandation rapide

- **Tu veux ça marche immédiatement et c'est gratuit** → **Groq**.
- **Tu tiens au RGPD / serveurs UE** → **Mistral** (free tier généreux).
- **Tu veux la meilleure qualité possible, peu importe le coût** → **OpenAI GPT-4o**.
- **Tu veux du 100 % local / privé** → **Ollama** (local en dev, Oracle Cloud
  ou VPS en prod — voir [ORACLE_CLOUD.md](ORACLE_CLOUD.md)).

---

## Option 1 — Groq (recommandée pour démarrer)

**Avantages** : gratuit (14 400 req/jour), ultra rapide (Llama 3.3 70B à
500 tok/s, ~10× plus vite qu'Ollama CPU), pas de carte bancaire requise.

**Limitations** : tes prompts transitent par Groq (US). Pour de la prospection
immobilière classique c'est OK ; pour des données sensibles cliente, à
arbitrer.

### Setup (3 min)

1. Va sur <https://console.groq.com> → connecte-toi avec Google.
2. Onglet **API Keys** → bouton **Create API Key** → copie `gsk_...`.
3. Dans Scalingo :
   ```bash
   scalingo --app veliora env-set \
     AI_PROVIDER=groq \
     AI_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx \
     AI_MODEL=llama-3.3-70b-versatile
   ```
4. Recharge `/crm` → onglet « Assistant IA » → voyant vert.

### Modèles Groq disponibles

| Modèle                                | Vitesse  | Qualité  | Cas d'usage                                  |
| ------------------------------------- | -------- | -------- | -------------------------------------------- |
| `llama-3.3-70b-versatile`             | ★★★★☆    | ★★★★☆    | **Défaut conseillé** — équilibre qualité/vitesse |
| `llama-3.1-8b-instant`                | ★★★★★    | ★★★☆☆    | Réponses instantanées, qualité acceptable    |
| `mixtral-8x7b-32768`                  | ★★★★☆    | ★★★★☆    | Contexte long (32k tokens)                   |
| `gemma2-9b-it`                        | ★★★★☆    | ★★★☆☆    | Alternative Google                           |

## Option 2 — Mistral (RGPD / UE)

**Avantages** : française, serveurs UE, RGPD-compliant. Free tier disponible.

### Setup

1. <https://console.mistral.ai/api-keys/> → **Create new key**.
2. Scalingo :
   ```bash
   scalingo --app veliora env-set \
     AI_PROVIDER=mistral \
     AI_API_KEY=xxxxxxxxxxxxxxxx \
     AI_MODEL=mistral-small-latest
   ```

Modèles : `mistral-small-latest` (rapide, conseillé), `mistral-large-latest`
(qualité top), `open-mistral-7b` (gratuit illimité au moment où ce doc est
écrit).

## Option 3 — Ollama auto-hébergé

Voir guides dédiés :
- **[ORACLE_CLOUD.md](ORACLE_CLOUD.md)** — Oracle Cloud Always Free 24/7,
  totalement gratuit.
- **[OLLAMA_DEPLOY.md](OLLAMA_DEPLOY.md)** — VPS payant (Hetzner / OVH).
- **[infra/ollama/home-tunnel/HOME_TUNNEL.md](infra/ollama/home-tunnel/HOME_TUNNEL.md)**
  — Ton PC + Cloudflare Tunnel (pas 24/7 mais 0 €).

## Option 4 — OpenRouter (free tier multi-modèles)

**Avantages** : un seul compte donne accès à tous les modèles (Llama, Claude,
Mistral, DeepSeek…). Free tier sur certains modèles `:free`.

### Setup

```bash
scalingo --app veliora env-set \
  AI_PROVIDER=openrouter \
  AI_API_KEY=sk-or-v1-xxxxxxxxxx \
  AI_MODEL=meta-llama/llama-3.3-70b-instruct:free
```

Liste des modèles gratuits : <https://openrouter.ai/models?max_price=0>

## Option 5 — OpenAI

```bash
scalingo --app veliora env-set \
  AI_PROVIDER=openai \
  AI_API_KEY=sk-xxxxxxxxxxxxxxx \
  AI_MODEL=gpt-4o-mini
```

Coût indicatif (au moment d'écrire ce doc) : `gpt-4o-mini` ≈ 0,15 $/1M tokens
en entrée, 0,60 $/1M en sortie. Pour une agence c'est ~3-10 $/mois.

## Option 6 — Together AI

Plateforme orientée open-source. `together` comme `AI_PROVIDER`. Crédit
gratuit à l'inscription, puis pay-per-token compétitif.

## Changer de fournisseur

Aucune migration nécessaire — il suffit de remettre à jour les variables
d'environnement et redémarrer l'app. L'historique des conversations est
préservé (il vit dans la base Veliora, pas chez le fournisseur).

```bash
# Exemple : passer de Groq à Mistral
scalingo --app veliora env-set \
  AI_PROVIDER=mistral \
  AI_API_KEY=ta_clé_mistral \
  AI_MODEL=mistral-small-latest
scalingo --app veliora restart
```

## Sécurité — bonnes pratiques pour toute clé d'API

- ⚠️ **Ne commit jamais** une clé dans Git.
- ⚠️ **Régénère** immédiatement si une clé fuite (Groq, Mistral, etc. ont
  tous un bouton « rotate »).
- 💡 **Monitor l'usage** sur le dashboard du provider pour repérer des
  débits anormaux.
- 💡 **Mets des quotas** quand le provider le permet (OpenAI = budget cap,
  Groq = quota par défaut).
