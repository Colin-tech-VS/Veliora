-- Veliora — schéma PostgreSQL pour Supabase (projet « Veliora »)
-- SQL Editor Supabase → New query → Run
--
-- Ensuite (obligatoire) : scripts/supabase_enable_rls.sql
-- (sinon tables « Unrestricted » dans le dashboard).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Sources
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    search_url      TEXT,
    enabled         SMALLINT NOT NULL DEFAULT 1,
    found_total     INTEGER NOT NULL DEFAULT 0,
    found_today     INTEGER NOT NULL DEFAULT 0,
    last_scan       TIMESTAMPTZ,
    last_error      TEXT,
    is_custom       SMALLINT NOT NULL DEFAULT 0,
    agency_id       TEXT,
    logo_url        TEXT,
    logo_fallback   TEXT,
    domain          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prospects
CREATE TABLE IF NOT EXISTS leads (
    id              BIGSERIAL PRIMARY KEY,
    first_name      TEXT,
    last_name       TEXT,
    phone           TEXT,
    email           TEXT,
    address         TEXT,
    city            TEXT,
    postcode        TEXT,
    sector          TEXT,
    surface         DOUBLE PRECISION,
    price           INTEGER,
    transaction_type TEXT NOT NULL DEFAULT 'vente',
    price_period    TEXT,
    published_at    TEXT,
    source          TEXT,
    source_id       TEXT REFERENCES sources(id),
    source_url      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'nouveau',
    pipeline        TEXT NOT NULL DEFAULT 'nouveau',
    listing_type    TEXT NOT NULL DEFAULT 'particulier',
    type            TEXT DEFAULT 'particulier',
    agency          TEXT,
    agency_id       TEXT,
    score           INTEGER NOT NULL DEFAULT 0,
    mandate_score   INTEGER NOT NULL DEFAULT 0,
    mandate_score_reason TEXT,
    previous_price  INTEGER,
    notes           TEXT,
    next_follow_up  TIMESTAMPTZ,
    dvf_median_m2   INTEGER,
    dvf_delta_pct   DOUBLE PRECISION,
    dvf_verdict     TEXT,
    dvf_verdict_label TEXT,
    dvf_commune     TEXT,
    dvf_sample_count INTEGER,
    dvf_compared_at TIMESTAMPTZ,
    dvf_sector      TEXT,
    dvf_reference_period TEXT,
    listing_title   TEXT,
    facts_audit     TEXT,
    price_change_count INTEGER DEFAULT 0,
    last_price_change_at TIMESTAMPTZ,
    priority_tier   TEXT,
    score_explanation TEXT,
    scores_computed_at TIMESTAMPTZ,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    listing_image_url TEXT,
    image_custom    SMALLINT NOT NULL DEFAULT 0,
    image_updated_at TIMESTAMPTZ,
    missing_fields  TEXT NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_agency_url ON leads(agency_id, source_url);
CREATE INDEX IF NOT EXISTS idx_leads_source_id ON leads(source_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_id ON leads(agency_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
-- La liste CRM trie systématiquement par date de création décroissante
-- (get_leads : « ORDER BY created_at DESC »). Sans index, Postgres trie tout
-- le pool partagé à chaque chargement -> lenteur croissante avec le volume.
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON leads(created_at DESC);

-- Estimations de prix : table dédiée (pas de colonne sur leads -> pas de verrou
-- ACCESS EXCLUSIVE en conflit avec les UPDATE leads du crawl/géocodage).
CREATE TABLE IF NOT EXISTS lead_estimates (
    lead_id    INTEGER NOT NULL,
    agency_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, agency_id)
);

-- Galerie photos d'une annonce (1 ligne par image). Les fichiers WebP nettoyés
-- (marquages iad/Orpi retirés) vivent sur disque ; cette table garde l'ordre et
-- l'URL source pour re-synchronisation.
CREATE TABLE IF NOT EXISTS lead_images (
    agency_id         TEXT NOT NULL,
    lead_id           INTEGER NOT NULL,
    position          INTEGER NOT NULL,
    source_url        TEXT,
    watermark_removed SMALLINT NOT NULL DEFAULT 0,
    created_at        TEXT,
    PRIMARY KEY (agency_id, lead_id, position)
);

-- Rapprochement d'adresse (DPE/DVF/cadastre/BAN) : table dédiée, même logique
-- anti-verrou que lead_estimates. Le détail (candidats + raisons) vit en JSON ;
-- probable_address / confidence sont dénormalisés pour tri & filtre rapides.
CREATE TABLE IF NOT EXISTS lead_address_matches (
    lead_id          INTEGER NOT NULL,
    agency_id        TEXT NOT NULL,
    probable_address TEXT,
    confidence       INTEGER NOT NULL DEFAULT 0,
    payload          TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (lead_id, agency_id)
);

CREATE INDEX IF NOT EXISTS idx_address_matches_agency
    ON lead_address_matches(agency_id, confidence DESC);

-- Caractéristiques structurées extraites de l'annonce (DPE, pièces, année,
-- équipements…), alimentées par tous les crawlers et consommées par le matching.
CREATE TABLE IF NOT EXISTS lead_features (
    lead_id    INTEGER NOT NULL,
    agency_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, agency_id)
);

