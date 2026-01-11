# Admin Tier Implementation

## Overview
A new **Admin** subscription tier has been implemented that provides unlimited access to all API resources with no payment requirements, credit checks, rate limiting, or trial restrictions.

## Features

The Admin tier provides:
- ✅ **Unlimited API requests** - No daily or monthly request limits
- ✅ **Unlimited token usage** - No token consumption limits
- ✅ **No credit deductions** - API usage does not consume credits
- ✅ **No rate limiting** - Bypasses all rate limit checks
- ✅ **No trial restrictions** - Not subject to trial validation
- ✅ **No payment required** - $0.00 monthly cost
- ✅ **Permanent status** - No expiration date
- ✅ **Priority access** - High concurrency limits (1000 concurrent requests)
- ✅ **All features** - Access to all models and features

## Implementation Details

### Database Changes

**Migration File:** `supabase/migrations/20251231000000_add_admin_tier_plan.sql`

The migration creates:
1. A new `Admin` plan in the `plans` table with:
   - `plan_type`: "admin"
   - `price_per_month`: $0.00
   - `daily_request_limit`: 2,147,483,647 (max integer)
   - `monthly_request_limit`: 2,147,483,647
   - `daily_token_limit`: 2,147,483,647
   - `monthly_token_limit`: 2,147,483,647
   - `max_concurrent_requests`: 1000
   - `features`: ['unlimited_access', 'priority_support', 'admin_features', 'all_models']

2. A helper function `is_admin_plan_user(p_user_id INTEGER)` to check if a user has an admin plan

### Code Changes

#### 1. Plan Enforcement (`src/db/plans.py`)
- Added `ADMIN_PLAN_TYPE` and `ADMIN_BYPASS_LIMITS` constants
- Added `is_admin_tier_user(user_id)` function to check admin status
- Updated `enforce_plan_limits()` to bypass all limit checks for admin users
- Updated `check_plan_entitlements()` to return unlimited entitlements for admin users

#### 2. Trial Validation (`src/services/trial_validation.py`)
- Updated `_validate_trial_access_uncached()` to bypass trial checks for admin users
- Admin users are treated as non-trial with unlimited access

#### 3. Credit Deduction (`src/db/users.py`)
- Updated `deduct_credits()` to skip credit deduction for admin users
- Admin users can use the API without consuming any credits

#### 4. Rate Limiting (`src/services/rate_limiting.py`)
- Updated `RateLimitManager.check_rate_limit()` to bypass rate limits for admin users
- Returns unlimited rate limit result for admin users

## Usage

### Assigning Admin Tier to a User

To assign the admin tier to a user, you need to:

1. **Run the migration** (if not already applied):
   ```bash
   supabase db push
   # or apply the specific migration
   psql $DATABASE_URL -f supabase/migrations/20251231000000_add_admin_tier_plan.sql
   ```

2. **Assign the admin plan to a user** using the existing `assign_user_plan()` function:
   ```python
   from src.db.plans import assign_user_plan, get_plan_id_by_tier

   # Get the admin plan ID
   admin_plan_id = get_plan_id_by_tier("admin")

   # Assign to a user (user_id, plan_id, duration_months)
   # Note: duration_months is used but admin plans are permanent
   assign_user_plan(user_id=123, plan_id=admin_plan_id, duration_months=999)
   ```

3. **Or directly via SQL**:
   ```sql
   -- First, get the admin plan ID
   SELECT id FROM plans WHERE plan_type = 'admin';

   -- Deactivate any existing plans for the user
   UPDATE user_plans SET is_active = FALSE WHERE user_id = YOUR_USER_ID;

   -- Assign the admin plan
   INSERT INTO user_plans (user_id, plan_id, started_at, expires_at, is_active)
   VALUES (
       YOUR_USER_ID,
       (SELECT id FROM plans WHERE plan_type = 'admin'),
       NOW(),
       NULL,  -- No expiration
       TRUE
   );

   -- Update user subscription status
   UPDATE users SET subscription_status = 'active' WHERE id = YOUR_USER_ID;
   ```

### Verifying Admin Status

You can verify if a user has admin tier using:

```python
from src.db.plans import is_admin_tier_user

# Check if user is admin
if is_admin_tier_user(user_id):
    print("User has admin tier - unlimited access!")
else:
    print("User is on a regular plan")
```

Or via SQL:
```sql
SELECT is_admin_plan_user(YOUR_USER_ID);
```

### API Behavior for Admin Users

When an admin tier user makes API requests:

1. **No Credit Checks**: The `deduct_credits()` function logs the admin status and returns without deducting credits
2. **No Rate Limiting**: Rate limit checks return unlimited results
3. **No Trial Validation**: Trial validation is skipped
4. **No Plan Limits**: Plan limit enforcement returns "allowed" immediately

All bypasses are logged with messages like:
- `"Admin tier user {user_id} - bypassing plan limit checks"`
- `"Admin tier user {user_id} - skipping credit deduction of ${amount}"`
- `"Admin tier user - bypassing rate limit checks"`
- `"Admin tier user - bypassing trial validation"`

## Security Considerations

⚠️ **Important Security Notes:**

1. **Assign Sparingly**: Only assign admin tier to trusted internal accounts or specific high-value customers
2. **Audit Trail**: All admin tier bypasses are logged for monitoring and auditing
3. **Database Access**: Ensure only authorized personnel can modify the `user_plans` table
4. **No Auto-Assignment**: Admin tier must be manually assigned; there's no self-service upgrade path
5. **Monitoring**: Monitor admin tier usage to detect potential abuse

## Testing

To test the admin tier implementation:

1. **Create a test user** with admin tier:
   ```python
   from src.db.users import create_user
   from src.db.plans import assign_user_plan, get_plan_id_by_tier

   # Create test user
   user = create_user(email="admin@test.com", password="secure_password")

   # Assign admin tier
   admin_plan_id = get_plan_id_by_tier("admin")
   assign_user_plan(user["id"], admin_plan_id, duration_months=999)
   ```

2. **Test API requests** with the admin user's API key:
   - Verify no credits are deducted
   - Verify no rate limiting occurs
   - Verify can make unlimited requests

3. **Check logs** for admin bypass messages

## Files Modified

### New Files:
- `supabase/migrations/20251231000000_add_admin_tier_plan.sql` - Database migration

### Modified Files:
- `src/db/plans.py` - Plan enforcement and entitlements
- `src/services/trial_validation.py` - Trial validation bypass
- `src/db/users.py` - Credit deduction bypass
- `src/services/rate_limiting.py` - Rate limit bypass

## Rollback

To rollback this feature:

1. **Remove admin plan assignments**:
   ```sql
   DELETE FROM user_plans WHERE plan_id IN (SELECT id FROM plans WHERE plan_type = 'admin');
   ```

2. **Remove admin plan**:
   ```sql
   DELETE FROM plans WHERE plan_type = 'admin';
   DROP FUNCTION IF EXISTS is_admin_plan_user(INTEGER);
   ```

3. **Revert code changes** (use git):
   ```bash
   git checkout HEAD -- src/db/plans.py src/services/trial_validation.py src/db/users.py src/services/rate_limiting.py
   ```

## Support

For questions or issues:
1. Check the logs for admin bypass messages
2. Verify the user has an active admin plan in the database
3. Ensure the migration was applied successfully
4. Check that `ADMIN_BYPASS_LIMITS = True` in `src/db/plans.py`

---

**Implementation Date**: 2025-12-31
**Version**: 1.0
**Status**: ✅ Ready for deployment
