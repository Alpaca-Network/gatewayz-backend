-- Add database-level constraint to prevent duplicate trial grants.
--
-- Problem: Trial eligibility is checked only at the service layer
-- (trial_service.py, partner_trial_service.py). Concurrent requests can both
-- pass the check and grant duplicate trial credits.
--
-- Solution: Create a trial_grants table with UNIQUE(user_id) so the database
-- itself rejects any second trial grant for the same user. A helper function
-- wraps the INSERT so both regular and partner trial flows can call it.

-- 1. Create the trial_grants audit table
CREATE TABLE IF NOT EXISTS trial_grants (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    api_key_id BIGINT REFERENCES api_keys_new(id) ON DELETE SET NULL,
    grant_type VARCHAR(50) NOT NULL DEFAULT 'standard',
    partner_code VARCHAR(50),
    trial_credits DECIMAL(10,2),
    trial_duration_days INTEGER,
    granted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- The critical constraint: one trial per user, enforced at the DB level
    CONSTRAINT uq_trial_grants_user_id UNIQUE (user_id)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_trial_grants_granted_at
    ON trial_grants(granted_at DESC);
CREATE INDEX IF NOT EXISTS idx_trial_grants_grant_type
    ON trial_grants(grant_type);
CREATE INDEX IF NOT EXISTS idx_trial_grants_partner_code
    ON trial_grants(partner_code) WHERE partner_code IS NOT NULL;

-- Comments
COMMENT ON TABLE trial_grants IS
    'Tracks trial grants with a unique constraint on user_id to prevent '
    'duplicate trials at the database level.';
COMMENT ON CONSTRAINT uq_trial_grants_user_id ON trial_grants IS
    'Ensures each user can only receive one trial grant, preventing race conditions.';

-- 2. Create an atomic helper function that records the grant and fails
--    with a clear error on duplicates.
CREATE OR REPLACE FUNCTION record_trial_grant(
    p_user_id BIGINT,
    p_api_key_id BIGINT DEFAULT NULL,
    p_grant_type VARCHAR DEFAULT 'standard',
    p_partner_code VARCHAR DEFAULT NULL,
    p_trial_credits DECIMAL DEFAULT NULL,
    p_trial_duration_days INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    INSERT INTO trial_grants (
        user_id, api_key_id, grant_type, partner_code,
        trial_credits, trial_duration_days
    ) VALUES (
        p_user_id, p_api_key_id, p_grant_type, p_partner_code,
        p_trial_credits, p_trial_duration_days
    );

    RETURN jsonb_build_object(
        'success', true,
        'user_id', p_user_id,
        'grant_type', p_grant_type
    );

EXCEPTION
    WHEN unique_violation THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'trial_already_granted',
            'message', 'A trial has already been granted to this user'
        );
END;
$$;

COMMENT ON FUNCTION record_trial_grant IS
    'Atomically records a trial grant. Returns {success:false, error:"trial_already_granted"} '
    'if the user already has a trial, preventing race-condition duplicates.';

-- 3. Backfill existing trials so the constraint is accurate going forward.
--    We pick the earliest trial_start_date per user from api_keys_new.
--
--    NOTE: The WHERE clause intentionally includes `trial_converted = TRUE`.
--    Converted users have already used their trial (and upgraded to a paid plan),
--    so they must be recorded in trial_grants to prevent them from re-claiming
--    a second free trial.
INSERT INTO trial_grants (user_id, api_key_id, grant_type, trial_credits, trial_duration_days, granted_at)
SELECT DISTINCT ON (ak.user_id)
    ak.user_id,
    ak.id,
    CASE
        WHEN ak.partner_code IS NOT NULL THEN 'partner'
        ELSE 'standard'
    END,
    ak.trial_credits,
    CASE
        WHEN ak.trial_start_date IS NOT NULL AND ak.trial_end_date IS NOT NULL
        THEN EXTRACT(DAY FROM (ak.trial_end_date - ak.trial_start_date))::INTEGER
        ELSE NULL
    END,
    COALESCE(ak.trial_start_date, ak.created_at)
FROM api_keys_new ak
WHERE ak.is_trial = TRUE
   OR ak.trial_converted = TRUE
   OR ak.trial_start_date IS NOT NULL
ORDER BY ak.user_id, ak.trial_start_date ASC NULLS LAST
ON CONFLICT (user_id) DO NOTHING;

-- 4. Grant permissions
GRANT SELECT, INSERT ON TABLE trial_grants TO service_role;
GRANT USAGE, SELECT ON SEQUENCE trial_grants_id_seq TO service_role;
GRANT SELECT ON TABLE trial_grants TO authenticated;

-- 5. Enable RLS
ALTER TABLE trial_grants ENABLE ROW LEVEL SECURITY;

-- RLS policy: service_role has full access (implicit via SECURITY DEFINER function),
-- authenticated users can only read their own grant.
CREATE POLICY trial_grants_select_own ON trial_grants
    FOR SELECT
    TO authenticated
    USING (user_id = (current_setting('request.jwt.claims', true)::jsonb ->> 'sub')::bigint);

-- Grant execute on the helper function
GRANT EXECUTE ON FUNCTION record_trial_grant TO service_role;
