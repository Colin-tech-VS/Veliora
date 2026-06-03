-- ============================================================================
-- Veliora — Diagnostic taille DB + purge tables volumineuses
-- ============================================================================
-- Dashboard → SQL Editor → exécuter section par section si besoin.
-- ============================================================================

-- A) Taille par table (identifier ce qui sature les 500 Mo Free)
SELECT
  relname AS table_name,
  pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;

-- B) Comptages rapides
SELECT 'leads' AS tbl, COUNT(*) FROM leads
UNION ALL SELECT 'crawl_logs', COUNT(*) FROM crawl_logs
UNION ALL SELECT 'activities', COUNT(*) FROM activities
UNION ALL SELECT 'lead_price_history', COUNT(*) FROM lead_price_history
UNION ALL SELECT 'dvf_commune_cache', COUNT(*) FROM dvf_commune_cache
UNION ALL SELECT 'geocode_cache', COUNT(*) FROM geocode_cache;

-- C) Purge crawl_logs (1 ligne par URL crawlée — principale cause de gonflement)
--    L'app Veliora garde désormais ~5000 lignes max automatiquement.
-- DELETE FROM crawl_logs;  -- décommenter pour tout vider
DELETE FROM crawl_logs
WHERE id <= (SELECT COALESCE(MAX(id), 0) FROM crawl_logs) - 5000;

-- D) Historique prix ancien (optionnel, > 1 an)
-- DELETE FROM lead_price_history
-- WHERE recorded_at < NOW() - INTERVAL '365 days';

-- E) Sessions / resets expirés (optionnel)
DELETE FROM auth_sessions WHERE expires_at < NOW();
DELETE FROM password_reset_tokens WHERE expires_at < NOW() OR used = 1;

-- F) Récupérer de l'espace disque logique
VACUUM (ANALYZE);

-- G) Taille totale base (doit rester < 500 Mo sur plan Free)
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
