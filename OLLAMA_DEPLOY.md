# Déployer Ollama en production pour Veliora

Veliora utilise [Ollama](https://ollama.com) (modèles open-source auto-hébergés)
pour l'assistant IA. **Ollama doit tourner sur une machine séparée** de
Scalingo : un dyno Scalingo n'a ni la RAM, ni le disque, ni le GPU nécessaires
pour servir un modèle 7B.

> ## 🆓 Tu veux du 24/7 totalement gratuit ?
>
> Va lire **[ORACLE_CLOUD.md](ORACLE_CLOUD.md)** — guide pas-à-pas pour
> déployer Ollama sur l'offre **Oracle Cloud Always Free** (VM ARM 24 Go RAM,
> gratuite à vie, 24/7). C'est l'option recommandée si tu débutes.
>
> Si tu veux juste tester sans avoir besoin de 24/7 → voir
> [infra/ollama/home-tunnel/HOME_TUNNEL.md](infra/ollama/home-tunnel/HOME_TUNNEL.md)
> (ton PC + tunnel Cloudflare gratuit, marche tant que le PC est allumé).
>
> Le reste de ce document décrit le déploiement sur **VPS payant** (Hetzner,
> OVH…) qui reste pertinent quand tu auras plusieurs agences ou que tu veux
> un GPU.

Ce guide explique comment monter un serveur Ollama dédié, le sécuriser, et
brancher l'application Veliora dessus.

## 1. Choisir un VPS

Le modèle par défaut de Veliora est **qwen2.5:7b-instruct** (≈ 5 Go RAM,
5 Go disque). Les modèles plus petits (`llama3.2:3b`) tournent sur des VPS
moins chers, plus rapidement, avec une qualité un peu en retrait.

| Fournisseur          | Offre               | RAM   | vCPU | Prix      | Tient quel modèle ?                  |
| -------------------- | ------------------- | ----- | ---- | --------- | ------------------------------------ |
| **Hetzner CX22**     | EU, sans GPU        | 8 Go  | 4    | ~ 4 €/mois| `llama3.2:3b` confortable            |
| **Hetzner CCX23**    | EU, vCPU dédiés     | 16 Go | 4    | ~ 16 €/mois| `qwen2.5:7b-instruct` confortable    |
| **Scaleway DEV1-L** | FR, mutualisé       | 8 Go  | 4    | ~ 9 €/mois | `llama3.2:3b`                        |
| **OVH VPS-3**        | FR, vCPU dédiés     | 16 Go | 4    | ~ 16 €/mois| `qwen2.5:7b-instruct`                |
| **Scaleway L4 GPU**  | FR, GPU 24 Go       | 48 Go | 8    | ~ 1,2 €/h | Très rapide, mais facturation horaire|

> **Recommandation** : Hetzner CCX23 ou OVH VPS-3 en mensuel pour démarrer.
> ~16 €/mois, 100 % UE, suffit pour servir une agence (~quelques centaines
> de requêtes/jour).

## 2. Préparer le VPS

```bash
# Sur le VPS (Ubuntu 22.04 LTS ou Debian 12 recommandé)
sudo apt update && sudo apt -y upgrade
sudo apt -y install ca-certificates curl gnupg

# Installer Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# (déconnectez-vous puis reconnectez-vous pour que le groupe soit pris en compte)
```

## 3. Pointer un nom de domaine vers le VPS

Créez un enregistrement DNS **A** chez votre registrar :

```
ollama.veliora.fr   A    <IP publique du VPS>
```

