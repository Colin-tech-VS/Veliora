# Ollama gratuit — Ton PC + Cloudflare Tunnel

L'objectif : faire tourner Ollama sur ton ordinateur (Windows / Mac / Linux) et
l'exposer en HTTPS via **Cloudflare Tunnel** (gratuit, aucun engagement, pas
besoin de carte bancaire). Scalingo appelle Ollama via cette URL HTTPS.

> ⚠️ **Limite à connaître** : ton PC doit être **allumé** quand un agent utilise
> l'assistant IA. Si tu fermes ton ordi, l'onglet IA affichera « VPS Ollama
> injoignable ». Pour l'usage typique d'une agence (heures de bureau), c'est
> sans impact.

## Pré-requis

- Un PC avec **8 Go RAM minimum** (16 Go pour le modèle 7B).
- Windows 10+ / macOS 12+ / Linux Ubuntu 22+.
- Une connexion Internet stable.
- **C'est tout.** Pas de domaine, pas de carte bancaire, pas de port à ouvrir
  dans la box.

## 1. Installer Ollama sur ton PC

### Windows

1. Télécharge l'installeur : <https://ollama.com/download/windows>
2. Lance `OllamaSetup.exe` → clic « Install ».
3. Ollama démarre automatiquement en tâche de fond (icône llama dans la barre
   système). Pour vérifier :
   ```powershell
   ollama list
   ```

### macOS / Linux

```bash
# macOS
brew install ollama
# Linux
curl -fsSL https://ollama.com/install.sh | sh
ollama serve   # à laisser tourner dans un terminal
```

## 2. Télécharger le modèle

Une seule fois (~5 Go) :

```bash
ollama pull qwen2.5:7b-instruct
ollama pull llama3.2:3b      # fallback rapide (recommandé)
```

Teste en local :
```bash
ollama run llama3.2:3b "Bonjour, est-ce que tu fonctionnes ?"
```
S'il répond → Ollama est OK.

## 3. Installer `cloudflared` (le tunnel)

### Windows

1. Télécharge `cloudflared-windows-amd64.exe` ici :
   <https://github.com/cloudflare/cloudflared/releases/latest>
2. Renomme-le `cloudflared.exe` et place-le dans `C:\Veliora\` (ou n'importe
   quel dossier que tu retrouveras).

### macOS / Linux

```bash
# macOS
brew install cloudflared
# Linux
sudo curl -L --output /usr/local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo chmod +x /usr/local/bin/cloudflared
```

## 4. Lancer le tunnel (mode rapide, sans compte Cloudflare)

C'est la version la plus simple : Cloudflare te donne **une URL HTTPS aléatoire
gratuite** du type `https://xxx-yyy-zzz.trycloudflare.com`, sans avoir besoin
de créer de compte. Idéale pour démarrer.

### Windows
Double-clique sur `start-tunnel.bat` (fourni dans ce dossier), ou en PowerShell :
```powershell
C:\Veliora\cloudflared.exe tunnel --url http://localhost:11434
```

### macOS / Linux
```bash
cloudflared tunnel --url http://localhost:11434
```

Tu verras dans la console quelque chose comme :

```
+--------------------------------------------------------------------------------------------+
|  Your quick Tunnel has been created! Visit it at (it may take a few moments to be reachable): |
|  https://something-random.trycloudflare.com                                                |
+--------------------------------------------------------------------------------------------+
```

**Copie cette URL** — c'est l'URL qu'on va donner à Scalingo.

> ⚠️ **L'URL change à chaque redémarrage** du tunnel en mode rapide. Pour avoir
> une URL stable (ex. `https://ollama-veliora.trycloudflare.com`), voir la
> section 6 ci-dessous (compte Cloudflare gratuit).

## 5. Brancher Veliora (Scalingo)

> 🔒 **Sécurité** : ce mode tunnel rapide rend Ollama accessible à toute
> personne qui devine l'URL. C'est OK pour démarrer, mais on va ajouter une
> protection par clé Bearer pour bloquer les intrus — voir section 6.

Pour démarrer sans auth (URL aléatoire = obscure mais pas privée) :

```bash
scalingo --app veliora env-set \
  OLLAMA_BASE_URL=https://ton-tunnel.trycloudflare.com \
  OLLAMA_MODEL=qwen2.5:7b-instruct \
  OLLAMA_FALLBACK_MODEL=llama3.2:3b
# (PAS de OLLAMA_API_KEY tant que tu n'as pas mis en place l'auth — voir §6)
```

