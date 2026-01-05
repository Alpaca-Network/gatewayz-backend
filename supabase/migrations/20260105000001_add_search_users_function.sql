-- Migration: Add PostgreSQL function for searching users by email
-- Created: 2026-01-05
-- Purpose: Bypass PostgREST edge function limitations by using native PostgreSQL function

-- Drop function if it exists (for re-running migration)
DROP FUNCTION IF EXISTS search_users_by_email(TEXT, INTEGER, INTEGER);

-- Create function to search users by email with pagination
CREATE OR REPLACE FUNCTION search_users_by_email(
    search_term TEXT,
    result_limit INTEGER DEFAULT 100,
    result_offset INTEGER DEFAULT 0
)
RETURNS TABLE (
    id INTEGER,
    username TEXT,
    email TEXT,
    credits NUMERIC,
    is_active BOOLEAN,
    role TEXT,
    registration_date TIMESTAMP WITH TIME ZONE,
    auth_method TEXT,
    subscription_status TEXT,
    trial_expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE,
    total_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    WITH counted AS (
        SELECT COUNT(*) as total
        FROM users
        WHERE email ILIKE '%' || search_term || '%'
    )
    SELECT
        u.id,
        u.username,
        u.email,
        u.credits,
        u.is_active,
        u.role,
        u.registration_date,
        u.auth_method,
        u.subscription_status,
        u.trial_expires_at,
        u.created_at,
        u.updated_at,
        c.total as total_count
    FROM users u
    CROSS JOIN counted c
    WHERE u.email ILIKE '%' || search_term || '%'
    ORDER BY u.created_at DESC
    LIMIT result_limit
    OFFSET result_offset;
END;
$$ LANGUAGE plpgsql STABLE;

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION search_users_by_email(TEXT, INTEGER, INTEGER) TO anon;
