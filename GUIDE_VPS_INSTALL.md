# Guide installation OVH VPS — etape par etape

**Votre cas :** VPS-2, Ubuntu 26.04, **pas de domaine** (IP seule pour commencer).

---

## AVANT la livraison OVH (sur votre PC Windows)

### Etape 0 — Sauvegarder les secrets Scalingo

```powershell
cd "c:\Users\ccayre\OneDrive - ANRH\Bureau\veliora"
.\scripts\export-scalingo-for-vps.ps1
```

Cela cree `scripts\ovh-vps-fill.env` (local, jamais sur GitHub) avec vos variables Scalingo.

Gardez aussi sous la main :
- **Decodo** : user + mot de passe + `fr.decodo.com:40000`

---

## QUAND OVH vous envoie l'email « VPS pret »

Notez :
- **IP publique** (ex. `51.210.12.34`)
- **Mot de passe root** (email OVH)

Envoyez-moi dans le chat : **l'IP uniquement** (pas le mot de passe).

---

## Etape 1 — Premiere connexion SSH (Windows)

Ouvrez PowerShell :

```powershell
ssh root@IP_DU_VPS
```

Tapez `yes` puis le mot de passe OVH.

*(Optionnel : changez le mot de passe root avec `passwd`)*

---

## Etape 2 — Installation automatique Veliora

Sur le VPS (connecte en root) :

```bash
apt-get update && apt-get install -y git
git clone https://github.com/Colin-tech-VS/Veliora.git
cd Veliora
bash scripts/deploy-ovh-vps.sh --domain IP_DU_VPS
```

Remplacez `IP_DU_VPS` par votre vraie IP (ex. `51.210.12.34`).

**Duree :** 10-15 min (pip + Playwright).

---

## Etape 3 — Renseigner les secrets

```bash
nano /opt/veliora/.env
```

**Minimum obligatoire :**

| Variable | Ou la trouver |
|----------|---------------|
| `DATABASE_URL` | `scripts\ovh-vps-fill.env` ou Scalingo |
| `CRAWL_PROXIES` | Decodo dashboard |
| `FLASK_SECRET_KEY` | Generer : `openssl rand -hex 32` |
| `APP_PUBLIC_URL` | `http://IP_DU_VPS` (deja mis si IP) |

**Stripe / SMTP** : copier depuis `ovh-vps-fill.env` si vous les utilisez.

Sauvegarder nano : `Ctrl+O` Entree, `Ctrl+X`.

---

## Etape 4 — Demarrer Veliora

```bash
systemctl start veliora
systemctl status veliora
```

Doit afficher `active (running)`.

---

## Etape 5 — Verifier

```bash
bash /opt/veliora/scripts/verify-ovh-vps.sh
```

Sur votre PC, ouvrez le navigateur :

- Vitrine : `http://IP_DU_VPS/`
- CRM : `http://IP_DU_VPS/crm`
- Sante API : `http://IP_DU_VPS/api/health`

---

## Etape 6 — Arreter le worker PC (apres validation VPS)

Sur votre PC Windows, fermez le terminal `run-crawl-worker.ps1` si encore ouvert.

Le VPS fait maintenant **site + veille** tout seul.

---

## Etape 7 — Plus tard : ajouter un domaine (optionnel)

1. Acheter un `.fr` chez OVH (~10 EUR/an)
2. DNS : enregistrement **A** → IP du VPS
3. Sur le VPS :
   ```bash
   sed -i 's|APP_PUBLIC_URL=.*|APP_PUBLIC_URL=https://votredomaine.fr|' /opt/veliora/.env
   certbot --nginx -d votredomaine.fr
   systemctl restart veliora
   ```
4. Mettre a jour webhook Stripe
5. Arreter Scalingo

---

## Depannage rapide

```bash
# Logs en direct
journalctl -u veliora -f

# Redemarrer
systemctl restart veliora

# Playwright (si erreur navigateur)
sudo -u veliora bash -lc 'cd /opt/veliora && source .venv/bin/activate && playwright install-deps chromium'
systemctl restart veliora
```

---

## Checklist finale

- [ ] `http://IP/api/health` repond OK
- [ ] CRM accessible `/crm`
- [ ] Logs : `Veille auto demarree au boot`
- [ ] Logs : proxy `fr.decodo.com:40000`
- [ ] Worker PC arrete
- [ ] (Plus tard) domaine + HTTPS
