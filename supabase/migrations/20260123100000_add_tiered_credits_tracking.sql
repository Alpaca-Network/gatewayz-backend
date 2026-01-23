-- Add new columns for tiered credit tracking
-- subscription_allowance: Monthly usage allowance from subscription (resets on renewal)
-- purchased_credits: One-time purchased credits (never expire)
-- allowance_reset_date: Timestamp when allowance was last reset

ALTER TABLE users
ADD COLUMN IF NOT EXISTS subscription_allowance DECIMAL(10,4) DEFAULT 0,
ADD COLUMN IF NOT EXISTS purchased_credits DECIMAL(10,4) DEFAULT 0,
ADD COLUMN IF NOT EXISTS allowance_reset_date TIMESTAMP WITH TIME ZONE;

-- Add indexes for query performance
CREATE INDEX IF NOT EXISTS idx_users_subscription_allowance ON users(subscription_allowance) WHERE subscription_allowance > 0;
CREATE INDEX IF NOT EXISTS idx_users_purchased_credits ON users(purchased_credits) WHERE purchased_credits > 0;

-- Add comments for documentation
COMMENT ON COLUMN users.subscription_allowance IS 'Monthly usage allowance from subscription - resets on renewal, forfeited on cancellation';
COMMENT ON COLUMN users.purchased_credits IS 'One-time purchased credits - never expire, kept on cancellation';
COMMENT ON COLUMN users.allowance_reset_date IS 'Timestamp when subscription allowance was last reset';