L'app redémarre. Sur l'onglet « Assistant IA » → voyant vert.

## 6. (Recommandé) Tunnel permanent + auth Bearer

Pour avoir une URL stable et bloquer les accès non autorisés, il te faut un
compte Cloudflare gratuit (gratuit pour de vrai, pas besoin de CB).

### a) Créer un compte Cloudflare

1. Va sur <https://dash.cloudflare.com/sign-up> → inscris-toi (email + mot
   de passe, sans carte bancaire).
2. Ce n'est PAS nécessaire d'avoir un domaine sur Cloudflare. Le tunnel
   permanent fonctionne avec un sous-domaine gratuit `*.trycloudflare.com`
   attaché à ton compte (URL stable).

### b) Authentifier `cloudflared` à ton compte

```bash
cloudflared tunnel login
```
Une page Cloudflare s'ouvre dans ton navigateur → autorise.

### c) Créer un tunnel nommé

```bash
cloudflared tunnel create veliora-ollama
# → note l'UUID renvoyé
```

### d) Configurer le tunnel + auth Bearer

Édite `~/.cloudflared/config.yml` (Windows : `C:\Users\TonNom\.cloudflared\config.yml`)
en copiant le fichier `cloudflared-config.example.yml` fourni dans ce dossier,
puis remplis-le.

### e) Démarrer le tunnel

```bash
cloudflared tunnel run veliora-ollama
```

### f) Mettre à jour Scalingo

```bash
scalingo --app veliora env-set \
  OLLAMA_BASE_URL=https://ollama-veliora.trycloudflare.com \
  OLLAMA_API_KEY=la-cle-bearer-de-ton-config-yml
```

## 7. Garder le tunnel allumé en permanence (service Windows)

Pour que `cloudflared` redémarre automatiquement au boot de Windows :

```powershell
# PowerShell admin
sc.exe create cloudflared binPath= "C:\Veliora\cloudflared.exe tunnel run veliora-ollama" start= auto
sc.exe start cloudflared
```

Sur Mac/Linux :
```bash
sudo cloudflared service install
```

## 8. Coût et performance attendus

- **Coût** : 0 € pour toujours. Tu paies juste l'électricité de ton PC.
  Estimation : ~3 €/mois si ton ordi tourne 8h/jour 5j/7.
- **Performance** sur PC standard (CPU récent, 16 Go RAM, **pas de GPU**) :
  - `llama3.2:3b` → 10–20 tokens/sec (réponse en 8–15 s)
  - `qwen2.5:7b-instruct` → 4–8 tokens/sec (réponse en 25–50 s)
- Sur PC avec **GPU NVIDIA** (RTX 3060 12 Go ou mieux) : 30–60 tokens/sec
  (instantané ou presque). Ollama détecte le GPU automatiquement.

## 9. Dépannage

| Symptôme                                     | Solution                                                                                       |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| « VPS Ollama injoignable »                   | Le tunnel `cloudflared` est-il lancé sur ton PC ? L'icône Ollama est-elle dans la barre système ? |
| Erreur 502 dans Scalingo                     | Ollama ne répond pas — redémarre Ollama (clic droit icône → Restart) et le tunnel.            |
| URL `.trycloudflare.com` qui change          | Mode rapide = URL temporaire. Passe à un tunnel nommé (§6).                                    |
| Réponse très lente                           | Bascule sur `llama3.2:3b` (variable `OLLAMA_MODEL=llama3.2:3b` sur Scalingo).                  |
| Ollama « out of memory »                     | Ferme Chrome / autres apps gourmandes. Ou prends un modèle plus petit.                         |

## 10. Quand passer à un VPS ?

L'option home-PC + tunnel est parfaite pour :
- ✅ Tester l'IA Veliora gratuitement
- ✅ Usage solo (toi seul ou 1–2 agents)
- ✅ Heures de bureau uniquement

Tu passeras à un VPS (cf. [OLLAMA_DEPLOY.md](../../../OLLAMA_DEPLOY.md)) quand :
- Tu as ≥ 3 agents qui utilisent l'IA en même temps
- Tu veux que l'IA réponde la nuit / le week-end
- Tu pars en vacances et ne veux pas laisser ton PC allumé
- Tu as besoin d'un GPU et tu n'as pas de carte NVIDIA

D'ici là : profite du gratuit. 🚀
