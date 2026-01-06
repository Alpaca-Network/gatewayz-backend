# Trial Analytics Endpoints - Frontend Integration Guide

**Status**: ✅ Deployed to Production
**Date**: January 6, 2026
**Commit**: `58f08795`

---

## Overview

5 new admin endpoints for monitoring trial usage, detecting abuse, and tracking conversions.

**Base URL**: `https://api.gatewayz.ai` (or your deployment URL)

**Authentication**: All endpoints require admin API key in header:
```
Authorization: Bearer YOUR_ADMIN_API_KEY
```

---

## Endpoints Reference

### 1. GET /admin/trial/users

**Purpose**: Fetch detailed trial user list with usage metrics

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| status | string | "all" | Filter: `active`, `expired`, `converted`, `all` |
| sort_by | string | "created_at" | Sort field: `requests`, `tokens`, `credits`, `created_at` |
| sort_order | string | "desc" | Sort order: `asc`, `desc` |
| limit | integer | 100 | Results per page (1-1000) |
| offset | integer | 0 | Pagination offset |
| domain_filter | string | null | Filter by email domain (e.g., "gmail.com") |

**Example Request**:
```bash
GET /admin/trial/users?status=active&sort_by=requests&limit=50&offset=0
```

**Example Response**:
```json
{
  "success": true,
  "users": [
    {
      "user_id": 123,
      "email": "user@example.com",
      "email_domain": "example.com",
      "api_key_id": 456,
      "api_key_preview": "gw_****abc123",
      "is_trial": true,
      "trial_start_date": "2025-01-01T00:00:00Z",
      "trial_end_date": "2025-01-04T00:00:00Z",
      "trial_status": "active",
      "trial_days_remaining": 2,
      "trial_used_tokens": 50000,
      "trial_max_tokens": 500000,
      "trial_token_utilization": 10.0,
      "trial_used_requests": 150,
      "trial_max_requests": 1000,
      "trial_request_utilization": 15.0,
      "trial_used_credits": 1.5,
      "trial_allocated_credits": 10.0,
      "trial_credit_utilization": 15.0,
      "trial_converted": false,
      "conversion_date": null,
      "requests_at_conversion": null,
      "tokens_at_conversion": null,
      "created_at": "2025-01-01T00:00:00Z",
      "signup_ip": null,
      "last_request_at": "2025-01-03T12:00:00Z"
    }
  ],
  "pagination": {
    "total": 1500,
    "limit": 50,
    "offset": 0,
    "has_more": true
  }
}
```

**Frontend Usage**:
```typescript
// Fetch active trials sorted by request count
const response = await fetch(
  '/admin/trial/users?status=active&sort_by=requests&limit=100',
  {
    headers: { 'Authorization': `Bearer ${adminApiKey}` }
  }
);
const data = await response.json();

// Display in table with pagination
<TrialUsersTable
  users={data.users}
  pagination={data.pagination}
  onPageChange={handlePageChange}
/>
```

---

### 2. GET /admin/trial/domain-analysis

**Purpose**: Analyze trial users by email domain, detect abuse

**Query Parameters**: None

**Example Request**:
```bash
GET /admin/trial/domain-analysis
```

**Example Response**:
```json
{
  "success": true,
  "domains": [
    {
      "domain": "tempmail.com",
      "total_users": 50,
      "active_trials": 45,
      "expired_trials": 5,
      "converted_trials": 0,
      "conversion_rate": 0.0,
      "total_requests": 45000,
      "total_tokens": 22500000,
      "total_credits_used": 450.0,
      "avg_requests_per_user": 900,
      "avg_tokens_per_user": 450000,
      "abuse_score": 9.2,
      "flagged": true
    },
    {
      "domain": "gmail.com",
      "total_users": 500,
      "active_trials": 100,
      "expired_trials": 200,
      "converted_trials": 200,
      "conversion_rate": 40.0,
      "total_requests": 150000,
      "total_tokens": 50000000,
      "total_credits_used": 500.0,
      "avg_requests_per_user": 300,
      "avg_tokens_per_user": 100000,
      "abuse_score": 2.5,
      "flagged": false
    }
  ],
  "suspicious_domains": [
    "tempmail.com",
    "10minutemail.com"
  ]
}
```

**Abuse Score Calculation**:
- **0-3**: Normal usage
- **3-7**: Moderate concern
- **7-10**: High risk (flagged)

**Factors**:
- High utilization (>80%) + Low conversion (<5%) = +4 points
- High average usage (>2x normal) = +3 points
- Many users with zero conversions = +3 points

