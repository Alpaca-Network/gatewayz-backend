-- Expose a lightweight RPC function that lets the application trigger a PostgREST
-- schema cache refresh when new columns (like key_version) are added.

CREATE OR REPLACE FUNCTION public.refresh_postgrest_schema_cache()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM pg_notify('pgrst', 'reload schema');
END;
$$;

COMMENT ON FUNCTION public.refresh_postgrest_schema_cache()
IS 'Notifies PostgREST to reload its schema cache so new columns are immediately available.';

GRANT EXECUTE ON FUNCTION public.refresh_postgrest_schema_cache() TO service_role;
