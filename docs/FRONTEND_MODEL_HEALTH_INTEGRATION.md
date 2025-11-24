# Frontend Integration Guide: Model Health Monitoring

This document provides a comprehensive guide for frontend developers to integrate the model health tracking system into the Gatewayz frontend application.

## Table of Contents
- [Overview](#overview)
- [Available API Endpoints](#available-api-endpoints)
- [Data Models](#data-models)
- [Frontend Components](#frontend-components)
- [Implementation Examples](#implementation-examples)
- [Best Practices](#best-practices)
- [UI/UX Recommendations](#uiux-recommendations)

---

## Overview

The model health tracking system monitors every model call across all providers (OpenRouter, Portkey, HuggingFace, etc.) and records:
- Response times
- Success/error rates
- Last call timestamps
- Error messages
- Provider-level statistics

This data can be used to create dashboards, alerts, and insights for users and administrators.

---

## Available API Endpoints

### 1. Get All Model Health Records

**Endpoint**: `GET /v1/model-health`

**Description**: Retrieve health metrics for all monitored models with optional filtering.

**Query Parameters**:
- `provider` (optional): Filter by provider name (e.g., "openrouter", "huggingface")
- `status` (optional): Filter by last status (e.g., "success", "error", "timeout")
- `limit` (optional): Maximum records to return (1-1000, default: 100)
- `offset` (optional): Number of records to skip for pagination (default: 0)

**Response Example**:
```json
{
  "total": 42,
  "limit": 100,
  "offset": 0,
  "filters": {
    "provider": null,
    "status": null
  },
  "models": [
    {
      "provider": "openrouter",
      "model": "anthropic/claude-3-opus",
      "last_response_time_ms": 1250.5,
      "last_status": "success",
      "last_called_at": "2025-11-24T12:30:45.123Z",
      "call_count": 1523,
      "success_count": 1498,
      "error_count": 25,
      "average_response_time_ms": 1180.2,
      "last_error_message": null,
      "created_at": "2025-11-20T08:15:00.000Z",
      "updated_at": "2025-11-24T12:30:45.123Z"
    }
  ]
}
```

**Use Cases**:
- Display a table/list of all models with their health status
- Filter models by provider or status
- Implement pagination for large datasets

---

### 2. Get Specific Model Health

**Endpoint**: `GET /v1/model-health/{provider}/{model}`

**Description**: Get detailed health metrics for a specific provider-model combination.

**Path Parameters**:
- `provider`: Provider name (e.g., "openrouter")
- `model`: Model identifier (e.g., "anthropic/claude-3-opus")

**Response Example**:
```json
{
  "provider": "openrouter",
  "model": "anthropic/claude-3-opus",
  "last_response_time_ms": 1250.5,
  "last_status": "success",
  "last_called_at": "2025-11-24T12:30:45.123Z",
  "call_count": 1523,
  "success_count": 1498,
  "error_count": 25,
  "average_response_time_ms": 1180.2,
  "last_error_message": null,
  "created_at": "2025-11-20T08:15:00.000Z",
  "updated_at": "2025-11-24T12:30:45.123Z"
}
```

**Use Cases**:
- Show detailed health metrics for a specific model
- Display model status badges in model selection dropdowns
- Show real-time health status before making a call

---

### 3. Get Unhealthy Models

**Endpoint**: `GET /v1/model-health/unhealthy`

**Description**: Retrieve models with high error rates (unhealthy models).

**Query Parameters**:
- `error_threshold` (optional): Minimum error rate to be considered unhealthy (0.0-1.0, default: 0.2)
- `min_calls` (optional): Minimum number of calls required to evaluate health (default: 10)

**Response Example**:
```json
{
  "threshold": 0.2,
  "min_calls": 10,
  "total_unhealthy": 3,
  "models": [
    {
      "provider": "huggingface",
      "model": "meta-llama/Llama-3-70b",
      "last_response_time_ms": 2500.0,
      "last_status": "timeout",
      "call_count": 50,
      "success_count": 30,
      "error_count": 20,
      "error_rate": 0.4,
      "average_response_time_ms": 2100.5,
      "last_error_message": "Request timeout after 30s"
    }
  ]
}
```

**Use Cases**:
- Display alerts for unhealthy models
- Show warning badges on model selection UI
- Create admin notifications for degraded models
- Automatically suggest alternative models

---

### 4. Get Overall Statistics

**Endpoint**: `GET /v1/model-health/stats`

**Description**: Get aggregate statistics across all monitored models.

**Response Example**:
```json
{
  "total_models": 127,
  "total_calls": 45623,
  "total_success": 44891,
  "total_errors": 732,
  "average_response_time": 1345.7,
  "success_rate": 0.9839
}
```

**Use Cases**:
- Display overall system health dashboard
- Show aggregate metrics (KPIs) in admin panel
- Create system-wide health reports

---

### 5. Get Provider Summary

**Endpoint**: `GET /v1/model-health/provider/{provider}/summary`

**Description**: Get health summary for all models from a specific provider.

**Path Parameters**:
- `provider`: Provider name (e.g., "openrouter", "huggingface")

**Response Example**:
```json
{
  "provider": "openrouter",
  "total_models": 45,
  "total_calls": 23456,
  "total_success": 23120,
  "total_errors": 336,
  "average_response_time": 1250.3,
  "success_rate": 0.9856
}
```

**Use Cases**:
- Compare providers side-by-side
- Show provider reliability metrics
- Help users choose the best provider

---

### 6. Get All Providers

**Endpoint**: `GET /v1/model-health/providers`

**Description**: Get list of all providers with basic statistics.

**Response Example**:
```json
{
  "total_providers": 8,
  "providers": [
    {
      "provider": "openrouter",
      "model_count": 45,
      "total_calls": 23456
    },
    {
      "provider": "huggingface",
      "model_count": 52,
      "total_calls": 15234
    }
  ]
}
```

**Use Cases**:
- List all available providers with stats
- Create provider comparison views
- Show provider activity levels

---

## Data Models

### ModelHealth

```typescript
interface ModelHealth {
  provider: string;                    // Provider name
  model: string;                       // Model identifier
  last_response_time_ms: number;       // Last response time in milliseconds
  last_status: string;                 // "success" | "error" | "timeout" | "rate_limited" | "network_error"
  last_called_at: string;              // ISO 8601 timestamp
  call_count: number;                  // Total number of calls
  success_count: number;               // Number of successful calls
  error_count: number;                 // Number of failed calls
  average_response_time_ms: number;    // Average response time
  last_error_message: string | null;   // Last error message (if any)
  created_at: string;                  // ISO 8601 timestamp
  updated_at: string;                  // ISO 8601 timestamp
}
```

### HealthStats

```typescript
interface HealthStats {
  total_models: number;
  total_calls: number;
  total_success: number;
  total_errors: number;
  average_response_time: number;
  success_rate: number;  // 0.0 - 1.0
}
```

### ProviderSummary

```typescript
interface ProviderSummary {
  provider: string;
  total_models: number;
  total_calls: number;
  total_success: number;
  total_errors: number;
  average_response_time: number;
  success_rate: number;  // 0.0 - 1.0
}
```

---

## Frontend Components

### Recommended Components to Build

#### 1. **Model Health Dashboard** (Admin/Monitoring View)
**Purpose**: Overview of all model health metrics

**Features**:
- KPI cards (total calls, success rate, avg response time)
- Model health table with sorting/filtering
- Provider comparison charts
- Real-time updates (polling every 30-60 seconds)

**API Calls**:
- `GET /v1/model-health/stats` - Overall stats
- `GET /v1/model-health?limit=50` - Model list
- `GET /v1/model-health/unhealthy` - Alerts

---

#### 2. **Model Status Indicator** (User-Facing)
**Purpose**: Show health status in model selection UI

**Features**:
- Status badge (green/yellow/red)
- Tooltip with response time and success rate
- Warning for unhealthy models

**API Calls**:
- `GET /v1/model-health/{provider}/{model}` - When hovering over a model

---

#### 3. **Provider Comparison View**
**Purpose**: Compare providers side-by-side

**Features**:
- Side-by-side provider cards
- Bar charts for success rates
- Line charts for response times
- Model count per provider

**API Calls**:
- `GET /v1/model-health/providers` - List providers
- `GET /v1/model-health/provider/{provider}/summary` - Per-provider stats

---

#### 4. **Unhealthy Models Alert Panel**
**Purpose**: Notify admins of degraded models

**Features**:
- Alert banner with count of unhealthy models
- Expandable list of affected models
- Suggested alternative models
- Dismiss/acknowledge functionality

**API Calls**:
- `GET /v1/model-health/unhealthy` - Fetch alerts

---

#### 5. **Model Detail Page**
**Purpose**: Detailed view of a single model's health

**Features**:
- Historical response time chart (would need time-series data)
- Error log table (last 10 errors)
- Call volume over time
- Status timeline

**API Calls**:
- `GET /v1/model-health/{provider}/{model}` - Current stats

---

## Implementation Examples

### Example 1: React Model Health Dashboard

```typescript
import React, { useEffect, useState } from 'react';
import { Card, Table, Badge, Spin } from 'antd'; // or your UI library

interface ModelHealth {
  provider: string;
  model: string;
  last_response_time_ms: number;
  last_status: string;
  call_count: number;
  success_count: number;
  error_count: number;
  average_response_time_ms: number;
  last_called_at: string;
}

interface HealthStats {
  total_models: number;
  total_calls: number;
  total_success: number;
  total_errors: number;
  average_response_time: number;
  success_rate: number;
}

const ModelHealthDashboard: React.FC = () => {
  const [stats, setStats] = useState<HealthStats | null>(null);
  const [models, setModels] = useState<ModelHealth[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchHealthData();
    // Poll every 60 seconds
    const interval = setInterval(fetchHealthData, 60000);
    return () => clearInterval(interval);
  }, []);

  const fetchHealthData = async () => {
    try {
      setLoading(true);

      // Fetch overall stats
      const statsRes = await fetch('/v1/model-health/stats');
      const statsData = await statsRes.json();
      setStats(statsData);

      // Fetch model list
      const modelsRes = await fetch('/v1/model-health?limit=100');
      const modelsData = await modelsRes.json();
      setModels(modelsData.models);

    } catch (error) {
      console.error('Failed to fetch health data:', error);
    } finally {
      setLoading(false);
    }
  };

  const getStatusBadge = (status: string) => {
    const colors = {
      success: 'green',
      error: 'red',
      timeout: 'orange',
      rate_limited: 'yellow',
      network_error: 'red',
    };
    return <Badge color={colors[status] || 'gray'} text={status} />;
  };

  const columns = [
    {
      title: 'Provider',
      dataIndex: 'provider',
      key: 'provider',
      sorter: (a, b) => a.provider.localeCompare(b.provider),
    },
    {
      title: 'Model',
      dataIndex: 'model',
      key: 'model',
      sorter: (a, b) => a.model.localeCompare(b.model),
    },
    {
      title: 'Status',
      dataIndex: 'last_status',
      key: 'status',
      render: (status: string) => getStatusBadge(status),
    },
    {
      title: 'Avg Response (ms)',
      dataIndex: 'average_response_time_ms',
      key: 'avg_response',
      render: (time: number) => Math.round(time),
      sorter: (a, b) => a.average_response_time_ms - b.average_response_time_ms,
    },
    {
      title: 'Success Rate',
      key: 'success_rate',
      render: (_, record) => {
        const rate = record.call_count > 0
          ? (record.success_count / record.call_count) * 100
          : 0;
        return `${rate.toFixed(1)}%`;
      },
      sorter: (a, b) => {
        const rateA = a.call_count > 0 ? a.success_count / a.call_count : 0;
        const rateB = b.call_count > 0 ? b.success_count / b.call_count : 0;
        return rateA - rateB;
      },
    },
    {
      title: 'Total Calls',
      dataIndex: 'call_count',
      key: 'call_count',
      sorter: (a, b) => a.call_count - b.call_count,
    },
    {
      title: 'Last Called',
      dataIndex: 'last_called_at',
      key: 'last_called',
      render: (timestamp: string) => new Date(timestamp).toLocaleString(),
      sorter: (a, b) => new Date(a.last_called_at).getTime() - new Date(b.last_called_at).getTime(),
    },
  ];

  if (loading) {
    return <Spin size="large" />;
  }

  return (
    <div className="model-health-dashboard">
      <h1>Model Health Dashboard</h1>

      {/* KPI Cards */}
      {stats && (
        <div className="kpi-cards" style={{ display: 'flex', gap: '16px', marginBottom: '24px' }}>
          <Card title="Total Models">
            <h2>{stats.total_models}</h2>
          </Card>
          <Card title="Total Calls">
            <h2>{stats.total_calls.toLocaleString()}</h2>
          </Card>
          <Card title="Success Rate">
            <h2>{(stats.success_rate * 100).toFixed(2)}%</h2>
          </Card>
          <Card title="Avg Response Time">
            <h2>{Math.round(stats.average_response_time)} ms</h2>
          </Card>
        </div>
      )}

      {/* Models Table */}
      <Card title="Model Health">
        <Table
          dataSource={models}
          columns={columns}
          rowKey={(record) => `${record.provider}-${record.model}`}
          pagination={{ pageSize: 20 }}
        />
      </Card>
    </div>
  );
};

export default ModelHealthDashboard;
```

---

### Example 2: Model Status Badge Component

```typescript
import React, { useEffect, useState } from 'react';
import { Badge, Tooltip } from 'antd';

interface ModelStatusBadgeProps {
  provider: string;
  model: string;
  showDetails?: boolean;
}

const ModelStatusBadge: React.FC<ModelStatusBadgeProps> = ({
  provider,
  model,
  showDetails = false,
}) => {
  const [health, setHealth] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (showDetails) {
      fetchModelHealth();
    }
  }, [provider, model, showDetails]);

  const fetchModelHealth = async () => {
    try {
      setLoading(true);
      const response = await fetch(`/v1/model-health/${provider}/${model}`);
      if (response.ok) {
        const data = await response.json();
        setHealth(data);
      }
    } catch (error) {
      console.error('Failed to fetch model health:', error);
    } finally {
      setLoading(false);
    }
  };

  const getStatusColor = () => {
    if (!health) return 'gray';

    const successRate = health.call_count > 0
      ? health.success_count / health.call_count
      : 1;

    if (successRate >= 0.95) return 'green';
    if (successRate >= 0.8) return 'yellow';
    return 'red';
  };

  const getTooltipContent = () => {
    if (!health) return 'Health data unavailable';

    const successRate = health.call_count > 0
      ? ((health.success_count / health.call_count) * 100).toFixed(1)
      : '0';

    return (
      <div>
        <div><strong>Status:</strong> {health.last_status}</div>
        <div><strong>Success Rate:</strong> {successRate}%</div>
        <div><strong>Avg Response:</strong> {Math.round(health.average_response_time_ms)}ms</div>
        <div><strong>Total Calls:</strong> {health.call_count}</div>
        {health.last_error_message && (
          <div><strong>Last Error:</strong> {health.last_error_message}</div>
        )}
      </div>
    );
  };

  if (!showDetails) {
    return null;
  }

  return (
    <Tooltip title={getTooltipContent()}>
      <Badge color={getStatusColor()} text={loading ? '...' : 'Health'} />
    </Tooltip>
  );
};

export default ModelStatusBadge;
```

---

### Example 3: Unhealthy Models Alert

```typescript
import React, { useEffect, useState } from 'react';
import { Alert, List, Button } from 'antd';
import { WarningOutlined } from '@ant-design/icons';

const UnhealthyModelsAlert: React.FC = () => {
  const [unhealthyModels, setUnhealthyModels] = useState<any[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    fetchUnhealthyModels();
    // Poll every 5 minutes
    const interval = setInterval(fetchUnhealthyModels, 300000);
    return () => clearInterval(interval);
  }, []);

  const fetchUnhealthyModels = async () => {
    try {
      const response = await fetch('/v1/model-health/unhealthy?error_threshold=0.2&min_calls=10');
      const data = await response.json();
      setUnhealthyModels(data.models);
    } catch (error) {
      console.error('Failed to fetch unhealthy models:', error);
    }
  };

  if (unhealthyModels.length === 0) {
    return null;
  }

  return (
    <div style={{ marginBottom: '16px' }}>
      <Alert
        message={`${unhealthyModels.length} model(s) experiencing issues`}
        description={
          <div>
            <p>Some models are experiencing higher than normal error rates.</p>
            <Button
              type="link"
              onClick={() => setExpanded(!expanded)}
              icon={<WarningOutlined />}
            >
              {expanded ? 'Hide Details' : 'Show Details'}
            </Button>
            {expanded && (
              <List
                dataSource={unhealthyModels}
                renderItem={(model) => (
                  <List.Item>
                    <div>
                      <strong>{model.provider}/{model.model}</strong>
                      <br />
                      Error Rate: {(model.error_rate * 100).toFixed(1)}%
                      ({model.error_count}/{model.call_count} calls)
                      <br />
                      Last Status: {model.last_status}
                      {model.last_error_message && (
                        <>
                          <br />
                          Error: {model.last_error_message}
                        </>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            )}
          </div>
        }
        type="warning"
        showIcon
      />
    </div>
  );
};

export default UnhealthyModelsAlert;
```

---

### Example 4: Provider Comparison Chart

```typescript
import React, { useEffect, useState } from 'react';
import { Card, Row, Col } from 'antd';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts';

interface ProviderData {
  provider: string;
  successRate: number;
  avgResponseTime: number;
  totalCalls: number;
}

const ProviderComparisonChart: React.FC = () => {
  const [providers, setProviders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProviderData();
  }, []);

  const fetchProviderData = async () => {
    try {
      setLoading(true);

      // Get list of providers
      const providersRes = await fetch('/v1/model-health/providers');
      const providersData = await providersRes.json();

      // Fetch summary for each provider
      const summaries = await Promise.all(
        providersData.providers.map(async (p: any) => {
          const res = await fetch(`/v1/model-health/provider/${p.provider}/summary`);
          return res.json();
        })
      );

      setProviders(summaries);
    } catch (error) {
      console.error('Failed to fetch provider data:', error);
    } finally {
      setLoading(false);
    }
  };

  const chartData = providers.map(p => ({
    name: p.provider,
    successRate: p.success_rate * 100,
    avgResponse: Math.round(p.average_response_time),
    totalCalls: p.total_calls,
  }));

  return (
    <Card title="Provider Comparison" loading={loading}>
      <Row gutter={[16, 16]}>
        <Col span={12}>
          <h3>Success Rate (%)</h3>
          <BarChart width={500} height={300} data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis domain={[0, 100]} />
            <Tooltip />
            <Legend />
            <Bar dataKey="successRate" fill="#52c41a" name="Success Rate %" />
          </BarChart>
        </Col>
        <Col span={12}>
          <h3>Average Response Time (ms)</h3>
          <BarChart width={500} height={300} data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="name" />
            <YAxis />
            <Tooltip />
            <Legend />
            <Bar dataKey="avgResponse" fill="#1890ff" name="Avg Response (ms)" />
          </BarChart>
        </Col>
      </Row>
    </Card>
  );
};

export default ProviderComparisonChart;
```

---

## Best Practices

### 1. **Polling Strategy**
- Poll overall stats every 30-60 seconds on dashboard views
- Poll unhealthy models every 5 minutes for alert banners
- Use exponential backoff on errors
- Don't poll on inactive tabs (use Page Visibility API)

```typescript
useEffect(() => {
  let intervalId: NodeJS.Timeout;

  const startPolling = () => {
    fetchData();
    intervalId = setInterval(fetchData, 60000);
  };

  const handleVisibilityChange = () => {
    if (document.hidden) {
      clearInterval(intervalId);
    } else {
      startPolling();
    }
  };

  startPolling();
  document.addEventListener('visibilitychange', handleVisibilityChange);

  return () => {
    clearInterval(intervalId);
    document.removeEventListener('visibilitychange', handleVisibilityChange);
  };
}, []);
```

### 2. **Error Handling**
- Show graceful fallbacks when health data is unavailable
- Don't block user workflows if health API fails
- Display "Health data unavailable" instead of breaking the UI

### 3. **Caching**
- Cache model health data in frontend state management (Redux, Zustand, etc.)
- Invalidate cache after user actions (e.g., after making a model call)
- Use stale-while-revalidate pattern

### 4. **Performance**
- Lazy load health data (fetch on hover/click, not on mount)
- Use pagination for large model lists
- Debounce filter inputs

### 5. **User Experience**
- Show loading skeletons while fetching
- Display relative timestamps ("2 minutes ago" vs ISO string)
- Use color-coding consistently (green=healthy, yellow=warning, red=error)

---

## UI/UX Recommendations

### Status Colors
```css
/* Recommended color scheme */
.status-success { color: #52c41a; }      /* Green */
.status-warning { color: #faad14; }      /* Yellow/Orange */
.status-error { color: #f5222d; }        /* Red */
.status-timeout { color: #fa8c16; }      /* Orange */
.status-unknown { color: #8c8c8c; }      /* Gray */
```

### Success Rate Thresholds
- **Healthy** (Green): ≥95% success rate
- **Warning** (Yellow): 80-95% success rate
- **Unhealthy** (Red): <80% success rate

### Response Time Thresholds
- **Fast** (Green): <1000ms
- **Moderate** (Yellow): 1000-3000ms
- **Slow** (Red): >3000ms

### Icons
- ✅ Success: Checkmark icon
- ⚠️ Warning: Warning triangle
- ❌ Error: X or alert icon
- ⏱️ Timeout: Clock icon
- 🚫 Rate Limited: Stop sign

### Layout Suggestions

#### Dashboard Layout
```
┌─────────────────────────────────────────────────────┐
│  Model Health Dashboard                             │
├─────────────────────────────────────────────────────┤
│  [Total Models] [Total Calls] [Success] [Avg Time] │  <- KPI Cards
├─────────────────────────────────────────────────────┤
│  [⚠️ 3 Unhealthy Models - Click to view]           │  <- Alert Banner
├─────────────────────────────────────────────────────┤
│  Provider | Model | Status | Response | Success    │  <- Table
│  --------- ------- -------- ---------- --------    │
│  openrouter | claude-3 | ✅ | 1200ms | 98.5%      │
│  huggingface | llama-3 | ⚠️ | 2800ms | 85.2%      │
└─────────────────────────────────────────────────────┘
```

#### Model Selection UI Enhancement
```
Model Dropdown:
┌──────────────────────────────────────┐
│ Select Model                       ▼ │
├──────────────────────────────────────┤
│ ✅ Claude 3 Opus (1.2s, 98% uptime) │
│ ✅ GPT-4 Turbo (0.9s, 99% uptime)   │
│ ⚠️ Llama 3 70B (2.8s, 85% uptime)   │
│ ❌ Mixtral 8x7B (timeout)           │
└──────────────────────────────────────┘
```

---

## Advanced Features (Optional)

### 1. Historical Charts
**Requirement**: Store time-series data (beyond current implementation)
- Line charts showing response time trends
- Call volume over time
- Error rate trends

### 2. Webhooks/Push Notifications
**Requirement**: Backend webhook system
- Real-time alerts for degraded models
- Browser push notifications for admins

### 3. Model Recommendations
**Feature**: Suggest alternative models when primary is unhealthy
```typescript
const getAlternativeModels = (provider: string, model: string) => {
  // Logic to suggest similar models from different providers
  // Based on capabilities, pricing, and current health
};
```

### 4. Automatic Failover UI
**Feature**: Show users which provider was used after failover
```
"Your request was routed to OpenRouter (primary: HuggingFace unavailable)"
```

### 5. Export Reports
**Feature**: Allow admins to export health reports as CSV/PDF
- Daily/weekly/monthly reports
- Downloadable health dashboards

---

## Testing Checklist

### Unit Tests
- [ ] Test status badge color logic
- [ ] Test success rate calculations
- [ ] Test timestamp formatting
- [ ] Test error handling for failed API calls

### Integration Tests
- [ ] Test polling mechanism
- [ ] Test pagination
- [ ] Test filters (provider, status)
- [ ] Test data refresh on user action

### E2E Tests
- [ ] Navigate to health dashboard
- [ ] Filter models by status
- [ ] Click on unhealthy models alert
- [ ] View provider comparison
- [ ] Verify real-time updates

---

## Deployment Considerations

### 1. Feature Flags
Wrap health features in feature flags for gradual rollout:
```typescript
if (featureFlags.modelHealthMonitoring) {
  return <ModelHealthDashboard />;
}
```

### 2. Permissions
- Admin users: Full access to all health endpoints
- Regular users: Limited to their own usage stats
- Public: No access (require authentication)

### 3. Analytics
Track usage of health features:
- Dashboard views
- Filter usage
- Model health tooltip hovers
- Alert click-through rates

---

## Support and Maintenance

### Monitoring
- Track API response times for health endpoints
- Monitor error rates for frontend health requests
- Alert on failed health data fetches

### Documentation
- Keep this document updated as endpoints evolve
- Document any breaking changes
- Provide changelog for API versions

### User Feedback
- Collect feedback on health dashboard usefulness
- Track which metrics are most viewed
- Iterate based on user needs

---

## Questions and Contact

For questions or clarifications about the model health API:
- Backend API: Check `src/routes/model_health.py`
- Database schema: See `supabase/migrations/20251121000001_add_model_health_tracking.sql`
- Data layer: Review `src/db/model_health.py`

---

**Last Updated**: 2025-11-24
**API Version**: v1
**Document Version**: 1.0