**Frontend Usage**:
```typescript
// Fetch and display domain analysis
const response = await fetch('/admin/trial/domain-analysis', {
  headers: { 'Authorization': `Bearer ${adminApiKey}` }
});
const data = await response.json();

// Highlight flagged domains
<DomainTable
  domains={data.domains}
  suspiciousDomains={data.suspicious_domains}
  highlightFlagged={true}
/>

// Show abuse score with color coding
function getAbuseColor(score: number) {
  if (score > 7) return 'red';
  if (score > 3) return 'yellow';
  return 'green';
}
```

---

### 3. GET /admin/trial/conversion-funnel

**Purpose**: Understand conversion patterns across trial lifecycle

**Query Parameters**: None

**Example Request**:
```bash
GET /admin/trial/conversion-funnel
```

**Example Response**:
```json
{
  "success": true,
  "funnel": {
    "total_trials_started": 1500,
    "completed_onboarding": 1400,
    "made_first_request": 1200,
    "made_10_requests": 800,
    "made_50_requests": 400,
    "made_100_requests": 250,
    "converted_to_paid": 200,
    "conversion_breakdown": {
      "converted_before_10_requests": 20,
      "converted_between_10_50_requests": 60,
      "converted_between_50_100_requests": 70,
      "converted_after_100_requests": 50
    },
    "avg_requests_at_conversion": 85.5,
    "median_requests_at_conversion": 75,
    "avg_tokens_at_conversion": 42500,
    "median_tokens_at_conversion": 37500
  }
}
```

**Frontend Usage**:
```typescript
// Display as funnel chart
const response = await fetch('/admin/trial/conversion-funnel', {
  headers: { 'Authorization': `Bearer ${adminApiKey}` }
});
const { funnel } = await response.json();

// Funnel visualization
<FunnelChart data={[
  { stage: 'Started', count: funnel.total_trials_started },
  { stage: '1st Request', count: funnel.made_first_request },
  { stage: '10 Requests', count: funnel.made_10_requests },
  { stage: '50 Requests', count: funnel.made_50_requests },
  { stage: '100 Requests', count: funnel.made_100_requests },
  { stage: 'Converted', count: funnel.converted_to_paid }
]} />

// Conversion breakdown pie chart
<PieChart data={[
  { label: 'Before 10', value: funnel.conversion_breakdown.converted_before_10_requests },
  { label: '10-50', value: funnel.conversion_breakdown.converted_between_10_50_requests },
  { label: '50-100', value: funnel.conversion_breakdown.converted_between_50_100_requests },
  { label: '100+', value: funnel.conversion_breakdown.converted_after_100_requests }
]} />

// Key metrics cards
<MetricCard label="Avg Requests at Conversion" value={funnel.avg_requests_at_conversion} />
<MetricCard label="Median Requests at Conversion" value={funnel.median_requests_at_conversion} />
```

---

### 4. GET /admin/trial/ip-analysis

**Purpose**: Detect multiple accounts from same IP (abuse detection)

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| min_accounts | integer | 2 | Minimum accounts per IP to show |

**Example Request**:
```bash
GET /admin/trial/ip-analysis?min_accounts=3
```

**Example Response**:
```json
{
  "success": true,
  "ips": [
    {
      "ip_address": "192.168.1.1",
      "total_accounts": 5,
      "active_trials": 4,
      "converted_accounts": 1,
      "total_requests": 4500,
      "total_tokens": 2250000,
      "flagged": true,
      "reason": "Multiple active trials from same IP"
    }
  ]
}
```

**⚠️ Current Status**: IP tracking not yet implemented. Returns empty array.

**TODO**:
1. Add `signup_ip` column to `users` table
2. Capture IP during registration
3. Update endpoint to query by IP

**Frontend Usage**:
```typescript
// Fetch IP analysis
const response = await fetch('/admin/trial/ip-analysis?min_accounts=2', {
  headers: { 'Authorization': `Bearer ${adminApiKey}` }
});
const { ips } = await response.json();

// Display flagged IPs
<IPTable
  ips={ips}
  highlightFlagged={true}
  onBlock={handleBlockIP}
/>
```

---

### 5. POST /admin/trial/save-conversion-metrics

**Purpose**: Save usage metrics when user converts from trial to paid

**Request Body**:
```json
{
  "user_id": 123,
  "api_key_id": 456,
  "requests_at_conversion": 85,
  "tokens_at_conversion": 42500,
  "credits_used_at_conversion": 4.25,
  "trial_days_used": 2,
  "converted_plan": "Pro Monthly",
  "conversion_trigger": "manual_upgrade"
}
```

