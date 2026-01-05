-- Migration: Add PostgreSQL function for searching user by exact API key
-- Created: 2026-01-05
-- Purpose: Simple function for exact API key lookup (no partial matching)

-- Drop function if it exists (for re-running migration)
DROP FUNCTION IF EXISTS search_user_by_api_key(TEXT);

-- Create function to search user by exact API key match
CREATE OR REPLACE FUNCTION search_user_by_api_key(
    search_api_key TEXT
)
RETURNS TABLE (
    user_id BIGINT,
    username TEXT,
    email TEXT,
    credits NUMERIC,
    is_active BOOLEAN,
    role TEXT,
    subscription_status TEXT,
    created_at TIMESTAMP WITH TIME ZONE,
    key_id BIGINT,
    api_key TEXT,
    key_name TEXT,
    environment_tag TEXT,
    is_primary BOOLEAN,
    key_is_active BOOLEAN,
    key_created_at TIMESTAMP WITH TIME ZONE,
    source TEXT
) AS $$
BEGIN
    RETURN QUERY
    -- Search in api_keys_new table first
    SELECT
        u.id::BIGINT as user_id,
        u.username::TEXT,
        u.email::TEXT,
        u.credits::NUMERIC,
        u.is_active::BOOLEAN,
        u.role::TEXT,
        u.subscription_status::TEXT,
        u.created_at::TIMESTAMP WITH TIME ZONE,
        ak.id::BIGINT as key_id,
        ak.api_key::TEXT,
        ak.key_name::TEXT,
        ak.environment_tag::TEXT,
        ak.is_primary::BOOLEAN,
        ak.is_active::BOOLEAN as key_is_active,
        ak.created_at::TIMESTAMP WITH TIME ZONE as key_created_at,
        'api_keys_new'::TEXT as source
    FROM api_keys_new ak
    JOIN users u ON ak.user_id = u.id
    WHERE ak.api_key = search_api_key

    UNION ALL

    -- Fallback: Search in legacy users.api_key column
    SELECT
        u.id::BIGINT as user_id,
        u.username::TEXT,
        u.email::TEXT,
        u.credits::NUMERIC,
        u.is_active::BOOLEAN,
        u.role::TEXT,
        u.subscription_status::TEXT,
        u.created_at::TIMESTAMP WITH TIME ZONE,
        NULL::BIGINT as key_id,
        u.api_key::TEXT,
        'Legacy Primary Key'::TEXT as key_name,
        'live'::TEXT as environment_tag,
        true::BOOLEAN as is_primary,
        true::BOOLEAN as key_is_active,
        u.created_at::TIMESTAMP WITH TIME ZONE as key_created_at,
        'users.api_key (legacy)'::TEXT as source
    FROM users u
    WHERE u.api_key = search_api_key
      -- Avoid duplicates if key exists in both places
      AND NOT EXISTS (
          SELECT 1 FROM api_keys_new ak
          WHERE ak.api_key = search_api_key
      );
END;
$$ LANGUAGE plpgsql STABLE;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION search_user_by_api_key(TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION search_user_by_api_key(TEXT) TO anon;
