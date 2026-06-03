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

-- 2) Retirer les droits par défaut sur les tables (anon / authenticated)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL ROUTINES IN SCHEMA public FROM anon, authenticated;

-- 3) Storage : si vous utilisez des buckets Supabase, activer RLS côté Storage
--    (Dashboard → Storage → chaque bucket → Policies, ou laisser buckets vides).
--    Veliora n'y stocke PAS les photos d'annonces (fichiers locaux / Scalingo).

-- 4) Vérification
SELECT tablename, rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
