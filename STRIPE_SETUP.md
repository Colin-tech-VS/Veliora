# Configuration Stripe — Veliora (500 €/mois)

## 1. Fichier `.env`

```bash
copy .env.example .env
```

Renseignez au minimum :

| Variable | Où la trouver |
|----------|----------------|
| `STRIPE_SECRET_KEY` | Dashboard → Developers → API keys → Secret key (`sk_test_…`) |
| `STRIPE_PUBLISHABLE_KEY` | Même page → Publishable key (`pk_test_…`) |
| `STRIPE_WEBHOOK_SECRET` | Developers → Webhooks → votre endpoint → Signing secret (`whsec_…`) |
| `STRIPE_PRICE_ID` | Produits → créer « Veliora agence » 500 € récurrent / mois → copier `price_…` |
| `APP_PUBLIC_URL` | URL publique (`http://localhost:8000` en local) |

En **développement sans Stripe** : laissez `STRIPE_SECRET_KEY` vide ou mettez `STRIPE_REQUIRE_PAYMENT=false` — le CRM reste accessible sans paiement.

## 2. Produit Stripe (recommandé)

1. [Dashboard Stripe](https://dashboard.stripe.com/) → **Produits** → **Ajouter un produit**
2. Nom : `Veliora — Abonnement agence`
3. Prix : **500 EUR**, récurrent **mensuel**
4. Copiez l’**ID du prix** (`price_…`) dans `STRIPE_PRICE_ID`

Sans `STRIPE_PRICE_ID`, Veliora crée le prix à la volée (pratique en test uniquement).

## 3. Webhook

### Production

URL : `https://votredomaine.fr/api/billing/webhook`

Événements à activer :

- `checkout.session.completed`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`

### Local (Stripe CLI)

```bash
stripe login
stripe listen --forward-to localhost:8000/api/billing/webhook
```

Copiez le `whsec_…` affiché dans `STRIPE_WEBHOOK_SECRET`.

## 4. Parcours agence

1. Inscription sur `/crm/auth?tab=register`
2. Redirection **Stripe Checkout** (carte / SEPA)
3. Retour `?checkout=success` → activation abonnement
4. Accès CRM `/crm`

Connexion sans abonnement actif → nouvelle redirection vers le paiement.

**Portail client** (factures, carte) : bouton « Gérer mon abonnement » sur la page auth (admin).

## 5. Dépendances

```bash
pip install -r requirements.txt
```

Puis relancez `python app.py` ou `demarrer.bat`.
