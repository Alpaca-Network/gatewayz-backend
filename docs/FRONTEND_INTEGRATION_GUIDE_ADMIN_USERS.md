# Frontend Integration Guide: Admin Users Dashboard

**Status**: ✅ Deployed to Production (main branch)
**Backend Commit**: `2d54dea0`
**Date**: January 4, 2026
**Backend Developer**: Manjesh Prasad

---

## Overview

The `/admin/users` endpoint has been upgraded with comprehensive search and pagination capabilities. This guide provides everything your frontend team needs to integrate this into the admin dashboard.

---

## 🎯 What Changed

### Before (Old Endpoint)
```
GET /admin/users?limit=50&offset=0
```
- Basic pagination only
- No search/filtering
- Statistics from entire database (not filtered results)

### After (New Endpoint - LIVE NOW)
```
GET /admin/users?email=test&is_active=true&limit=10&offset=0
```
- ✅ Email search (case-insensitive partial match)
- ✅ API key search (case-insensitive partial match)
- ✅ Active status filtering (boolean)
- ✅ Pagination (1-10000 records per page)
- ✅ Statistics reflect **filtered results only** (not entire database)
- ✅ **30-60x performance improvement**
- ✅ **Backward compatible** (no parameters still works)

---

## 📡 API Endpoint Details

### Base URL
```
Production: https://api.gatewayz.ai/admin/users
Staging: https://staging-api.gatewayz.ai/admin/users
Development: http://localhost:8000/admin/users
```

### HTTP Method
```
GET
```

### Authentication Required
```
Authorization: Bearer {admin_user_api_key}
```

**Important**: This endpoint requires an API key from a user with `role='admin'` in the database. Regular user API keys will return `403 Forbidden`.

---

## 📥 Request Parameters

All parameters are **optional** and can be combined.

| Parameter | Type | Required | Default | Min | Max | Description |
|-----------|------|----------|---------|-----|-----|-------------|
| `email` | string | No | null | - | - | Case-insensitive partial match (e.g., "john" matches "john@example.com") |
| `api_key` | string | No | null | - | - | Case-insensitive partial match (e.g., "gw_live" matches "gw_live_123...") |
| `is_active` | boolean | No | null | - | - | Filter by active status (true = active only, false = inactive only, null/omit = all) |
| `limit` | integer | No | 100 | 1 | 10000 | Number of users to return per page |
| `offset` | integer | No | 0 | 0 | ∞ | Number of users to skip (for pagination) |

### Example Requests

#### 1. No Filters (Backward Compatible)
```bash
GET /admin/users?limit=10
```

#### 2. Email Search
```bash
GET /admin/users?email=gmail&limit=10
```

#### 3. Active Users Only
```bash
GET /admin/users?is_active=true&limit=10
```

#### 4. API Key Search
```bash
GET /admin/users?api_key=gw_live&limit=10
```

#### 5. Combined Filters
```bash
GET /admin/users?email=test&is_active=true&limit=10&offset=0
```

#### 6. Pagination (Page 2)
```bash
GET /admin/users?email=gmail&limit=10&offset=10
```

---

## 📤 Response Format

### Success Response (200 OK)

```json
{
  "status": "success",
  "total_users": 150,
  "has_more": true,
  "pagination": {
    "limit": 10,
    "offset": 0,
    "current_page": 1,
    "total_pages": 15
  },
  "filters_applied": {
    "email": "test",
    "api_key": null,
    "is_active": true
  },
  "statistics": {
    "active_users": 140,
    "inactive_users": 10,
    "admin_users": 5,
    "developer_users": 20,
    "regular_users": 125,
    "total_credits": 15000.50,
    "average_credits": 100.00,
    "subscription_breakdown": {
      "trial": 50,
      "active": 80,
      "cancelled": 20
    }
  },
  "users": [
    {
      "id": 123,
      "username": "John Doe",
      "email": "john@test.com",
      "credits": 100.5,
      "is_active": true,
      "role": "user",
      "registration_date": "2025-12-01T10:00:00Z",
      "auth_method": "email",
      "subscription_status": "active",
      "trial_expires_at": null,
      "created_at": "2025-12-01T10:00:00Z",
      "updated_at": "2026-01-03T15:30:00Z"
    }
    // ... more users (up to 'limit' count)
  ],
  "timestamp": "2026-01-04T10:30:00Z"
}
```

### Response Fields Explained

#### Root Level
- `status` (string): Always "success" for successful requests
- `total_users` (integer): **Total count of users matching the filters** (NOT total in database)
- `has_more` (boolean): Whether more results exist beyond current page
- `timestamp` (string): ISO 8601 timestamp of response generation

