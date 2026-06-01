-- ============================================================================
-- Veliora — Activer la Row Level Security (RLS) sur toutes les tables publiques
-- ============================================================================
--
-- Pourquoi : dans le dashboard Supabase, une table sans RLS est marquée
-- « Unrestricted ». Si l'API REST/GraphQL auto-générée (PostgREST) est
-- joignable avec la clé `anon`, N'IMPORTE QUI peut alors lire/écrire toutes
-- les données (leads, contacts, etc.). Activer la RLS sans politique = refus
-- par défaut pour anon/authenticated → la fuite est fermée.
--
-- Pourquoi c'est SANS RISQUE pour Veliora :
-- L'app ne passe PAS par l'API anon : elle se connecte en direct au Postgres
-- via le pooler Supabase avec le rôle privilégié (postgres / service_role).
-- Ce rôle CONTOURNE la RLS (BYPASSRLS + propriétaire des tables). On active
-- donc ENABLE (et non FORCE) : le propriétaire et service_role gardent l'accès
-- complet, seuls anon/authenticated sont bloqués.
--
-- Comment l'appliquer :
--   Supabase Dashboard → SQL Editor → coller ce script → Run.
--   (Le SQL Editor se connecte en postgres, donc l'app continue de fonctionner.)
--
-- Vérifier ensuite : Dashboard → Table Editor → les tables ne doivent plus
-- afficher « Unrestricted ». Et l'app Veliora doit continuer à lister les leads.
-- ============================================================================

DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT tablename
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', r.tablename);
  END LOOP;
END $$;

-- Contrôle : liste les tables publiques et leur statut RLS (rowsecurity = true attendu).
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;
