-- Allow the backend service role to trigger a PostgREST schema cache reload via RPC.

DROP FUNCTION IF EXISTS public.refresh_postgrest_schema_cache();

CREATE FUNCTION public.refresh_postgrest_schema_cache()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    PERFORM pg_notify('pgrst', 'reload schema');
END;
$$;

REVOKE ALL ON FUNCTION public.refresh_postgrest_schema_cache() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refresh_postgrest_schema_cache() TO service_role;

COMMENT ON FUNCTION public.refresh_postgrest_schema_cache() IS
    'Utility RPC that forces PostgREST to refresh its schema cache when the backend detects stale metadata.';
