-- =============================================================================
-- Veliora — Maintenance SEULE (purge + VACUUM) — lancer avec app arrêtée ou calme
-- =============================================================================

SET lock_timeout = '60s';

DELETE FROM crawl_logs
WHERE id <= (SELECT COALESCE(MAX(id), 0) FROM crawl_logs) - 5000;

DELETE FROM auth_sessions WHERE expires_at < NOW();
DELETE FROM password_reset_tokens WHERE expires_at < NOW() OR used = 1;

VACUUM (ANALYZE);

SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;

SELECT relname AS table_name, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 15;