#### Pagination Object
- `limit` (integer): Records per page (from request)
- `offset` (integer): Records skipped (from request)
- `current_page` (integer): Calculated page number (offset/limit + 1)
- `total_pages` (integer): Total pages based on filtered results

#### Filters Applied Object
Shows which filters were applied (helps with debugging):
- `email` (string | null): Email filter value or null
- `api_key` (string | null): API key filter value or null
- `is_active` (boolean | null): Active status filter or null

#### Statistics Object
**CRITICAL**: These stats reflect **ONLY the filtered users**, NOT the entire database!

- `active_users` (integer): Count of active users in filtered results
- `inactive_users` (integer): Count of inactive users in filtered results
- `admin_users` (integer): Count of admin role users in filtered results
- `developer_users` (integer): Count of developer role users in filtered results
- `regular_users` (integer): Count of regular (user role) users in filtered results
- `total_credits` (float): Sum of all credits from filtered users
- `average_credits` (float): Average credits per user (from filtered results)
- `subscription_breakdown` (object): Count of each subscription status in filtered results

#### Users Array
Array of user objects (length ≤ limit):
- `id` (integer): User's unique ID
- `username` (string): User's display name
- `email` (string): User's email address
- `credits` (float): User's current credit balance
- `is_active` (boolean): Whether user account is active
- `role` (string): User role ("user", "admin", "developer")
- `registration_date` (string): ISO 8601 registration timestamp
- `auth_method` (string): Authentication method ("email", "google", "phone", etc.)
- `subscription_status` (string): Subscription status ("trial", "active", "cancelled", etc.)
- `trial_expires_at` (string | null): Trial expiration date or null
- `created_at` (string): ISO 8601 creation timestamp
- `updated_at` (string): ISO 8601 last update timestamp

---

## ❌ Error Responses

### 401 Unauthorized
```json
{
  "detail": "Authorization header is required"
}
```
**Cause**: No Authorization header provided
**Fix**: Add `Authorization: Bearer {admin_api_key}` header

### 404 Not Found
```json
{
  "detail": "User not found"
}
```
**Cause**: Invalid API key or user doesn't exist
**Fix**: Verify the API key is correct and belongs to an active user

### 403 Forbidden
```json
{
  "detail": "Administrator privileges required"
}
```
**Cause**: API key belongs to non-admin user
**Fix**: Use an API key from a user with `role='admin'`

### 422 Validation Error
```json
{
  "detail": [
    {
      "loc": ["query", "limit"],
      "msg": "ensure this value is less than or equal to 10000",
      "type": "value_error.number.not_le"
    }
  ]
}
```
**Cause**: Invalid parameter values (e.g., limit > 10000)
**Fix**: Check parameter constraints in the table above

### 500 Internal Server Error
```json
{
  "detail": "Failed to get users information"
}
```
**Cause**: Server-side error (database issue, etc.)
**Fix**: Check server logs, contact backend team

---

## 🎨 Frontend Implementation Guide

### Recommended UI Components

#### 1. Search Filters Section
```typescript
interface SearchFilters {
  email: string | null;
  apiKey: string | null;
  isActive: boolean | null;
}

// Example state
const [filters, setFilters] = useState<SearchFilters>({
  email: null,
  apiKey: null,
  isActive: null,
});
```

**UI Elements**:
- Email search input (text field with debounce)
- API key search input (text field with debounce)
- Active status dropdown (All / Active / Inactive)
- Clear filters button

#### 2. Pagination Controls
```typescript
interface PaginationState {
  limit: number;
  offset: number;
  currentPage: number;
  totalPages: number;
}

// Example state
const [pagination, setPagination] = useState<PaginationState>({
  limit: 10,
  offset: 0,
  currentPage: 1,
  totalPages: 0,
});
```

**UI Elements**:
- Items per page dropdown (10, 25, 50, 100)
- Previous/Next buttons
- Page number input
- Total pages display
- "Showing X-Y of Z results" text

#### 3. Statistics Dashboard
```typescript
interface Statistics {
  activeUsers: number;
  inactiveUsers: number;
  adminUsers: number;
  developerUsers: number;
  regularUsers: number;
  totalCredits: number;
  averageCredits: number;
  subscriptionBreakdown: Record<string, number>;
}
```

**UI Elements**:
- Stats cards (Active/Inactive/Total users)
- Role distribution chart (Admin/Developer/Regular)
- Credit statistics (Total/Average)
- Subscription breakdown chart

