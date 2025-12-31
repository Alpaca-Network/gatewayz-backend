-- Add Admin Tier Plan
-- This creates an admin subscription tier with unlimited resources
-- Admin tier bypasses all checks: rate limits, credit deductions, trial validations

-- Add plan_type column if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'plans' AND column_name = 'plan_type'
    ) THEN
        ALTER TABLE plans ADD COLUMN plan_type TEXT;
        COMMENT ON COLUMN plans.plan_type IS 'Type of plan: standard, pro, max, admin, etc.';
    END IF;
END $$;

-- Insert the admin plan with effectively unlimited limits
INSERT INTO plans (
    name,
    description,
    plan_type,
    price_per_month,
    daily_request_limit,
    daily_token_limit,
    monthly_request_limit,
    monthly_token_limit,
    max_concurrent_requests,
    features,
    is_active
)
VALUES (
    'Admin',
    'Administrative tier with unlimited access and no resource constraints',
    'admin',
    0.00,        -- No monthly cost
    2147483647,  -- Max integer value (~2.1 billion requests/day)
    2147483647,  -- Max integer value (~2.1 billion tokens/day)
    2147483647,  -- Max integer value (~2.1 billion requests/month)
    2147483647,  -- Max integer value (~2.1 billion tokens/month)
    1000,        -- High concurrent request limit
    '["unlimited_access", "priority_support", "admin_features", "all_models"]'::jsonb,
    TRUE         -- Active
)
ON CONFLICT (name) DO UPDATE SET
    description = EXCLUDED.description,
    plan_type = EXCLUDED.plan_type,
    price_per_month = EXCLUDED.price_per_month,
    daily_request_limit = EXCLUDED.daily_request_limit,
    daily_token_limit = EXCLUDED.daily_token_limit,
    monthly_request_limit = EXCLUDED.monthly_request_limit,
    monthly_token_limit = EXCLUDED.monthly_token_limit,
    max_concurrent_requests = EXCLUDED.max_concurrent_requests,
    features = EXCLUDED.features,
    is_active = EXCLUDED.is_active;

-- Add comment to document the admin plan
COMMENT ON TABLE plans IS 'Subscription plans including the special admin tier with unlimited resources';

-- Create helper function to check if a user has admin plan
CREATE OR REPLACE FUNCTION is_admin_plan_user(p_user_id INTEGER)
RETURNS BOOLEAN AS $$
DECLARE
    v_plan_type TEXT;
BEGIN
    SELECT p.plan_type INTO v_plan_type
    FROM user_plans up
    JOIN plans p ON up.plan_id = p.id
    WHERE up.user_id = p_user_id
        AND up.is_active = TRUE
        AND p.is_active = TRUE
    LIMIT 1;

    RETURN v_plan_type = 'admin';
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION is_admin_plan_user(INTEGER) IS 'Check if a user has an active admin plan (bypasses all resource limits)';
