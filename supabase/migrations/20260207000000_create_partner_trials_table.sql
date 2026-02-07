-- Create partner trials configuration table
-- This table stores partner-specific trial configurations (e.g., Redbeard 14-day Pro trial)

CREATE TABLE IF NOT EXISTS partner_trials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_code VARCHAR(50) UNIQUE NOT NULL,
    partner_name VARCHAR(255) NOT NULL,
    trial_duration_days INTEGER NOT NULL DEFAULT 14,
    trial_tier VARCHAR(50) NOT NULL DEFAULT 'pro',
    trial_credits_usd DECIMAL(10,2) NOT NULL DEFAULT 20.00,
    trial_max_tokens BIGINT DEFAULT 1000000,
    trial_max_requests INTEGER DEFAULT 10000,
    daily_usage_limit_usd DECIMAL(10,2) DEFAULT 5.00,
    is_active BOOLEAN DEFAULT true,
    landing_page_url VARCHAR(500),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for partner_trials
CREATE INDEX IF NOT EXISTS idx_partner_trials_code ON partner_trials(partner_code);
CREATE INDEX IF NOT EXISTS idx_partner_trials_active ON partner_trials(is_active) WHERE is_active = true;

-- Add partner fields to users table
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS partner_code VARCHAR(50),
    ADD COLUMN IF NOT EXISTS partner_trial_id UUID REFERENCES partner_trials(id),
    ADD COLUMN IF NOT EXISTS partner_signup_timestamp TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS partner_metadata JSONB DEFAULT '{}';

-- Index for partner tracking on users
CREATE INDEX IF NOT EXISTS idx_users_partner_code ON users(partner_code) WHERE partner_code IS NOT NULL;

-- Add partner fields to api_keys_new table
ALTER TABLE api_keys_new
    ADD COLUMN IF NOT EXISTS partner_code VARCHAR(50),
    ADD COLUMN IF NOT EXISTS partner_trial_tier VARCHAR(50),
    ADD COLUMN IF NOT EXISTS partner_trial_credits DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS is_trial BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS trial_start_date TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS trial_end_date TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS trial_credits DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS trial_max_tokens BIGINT,
    ADD COLUMN IF NOT EXISTS trial_max_requests INTEGER,
    ADD COLUMN IF NOT EXISTS trial_used_tokens BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trial_used_requests INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trial_used_credits DECIMAL(10,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS trial_converted BOOLEAN DEFAULT false;

-- Create partner trial analytics table
CREATE TABLE IF NOT EXISTS partner_trial_analytics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    partner_code VARCHAR(50) NOT NULL,
    user_id BIGINT REFERENCES users(id),
    trial_started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    trial_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    trial_converted_at TIMESTAMP WITH TIME ZONE,
    converted_to_tier VARCHAR(50),
    conversion_revenue_usd DECIMAL(10,2),
    total_credits_used DECIMAL(10,2) DEFAULT 0,
    total_tokens_used BIGINT DEFAULT 0,
    total_requests_made INTEGER DEFAULT 0,
    trial_status VARCHAR(50) DEFAULT 'active',
    signup_source VARCHAR(255),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for analytics queries
CREATE INDEX IF NOT EXISTS idx_partner_analytics_code ON partner_trial_analytics(partner_code);
CREATE INDEX IF NOT EXISTS idx_partner_analytics_status ON partner_trial_analytics(trial_status);
CREATE INDEX IF NOT EXISTS idx_partner_analytics_started ON partner_trial_analytics(trial_started_at);
CREATE INDEX IF NOT EXISTS idx_partner_analytics_user ON partner_trial_analytics(user_id);
CREATE INDEX IF NOT EXISTS idx_partner_analytics_converted ON partner_trial_analytics(trial_converted_at)
    WHERE trial_converted_at IS NOT NULL;

-- Insert Redbeard partner configuration
INSERT INTO partner_trials (
    partner_code,
    partner_name,
    trial_duration_days,
    trial_tier,
    trial_credits_usd,
    trial_max_tokens,
    trial_max_requests,
    daily_usage_limit_usd,
    landing_page_url,
    is_active
) VALUES (
    'REDBEARD',
    'Red Beard Ventures',
    14,
    'pro',
    20.00,
    1000000,
    10000,
    5.00,
    'https://www.gatewayz.ai/redbeard',
    true
) ON CONFLICT (partner_code) DO UPDATE SET
    partner_name = EXCLUDED.partner_name,
    trial_duration_days = EXCLUDED.trial_duration_days,
    trial_tier = EXCLUDED.trial_tier,
    trial_credits_usd = EXCLUDED.trial_credits_usd,
    trial_max_tokens = EXCLUDED.trial_max_tokens,
    trial_max_requests = EXCLUDED.trial_max_requests,
    daily_usage_limit_usd = EXCLUDED.daily_usage_limit_usd,
    landing_page_url = EXCLUDED.landing_page_url,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- Add comments
COMMENT ON TABLE partner_trials IS 'Configuration for partner-specific trial offers (e.g., Redbeard 14-day Pro trial)';
COMMENT ON COLUMN partner_trials.partner_code IS 'Unique partner identifier used in signup URLs (e.g., REDBEARD)';
COMMENT ON COLUMN partner_trials.trial_duration_days IS 'Number of days the trial lasts';
COMMENT ON COLUMN partner_trials.trial_tier IS 'Subscription tier during trial (basic, pro, max)';
COMMENT ON COLUMN partner_trials.trial_credits_usd IS 'Amount of credits in USD for the trial';
COMMENT ON COLUMN partner_trials.daily_usage_limit_usd IS 'Maximum daily usage in USD during trial';

COMMENT ON TABLE partner_trial_analytics IS 'Analytics tracking for partner trial signups and conversions';
COMMENT ON COLUMN partner_trial_analytics.trial_status IS 'Status: active, expired, converted, canceled';

-- Create trigger for updated_at on partner_trials
CREATE OR REPLACE FUNCTION update_partner_trials_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_partner_trials_updated_at ON partner_trials;
CREATE TRIGGER trigger_partner_trials_updated_at
    BEFORE UPDATE ON partner_trials
    FOR EACH ROW
    EXECUTE FUNCTION update_partner_trials_updated_at();

-- Create trigger for updated_at on partner_trial_analytics
DROP TRIGGER IF EXISTS trigger_partner_trial_analytics_updated_at ON partner_trial_analytics;
CREATE TRIGGER trigger_partner_trial_analytics_updated_at
    BEFORE UPDATE ON partner_trial_analytics
    FOR EACH ROW
    EXECUTE FUNCTION update_partner_trials_updated_at();