Caddy obtiendra automatiquement un certificat TLS Let's Encrypt pour ce nom
au démarrage (vous n'avez rien à faire).

Si vous n'avez pas encore de nom de domaine, vous pouvez utiliser un
sous-domaine gratuit (DuckDNS, ou un FreeDNS) — l'important est que le DNS
public résolve vers l'IP du VPS, sinon Let's Encrypt refusera le certificat.

## 4. Déployer la stack

Copiez le dossier [`infra/ollama/`](infra/ollama/) sur le VPS (par scp, git
clone, ou rsync). Puis :

```bash
cd infra/ollama
cp .env.example .env
# Générer une clé Bearer secrète (notez-la, vous en aurez besoin côté Scalingo)
echo "OLLAMA_API_KEY=$(openssl rand -base64 48 | tr -d /=+ | cut -c1-48)" >> .env
# Renseigner le domaine
sed -i 's|ollama.veliora.fr|VOTRE_DOMAINE_ICI|' .env

docker compose up -d
# Télécharger les modèles (la première fois — quelques minutes / Go)
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose exec ollama ollama pull llama3.2:3b
```

Vérifiez :

```bash
# Le démon répond (depuis le VPS lui-même, sans auth)
docker compose exec ollama ollama list

# Le proxy HTTPS répond (depuis n'importe où, avec la clé)
curl -H "Authorization: Bearer VOTRE_CLE" https://ollama.votre-domaine.fr/api/tags
# → liste JSON des modèles
```

Si vous obtenez un 401, votre clé est fausse. Si vous obtenez du HTML Caddy
ou un erreur TLS, vérifiez que le DNS résolve bien vers le VPS et que les
ports 80 + 443 sont ouverts (Hetzner Cloud Firewall, ufw, etc.).

## 5. Brancher Veliora (Scalingo)

```bash
scalingo --app veliora env-set \
  OLLAMA_BASE_URL=https://ollama.votre-domaine.fr \
  OLLAMA_API_KEY=VOTRE_CLE \
  OLLAMA_MODEL=qwen2.5:7b-instruct \
  OLLAMA_FALLBACK_MODEL=llama3.2:3b
```

Scalingo redémarre l'app. Allez sur l'onglet « Assistant IA » du CRM : le
voyant en haut à droite doit passer au **vert** avec le nom du modèle.

## 6. Sécurité / bonnes pratiques

- **N'exposez jamais le port 11434 directement** sur Internet. Ollama n'a pas
  d'authentification native — n'importe qui pourrait utiliser votre GPU à
  votre place. Le `docker-compose.yml` fourni n'expose que via Caddy.
- **Conservez la clé `OLLAMA_API_KEY` secrète**. Si elle fuite, regénérez-en
  une avec `openssl rand -base64 48`, mettez-la dans `infra/ollama/.env`,
  faites `docker compose restart caddy`, puis `scalingo env-set OLLAMA_API_KEY=…`.
- **Mettez à jour Ollama régulièrement** :
  ```bash
  docker compose pull && docker compose up -d
  ```
- **Sauvegardez les modèles** : ils sont dans le volume `ollama_data`. En cas
  de panne du VPS, vous pouvez les retélécharger (5–10 min) plutôt que de les
  sauvegarder.

## 7. Performance attendue

Sur **Hetzner CCX23 (16 Go RAM, 4 vCPU dédiés, sans GPU)** :

- `llama3.2:3b` : ~ 15–25 tokens/s → réponse de 200 mots en 10–15 s
- `qwen2.5:7b-instruct` : ~ 5–10 tokens/s → réponse de 200 mots en 25–45 s

Sur **Scaleway L4 GPU** : 50–100 tokens/s sur les deux modèles (réponse
quasi-instantanée).

Sur **un mini-PC home avec RTX 3060 12 Go** : équivalent au L4, gratuit en
électricité une fois acheté — option valable si vous gardez Veliora pour une
seule agence et acceptez les pannes de FAI maison.

## 8. Dépannage

| Symptôme                                              | Cause probable                                     | Solution                                                                            |
| ----------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Onglet IA : voyant rouge « Ollama injoignable »       | DNS / firewall / docker arrêté                     | `docker compose ps` sur le VPS, vérifier qu'A pointe vers la bonne IP               |
| Onglet IA : « Ollama refuse la requête (HTTP 401) »   | `OLLAMA_API_KEY` différente côté Scalingo et VPS    | Re-définir la même clé des deux côtés, redémarrer                                   |
| « HTTP 404 » sur `/api/tags`                          | Caddy mal configuré ou Ollama down                  | `docker compose logs caddy` et `docker compose logs ollama`                         |
| Réponse extrêmement lente                             | Modèle 7B sur CPU faible / RAM saturée              | Passer à `llama3.2:3b` ou prendre un VPS plus gros                                  |
| Caddy ne reçoit pas le certificat TLS                 | Port 80 fermé ou DNS pas encore propagé             | Ouvrir 80+443 dans le firewall ; attendre 5–10 min après création du DNS            |

## 9. Coût total estimé

- VPS Hetzner CCX23 : 16 €/mois
- Nom de domaine `.fr` : ~ 10 €/an
- **Total ≈ 17 €/mois** pour une IA 100 % auto-hébergée, RGPD, sans
  facturation à l'usage.

À comparer avec ~15–40 €/mois sur API hébergée (Mistral / OpenAI), avec
toutefois une qualité supérieure et zéro maintenance.
