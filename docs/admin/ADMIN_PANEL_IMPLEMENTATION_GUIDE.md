# Admin User Management Panel - Implementation Guide

This guide provides a complete walkthrough for building a robust admin user management panel using the Gatewayz backend API.

## Table of Contents

1. [Overview](#overview)
2. [Authentication Setup](#authentication-setup)
3. [Core Features](#core-features)
4. [API Integration](#api-integration)
5. [UI Components](#ui-components)
6. [Implementation Examples](#implementation-examples)
7. [Security Best Practices](#security-best-practices)
8. [Error Handling](#error-handling)
9. [Advanced Features](#advanced-features)

---

## Overview

### Admin Panel Capabilities

Your admin panel should support the following user management operations:

- **View Users**: List all users with filtering, sorting, and search
- **User Details**: View comprehensive user information
- **Edit Users**: Update username, email, and status
- **Manage Roles**: Change user roles (user, developer, admin)
- **Manage Tiers**: Update subscription tiers (basic, pro, max)
- **Credit Management**: View, add, or set user credits
- **Activate/Deactivate**: Enable or disable user accounts
- **Delete Users**: Permanently remove users (with confirmation)
- **View Transactions**: Monitor credit transactions and usage
- **System Monitoring**: View platform-wide statistics

---

## Authentication Setup

### 1. Backend Environment Configuration

**IMPORTANT:** All admin endpoints now require the `ADMIN_API_KEY` environment variable.

Set this in your backend `.env` file:

```bash
# Generate a secure admin API key
python3 -c "import secrets; print('sk_admin_live_' + secrets.token_urlsafe(32))"

# Add to your backend .env file:
ADMIN_API_KEY=sk_admin_live_your-secure-admin-key-here
```

### 2. Admin Panel Environment Configuration

Create a `.env` file in your admin panel:

```env
# Admin API Configuration
VITE_API_BASE_URL=https://your-api.gatewayz.app
VITE_ADMIN_API_KEY=sk_admin_live_your-secure-admin-key-here  # Same key as backend

# Optional: Different environments
VITE_API_BASE_URL_DEV=http://localhost:8000
VITE_API_BASE_URL_STAGING=https://staging-api.gatewayz.app
VITE_API_BASE_URL_PROD=https://api.gatewayz.app

# Use different admin keys per environment for security
VITE_ADMIN_API_KEY_DEV=sk_admin_dev_xxxxx
VITE_ADMIN_API_KEY_STAGING=sk_admin_staging_xxxxx
VITE_ADMIN_API_KEY_PROD=sk_admin_live_xxxxx
```

### 3. API Client Setup

Create an API client with admin authentication:

```typescript
// src/lib/adminApi.ts
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
const ADMIN_API_KEY = import.meta.env.VITE_ADMIN_API_KEY;

class AdminAPI {
  private baseURL: string;
  private apiKey: string;

  constructor() {
    this.baseURL = API_BASE_URL;
    this.apiKey = ADMIN_API_KEY;

    if (!this.apiKey) {
      throw new Error('ADMIN_API_KEY environment variable is required');
    }
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;

    const headers = {
      'Authorization': `Bearer ${this.apiKey}`,
      'Content-Type': 'application/json',
      ...options.headers,
    };

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({
        detail: 'Unknown error'
      }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  }

  // API methods will be added below...
}

export const adminApi = new AdminAPI();
```

---

## Core Features

### 1. List All Users

**Endpoint:** `GET /admin/users`

**Purpose:** Display a table of all users with statistics

**Implementation:**

```typescript
// Add to AdminAPI class
async getAllUsers() {
  return this.request<{
    status: string;
    total_users: number;
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
  }>('/admin/users');
}
```

**React Component Example:**

```typescript
// src/components/UsersList.tsx
import { useQuery } from '@tanstack/react-query';
import { adminApi } from '@/lib/adminApi';

export function UsersList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => adminApi.getAllUsers(),
    refetchInterval: 30000, // Refresh every 30s
  });

  if (isLoading) return <LoadingSpinner />;
  if (error) return <ErrorMessage error={error} />;

  return (
    <div className="space-y-6">
      {/* Statistics Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <StatCard
          title="Total Users"
          value={data.total_users}
          icon={<UsersIcon />}
        />
        <StatCard
          title="Active Users"
          value={data.statistics.active_users}
          icon={<CheckCircleIcon />}
          trend="up"
        />
        <StatCard
          title="Total Credits"
          value={`$${data.statistics.total_credits.toFixed(2)}`}
          icon={<CreditCardIcon />}
        />
        <StatCard
          title="Avg Credits/User"
          value={`$${data.statistics.average_credits.toFixed(2)}`}
          icon={<TrendingUpIcon />}
        />
      </div>

      {/* Users Table */}
      <UsersTable users={data.users} />
    </div>
  );
}
```

### 2. View User Details

**Endpoint:** `GET /admin/users/{user_id}`

**Purpose:** Show comprehensive user information including API keys, usage, and activity

**Implementation:**

```typescript
// Add to AdminAPI class
async getUserById(userId: number) {
  return this.request<{
    status: string;
    user: User;
    api_keys: ApiKey[];
    recent_usage: UsageRecord[];
    recent_activity: ActivityLog[];
    timestamp: string;
  }>(`/admin/users/${userId}`);
}
```

**React Component Example:**

```typescript
// src/components/UserDetailsModal.tsx
export function UserDetailsModal({ userId, onClose }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'user', userId],
    queryFn: () => adminApi.getUserById(userId),
    enabled: !!userId,
  });

  if (isLoading) return <ModalSkeleton />;

  const user = data.user;

  return (
    <Modal onClose={onClose} size="xl">
      <div className="space-y-6">
        {/* User Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Avatar name={user.username} />
            <div>
              <h2 className="text-2xl font-bold">{user.username}</h2>
              <p className="text-gray-600">{user.email}</p>
            </div>
          </div>
          <StatusBadge isActive={user.is_active} />
        </div>

        {/* User Info Grid */}
        <div className="grid grid-cols-2 gap-4">
          <InfoCard label="User ID" value={user.id} />
          <InfoCard label="Role" value={user.role || 'user'} />
          <InfoCard label="Tier" value={user.tier || 'basic'} />
          <InfoCard
            label="Credits"
            value={`$${user.credits.toFixed(2)}`}
          />
          <InfoCard
            label="Subscription Status"
            value={user.subscription_status}
          />
          <InfoCard
            label="Registered"
            value={formatDate(user.registration_date)}
          />
        </div>

        {/* API Keys Section */}
        <div>
          <h3 className="text-lg font-semibold mb-3">API Keys</h3>
          <APIKeysTable keys={data.api_keys} />
        </div>

        {/* Recent Activity */}
        <div>
          <h3 className="text-lg font-semibold mb-3">Recent Activity</h3>
          <ActivityTimeline activity={data.recent_activity} />
        </div>

        {/* Recent Usage */}
        <div>
          <h3 className="text-lg font-semibold mb-3">Recent Usage</h3>
          <UsageChart data={data.recent_usage} />
        </div>
      </div>
    </Modal>
  );
}
```

### 3. Edit User Details

**Endpoint:** `PUT /admin/users/{user_id}`

**Purpose:** Update username, email, or active status

**Implementation:**

```typescript
// Add to AdminAPI class
async updateUser(
  userId: number,
  data: {
    username?: string;
    email?: string;
    is_active?: boolean;
  }
) {
  return this.request(`/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}
```

**React Component Example:**

```typescript
// src/components/EditUserForm.tsx
import { useForm } from 'react-hook-form';
import { useMutation, useQueryClient } from '@tanstack/react-query';

export function EditUserForm({ user, onSuccess }: Props) {
  const queryClient = useQueryClient();
  const { register, handleSubmit, formState: { errors } } = useForm({
    defaultValues: {
      username: user.username,
      email: user.email,
      is_active: user.is_active,
    },
  });

  const mutation = useMutation({
    mutationFn: (data) => adminApi.updateUser(user.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries(['admin', 'users']);
      queryClient.invalidateQueries(['admin', 'user', user.id]);
      toast.success('User updated successfully');
      onSuccess?.();
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });

  const onSubmit = (data) => {
    // Only send fields that changed
    const changedFields = {};
    if (data.username !== user.username)
      changedFields.username = data.username;
    if (data.email !== user.email)
      changedFields.email = data.email;
    if (data.is_active !== user.is_active)
      changedFields.is_active = data.is_active;

    if (Object.keys(changedFields).length === 0) {
      toast.info('No changes detected');
      return;
    }

    mutation.mutate(changedFields);
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <Input
        label="Username"
        {...register('username', { required: 'Username is required' })}
        error={errors.username?.message}
      />

      <Input
        label="Email"
        type="email"
        {...register('email', {
          required: 'Email is required',
          pattern: {
            value: /^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$/i,
            message: 'Invalid email address',
          },
        })}
        error={errors.email?.message}
      />

      <Checkbox
        label="Active Account"
        {...register('is_active')}
      />

      <div className="flex justify-end space-x-3">
        <Button type="button" variant="secondary" onClick={onSuccess}>
          Cancel
        </Button>
        <Button
          type="submit"
          isLoading={mutation.isPending}
        >
          Save Changes
        </Button>
      </div>
    </form>
  );
}
```

### 4. Manage User Role

**Endpoint:** `POST /admin/roles/update`

**Purpose:** Change user role (user, developer, admin)

**Implementation:**

```typescript
// Add to AdminAPI class
async updateUserRole(
  userId: number,
  newRole: 'user' | 'developer' | 'admin',
  reason?: string
) {
  return this.request('/admin/roles/update', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      new_role: newRole,
      reason,
    }),
  });
}
```

**React Component Example:**

```typescript
// src/components/RoleSelector.tsx
export function RoleSelector({ user }: Props) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [showReasonModal, setShowReasonModal] = useState(false);
  const [selectedRole, setSelectedRole] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: ({ role, reason }: { role: string; reason: string }) =>
      adminApi.updateUserRole(user.id, role, reason),
    onSuccess: () => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success('Role updated successfully');
      setShowReasonModal(false);
      setReason('');
    },
  });

  const handleRoleChange = (newRole: string) => {
    if (newRole === 'admin') {
      // Require reason for admin role
      setSelectedRole(newRole);
      setShowReasonModal(true);
    } else {
      mutation.mutate({ role: newRole, reason: '' });
    }
  };

  const confirmRoleChange = () => {
    if (selectedRole) {
      mutation.mutate({ role: selectedRole, reason });
    }
  };

  const currentRole = user.role || 'user';

  return (
    <>
      <Select
        label="User Role"
        value={currentRole}
        onChange={(e) => handleRoleChange(e.target.value)}
        disabled={mutation.isPending}
      >
        <option value="user">User</option>
        <option value="developer">Developer</option>
        <option value="admin">Admin</option>
      </Select>

      {/* Reason Modal for Admin Role */}
      {showReasonModal && (
        <Modal onClose={() => setShowReasonModal(false)}>
          <h3 className="text-lg font-semibold mb-4">
            Confirm Admin Role Assignment
          </h3>
          <p className="text-gray-600 mb-4">
            You are about to grant admin privileges to {user.username}.
            Please provide a reason for this change.
          </p>
          <Textarea
            label="Reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g., Promoted to platform administrator"
            required
          />
          <div className="flex justify-end space-x-3 mt-4">
            <Button
              variant="secondary"
              onClick={() => setShowReasonModal(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={confirmRoleChange}
              disabled={!reason.trim()}
              isLoading={mutation.isPending}
            >
              Confirm
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
```

### 5. Manage User Tier

**Endpoint:** `PUT /admin/users/{user_id}/tier`

**Purpose:** Update subscription tier (basic, pro, max)

**Implementation:**

```typescript
// Add to AdminAPI class
async updateUserTier(
  userId: number,
  tier: 'basic' | 'pro' | 'max'
) {
  return this.request(`/admin/users/${userId}/tier?tier=${tier}`, {
    method: 'PUT',
  });
}
```

**React Component Example:**

```typescript
// src/components/TierSelector.tsx
export function TierSelector({ user }: Props) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (tier: string) => adminApi.updateUserTier(user.id, tier),
    onSuccess: () => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success('Tier updated successfully');
    },
  });

  const tiers = [
    { value: 'basic', label: 'Basic', color: 'gray' },
    { value: 'pro', label: 'Pro', color: 'blue' },
    { value: 'max', label: 'MAX', color: 'purple' },
  ];

  const currentTier = user.tier || 'basic';

  return (
    <div className="space-y-2">
      <label className="text-sm font-medium text-gray-700">
        Subscription Tier
      </label>
      <div className="flex space-x-2">
        {tiers.map((tier) => (
          <button
            key={tier.value}
            onClick={() => mutation.mutate(tier.value)}
            disabled={mutation.isPending || currentTier === tier.value}
            className={`
              px-4 py-2 rounded-lg font-medium transition-colors
              ${currentTier === tier.value
                ? `bg-${tier.color}-600 text-white`
                : `bg-gray-100 text-gray-700 hover:bg-${tier.color}-100`
              }
              ${mutation.isPending ? 'opacity-50 cursor-not-allowed' : ''}
            `}
          >
            {tier.label}
          </button>
        ))}
      </div>
    </div>
  );
}
```

### 6. Credit Management

**Endpoints:**
- `PUT /admin/users/{user_id}/credits` - Set absolute balance
- `POST /admin/add_credits` - Add credits incrementally

**Implementation:**

```typescript
// Add to AdminAPI class
async setUserCredits(userId: number, credits: number) {
  return this.request(
    `/admin/users/${userId}/credits?credits=${credits}`,
    { method: 'PUT' }
  );
}

async addUserCredits(apiKey: string, credits: number) {
  return this.request('/admin/add_credits', {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey, credits }),
  });
}

async getUserTransactions(
  userId: number,
  limit: number = 50,
  offset: number = 0
) {
  const params = new URLSearchParams({
    user_id: userId.toString(),
    limit: limit.toString(),
    offset: offset.toString(),
    include_summary: 'true',
  });

  return this.request(`/admin/credit-transactions?${params}`);
}
```

**React Component Example:**

```typescript
// src/components/CreditManager.tsx
export function CreditManager({ user }: Props) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'set' | 'add'>('add');
  const [amount, setAmount] = useState('');

  const setMutation = useMutation({
    mutationFn: (credits: number) =>
      adminApi.setUserCredits(user.id, credits),
    onSuccess: (data) => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success(
        `Credits set to $${data.balance_after}
        (${data.difference > 0 ? '+' : ''}$${data.difference})`
      );
      setAmount('');
    },
  });

  const addMutation = useMutation({
    mutationFn: (credits: number) =>
      adminApi.addUserCredits(user.api_key, credits),
    onSuccess: (data) => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success(`Added $${data.credits} credits`);
      setAmount('');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const credits = parseFloat(amount);

    if (isNaN(credits) || credits < 0) {
      toast.error('Please enter a valid amount');
      return;
    }

    if (mode === 'set') {
      setMutation.mutate(credits);
    } else {
      addMutation.mutate(credits);
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-blue-50 p-4 rounded-lg">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm text-gray-600">Current Balance</p>
            <p className="text-2xl font-bold text-blue-600">
              ${user.credits.toFixed(2)}
            </p>
          </div>
          <CreditCardIcon className="w-12 h-12 text-blue-400" />
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="flex space-x-2">
          <Button
            type="button"
            variant={mode === 'add' ? 'primary' : 'secondary'}
            onClick={() => setMode('add')}
          >
            Add Credits
          </Button>
          <Button
            type="button"
            variant={mode === 'set' ? 'primary' : 'secondary'}
            onClick={() => setMode('set')}
          >
            Set Balance
          </Button>
        </div>

        <Input
          type="number"
          step="0.01"
          min="0"
          label={mode === 'add' ? 'Amount to Add' : 'New Balance'}
          value={amount}
          onChange={(e) => setAmount(e.target.value)}
          placeholder="0.00"
          prefix="$"
        />

        <Button
          type="submit"
          isLoading={setMutation.isPending || addMutation.isPending}
          className="w-full"
        >
          {mode === 'add' ? 'Add Credits' : 'Set Balance'}
        </Button>
      </form>

      {/* Transaction History */}
      <TransactionHistory userId={user.id} />
    </div>
  );
}
```

### 7. Activate/Deactivate User

**Endpoint:** `PATCH /admin/users/{user_id}/status`

**Implementation:**

```typescript
// Add to AdminAPI class
async updateUserStatus(userId: number, isActive: boolean) {
  return this.request(
    `/admin/users/${userId}/status?is_active=${isActive}`,
    { method: 'PATCH' }
  );
}
```

**React Component Example:**

```typescript
// src/components/UserStatusToggle.tsx
export function UserStatusToggle({ user }: Props) {
  const queryClient = useQueryClient();
  const [showConfirm, setShowConfirm] = useState(false);

  const mutation = useMutation({
    mutationFn: (isActive: boolean) =>
      adminApi.updateUserStatus(user.id, isActive),
    onSuccess: (data) => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success(data.message);
      setShowConfirm(false);
    },
  });

  const handleToggle = () => {
    if (user.is_active) {
      // Deactivating requires confirmation
      setShowConfirm(true);
    } else {
      // Activating doesn't require confirmation
      mutation.mutate(true);
    }
  };

  return (
    <>
      <Switch
        checked={user.is_active}
        onChange={handleToggle}
        disabled={mutation.isPending}
        label={user.is_active ? 'Active' : 'Inactive'}
      />

      {/* Deactivation Confirmation Modal */}
      {showConfirm && (
        <ConfirmationModal
          title="Deactivate User"
          message={`Are you sure you want to deactivate ${user.username}?
                   They will not be able to use their API keys.`}
          confirmText="Deactivate"
          confirmVariant="danger"
          onConfirm={() => mutation.mutate(false)}
          onCancel={() => setShowConfirm(false)}
          isLoading={mutation.isPending}
        />
      )}
    </>
  );
}
```

### 8. Delete User

**Endpoint:** `DELETE /admin/users/{user_id}`

**Implementation:**

```typescript
// Add to AdminAPI class
async deleteUser(userId: number) {
  return this.request(
    `/admin/users/${userId}?confirm=true`,
    { method: 'DELETE' }
  );
}
```

**React Component Example:**

```typescript
// src/components/DeleteUserButton.tsx
export function DeleteUserButton({ user, onSuccess }: Props) {
  const queryClient = useQueryClient();
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmText, setConfirmText] = useState('');

  const mutation = useMutation({
    mutationFn: () => adminApi.deleteUser(user.id),
    onSuccess: (data) => {
      queryClient.invalidateQueries(['admin', 'users']);
      toast.success(`User ${user.username} deleted successfully`);
      onSuccess?.();
    },
  });

  const handleDelete = () => {
    if (confirmText !== user.username) {
      toast.error('Username does not match');
      return;
    }
    mutation.mutate();
  };

  return (
    <>
      <Button
        variant="danger"
        onClick={() => setShowConfirm(true)}
        icon={<TrashIcon />}
      >
        Delete User
      </Button>

      {/* Deletion Confirmation Modal */}
      {showConfirm && (
        <Modal onClose={() => setShowConfirm(false)}>
          <div className="space-y-4">
            <div className="flex items-center space-x-3">
              <div className="bg-red-100 p-3 rounded-full">
                <AlertTriangleIcon className="w-6 h-6 text-red-600" />
              </div>
              <div>
                <h3 className="text-lg font-semibold">Delete User</h3>
                <p className="text-sm text-gray-600">
                  This action cannot be undone
                </p>
              </div>
            </div>

            <div className="bg-red-50 p-4 rounded-lg">
              <p className="text-sm text-red-800">
                This will permanently delete:
              </p>
              <ul className="list-disc list-inside text-sm text-red-700 mt-2">
                <li>User account ({user.username})</li>
                <li>All API keys</li>
                <li>Usage records and activity logs</li>
                <li>Credit transaction history</li>
                <li>All associated data</li>
              </ul>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Type <code className="bg-gray-100 px-2 py-1 rounded">
                  {user.username}
                </code> to confirm
              </label>
              <Input
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={user.username}
                autoFocus
              />
            </div>

            <div className="flex justify-end space-x-3">
              <Button
                variant="secondary"
                onClick={() => setShowConfirm(false)}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                onClick={handleDelete}
                disabled={confirmText !== user.username}
                isLoading={mutation.isPending}
              >
                Delete Permanently
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
```

---

## UI Components

### Complete Users Table

```typescript
// src/components/UsersTable.tsx
import { useState } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from '@tanstack/react-table';

export function UsersTable({ users }: { users: User[] }) {
  const [sorting, setSorting] = useState([]);
  const [filtering, setFiltering] = useState('');
  const [selectedUser, setSelectedUser] = useState<User | null>(null);

  const columns = [
    {
      accessorKey: 'id',
      header: 'ID',
      size: 80,
    },
    {
      accessorKey: 'username',
      header: 'Username',
      cell: ({ row }) => (
        <div className="flex items-center space-x-2">
          <Avatar name={row.original.username} size="sm" />
          <span className="font-medium">{row.original.username}</span>
        </div>
      ),
    },
    {
      accessorKey: 'email',
      header: 'Email',
    },
    {
      accessorKey: 'role',
      header: 'Role',
      cell: ({ row }) => (
        <Badge variant={getRoleVariant(row.original.role)}>
          {row.original.role || 'user'}
        </Badge>
      ),
    },
    {
      accessorKey: 'tier',
      header: 'Tier',
      cell: ({ row }) => (
        <Badge variant={getTierVariant(row.original.tier)}>
          {row.original.tier || 'basic'}
        </Badge>
      ),
    },
    {
      accessorKey: 'credits',
      header: 'Credits',
      cell: ({ row }) => (
        <span className="font-mono">
          ${row.original.credits.toFixed(2)}
        </span>
      ),
    },
    {
      accessorKey: 'subscription_status',
      header: 'Status',
      cell: ({ row }) => (
        <Badge variant={getStatusVariant(row.original.subscription_status)}>
          {row.original.subscription_status}
        </Badge>
      ),
    },
    {
      accessorKey: 'is_active',
      header: 'Active',
      cell: ({ row }) => (
        <div className="flex items-center">
          {row.original.is_active ? (
            <CheckCircleIcon className="w-5 h-5 text-green-500" />
          ) : (
            <XCircleIcon className="w-5 h-5 text-red-500" />
          )}
        </div>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      cell: ({ row }) => (
        <div className="flex space-x-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setSelectedUser(row.original)}
          >
            View
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {/* Open edit modal */}}
          >
            Edit
          </Button>
        </div>
      ),
    },
  ];

  const table = useReactTable({
    data: users,
    columns,
    state: {
      sorting,
      globalFilter: filtering,
    },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFiltering,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  return (
    <div className="space-y-4">
      {/* Search */}
      <Input
        placeholder="Search users..."
        value={filtering}
        onChange={(e) => setFiltering(e.target.value)}
        icon={<SearchIcon />}
      />

      {/* Table */}
      <div className="border rounded-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-gray-50">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-4 py-3 text-left text-sm font-semibold text-gray-700"
                    onClick={header.column.getToggleSortingHandler()}
                  >
                    <div className="flex items-center space-x-2 cursor-pointer">
                      <span>
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                      </span>
                      {header.column.getIsSorted() && (
                        <span>
                          {header.column.getIsSorted() === 'asc' ? '↑' : '↓'}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-gray-200">
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                className="hover:bg-gray-50 transition-colors"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-3 text-sm">
                    {flexRender(
                      cell.column.columnDef.cell,
                      cell.getContext()
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-gray-600">
          Showing {table.getState().pagination.pageIndex *
                   table.getState().pagination.pageSize + 1} to{' '}
          {Math.min(
            (table.getState().pagination.pageIndex + 1) *
            table.getState().pagination.pageSize,
            users.length
          )}{' '}
          of {users.length} users
        </div>
        <div className="flex space-x-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            Previous
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            Next
          </Button>
        </div>
      </div>

      {/* User Details Modal */}
      {selectedUser && (
        <UserDetailsModal
          userId={selectedUser.id}
          onClose={() => setSelectedUser(null)}
        />
      )}
    </div>
  );
}
```

---

## Security Best Practices

### 1. Environment Variables

**Never commit API keys to version control:**

```bash
# .gitignore
.env
.env.local
.env.*.local
```

**CRITICAL:** The `ADMIN_API_KEY` is a highly sensitive credential that grants full administrative access. Treat it like a root password.

### 2. API Key Rotation

**Best practices for admin key rotation:**

```bash
# 1. Generate a new admin key
python3 -c "import secrets; print('sk_admin_live_' + secrets.token_urlsafe(32))"

# 2. Update backend .env
ADMIN_API_KEY=sk_admin_live_NEW_KEY_HERE

# 3. Update admin panel .env
VITE_ADMIN_API_KEY=sk_admin_live_NEW_KEY_HERE

# 4. Redeploy both backend and admin panel

# Recommended rotation schedule:
# - Production: Every 90 days
# - Staging: Every 180 days
# - Development: As needed
```

### 3. Key Management per Environment

Use different admin keys for each environment:

```bash
# Backend .env (production)
ADMIN_API_KEY=sk_admin_live_xxxxx

# Backend .env (staging)
ADMIN_API_KEY=sk_admin_staging_xxxxx

# Backend .env (development)
ADMIN_API_KEY=sk_admin_dev_xxxxx
```

### 4. Audit Logging

**Important:** With environment-based admin keys, there's no user context. Implement client-side tracking:

```typescript
// src/lib/auditLog.ts
export async function logAdminAction(
  action: string,
  targetUserId: number,
  details: any
) {
  // Log locally with admin identifier
  const adminIdentifier = localStorage.getItem('admin_identifier') || 'unknown';

  console.info('[ADMIN ACTION]', {
    action,
    admin: adminIdentifier,
    target_user_id: targetUserId,
    details,
    timestamp: new Date().toISOString(),
  });

  // Optionally send to external logging service (not backend)
  if (import.meta.env.VITE_AUDIT_LOG_WEBHOOK) {
    await fetch(import.meta.env.VITE_AUDIT_LOG_WEBHOOK, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        admin: adminIdentifier,
        target_user_id: targetUserId,
        details,
        timestamp: new Date().toISOString(),
      }),
    });
  }
}

// Usage in mutations
mutation.onSuccess = (data) => {
  logAdminAction('UPDATE_USER_CREDITS', userId, {
    old_balance: data.balance_before,
    new_balance: data.balance_after,
  });
};
```

### 5. Access Control

**Note:** With environment-based admin keys, there's no built-in role-based access. Consider implementing client-side access control:

```typescript
// src/lib/permissions.ts
export const ADMIN_ROLES = {
  SUPER_ADMIN: 'super_admin',
  ADMIN: 'admin',
  VIEWER: 'viewer',
} as const;

export const ADMIN_PERMISSIONS = {
  VIEW_USERS: 'view_users',
  EDIT_USERS: 'edit_users',
  DELETE_USERS: 'delete_users',
  MANAGE_CREDITS: 'manage_credits',
  MANAGE_ROLES: 'manage_roles',
  VIEW_TRANSACTIONS: 'view_transactions',
} as const;

const ROLE_PERMISSIONS = {
  [ADMIN_ROLES.SUPER_ADMIN]: Object.values(ADMIN_PERMISSIONS),
  [ADMIN_ROLES.ADMIN]: [
    ADMIN_PERMISSIONS.VIEW_USERS,
    ADMIN_PERMISSIONS.EDIT_USERS,
    ADMIN_PERMISSIONS.MANAGE_CREDITS,
    ADMIN_PERMISSIONS.VIEW_TRANSACTIONS,
  ],
  [ADMIN_ROLES.VIEWER]: [
    ADMIN_PERMISSIONS.VIEW_USERS,
    ADMIN_PERMISSIONS.VIEW_TRANSACTIONS,
  ],
};

// Store admin role in localStorage after login
export function setAdminRole(role: string) {
  localStorage.setItem('admin_role', role);
}

export function getAdminRole(): string {
  return localStorage.getItem('admin_role') || ADMIN_ROLES.VIEWER;
}

export function hasPermission(permission: string): boolean {
  const role = getAdminRole();
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false;
}

// Usage in components
{hasPermission(ADMIN_PERMISSIONS.DELETE_USERS) && (
  <DeleteUserButton user={user} />
)}
```

### 5. HTTPS Only

Ensure all API calls use HTTPS in production:

```typescript
// src/lib/adminApi.ts
constructor() {
  this.baseURL = API_BASE_URL;

  // Enforce HTTPS in production
  if (import.meta.env.PROD && !this.baseURL.startsWith('https://')) {
    throw new Error('API must use HTTPS in production');
  }

  this.apiKey = ADMIN_API_KEY;
}
```

---

## Error Handling

### Comprehensive Error Handler

```typescript
// src/lib/errorHandler.ts
export class APIError extends Error {
  constructor(
    public statusCode: number,
    public message: string,
    public details?: any
  ) {
    super(message);
    this.name = 'APIError';
  }
}

export function handleAPIError(error: unknown): string {
  if (error instanceof APIError) {
    switch (error.statusCode) {
      case 400:
        return error.message || 'Invalid request';
      case 401:
        return 'Unauthorized. Please check your admin API key.';
      case 403:
        return 'Forbidden. Admin privileges required.';
      case 404:
        return 'Resource not found';
      case 429:
        return 'Too many requests. Please try again later.';
      case 500:
        return 'Server error. Please try again later.';
      default:
        return error.message || 'An error occurred';
    }
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'An unexpected error occurred';
}

// Global error boundary
export function GlobalErrorBoundary({ children }: Props) {
  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <ErrorBoundary
          onReset={reset}
          fallbackRender={({ error, resetErrorBoundary }) => (
            <div className="flex items-center justify-center min-h-screen">
              <div className="text-center space-y-4">
                <AlertTriangleIcon className="w-16 h-16 text-red-500 mx-auto" />
                <h2 className="text-2xl font-bold">Something went wrong</h2>
                <p className="text-gray-600">{handleAPIError(error)}</p>
                <Button onClick={resetErrorBoundary}>Try Again</Button>
              </div>
            </div>
          )}
        >
          {children}
        </ErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}
```

---

## Advanced Features

### 1. Bulk Operations

```typescript
// src/components/BulkActions.tsx
export function BulkActions({ selectedUsers }: Props) {
  const [action, setAction] = useState<string>('');

  const bulkUpdateMutation = useMutation({
    mutationFn: async (updates: any) => {
      const promises = selectedUsers.map((user) =>
        adminApi.updateUser(user.id, updates)
      );
      return Promise.all(promises);
    },
    onSuccess: () => {
      toast.success(`Updated ${selectedUsers.length} users`);
    },
  });

  const handleBulkAction = () => {
    switch (action) {
      case 'activate':
        bulkUpdateMutation.mutate({ is_active: true });
        break;
      case 'deactivate':
        bulkUpdateMutation.mutate({ is_active: false });
        break;
      case 'set_tier_basic':
        // Bulk tier update...
        break;
    }
  };

  return (
    <div className="flex items-center space-x-3">
      <Select value={action} onChange={(e) => setAction(e.target.value)}>
        <option value="">Select action...</option>
        <option value="activate">Activate selected</option>
        <option value="deactivate">Deactivate selected</option>
        <option value="set_tier_basic">Set tier to Basic</option>
        <option value="set_tier_pro">Set tier to Pro</option>
      </Select>
      <Button
        onClick={handleBulkAction}
        disabled={!action || selectedUsers.length === 0}
        isLoading={bulkUpdateMutation.isPending}
      >
        Apply to {selectedUsers.length} users
      </Button>
    </div>
  );
}
```

### 2. Export Users to CSV

```typescript
// src/lib/export.ts
export function exportUsersToCSV(users: User[]) {
  const headers = [
    'ID',
    'Username',
    'Email',
    'Role',
    'Tier',
    'Credits',
    'Status',
    'Active',
    'Registered',
  ];

  const rows = users.map((user) => [
    user.id,
    user.username,
    user.email,
    user.role || 'user',
    user.tier || 'basic',
    user.credits.toFixed(2),
    user.subscription_status,
    user.is_active ? 'Yes' : 'No',
    new Date(user.registration_date).toLocaleDateString(),
  ]);

  const csv = [
    headers.join(','),
    ...rows.map((row) => row.join(',')),
  ].join('\n');

  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `users-${new Date().toISOString().split('T')[0]}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
```

### 3. Real-time Updates

```typescript
// src/hooks/useRealtimeUsers.ts
import { useQuery } from '@tanstack/react-query';

export function useRealtimeUsers(refreshInterval = 30000) {
  return useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => adminApi.getAllUsers(),
    refetchInterval: refreshInterval,
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });
}
```

### 4. Advanced Filtering

```typescript
// src/components/AdvancedFilters.tsx
export function AdvancedFilters({ onFilter }: Props) {
  const [filters, setFilters] = useState({
    role: '',
    tier: '',
    status: '',
    minCredits: '',
    maxCredits: '',
    isActive: '',
  });

  const handleApplyFilters = () => {
    onFilter(filters);
  };

  return (
    <div className="bg-white p-4 rounded-lg border space-y-4">
      <h3 className="font-semibold">Filters</h3>

      <div className="grid grid-cols-2 gap-4">
        <Select
          label="Role"
          value={filters.role}
          onChange={(e) => setFilters({ ...filters, role: e.target.value })}
        >
          <option value="">All Roles</option>
          <option value="user">User</option>
          <option value="developer">Developer</option>
          <option value="admin">Admin</option>
        </Select>

        <Select
          label="Tier"
          value={filters.tier}
          onChange={(e) => setFilters({ ...filters, tier: e.target.value })}
        >
          <option value="">All Tiers</option>
          <option value="basic">Basic</option>
          <option value="pro">Pro</option>
          <option value="max">MAX</option>
        </Select>

        <Select
          label="Status"
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value })}
        >
          <option value="">All Statuses</option>
          <option value="trial">Trial</option>
          <option value="active">Active</option>
          <option value="expired">Expired</option>
        </Select>

        <Select
          label="Active"
          value={filters.isActive}
          onChange={(e) => setFilters({ ...filters, isActive: e.target.value })}
        >
          <option value="">All</option>
          <option value="true">Active Only</option>
          <option value="false">Inactive Only</option>
        </Select>

        <Input
          type="number"
          label="Min Credits"
          value={filters.minCredits}
          onChange={(e) => setFilters({ ...filters, minCredits: e.target.value })}
          placeholder="0.00"
        />

        <Input
          type="number"
          label="Max Credits"
          value={filters.maxCredits}
          onChange={(e) => setFilters({ ...filters, maxCredits: e.target.value })}
          placeholder="1000.00"
        />
      </div>

      <div className="flex justify-end space-x-2">
        <Button
          variant="secondary"
          onClick={() => {
            setFilters({
              role: '',
              tier: '',
              status: '',
              minCredits: '',
              maxCredits: '',
              isActive: '',
            });
            onFilter({});
          }}
        >
          Clear Filters
        </Button>
        <Button onClick={handleApplyFilters}>
          Apply Filters
        </Button>
      </div>
    </div>
  );
}
```

---

## Complete Example: Admin Dashboard Layout

```typescript
// src/pages/AdminDashboard.tsx
import { useState } from 'react';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';

export function AdminDashboard() {
  const [activeTab, setActiveTab] = useState('users');

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b">
        <div className="max-w-7xl mx-auto px-4 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold">Gatewayz Admin Panel</h1>
              <p className="text-sm text-gray-600">
                Manage users, credits, and system settings
              </p>
            </div>
            <div className="flex items-center space-x-4">
              <NotificationBell />
              <AdminUserMenu />
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 py-8">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList>
            <TabsTrigger value="users">Users</TabsTrigger>
            <TabsTrigger value="transactions">Transactions</TabsTrigger>
            <TabsTrigger value="analytics">Analytics</TabsTrigger>
            <TabsTrigger value="settings">Settings</TabsTrigger>
          </TabsList>

          <TabsContent value="users" className="mt-6">
            <UsersList />
          </TabsContent>

          <TabsContent value="transactions" className="mt-6">
            <TransactionsList />
          </TabsContent>

          <TabsContent value="analytics" className="mt-6">
            <AnalyticsDashboard />
          </TabsContent>

          <TabsContent value="settings" className="mt-6">
            <SystemSettings />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  );
}
```

---

## Testing

### Unit Tests Example

```typescript
// src/lib/__tests__/adminApi.test.ts
import { describe, it, expect, vi } from 'vitest';
import { adminApi } from '../adminApi';

describe('AdminAPI', () => {
  it('should get all users', async () => {
    const mockUsers = [
      { id: 1, username: 'user1', email: 'user1@example.com' },
      { id: 2, username: 'user2', email: 'user2@example.com' },
    ];

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ users: mockUsers }),
    });

    const result = await adminApi.getAllUsers();
    expect(result.users).toEqual(mockUsers);
  });

  it('should handle errors correctly', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Unauthorized' }),
    });

    await expect(adminApi.getAllUsers()).rejects.toThrow('Unauthorized');
  });
});
```

---

## Conclusion

This implementation guide provides everything you need to build a production-ready admin user management panel:

✅ **Complete API Integration** - All admin endpoints covered
✅ **Reusable Components** - Modular, maintainable code
✅ **Security Best Practices** - API key management, audit logging, permissions
✅ **Error Handling** - Comprehensive error management
✅ **Advanced Features** - Bulk operations, exports, real-time updates
✅ **Production Ready** - Testing, monitoring, and deployment guidance

### Next Steps

1. Set up your environment variables
2. Implement the API client
3. Build the core components (users table, details modal)
4. Add management features (edit, credits, roles)
5. Implement security measures
6. Add advanced features (bulk operations, analytics)
7. Test thoroughly
8. Deploy to production

For questions or issues, refer to the API documentation at `ADMIN_ENDPOINTS_SUMMARY.md`.
