-- =============================================================================
-- VELIORA — Script unique Supabase (SQL Editor → coller tout → Run)
-- =============================================================================
-- Projet : Veliora
-- Idempotent : CREATE IF NOT EXISTS, pas de DROP — safe sur base déjà en prod.
--
-- Fait en une fois :
--   1. Schéma tables Veliora (si manquantes)
--   2. Tables IA + géocodage
--   3. Sécurité : RLS + blocage API anon/authenticated (fini « Unrestricted »)
--   4. Maintenance : purge crawl_logs, sessions expirées, VACUUM
--   5. Rapport taille base
--
-- Veliora (Scalingo) utilise DATABASE_URL → rôle postgres → continue à fonctionner.
--
-- IMPORTANT — éviter le deadlock 40P01 :
--   Arrêtez Veliora sur Scalingo (scale 0 ou stop) pendant 2 minutes, puis Run.
--   Si erreur deadlock : ne pas tout relancer — utiliser supabase_rls_seul.sql
--   puis supabase_maintenance_seul.sql (app toujours arrêtée).
--
-- Storage (images fichiers) : PAS géré ici. Vider les buckets dans
-- Dashboard → Storage si alerte « image / storage ».
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- ÉTAPE 1 — Extension + schéma principal
-- ─────────────────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

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

CREATE TABLE IF NOT EXISTS lead_estimates (
    lead_id    INTEGER NOT NULL,
    agency_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, agency_id)
);

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

CREATE TABLE IF NOT EXISTS lead_features (
    lead_id    INTEGER NOT NULL,
    agency_id  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (lead_id, agency_id)
);

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

CREATE TABLE IF NOT EXISTS activities (
    id              BIGSERIAL PRIMARY KEY,
    type            TEXT NOT NULL,
    text            TEXT NOT NULL,
    agency_id       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

-- ─────────────────────────────────────────────────────────────────────────────
-- ÉTAPE 2 — Tables complémentaires (IA, carte)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS geocode_cache (
    cache_key         TEXT PRIMARY KEY,
    latitude          DOUBLE PRECISION NOT NULL,
    longitude         DOUBLE PRECISION NOT NULL,
    formatted_address TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id          TEXT PRIMARY KEY,
    agency_id   TEXT NOT NULL,
    user_id     TEXT,
    title       TEXT NOT NULL DEFAULT 'Nouvelle conversation',
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    meta_json       TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_memories (
    id          TEXT PRIMARY KEY,
    agency_id   TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'general',
    content     TEXT NOT NULL,
    source      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_messages_conv ON ai_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_agency ON ai_conversations(agency_id, updated_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- ÉTAPE 3 — Sécurité RLS (requêtes séparées — moins de deadlock qu’une boucle)
-- App Veliora ARRÊTÉE avant cette section.
-- ─────────────────────────────────────────────────────────────────────────────
SET lock_timeout = '45s';

ALTER TABLE IF EXISTS public.sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.lead_estimates ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.lead_address_matches ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.lead_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.crawl_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.crawl_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agencies ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agency_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.auth_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agency_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.lead_price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.lead_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agency_scoring_weights ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.dvf_commune_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agency_legal_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.seller_mandates ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.property_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.mandate_dossiers ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.geocode_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.ai_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.ai_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.ai_memories ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM anon, authenticated;

-- ─────────────────────────────────────────────────────────────────────────────
-- ÉTAPE 4 — Maintenance (app arrêtée de préférence)
-- Si deadlock ici : ignorer et lancer scripts/supabase_maintenance_seul.sql
-- ─────────────────────────────────────────────────────────────────────────────
DELETE FROM crawl_logs
WHERE id <= (SELECT COALESCE(MAX(id), 0) FROM crawl_logs) - 5000;

DELETE FROM auth_sessions WHERE expires_at < NOW();
DELETE FROM password_reset_tokens WHERE expires_at < NOW() OR used = 1;

-- VACUUM peut attendre un peu (verrou fort) — décommenter si besoin :
-- VACUUM (ANALYZE);

-- ─────────────────────────────────────────────────────────────────────────────
-- ÉTAPE 5 — Rapport (résultats dans l’onglet Results)
-- ─────────────────────────────────────────────────────────────────────────────
SELECT '=== TAILLE PAR TABLE ===' AS rapport;

SELECT
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

SELECT '=== COMPTAGES ===' AS rapport;

SELECT 'leads' AS tbl, COUNT(*)::bigint AS n FROM leads
UNION ALL SELECT 'crawl_logs', COUNT(*) FROM crawl_logs
UNION ALL SELECT 'activities', COUNT(*) FROM activities
UNION ALL SELECT 'lead_price_history', COUNT(*) FROM lead_price_history
UNION ALL SELECT 'dvf_commune_cache', COUNT(*) FROM dvf_commune_cache
UNION ALL SELECT 'geocode_cache', COUNT(*) FROM geocode_cache
UNION ALL SELECT 'ai_messages', COUNT(*) FROM ai_messages;

SELECT '=== RLS (true = sécurisé) ===' AS rapport;

SELECT tablename, rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

SELECT '=== TAILLE TOTALE BASE ===' AS rapport;

SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;

-- Fin — Free : viser < 400 Mo | Pro : 8 Go
-- Vérifier Veliora : ouvrir l’app, liste des leads + connexion OK.
