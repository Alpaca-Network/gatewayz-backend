-- SQL script to revoke credits from @rccg-clf.org fraudulent accounts
-- Generated: 2026-01-05
-- Purpose: Remove credits from bot accounts and deactivate them

-- Step 1: Deactivate all @rccg-clf.org accounts
UPDATE users
SET
    is_active = false,
    credits = 0,
    updated_at = NOW()
WHERE email ILIKE '%@rccg-clf.org';

-- Step 2: Log the credit revocation in credit_transactions
INSERT INTO credit_transactions (user_id, amount, description, created_at)
SELECT
    id as user_id,
    -credits as amount,
    'Credit revocation - fraudulent account (@rccg-clf.org bot attack)' as description,
    NOW() as created_at
FROM users
WHERE email ILIKE '%@rccg-clf.org' AND credits > 0;

-- Step 3: Get summary of affected accounts
SELECT
    COUNT(*) as total_accounts,
    SUM(CASE WHEN is_active = false THEN 1 ELSE 0 END) as deactivated_accounts,
    SUM(credits) as total_credits_revoked
FROM users
WHERE email ILIKE '%@rccg-clf.org';
