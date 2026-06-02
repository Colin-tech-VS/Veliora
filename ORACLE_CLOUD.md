# Ollama 24/7 gratuit — Oracle Cloud Always Free

Ce guide te fait passer de **rien** à **un démon Ollama dispo 24/7, sécurisé
en HTTPS, branché à Scalingo, gratuit à vie**.

> **Coût total** : 0 €. Oracle exige une CB pour vérification d'identité mais
> ne débite rien tant que tu restes dans le free tier. Tu peux supprimer la CB
> après création du compte si tu veux dormir tranquille.

**Temps estimé** : 45 min (dont ~15 min d'attente pendant la création de la VM).

## Étape 1 — Créer ton compte Oracle Cloud

1. Va sur <https://www.oracle.com/cloud/free/> → bouton « Start for free ».
2. Remplis le formulaire :
   - **Country/Territory** : France
   - **Home Region** : choisis ce qui est proche (Paris si dispo, sinon Frankfurt).
     ⚠️ La région ne peut **plus** être changée après. Si Paris affiche
     « Capacity limited », prends Frankfurt.
   - Email + mot de passe + numéro de téléphone (un vrai, ils envoient un SMS).
3. **Vérification carte bancaire** : carte personnelle, ils débitent 0 € puis
   remboursent les 0 € (pour valider la carte). Pas d'auto-débit, pas
   d'abonnement.
4. Tu reçois un email « Your Oracle Cloud account is ready ». Connecte-toi sur
   <https://cloud.oracle.com>.

## Étape 2 — Créer la VM gratuite (Ampere A1)

Dans la console Oracle :

1. Menu hamburger (en haut à gauche) → **Compute** → **Instances** → bouton
   **Create instance**.
2. **Name** : `veliora-ollama`
3. **Image and shape** → clique sur **Edit** :
   - **Image** : Canonical Ubuntu **22.04** (gratuit).
   - **Shape** → onglet **Ampere** → choisis **VM.Standard.A1.Flex**.
   - **OCPU** : `4` (curseur à fond)
   - **Memory (GB)** : `24` (curseur à fond)
   - Confirme.

   ⚠️ Si Oracle te dit « Out of capacity » : retente dans 30 min,
   éventuellement avec une autre région (Edit → Region). Le bug est connu, ça
   finit par passer.

4. **Networking** → laisse les valeurs par défaut (Oracle crée un VCN et un
   subnet automatiquement). **Assign a public IPv4 address** : ✅ activé.
5. **Add SSH keys** → choisis **Generate a key pair for me** → clique sur
   **Save Private Key** et **Save Public Key** (garde-les précieusement, tu
   en auras besoin pour te connecter).
6. **Boot volume** : laisse 50 Go (suffit pour le modèle).
7. **Create** → la VM se crée en 1–2 min.

Note l'**IP publique** affichée dans la console (« Public IP Address »).

## Étape 3 — Ouvrir les ports 80 et 443 dans le firewall Oracle

Oracle bloque tout par défaut sauf le SSH. Il faut ouvrir 80 (HTTP, pour
Let's Encrypt) et 443 (HTTPS).

1. Dans la console : **Networking** → **Virtual Cloud Networks** → clique
   sur le VCN qui s'est créé automatiquement (genre `vcn-XXXX`).
2. Clique sur le **subnet** (ex. `subnet-XXXX`).
3. Clique sur **Default Security List for vcn-XXXX**.
4. **Add Ingress Rules** :
   - **Source CIDR** : `0.0.0.0/0`
   - **IP Protocol** : `TCP`
   - **Destination Port Range** : `80,443`
   - **Description** : `HTTP/HTTPS Caddy`
   - Clique sur **Add Ingress Rules**.

## Étape 4 — Se connecter à la VM en SSH

Sur ton PC Windows (PowerShell) ou Mac/Linux :

```bash
# Mac/Linux
chmod 600 ~/Downloads/ssh-key-XXXX.key    # la clé privée téléchargée
ssh -i ~/Downloads/ssh-key-XXXX.key ubuntu@TON_IP_PUBLIQUE

# Windows PowerShell
ssh -i C:\Users\TonNom\Downloads\ssh-key-XXXX.key ubuntu@TON_IP_PUBLIQUE
```

Si Windows refuse les permissions de la clé : clic droit sur le fichier →
Propriétés → Sécurité → Avancé → Désactiver l'héritage → Convertir → ne
garder que ton utilisateur.

## Étape 5 — Avoir un nom de domaine (gratuit)

Caddy a besoin d'un nom de domaine pointant vers l'IP de ta VM pour générer
le certificat HTTPS Let's Encrypt. **DuckDNS** offre ça gratuitement, sans
limite.

1. Va sur <https://www.duckdns.org> → connecte-toi avec ton compte Google
   ou GitHub (pas de CB).
2. Crée un sous-domaine : `veliora-ollama` → tu obtiens
   `veliora-ollama.duckdns.org`.
3. Dans la case **current ip** : colle l'IP publique de ta VM Oracle, clique
   **update ip**.
4. Note ton **token** affiché en haut de la page (utile si tu veux automatiser
   les renouvellements d'IP — mais Oracle te donne une IP fixe, donc inutile
   au quotidien).

## Étape 6 — Installer Docker + déployer Ollama

Sur ta VM (en SSH) :

```bash
# Installer Docker + Compose plugin
sudo apt update && sudo apt -y upgrade
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Recharger les permissions du groupe sans avoir à se déconnecter
newgrp docker

# Ouvrir 80 + 443 dans le firewall OS (Ubuntu sur Oracle a iptables très restrictif)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

# Cloner Veliora pour récupérer la stack infra
git clone https://github.com/Colin-tech-VS/Veliora.git
cd Veliora/infra/ollama

# Générer le .env
cp .env.example .env
KEY=$(openssl rand -base64 48 | tr -d '/=+' | cut -c1-48)
sed -i "s|remplacez-par-une-cle-aleatoire-tres-longue|$KEY|" .env
sed -i "s|ollama.veliora.fr|veliora-ollama.duckdns.org|" .env
cat .env
# ➜ Note la valeur de OLLAMA_API_KEY, tu en auras besoin côté Scalingo

# Lancer la stack
docker compose up -d

# Télécharger les modèles (~5 min)
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose exec ollama ollama pull llama3.2:3b
```

## Étape 7 — Vérifier que tout marche

Depuis ta machine perso :

```bash
curl -H "Authorization: Bearer LA_CLE_DU_.ENV" \
     https://veliora-ollama.duckdns.org/api/tags
```

Tu dois voir un JSON listant les deux modèles. Si tu obtiens :

- **403 Forbidden** → la clé Bearer est fausse ; vérifie le `.env`.
- **Erreur TLS / connexion refusée** → DuckDNS pas encore propagé (attends 5 min)
  ou les règles ingress Oracle ne sont pas appliquées.
- **Connection timed out** → le firewall Oracle ou iptables bloque encore.

## Étape 8 — Brancher Veliora (Scalingo)

```bash
scalingo --app veliora env-set \
  OLLAMA_BASE_URL=https://veliora-ollama.duckdns.org \
  OLLAMA_API_KEY=LA_CLE_DU_.ENV \
  OLLAMA_MODEL=qwen2.5:7b-instruct \
  OLLAMA_FALLBACK_MODEL=llama3.2:3b
```

L'app redémarre. Ouvre <https://veliora.osc-fr1.scalingo.io/crm> → onglet
« Assistant IA » → voyant **vert** « Prêt · qwen2.5:7b-instruct ».

## Étape 9 — (Optionnel) Garder la stack à jour

Tous les 1–2 mois :

```bash
ssh ubuntu@TON_IP
cd Veliora/infra/ollama
git pull
docker compose pull && docker compose up -d
docker image prune -f
```

## Performances attendues (Ampere A1, 4 OCPU, 24 Go RAM, sans GPU)

- `llama3.2:3b` : **15–25 tokens/s** → réponse de 200 mots en ~10 s ✅
- `qwen2.5:7b-instruct` : **5–10 tokens/s** → réponse de 200 mots en ~30 s
- Plusieurs requêtes en parallèle : OK jusqu'à 2 simultanées sans
  ralentissement notable.

C'est plus lent qu'un GPU, mais c'est **gratuit, 24/7, hébergé en UE, RGPD**,
sans facturation à l'usage. Pour démarrer une agence, c'est largement
suffisant.

## Sécurité — points importants

- ✅ Tu as déjà : HTTPS (Let's Encrypt automatique), auth Bearer (Caddy),
  pas d'exposition directe d'Ollama.
- ⚠️ **Garde la clé Bearer secrète**. Si elle fuite, regénère-la :
  ```bash
  KEY=$(openssl rand -base64 48 | tr -d '/=+' | cut -c1-48)
  sed -i "s|^OLLAMA_API_KEY=.*$|OLLAMA_API_KEY=$KEY|" .env
  docker compose restart caddy
  # Puis met à jour Scalingo avec la nouvelle clé
  ```
- ⚠️ Active **les mises à jour automatiques de sécurité** Ubuntu :
  ```bash
  sudo apt -y install unattended-upgrades
  sudo dpkg-reconfigure -plow unattended-upgrades   # → Yes
  ```

## Limites du tier gratuit Oracle

- **Capacité ARM A1** : Oracle limite les ressources gratuites globales par
  région. Pendant les pics de demande, la création de VM peut échouer avec
  « Out of capacity ». Solution : retenter quelques heures plus tard ou
  changer de région (Frankfurt > Paris en termes de dispo).
- **Inactivité** : Oracle peut suspendre les comptes Always Free qui ne se
  connectent jamais. Garde l'habitude de te connecter à la console Oracle
  une fois par mois (juste un login suffit).
- **Bande passante sortante** : 10 To/mois gratuits. Largement assez pour
  une agence (~quelques Go max).

## Coût récap

| Élément                       | Coût           |
| ----------------------------- | -------------- |
| VM Oracle Ampere A1 24 Go     | 0 € / mois     |
| Trafic réseau (10 To/mois)    | 0 €            |
| Nom de domaine DuckDNS        | 0 €            |
| Certificat TLS Let's Encrypt  | 0 €            |
| **Total**                     | **0 € / mois** |

🎉 Bienvenue dans l'IA auto-hébergée sans facture.
