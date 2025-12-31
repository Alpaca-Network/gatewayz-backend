# Admin Disable Trial Endpoint

## Overview
A new admin endpoint has been created to disable trial status for users, converting them from trial to regular active users.

## Endpoint Details

**URL:** `POST /admin/disable-trial`

**Authentication:** Requires admin API key via `Authorization: Bearer <ADMIN_API_KEY>`

**Tags:** `admin`

## Request

### Request Body
```json
{
  "user_id": 123
}
```

### Parameters
- `user_id` (integer, required): The ID of the user whose trial status should be disabled

## Response

### Success Response (200 OK)
```json
{
  "status": "success",
  "message": "Trial disabled for user john_doe",
  "user": {
    "id": 123,
    "username": "john_doe",
    "email": "john@example.com",
    "previous_status": "trial",
    "new_status": "active"
  },
  "updates": {
    "api_keys_updated": 2,
    "trial_status_cleared": true,
    "trial_limits_reset": true
  },
  "timestamp": "2025-12-31T12:00:00.000Z"
}
```

### Error Responses

**404 Not Found** - User doesn't exist:
```json
{
  "detail": "User with ID 123 not found"
}
```

**401 Unauthorized** - Invalid or missing admin API key:
```json
{
  "detail": "Invalid admin API key"
}
```

**500 Internal Server Error** - Server error:
```json
{
  "detail": "Failed to disable trial: <error message>"
}
```

## What This Endpoint Does

When you call this endpoint, it performs the following actions:

1. **Validates User Exists**
   - Checks if the user with the given ID exists
   - Returns 404 if user not found

2. **Updates API Keys** (in `api_keys_new` table)
   - Sets `is_trial` = `false`
   - Clears `trial_end_date` (sets to `null`)
   - Resets `trial_used_tokens` to `0`
   - Resets `trial_used_requests` to `0`
   - Resets `trial_used_credits` to `0.0`
   - Updates `updated_at` timestamp

3. **Updates User Status** (in `users` table)
   - Changes `subscription_status` from `"trial"` to `"active"`
   - Clears `trial_expires_at` (sets to `null`)
   - Updates `updated_at` timestamp

4. **Invalidates Caches**
   - Clears trial validation cache for all user's API keys
   - Clears user lookup cache
   - Ensures changes take effect immediately

## Usage Examples

### Using cURL

```bash
# Set your admin API key
export ADMIN_API_KEY="your-admin-key-here"

# Disable trial for user ID 123
curl -X POST "https://api.gatewayz.ai/admin/disable-trial" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123}'
```

### Using Python

```python
import requests

ADMIN_API_KEY = "your-admin-key-here"
API_BASE_URL = "https://api.gatewayz.ai"

def disable_trial(user_id: int):
    response = requests.post(
        f"{API_BASE_URL}/admin/disable-trial",
        headers={
            "Authorization": f"Bearer {ADMIN_API_KEY}",
            "Content-Type": "application/json"
        },
        json={"user_id": user_id}
    )

    if response.status_code == 200:
        print(f"✅ Trial disabled successfully!")
        print(response.json())
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.json())

    return response.json()

# Example: Disable trial for user 123
disable_trial(123)
```

### Using JavaScript/TypeScript

```typescript
const ADMIN_API_KEY = "your-admin-key-here";
const API_BASE_URL = "https://api.gatewayz.ai";

async function disableTrial(userId: number) {
  const response = await fetch(`${API_BASE_URL}/admin/disable-trial`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${ADMIN_API_KEY}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ user_id: userId })
  });

  if (response.ok) {
    const data = await response.json();
    console.log("✅ Trial disabled successfully!", data);
    return data;
  } else {
    const error = await response.json();
    console.error("❌ Error:", error);
    throw new Error(error.detail);
  }
}

// Example: Disable trial for user 123
disableTrial(123);
```

## Common Use Cases

### 1. Convert Trial User to Paid User
When a trial user upgrades to a paid subscription, disable their trial status:

```bash
# Step 1: Disable trial
curl -X POST "https://api.gatewayz.ai/admin/disable-trial" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123}'

# Step 2: Add credits or assign a plan
curl -X POST "https://api.gatewayz.ai/admin/add_credits" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "user-api-key", "credits": 100.0}'
```

### 2. Remove Trial Restrictions for VIP Users
For users who should have unlimited access without trial limits:

```python
# Disable trial
disable_trial(user_id=123)

# Then assign admin tier (see ADMIN_TIER_IMPLEMENTATION.md)
from src.db.plans import assign_user_plan, get_plan_id_by_tier

admin_plan_id = get_plan_id_by_tier("admin")
assign_user_plan(user_id=123, plan_id=admin_plan_id, duration_months=999)
```

### 3. Manually Convert Expired Trials
For users whose trial has expired but you want to give them continued access:

```bash
# Disable trial status
curl -X POST "https://api.gatewayz.ai/admin/disable-trial" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 123}'

# Add credits to continue usage
curl -X POST "https://api.gatewayz.ai/admin/add_credits" \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"api_key": "user-api-key", "credits": 50.0}'
```

## Verification

After disabling trial, you can verify the changes:

### Check User Status
```python
from src.db.users import get_user_by_id

user = get_user_by_id(123)
print(f"Subscription Status: {user['subscription_status']}")  # Should be 'active'
print(f"Trial Expires At: {user['trial_expires_at']}")  # Should be None
```

### Check API Key Status
```python
from src.config.supabase_config import get_supabase_client

client = get_supabase_client()
result = client.table("api_keys_new").select("*").eq("user_id", 123).execute()

for key in result.data:
    print(f"API Key: {key['api_key'][:20]}...")
    print(f"  Is Trial: {key['is_trial']}")  # Should be False
    print(f"  Trial End Date: {key['trial_end_date']}")  # Should be None
```

### Test API Access
```bash
# User should now have regular access (not trial restrictions)
curl -X POST "https://api.gatewayz.ai/v1/chat/completions" \
  -H "Authorization: Bearer user-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Important Notes

⚠️ **Important Considerations:**

1. **Credits Required**: Disabling trial doesn't add credits. Users need credits to use the API after trial is disabled.

2. **Immediate Effect**: Changes take effect immediately due to cache invalidation.

3. **All API Keys Updated**: This endpoint updates ALL API keys belonging to the user, not just one.

4. **Subscription Status**: The user's subscription_status changes from "trial" to "active", but they still need credits or a plan.

5. **No Undo**: There's no built-in endpoint to re-enable trial status. If needed, you'd have to manually update the database.

## Troubleshooting

### User still shows as trial after endpoint call
- Check the API response - it should show `"new_status": "active"`
- Verify cache was invalidated (check logs for "Invalidated caches for user")
- Try making a test API request to force cache refresh

### 402 Payment Required error after disabling trial
- User needs credits added to their account
- Use `/admin/add_credits` endpoint to add credits
- Or assign them to a paid plan with credits

### Changes not reflecting immediately
- The endpoint automatically invalidates caches
- If issues persist, restart the application server
- Check Redis cache if using Redis

## Related Endpoints

- `POST /admin/add_credits` - Add credits to a user
- `POST /admin/assign-plan` - Assign a subscription plan to a user
- `GET /admin/users` - List all users with pagination
- `GET /admin/monitor` - Get monitoring data including user stats

## Files Modified

- `src/routes/admin.py` - Added new endpoint and request model

## Implementation Date

**Date**: 2025-12-31
**Version**: 1.0
**Status**: ✅ Ready for use
