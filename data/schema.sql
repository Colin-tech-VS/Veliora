-- Veliora — Schéma Supabase-ready
-- SQLite local utilise la même structure (types adaptés).
-- Migration Supabase : remplacer INTEGER id par UUID DEFAULT gen_random_uuid()

-- Sources de crawl
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    base_url        TEXT NOT NULL,
    search_url      TEXT,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    found_total     INTEGER NOT NULL DEFAULT 0,
    found_today     INTEGER NOT NULL DEFAULT 0,
    last_scan       TIMESTAMPTZ,
    last_error      TEXT,
    is_custom       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Prospects / leads
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY,  -- Supabase: UUID
    first_name      TEXT,
    last_name       TEXT,
    phone           TEXT,
    email           TEXT,
    address         TEXT,
    surface         REAL,
    price           INTEGER,
    transaction_type TEXT NOT NULL DEFAULT 'vente',
    price_period    TEXT,
    published_at    TEXT,
    agency_id       TEXT,
    source          TEXT,
    source_id       TEXT REFERENCES sources(id),
    source_url      TEXT UNIQUE NOT NULL,
    status          TEXT NOT NULL DEFAULT 'nouveau',
    pipeline        TEXT NOT NULL DEFAULT 'nouveau',
    listing_type    TEXT NOT NULL DEFAULT 'particulier',
    agency          TEXT,
    score           INTEGER NOT NULL DEFAULT 0,
    mandate_score   INTEGER NOT NULL DEFAULT 0,
    mandate_score_reason TEXT,
    previous_price  INTEGER,
    notes           TEXT,
    next_follow_up  TIMESTAMPTZ,
    dvf_median_m2   INTEGER,
    dvf_delta_pct   REAL,
    dvf_verdict     TEXT,
    dvf_verdict_label TEXT,
    dvf_commune     TEXT,
    dvf_sample_count INTEGER,
    dvf_compared_at TIMESTAMPTZ,
    missing_fields  JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Jobs de crawl (async)
CREATE TABLE IF NOT EXISTS crawl_jobs (
    id              TEXT PRIMARY KEY,  -- UUID
    source_id       TEXT REFERENCES sources(id),
    target_url      TEXT NOT NULL,
    job_type        TEXT NOT NULL,  -- single_source | all_sources | url
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | failed
    progress        INTEGER NOT NULL DEFAULT 0,
    leads_found     INTEGER NOT NULL DEFAULT 0,
    leads_saved     INTEGER NOT NULL DEFAULT 0,
    errors          JSONB NOT NULL DEFAULT '[]',
    warnings        JSONB NOT NULL DEFAULT '[]',
    message         TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Activité CRM
CREATE TABLE IF NOT EXISTS activities (
    id              INTEGER PRIMARY KEY,
    type            TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Logs détaillés par URL crawlée
CREATE TABLE IF NOT EXISTS crawl_logs (
    id              INTEGER PRIMARY KEY,
    job_id          TEXT REFERENCES crawl_jobs(id),
    source_id       TEXT,
    url             TEXT NOT NULL,
    status          TEXT NOT NULL,
    message         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_source_id ON leads(source_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_crawl_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_crawl_logs_job_id ON crawl_logs(job_id);

-- Multi-agences (préparation auth)
CREATE TABLE IF NOT EXISTS agencies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
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
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token           TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    agency_id       TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agency_settings (
    agency_id           TEXT PRIMARY KEY REFERENCES agencies(id),
    target_cities       JSONB NOT NULL DEFAULT '[]',
    target_neighborhoods JSONB NOT NULL DEFAULT '[]',
    mandate_goal_month  INTEGER NOT NULL DEFAULT 5,
    updated_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS lead_price_history (
    id              INTEGER PRIMARY KEY,
    lead_id         INTEGER NOT NULL REFERENCES leads(id),
    agency_id       TEXT NOT NULL,
    price           INTEGER NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lead_outcomes (
    id              INTEGER PRIMARY KEY,
    lead_id         INTEGER NOT NULL,
    agency_id       TEXT NOT NULL,
    outcome_type    TEXT NOT NULL,
    outcome_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id        TEXT,
    notes           TEXT,
    scores_snapshot JSONB
);

CREATE TABLE IF NOT EXISTS agency_scoring_weights (
    agency_id       TEXT PRIMARY KEY,
    weights_json    JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);

-- leads: price_change_count, last_price_change_at, priority_tier, score_explanation, scores_computed_at

-- crawl_jobs extensions: city, eta_seconds, listings_total, listings_done, leads_updated