**Example Request**:
```bash
POST /admin/trial/save-conversion-metrics
Content-Type: application/json

{
  "user_id": 123,
  "api_key_id": 456,
  "requests_at_conversion": 85,
  "tokens_at_conversion": 42500,
  "credits_used_at_conversion": 4.25,
  "trial_days_used": 2,
  "converted_plan": "Pro Monthly",
  "conversion_trigger": "manual_upgrade"
}
```

**Example Response**:
```json
{
  "success": true,
  "message": "Conversion metrics saved successfully"
}
```

**Frontend Usage**:
```typescript
// Call during upgrade flow
async function handleTrialUpgrade(userId: number, apiKeyId: number, plan: string) {
  // Get current usage
  const usage = await getCurrentUsage(apiKeyId);

  // Calculate trial days used
  const trialStart = await getTrialStartDate(apiKeyId);
  const daysUsed = Math.floor((Date.now() - trialStart) / (1000 * 60 * 60 * 24));

  // Save conversion metrics
  await fetch('/admin/trial/save-conversion-metrics', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${adminApiKey}`
    },
    body: JSON.stringify({
      user_id: userId,
      api_key_id: apiKeyId,
      requests_at_conversion: usage.requests,
      tokens_at_conversion: usage.tokens,
      credits_used_at_conversion: usage.credits,
      trial_days_used: daysUsed,
      converted_plan: plan,
      conversion_trigger: 'manual_upgrade'
    })
  });

  // Proceed with upgrade...
}
```

---

## Database Migration Required

**⚠️ IMPORTANT**: Run this migration before using the endpoints:

```bash
# Apply migration
psql $DATABASE_URL -f supabase/migrations/20260106000000_create_trial_conversion_metrics.sql
```

Or via Supabase CLI:
```bash
supabase db push
```

**Migration creates**:
- Table: `trial_conversion_metrics`
- Indexes for fast queries
- RLS policies

---

## Error Handling

All endpoints return standard error format:

```json
{
  "detail": "Error message here"
}
```

**Common HTTP Status Codes**:
- `200`: Success
- `401`: Unauthorized (missing or invalid admin API key)
- `422`: Validation error (invalid parameters)
- `500`: Server error

**Frontend Error Handling**:
```typescript
try {
  const response = await fetch('/admin/trial/users', {
    headers: { 'Authorization': `Bearer ${adminApiKey}` }
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Request failed');
  }

  const data = await response.json();
  // Process data...
} catch (error) {
  console.error('Failed to fetch trial users:', error);
  showErrorNotification(error.message);
}
```

---

## Testing Checklist

### Before Production Use:

- [ ] Run database migration
- [ ] Test `/admin/trial/users` with different filters
- [ ] Test `/admin/trial/domain-analysis` for abuse detection
- [ ] Test `/admin/trial/conversion-funnel` for funnel data
- [ ] Test `/admin/trial/ip-analysis` (returns empty until IP tracking added)
- [ ] Test `/admin/trial/save-conversion-metrics` during upgrade flow
- [ ] Verify admin authentication works
- [ ] Check pagination in trial users endpoint
- [ ] Test sorting options (requests, tokens, credits, created_at)
- [ ] Test domain filtering

### Test URLs (Replace with your API URL):

```bash
# 1. Get active trials
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  "https://api.gatewayz.ai/admin/trial/users?status=active&limit=10"

# 2. Get domain analysis
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  "https://api.gatewayz.ai/admin/trial/domain-analysis"

# 3. Get conversion funnel
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  "https://api.gatewayz.ai/admin/trial/conversion-funnel"

# 4. Get IP analysis
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  "https://api.gatewayz.ai/admin/trial/ip-analysis?min_accounts=2"

# 5. Save conversion metrics
curl -X POST -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id":123,"api_key_id":456,"requests_at_conversion":85,"tokens_at_conversion":42500,"credits_used_at_conversion":4.25,"trial_days_used":2,"converted_plan":"Pro Monthly","conversion_trigger":"manual_upgrade"}' \
  "https://api.gatewayz.ai/admin/trial/save-conversion-metrics"
```

---

## Next Steps

### Immediate:
1. ✅ Run database migration
2. ✅ Test endpoints with Postman/cURL
3. ✅ Integrate into frontend

### Future Enhancements:
- [ ] Implement IP tracking (`signup_ip` field)
- [ ] Add automated abuse alerts (email/Slack when domain flagged)
- [ ] Create Grafana dashboards for trial metrics
- [ ] Add real-time conversion tracking
- [ ] Implement A/B testing for trial configurations

---

## Support

**Questions?** Contact the backend team or check:
- API Documentation: `/docs` (Swagger UI)
- Source Code: `src/routes/trial_analytics.py`
- Schemas: `src/schemas/trial_analytics.py`

---

**Deployment Status**: ✅ Live on `main` branch (commit `58f08795`)
