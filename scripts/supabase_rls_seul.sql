-- =============================================================================
-- Veliora — Activer la RLS SEULEMENT (à lancer après un deadlock ou en 2e passe)
-- =============================================================================
--
-- AVANT DE LANCER :
--   1. Scalingo → arrêter l’app Veliora (ou scale à 0) 1–2 min
--   2. Ne pas lancer de crawl en cours
--   3. SQL Editor → coller ce fichier → Run
--
-- Pas de boucle PL/pgSQL (évite deadlock avec l’app).
-- Pas de DELETE / VACUUM ici.
-- =============================================================================

SET lock_timeout = '45s';
SET statement_timeout = '120s';

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

SELECT tablename, rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
