-- Stop granting anon / authenticated access to NEW objects in the public schema.
--
-- Why: 20251009030427_remote_schema.sql (lines ~1576-1599) set default privileges
-- so that every table, sequence and function the "postgres" role creates in
-- "public" is automatically GRANT ALL to "anon" and "authenticated". The anon
-- (publishable) key ships in the frontend, so any new table that forgot to
-- enable RLS was readable and writable by anyone on the internet. The backend
-- talks to the database with the service/secret key (service_role), which
-- keeps its own explicit default grant and is not affected here.
--
-- Scope:
--   * Mirrors exactly the role the original grant was made for (FOR ROLE
--     "postgres") and the same schema ("public").
--   * Only changes DEFAULT privileges, i.e. objects created AFTER this
--     migration. Grants on existing tables, sequences and functions are NOT
--     touched; tightening those is a separate, table-by-table change.
--   * A new object that really must be reachable with the anon/authenticated
--     key now needs an explicit GRANT (plus RLS) in its own migration.
--
-- Note on functions and PUBLIC: PostgreSQL grants EXECUTE on new functions to
-- PUBLIC through its built-in global default. A per-schema ALTER DEFAULT
-- PRIVILEGES cannot remove a globally granted privilege, so the PUBLIC part of
-- the statement below only cancels a per-schema grant if one exists. New
-- SECURITY DEFINER functions must still REVOKE EXECUTE ... FROM PUBLIC (and
-- anon, authenticated) explicitly, as existing migrations already do.

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public"
    REVOKE ALL ON TABLES FROM "anon", "authenticated";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public"
    REVOKE ALL ON SEQUENCES FROM "anon", "authenticated";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public"
    REVOKE EXECUTE ON FUNCTIONS FROM "anon", "authenticated", PUBLIC;
