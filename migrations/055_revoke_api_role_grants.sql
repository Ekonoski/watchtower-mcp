-- 055: revoke the data-API roles' grants (2026-09-04, the external review:
-- 41 public tables without row-level security, and — checked here — the
-- anon and authenticated roles holding EVERY privilege on all 127 public
-- tables, so anyone with the project's anon key could read or truncate
-- trade_journal, cipher_exemplars, fill_audit, options_expression through
-- the REST API). Nothing Watchtower runs uses those roles: the MCP
-- server, the scheduler and the dashboard all connect to Postgres
-- directly via DATABASE_URL. So the roles get nothing, now and by
-- default for every future table (the review's point: security must be
-- part of creating a table, not remembered per migration). service_role
-- is untouched. Reversible with the matching GRANTs. Applied live via MCP.

REVOKE ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
REVOKE USAGE ON SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
