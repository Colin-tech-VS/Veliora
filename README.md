# Veliora

**Priorité mandat pour agences immobilières** — qui appeler en premier pour signer un mandat avant les autres.

## En une phrase

Veliora détecte les opportunités sur vos portails, calcule un **score mandat expliqué** (0–100), compare au **marché DVF**, et livre chaque matin une **liste d’appels prioritaires**.

## Offre commerciale

| | |
|---|---|
| **Prix** | 500 € HT / mois / agence (TVA en sus) |
| **Page offre** | [/offre](https://veliora.fr/offre) — tout inclus, limites, feuille de route |
| **Vitrine** | `/` |
| **Inscription** | `/crm/auth?tab=register` |

## Démarrage développeur

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
python app.py
```

Ouvrir http://localhost:8000

### App web (PWA)

- Ouvrez **http://localhost:8000/crm** (de préférence pas Live Server seul).
- **Installer l’app** (bannière Sources ou menu navigateur) pour une expérience plein écran.
- Lancez un crawl puis **⤓ Arrière-plan** : vous pouvez changer d’onglet ou fermer l’app — le crawl continue **sur le serveur**.
- Autorisez les **notifications** pour être alerté à la fin du crawl.

Voir aussi : `STRIPE_SETUP.md`, `DATA.md`, `SCALINGO.md`, `SUPABASE.md`

## Déploiement Scalingo

- Branche **`main`** sur le dépôt GitHub **Veliora**
- `Procfile` + `wsgi.py` (Gunicorn)
- Config : `scalingo.json` — volume `/var/lib/data` pour SQLite persistant
- Guide complet : **[SCALINGO.md](SCALINGO.md)**

```bash
scalingo create Veliora --region osc-fr1
scalingo --app veliora env-set APP_PUBLIC_URL=https://votre-app.scalingo.io
git push scalingo main
```

## Modules principaux

| Module | Rôle |
|--------|------|
| `crawler/` | Veille Playwright multi-portails |
| `crm/radar.py` | Score mandat + briefing matinal |
| `crm/dvf.py` | Comparatif prix vs ventes DVF |
| `crm/mandates/` | Mandats vente / location |
| `crm/billing/` | Abonnement Stripe |
| `vitrine/` | Site marketing |

## Configuration `.env` importante

- `LEGAL_*` — mentions légales (injectées sur `/legal`)
- `STRIPE_*` — paiement
- `SMTP_*` — emails (mandats, reset password)
- `SITE_URL` — sitemap / canonical

## Ce que Veliora n’est pas (encore)

- CRM complet / sync Hektor
- Signature électronique intégrée
- Matching acheteurs ↔ annonces
- IA générative (le score = règles métier transparentes)

Feuille de route détaillée : page **/offre**.