-- Crawl jobs
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id              TEXT PRIMARY KEY,
    source_id       TEXT REFERENCES sources(id),
    target_url      TEXT NOT NULL DEFAULT '',
    job_type        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    progress        INTEGER NOT NULL DEFAULT 0,
    leads_found     INTEGER NOT NULL DEFAULT 0,
    leads_saved     INTEGER NOT NULL DEFAULT 0,
    leads_updated   INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '[]',
    warnings        TEXT NOT NULL DEFAULT '[]',
    message         TEXT,
    city            TEXT,
    eta_seconds     INTEGER,
    listings_total  INTEGER,
    listings_done   INTEGER,
    agency_id       TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_agency ON crawl_jobs(agency_id);

CREATE TABLE IF NOT EXISTS crawl_lead_changes (
    id              BIGSERIAL PRIMARY KEY,
    job_id          TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    lead_id         BIGINT NOT NULL,
    change_type     TEXT NOT NULL,
    summary         TEXT NOT NULL,
    details_json    TEXT NOT NULL DEFAULT '[]',
    source_name     TEXT,
    listing_url     TEXT,
    owner_label     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_lead_changes_agency ON crawl_lead_changes(agency_id, created_at DESC);

-- Activité
CREATE TABLE IF NOT EXISTS activities (
    id              BIGSERIAL PRIMARY KEY,
    type            TEXT NOT NULL,
    text            TEXT NOT NULL,
    agency_id       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Logs crawl
CREATE TABLE IF NOT EXISTS crawl_logs (
    id              BIGSERIAL PRIMARY KEY,
    job_id          TEXT,
    source_id       TEXT,
    url             TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    message         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_crawl_logs_job_id ON crawl_logs(job_id);

-- Agences & auth
CREATE TABLE IF NOT EXISTS agencies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    subscription_status TEXT NOT NULL DEFAULT 'active',
    subscription_current_period_end TIMESTAMPTZ,
    subscription_plan TEXT DEFAULT 'veliora_pro',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agency_users (
    id              TEXT PRIMARY KEY,
    agency_id       TEXT NOT NULL REFERENCES agencies(id),
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'collaborator',
    first_name      TEXT,
    last_name       TEXT,
    active          SMALLINT NOT NULL DEFAULT 1,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agency_users_agency ON agency_users(agency_id);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    email           TEXT NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    used            SMALLINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agency_settings (
    agency_id           TEXT PRIMARY KEY REFERENCES agencies(id),
    target_cities       TEXT NOT NULL DEFAULT '[]',
    target_neighborhoods TEXT NOT NULL DEFAULT '[]',
    mandate_goal_month  INTEGER NOT NULL DEFAULT 5,
    onboarding_step     INTEGER NOT NULL DEFAULT 0,
    onboarding_completed SMALLINT NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ
);

-- Scoring & DVF
CREATE TABLE IF NOT EXISTS lead_price_history (
    id              BIGSERIAL PRIMARY KEY,
    lead_id         BIGINT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    agency_id       TEXT NOT NULL,
    price           INTEGER NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_history_lead ON lead_price_history(lead_id, agency_id);

CREATE TABLE IF NOT EXISTS lead_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    lead_id         BIGINT NOT NULL,
    agency_id       TEXT NOT NULL,
    outcome_type    TEXT NOT NULL,
    outcome_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id        TEXT,
    notes           TEXT,
    scores_snapshot TEXT
);

CREATE INDEX IF NOT EXISTS idx_lead_outcomes_lead ON lead_outcomes(lead_id, agency_id, outcome_at DESC);

CREATE TABLE IF NOT EXISTS agency_scoring_weights (
    agency_id       TEXT PRIMARY KEY,
    weights_json    TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dvf_commune_cache (
    cache_key       TEXT PRIMARY KEY,
    payload         TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Mandats
CREATE TABLE IF NOT EXISTS agency_legal_profiles (
    agency_id       TEXT PRIMARY KEY,
    profile_json    TEXT NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS seller_mandates (
    id              TEXT PRIMARY KEY,
    agency_id       TEXT NOT NULL,
    lead_id         BIGINT,
    mandate_type    TEXT NOT NULL,
    exclusivity     TEXT NOT NULL DEFAULT 'exclusif',
    status          TEXT NOT NULL DEFAULT 'draft',
    title           TEXT NOT NULL,
    fields_json     TEXT NOT NULL DEFAULT '{}',
    body_html       TEXT,
    recipient_email TEXT,
    sent_at         TIMESTAMPTZ,
    signed_at       TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_seller_mandates_agency ON seller_mandates(agency_id);

CREATE TABLE IF NOT EXISTS property_clients (
    id              TEXT PRIMARY KEY,
    agency_id       TEXT NOT NULL,
    segment         TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    phone           TEXT,
    email           TEXT,
    budget_min      INTEGER,
    budget_max      INTEGER,
    property_type   TEXT,
    rooms_min       INTEGER,
    surface_min     DOUBLE PRECISION,
    cities_json     TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'actif',
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_clients_agency ON property_clients(agency_id, segment);

CREATE TABLE IF NOT EXISTS mandate_dossiers (
    id              TEXT PRIMARY KEY,
    agency_id       TEXT NOT NULL,
    mandate_id      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    property_address TEXT,
    postal_code     TEXT,
    city            TEXT,
    surface         DOUBLE PRECISION,
    rooms           TEXT,
    price           INTEGER,
    property_type   TEXT,
    photos_json     TEXT NOT NULL DEFAULT '[]',
    linked_clients_json TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'actif',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mandate_dossiers_mandate ON mandate_dossiers(mandate_id, agency_id);