#### 4. Users Table
**Columns**:
- ID
- Username
- Email
- Credits
- Active Status (badge/chip)
- Role (badge/chip)
- Subscription Status
- Registration Date
- Actions (View/Edit/Deactivate buttons)

---

## 📝 Sample Frontend Code

### React + TypeScript Example

```typescript
import { useState, useEffect } from 'react';

interface AdminUsersResponse {
  status: string;
  total_users: number;
  has_more: boolean;
  pagination: {
    limit: number;
    offset: number;
    current_page: number;
    total_pages: number;
  };
  filters_applied: {
    email: string | null;
    api_key: string | null;
    is_active: boolean | null;
  };
  statistics: {
    active_users: number;
    inactive_users: number;
    admin_users: number;
    developer_users: number;
    regular_users: number;
    total_credits: number;
    average_credits: number;
    subscription_breakdown: Record<string, number>;
  };
  users: User[];
  timestamp: string;
}

interface User {
  id: number;
  username: string;
  email: string;
  credits: number;
  is_active: boolean;
  role: string;
  registration_date: string;
  auth_method: string;
  subscription_status: string;
  trial_expires_at: string | null;
  created_at: string;
  updated_at: string;
}

function AdminUsersDashboard() {
  const [data, setData] = useState<AdminUsersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [emailFilter, setEmailFilter] = useState('');
  const [apiKeyFilter, setApiKeyFilter] = useState('');
  const [activeFilter, setActiveFilter] = useState<boolean | null>(null);

  // Pagination
  const [limit, setLimit] = useState(10);
  const [offset, setOffset] = useState(0);

  // Admin API key from auth context/storage
  const adminApiKey = 'YOUR_ADMIN_API_KEY'; // Get from auth context

  const fetchUsers = async () => {
    setLoading(true);
    setError(null);

    try {
      // Build query parameters
      const params = new URLSearchParams();
      if (emailFilter) params.append('email', emailFilter);
      if (apiKeyFilter) params.append('api_key', apiKeyFilter);
      if (activeFilter !== null) params.append('is_active', String(activeFilter));
      params.append('limit', String(limit));
      params.append('offset', String(offset));

      const response = await fetch(
        `https://api.gatewayz.ai/admin/users?${params.toString()}`,
        {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${adminApiKey}`,
            'Content-Type': 'application/json',
          },
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to fetch users');
      }

      const data: AdminUsersResponse = await response.json();
      setData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  };

  // Fetch on mount and when filters/pagination change
  useEffect(() => {
    const debounceTimer = setTimeout(() => {
      fetchUsers();
    }, 500); // Debounce search inputs

    return () => clearTimeout(debounceTimer);
  }, [emailFilter, apiKeyFilter, activeFilter, limit, offset]);

  const handlePageChange = (newPage: number) => {
    setOffset((newPage - 1) * limit);
  };

  const handleLimitChange = (newLimit: number) => {
    setLimit(newLimit);
    setOffset(0); // Reset to first page
  };

  const handleClearFilters = () => {
    setEmailFilter('');
    setApiKeyFilter('');
    setActiveFilter(null);
    setOffset(0);
  };

  if (loading && !data) {
    return <div>Loading...</div>;
  }

  if (error) {
    return <div>Error: {error}</div>;
  }

  return (
    <div className="admin-users-dashboard">
      {/* Statistics Section */}
      <div className="statistics">
        <StatCard title="Total Users" value={data?.total_users || 0} />
        <StatCard title="Active Users" value={data?.statistics.active_users || 0} />
        <StatCard title="Inactive Users" value={data?.statistics.inactive_users || 0} />
        <StatCard title="Total Credits" value={`$${data?.statistics.total_credits.toFixed(2) || 0}`} />
        <StatCard title="Avg Credits" value={`$${data?.statistics.average_credits.toFixed(2) || 0}`} />
      </div>

      {/* Filters Section */}
      <div className="filters">
        <input
          type="text"
          placeholder="Search by email..."
          value={emailFilter}
          onChange={(e) => setEmailFilter(e.target.value)}
        />
        <input
          type="text"
          placeholder="Search by API key..."
          value={apiKeyFilter}
          onChange={(e) => setApiKeyFilter(e.target.value)}
        />
        <select
          value={activeFilter === null ? '' : String(activeFilter)}
          onChange={(e) => setActiveFilter(e.target.value === '' ? null : e.target.value === 'true')}
        >
          <option value="">All Users</option>
          <option value="true">Active Only</option>
          <option value="false">Inactive Only</option>
        </select>
        <button onClick={handleClearFilters}>Clear Filters</button>
      </div>

      {/* Users Table */}
      <table className="users-table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Username</th>
            <th>Email</th>
            <th>Credits</th>
            <th>Status</th>
            <th>Role</th>
            <th>Subscription</th>
            <th>Registered</th>
          </tr>
        </thead>
        <tbody>
          {data?.users.map((user) => (
            <tr key={user.id}>
              <td>{user.id}</td>
              <td>{user.username}</td>
              <td>{user.email}</td>
              <td>${user.credits.toFixed(2)}</td>
              <td>
                <span className={user.is_active ? 'badge-active' : 'badge-inactive'}>
                  {user.is_active ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td>
                <span className={`badge-role-${user.role}`}>{user.role}</span>
              </td>
              <td>{user.subscription_status}</td>
              <td>{new Date(user.registration_date).toLocaleDateString()}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Pagination Controls */}
      <div className="pagination">
        <select value={limit} onChange={(e) => handleLimitChange(Number(e.target.value))}>
          <option value="10">10 per page</option>
          <option value="25">25 per page</option>
          <option value="50">50 per page</option>
          <option value="100">100 per page</option>
          <option value="1000">1000 per page</option>
          <option value="10000">10000 per page</option>
        </select>

        <button
          disabled={!data || offset === 0}
          onClick={() => handlePageChange(data!.pagination.current_page - 1)}
        >
          Previous
        </button>

        <span>
          Page {data?.pagination.current_page || 1} of {data?.pagination.total_pages || 1}
        </span>

        <button
          disabled={!data || !data.has_more}
          onClick={() => handlePageChange(data!.pagination.current_page + 1)}
        >
          Next
        </button>

        <span className="result-count">
          Showing {offset + 1} - {Math.min(offset + limit, data?.total_users || 0)} of {data?.total_users || 0} results
        </span>
      </div>
    </div>
  );
}

function StatCard({ title, value }: { title: string; value: string | number }) {
  return (
    <div className="stat-card">
      <h3>{title}</h3>
      <p className="stat-value">{value}</p>
    </div>
  );
}

export default AdminUsersDashboard;
```

---

## ⚡ Performance Considerations

### Search Debouncing
**Always debounce search inputs** to avoid excessive API calls:

```typescript
// Good ✅
const [searchQuery, setSearchQuery] = useState('');

useEffect(() => {
  const timer = setTimeout(() => {
    fetchUsers(searchQuery);
  }, 500); // 500ms debounce

  return () => clearTimeout(timer);
}, [searchQuery]);

// Bad ❌ - Fires on every keystroke
onChange={(e) => fetchUsers(e.target.value)}
```

### Caching
Consider caching results to reduce API calls:

```typescript
const cache = useRef<Map<string, AdminUsersResponse>>(new Map());

const getCacheKey = (filters: any, pagination: any) => {
  return JSON.stringify({ filters, pagination });
};

const fetchUsers = async () => {
  const cacheKey = getCacheKey(filters, pagination);

  // Check cache first
  if (cache.current.has(cacheKey)) {
    setData(cache.current.get(cacheKey)!);
    return;
  }

  // Fetch from API
  const response = await fetch(/* ... */);
  const data = await response.json();

  // Cache result (with 5 min expiry)
  cache.current.set(cacheKey, data);
  setTimeout(() => cache.current.delete(cacheKey), 5 * 60 * 1000);

  setData(data);
};
```

### Optimistic Pagination
Show next/previous page immediately while loading new data:

```typescript
const [optimisticPage, setOptimisticPage] = useState(1);

const handleNextPage = () => {
  setOptimisticPage(prev => prev + 1); // Update UI immediately
  setOffset(offset + limit); // Trigger data fetch
};
```

---

## 🔒 Security Notes

### 1. Never Expose Admin API Key in Frontend Code
```typescript
// Bad ❌ - Hardcoded key
const adminApiKey = "gw_live_12345...";

// Good ✅ - From secure storage/auth context
const { adminApiKey } = useAuth();
```

### 2. Validate Admin Role on Frontend
Even though backend validates, show appropriate UI:

```typescript
if (currentUser.role !== 'admin') {
  return <Redirect to="/dashboard" />;
}
```

### 3. Handle Expired Sessions
```typescript
if (response.status === 401 || response.status === 403) {
  // Redirect to login
  logout();
  navigate('/login');
}
```

---

## 🧪 Testing Checklist

### Functional Tests
- [ ] Email search returns correct filtered results
- [ ] API key search returns correct filtered results
- [ ] Active status filter works (true/false/null)
- [ ] Combined filters work together
- [ ] Pagination works (next/previous/jump to page)
- [ ] Statistics reflect filtered results (not entire database)
- [ ] Clearing filters resets to all users
- [ ] Changing limit resets to page 1
- [ ] Empty search results show appropriate message

### Edge Cases
- [ ] Search with no results shows empty state
- [ ] Pagination at last page disables "Next" button
- [ ] Pagination at first page disables "Previous" button
- [ ] Invalid limit values show validation error
- [ ] Invalid offset values are handled gracefully
- [ ] Long email addresses don't break table layout
- [ ] Large credit values display correctly
- [ ] Null/undefined values display as appropriate

### Performance Tests
- [ ] Search debounce works (no API call on every keystroke)
- [ ] Loading states display correctly
- [ ] Table renders smoothly with 100 users
- [ ] Pagination changes are instant
- [ ] No memory leaks on component unmount

### Security Tests
- [ ] Non-admin users can't access page
- [ ] Invalid API key shows error
- [ ] Expired sessions redirect to login
- [ ] API key not visible in browser console/network tab

---

## 📊 Analytics & Monitoring

### Events to Track

```typescript
// Track search usage
analytics.track('Admin Users Search', {
  filter_type: 'email', // or 'api_key' or 'is_active'
  has_results: data.total_users > 0,
  result_count: data.total_users,
});

// Track pagination
analytics.track('Admin Users Pagination', {
  page: data.pagination.current_page,
  limit: data.pagination.limit,
  total_pages: data.pagination.total_pages,
});

// Track filter combinations
analytics.track('Admin Users Filter Applied', {
  email_filter: !!filters.email,
  api_key_filter: !!filters.apiKey,
  active_filter: filters.isActive !== null,
  combined_filters: [filters.email, filters.apiKey, filters.isActive].filter(Boolean).length,
});
```

---

## 🐛 Troubleshooting

### Issue: Getting 404 "User not found"
**Solution**:
- Verify you're using an admin user's API key (not the ADMIN_API_KEY env var)
- Check that the user exists and has `role='admin'` in database

### Issue: Statistics don't match expectations
**Solution**:
- Remember: statistics reflect **filtered results only**
- If filtering for "gmail" users, stats show only Gmail users' totals
- Check `filters_applied` in response to verify what filters are active

### Issue: Search is slow
**Solution**:
- Ensure database migration was applied (creates indexes)
- Check that debouncing is implemented (500ms recommended)
- Consider adding loading states to improve perceived performance

### Issue: Pagination shows wrong page numbers
**Solution**:
- Verify offset calculation: `offset = (page - 1) * limit`
- Check that offset resets to 0 when filters change
- Ensure `has_more` is used to disable "Next" button

---

## 📞 Support

### Backend Team Contact
- **Developer**: Manjesh Prasad
- **Backend Commit**: `2d54dea0`
- **Documentation**: See `docs/ADMIN_USERS_ENDPOINT_STRUCTURE.md` in backend repo

### Additional Resources
- Full technical documentation: `docs/ADMIN_USERS_ENDPOINT_STRUCTURE.md`
- Implementation summary: `docs/ADMIN_USERS_SEARCH_IMPLEMENTATION_SUMMARY.md`
- Testing checklist: `docs/TESTING_CHECKLIST_ADMIN_USERS.md`
- Backend repo: https://github.com/Alpaca-Network/gatewayz-backend

---

## ✅ Deployment Checklist

### Before Starting Frontend Development
- [ ] Verify backend is deployed to production (commit `2d54dea0` on main)
- [ ] Confirm database migration has been applied
- [ ] Test endpoint manually with Postman/curl
- [ ] Verify admin API key is available and works
- [ ] Review this documentation thoroughly

### During Development
- [ ] Implement search filters UI
- [ ] Implement pagination controls
- [ ] Implement statistics dashboard
- [ ] Add debouncing to search inputs
- [ ] Add loading states
- [ ] Add error handling
- [ ] Test all filter combinations
- [ ] Test pagination edge cases

### Before Frontend Deployment
- [ ] Complete all functional tests
- [ ] Complete all edge case tests
- [ ] Complete security tests
- [ ] Performance test with large datasets
- [ ] Review with backend team
- [ ] Update frontend documentation

---

## 🚀 Quick Start Summary

**For the impatient developer:**

1. **Endpoint**: `GET https://api.gatewayz.ai/admin/users`
2. **Auth**: `Authorization: Bearer {admin_user_api_key}`
3. **Params**: `?email=&api_key=&is_active=&limit=&offset=`
4. **Response**: See "Response Format" section above
5. **Code**: Copy the React example above
6. **Test**: Use Postman collection or curl examples
7. **Deploy**: Run tests, review, deploy!

---

**Happy coding! 🎉**
