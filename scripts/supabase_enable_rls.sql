-- ============================================================================
-- Veliora — Sécuriser Supabase : RLS + révoquer l'accès API anon/authenticated
-- ============================================================================
--
-- Symptôme dashboard : tables marquées « Unrestricted ».
-- Risque : clé anon + API REST PostgREST → lecture/écriture de TOUTES les données.
--
-- Veliora se connecte en Postgres (pooler, rôle postgres) → BYPASSRLS → l'app
-- continue de fonctionner après ce script.
--
-- À exécuter : Dashboard → SQL Editor → Run (une fois par projet).
-- ============================================================================

-- 1) Row Level Security sur toutes les tables public
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT tablename FROM pg_tables WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', r.tablename);
  END LOOP;
END $$;

-- 2) Retirer les droits sur les tables EXISTANTES (anon / authenticated)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM anon, authenticated;

-- 2 bis) Verrouiller aussi les FUTURES tables/séquences créées par l'app
--        (rôle postgres) : plus aucune nouvelle table ne ré-ouvre l'API.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- Note « RLS Enabled No Policy » (entrées sans badge Critical) : c'est l'état
-- VOULU et sûr ici. Veliora n'utilise pas l'API PostgREST ; RLS activé + aucun
-- policy = table fermée à anon/authenticated, accessible seulement à l'app
-- (rôle propriétaire). N'AJOUTEZ PAS de policy permissive (ré-ouvrirait l'API).

-- 3) Storage : si vous utilisez des buckets Supabase, activer RLS côté Storage
--    (Dashboard → Storage → chaque bucket → Policies, ou laisser buckets vides).
--    Veliora n'y stocke PAS les photos d'annonces (fichiers locaux / Scalingo).

-- 4) Vérification
SELECT tablename, rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
