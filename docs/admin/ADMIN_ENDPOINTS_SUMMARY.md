# Admin Endpoints Summary

This document provides an overview of all admin endpoints for user management in the Gatewayz backend.

## Authentication

**All admin endpoints require the `ADMIN_API_KEY` environment variable.**

### Setup

1. **Generate an admin API key:**
```bash
python3 -c "import secrets; print('sk_admin_live_' + secrets.token_urlsafe(32))"
```

2. **Add to backend `.env`:**
```bash
ADMIN_API_KEY=sk_admin_live_your-secure-admin-key-here
```

3. **Use in API requests:**
```bash
curl -H "Authorization: Bearer sk_admin_live_your-secure-admin-key-here" \
  https://api.gatewayz.app/admin/users
```

### Security

- ⚠️ **Keep this key secret** - It grants full administrative access
- ✅ Use different keys for dev/staging/production
- ✅ Rotate keys every 90 days
- ✅ Never commit keys to version control

## Admin Endpoints

### 1. List All Users
**Endpoint:** `GET /admin/users`
**Description:** Get all users with statistics
**Response:**
```json
{
  "status": "success",
  "total_users": 100,
  "statistics": {
    "active_users": 95,
    "inactive_users": 5,
    "admin_users": 3,
    "developer_users": 10,
    "regular_users": 87,
    "total_credits": 10000.50,
    "average_credits": 100.01,
    "subscription_breakdown": {
      "trial": 20,
      "active": 70,
      "expired": 10
    }
  },
  "users": [...],
  "timestamp": "2025-12-31T..."
}
```

### 2. Get User by ID
**Endpoint:** `GET /admin/users/{user_id}`
**Description:** Get detailed information for a specific user
**Response:**
```json
{
  "status": "success",
  "user": {...},
  "api_keys": [...],
  "recent_usage": [...],
  "recent_activity": [...],
  "timestamp": "2025-12-31T..."
}
```

### 3. Update User Details
**Endpoint:** `PUT /admin/users/{user_id}`
**Parameters:**
- `username` (optional): New username
- `email` (optional): New email
- `is_active` (optional): Active status (boolean)

**Description:** Update basic user information. At least one field must be provided.

**Example:**
```bash
curl -X PUT "https://api.example.com/admin/users/123" \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "newusername",
    "email": "newemail@example.com",
    "is_active": true
  }'
```

**Response:**
```json
{
  "status": "success",
  "message": "User 123 updated successfully",
  "user": {...},
  "timestamp": "2025-12-31T..."
}
```

### 4. Update User Role
**Endpoint:** `POST /admin/roles/update`
**Description:** Update user's role (user, developer, admin)
**Body:**
```json
{
  "user_id": 123,
  "new_role": "developer",
  "reason": "Promoted to developer"
}
```

### 5. Set User Tier
**Endpoint:** `PUT /admin/users/{user_id}/tier`
**Parameters:**
- `tier`: Subscription tier (basic, pro, max)

**Description:** Update user's subscription tier

**Example:**
```bash
curl -X PUT "https://api.example.com/admin/users/123/tier?tier=pro" \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "message": "User 123 tier updated to pro",
  "user_id": 123,
  "new_tier": "pro",
  "timestamp": "2025-12-31T..."
}
```

### 6. Set User Credits
**Endpoint:** `PUT /admin/users/{user_id}/credits`
**Parameters:**
- `credits`: Absolute credit balance (float)

**Description:** Set user's absolute credit balance (not adding to existing)

**Example:**
```bash
curl -X PUT "https://api.example.com/admin/users/123/credits?credits=500.0" \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "message": "User 123 credits set to 500.0",
  "user_id": 123,
  "balance_before": 250.0,
  "balance_after": 500.0,
  "difference": 250.0,
  "timestamp": "2025-12-31T..."
}
```

### 7. Add Credits to User
**Endpoint:** `POST /admin/add_credits`
**Description:** Add credits to user (incremental)
**Body:**
```json
{
  "api_key": "user_api_key",
  "credits": 100
}
```

### 8. Update User Status
**Endpoint:** `PATCH /admin/users/{user_id}/status`
**Parameters:**
- `is_active`: Active status (boolean)

**Description:** Activate or deactivate a user

**Example:**
```bash
curl -X PATCH "https://api.example.com/admin/users/123/status?is_active=false" \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "message": "User 123 deactivated",
  "user_id": 123,
  "is_active": false,
  "previous_status": true,
  "timestamp": "2025-12-31T..."
}
```

### 9. Delete User
**Endpoint:** `DELETE /admin/users/{user_id}`
**Parameters:**
- `confirm`: Must be `true` to confirm deletion

**Description:** Permanently delete a user and all associated data

**Example:**
```bash
curl -X DELETE "https://api.example.com/admin/users/123?confirm=true" \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

**Response:**
```json
{
  "status": "success",
  "message": "User 123 deleted successfully",
  "deleted_user": {
    "id": 123,
    "username": "olduser",
    "email": "old@example.com"
  },
  "timestamp": "2025-12-31T..."
}
```

## Additional Admin Endpoints

### Get All Credit Transactions
**Endpoint:** `GET /admin/credit-transactions`
**Parameters:** limit, offset, user_id, transaction_type, from_date, to_date, etc.
**Description:** View all credit transactions across all users with advanced filtering

### Add Credits
**Endpoint:** `POST /admin/add_credits`
**Description:** Add credits to a user account (incremental)

### Set Rate Limits
**Endpoint:** `POST /admin/limit`
**Description:** Set rate limits for a user

### Get Admin Monitor Data
**Endpoint:** `GET /admin/monitor`
**Description:** Get system-wide monitoring data and statistics

## Security Features

- ✅ All endpoints require `ADMIN_API_KEY` environment variable
- ✅ Constant-time comparison prevents timing attacks
- ✅ User cache automatically invalidated on updates
- ✅ Credit transactions logged with admin action metadata
- ✅ Deletion requires explicit confirmation
- ✅ Separate admin credential (not tied to user accounts)
- ✅ No database lookup required for authentication

## Error Responses

All endpoints return standard error responses:
```json
{
  "detail": "Error message"
}
```

**Common HTTP Status Codes:**
- `200` - Success
- `400` - Bad request (invalid parameters)
- `401` - Unauthorized (invalid admin key)
- `403` - Forbidden (not an admin)
- `404` - User not found
- `500` - Internal server error

## Notes

- The `PUT /admin/users/{user_id}/credits` endpoint sets the **absolute** balance
- The `POST /admin/add_credits` endpoint **adds** credits to the existing balance
- User cache is automatically invalidated after updates to ensure fresh data
- All credit changes are logged in the credit_transactions table
- Deleting a user cascades to delete all associated records (API keys, usage, transactions, etc.)
