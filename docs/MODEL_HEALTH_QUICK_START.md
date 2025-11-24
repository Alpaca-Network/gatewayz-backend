# Model Health Tracking - Quick Start Guide

A quick reference for implementing model health monitoring in your frontend.

## TL;DR

The backend now tracks health metrics for every model call. Use these endpoints to show users which models are performing well.

---

## Quick Reference: API Endpoints

| Endpoint | Purpose | Use Case |
|----------|---------|----------|
| `GET /v1/model-health` | List all models with health data | Dashboard table |
| `GET /v1/model-health/{provider}/{model}` | Get specific model health | Status badges |
| `GET /v1/model-health/unhealthy` | Get problematic models | Alert banners |
| `GET /v1/model-health/stats` | Overall system statistics | KPI cards |
| `GET /v1/model-health/provider/{provider}/summary` | Provider-level stats | Provider comparison |
| `GET /v1/model-health/providers` | List all providers | Provider list |

---

## 5-Minute Implementation: Status Badge

Add health indicators to your model selection UI:

```typescript
// 1. Create a simple hook
const useModelHealth = (provider: string, model: string) => {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    fetch(`/v1/model-health/${provider}/${model}`)
      .then(res => res.json())
      .then(setHealth)
      .catch(console.error);
  }, [provider, model]);

  return health;
};

// 2. Use it in your component
const ModelOption = ({ provider, model }) => {
  const health = useModelHealth(provider, model);

  const getStatusIcon = () => {
    if (!health) return '○';
    const successRate = health.success_count / health.call_count;
    if (successRate >= 0.95) return '✅';
    if (successRate >= 0.8) return '⚠️';
    return '❌';
  };

  return (
    <div>
      {getStatusIcon()} {model}
      {health && (
        <small>
          {Math.round(health.average_response_time_ms)}ms
        </small>
      )}
    </div>
  );
};
```

---

## 15-Minute Implementation: Dashboard

Create a simple health dashboard:

```typescript
const ModelHealthDashboard = () => {
  const [stats, setStats] = useState(null);
  const [models, setModels] = useState([]);

  useEffect(() => {
    // Fetch stats
    fetch('/v1/model-health/stats')
      .then(res => res.json())
      .then(setStats);

    // Fetch models
    fetch('/v1/model-health?limit=50')
      .then(res => res.json())
      .then(data => setModels(data.models));
  }, []);

  return (
    <div>
      {/* KPIs */}
      <div className="kpis">
        <div>Models: {stats?.total_models}</div>
        <div>Success Rate: {(stats?.success_rate * 100).toFixed(1)}%</div>
        <div>Avg Response: {Math.round(stats?.average_response_time)}ms</div>
      </div>

      {/* Models Table */}
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Model</th>
            <th>Status</th>
            <th>Response Time</th>
            <th>Success Rate</th>
          </tr>
        </thead>
        <tbody>
          {models.map(m => (
            <tr key={`${m.provider}-${m.model}`}>
              <td>{m.provider}</td>
              <td>{m.model}</td>
              <td>{m.last_status}</td>
              <td>{Math.round(m.average_response_time_ms)}ms</td>
              <td>{((m.success_count / m.call_count) * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
```

---

## 30-Minute Implementation: Alert System

Add unhealthy model alerts:

```typescript
const UnhealthyModelsAlert = () => {
  const [unhealthy, setUnhealthy] = useState([]);

  useEffect(() => {
    const fetchAlerts = () => {
      fetch('/v1/model-health/unhealthy?error_threshold=0.2')
        .then(res => res.json())
        .then(data => setUnhealthy(data.models));
    };

    fetchAlerts();
    const interval = setInterval(fetchAlerts, 300000); // Every 5 minutes
    return () => clearInterval(interval);
  }, []);

  if (unhealthy.length === 0) return null;

  return (
    <div className="alert alert-warning">
      <strong>⚠️ {unhealthy.length} model(s) experiencing issues</strong>
      <ul>
        {unhealthy.map(m => (
          <li key={`${m.provider}-${m.model}`}>
            {m.provider}/{m.model}: {(m.error_rate * 100).toFixed(1)}% error rate
          </li>
        ))}
      </ul>
    </div>
  );
};
```

---

## Data Structure

### ModelHealth Object
```typescript
{
  provider: "openrouter",
  model: "anthropic/claude-3-opus",
  last_response_time_ms: 1250.5,
  last_status: "success",  // or "error", "timeout", "rate_limited", "network_error"
  last_called_at: "2025-11-24T12:30:45.123Z",
  call_count: 1523,
  success_count: 1498,
  error_count: 25,
  average_response_time_ms: 1180.2,
  last_error_message: null,  // or error string
  created_at: "2025-11-20T08:15:00.000Z",
  updated_at: "2025-11-24T12:30:45.123Z"
}
```

---

## Color Coding

Use consistent colors across your UI:

```css
/* Status colors */
.status-success { color: #52c41a; }      /* Green - 95%+ success */
.status-warning { color: #faad14; }      /* Yellow - 80-95% success */
.status-error { color: #f5222d; }        /* Red - <80% success */

/* Response time colors */
.response-fast { color: #52c41a; }       /* <1000ms */
.response-moderate { color: #faad14; }   /* 1000-3000ms */
.response-slow { color: #f5222d; }       /* >3000ms */
```

---

## Common Calculations

```typescript
// Success rate
const successRate = (model.success_count / model.call_count) * 100;

// Status color based on success rate
const getStatusColor = (successRate) => {
  if (successRate >= 95) return 'green';
  if (successRate >= 80) return 'yellow';
  return 'red';
};

// Response time classification
const getResponseTimeClass = (ms) => {
  if (ms < 1000) return 'fast';
  if (ms < 3000) return 'moderate';
  return 'slow';
};

// Time ago helper
const timeAgo = (timestamp) => {
  const seconds = Math.floor((Date.now() - new Date(timestamp)) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
};
```

---

## Polling Best Practices

```typescript
// Smart polling that stops when tab is hidden
useEffect(() => {
  let intervalId;

  const poll = () => {
    if (!document.hidden) {
      fetchHealthData();
    }
  };

  // Initial fetch
  poll();

  // Set up polling
  intervalId = setInterval(poll, 60000); // Every 60 seconds

  // Cleanup
  return () => clearInterval(intervalId);
}, []);
```

---

## Error Handling

```typescript
const fetchModelHealth = async (provider, model) => {
  try {
    const response = await fetch(`/v1/model-health/${provider}/${model}`);

    if (!response.ok) {
      if (response.status === 404) {
        // No health data yet for this model
        return null;
      }
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    console.error('Failed to fetch model health:', error);
    // Return null or default data, don't break the UI
    return null;
  }
};
```

---

## Ready-to-Use Components

### 1. Status Indicator
```typescript
const StatusIndicator = ({ status }) => {
  const icons = {
    success: '✅',
    error: '❌',
    timeout: '⏱️',
    rate_limited: '🚫',
    network_error: '📡'
  };
  return <span>{icons[status] || '○'}</span>;
};
```

### 2. Health Badge
```typescript
const HealthBadge = ({ successRate }) => {
  if (successRate >= 95) return <span className="badge badge-success">Healthy</span>;
  if (successRate >= 80) return <span className="badge badge-warning">Degraded</span>;
  return <span className="badge badge-danger">Unhealthy</span>;
};
```

### 3. Response Time Badge
```typescript
const ResponseTimeBadge = ({ ms }) => {
  const rounded = Math.round(ms);
  const className = ms < 1000 ? 'fast' : ms < 3000 ? 'moderate' : 'slow';
  return <span className={`response-time ${className}`}>{rounded}ms</span>;
};
```

---

## Testing Tips

### Mock Data for Development
```typescript
const mockModelHealth = {
  provider: "openrouter",
  model: "test-model",
  last_response_time_ms: 1200,
  last_status: "success",
  last_called_at: new Date().toISOString(),
  call_count: 100,
  success_count: 98,
  error_count: 2,
  average_response_time_ms: 1150,
  last_error_message: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

// Use in development
const health = process.env.NODE_ENV === 'development'
  ? mockModelHealth
  : useModelHealth(provider, model);
```

---

## Common Issues and Solutions

### Issue: Health data not available for new models
**Solution**: Health data is created after the first call to a model. Show "No data yet" instead of error.

### Issue: Stale data being displayed
**Solution**: Implement proper polling or use React Query for automatic background refetching.

### Issue: Too many API calls
**Solution**: Implement debouncing, caching, and only fetch on-demand (hover/click).

### Issue: Dashboard is slow
**Solution**: Use pagination, lazy loading, and virtualization for large tables.

---

## Next Steps

1. **Start Simple**: Add status indicators to your model selection UI
2. **Build Dashboard**: Create an admin dashboard with KPIs and tables
3. **Add Alerts**: Implement unhealthy model notifications
4. **Optimize**: Add caching, polling, and error handling
5. **Enhance**: Add charts, historical data, and advanced features

---

## Resources

- Full Implementation Guide: `docs/FRONTEND_MODEL_HEALTH_INTEGRATION.md`
- Backend API Code: `src/routes/model_health.py`
- Database Schema: `supabase/migrations/20251121000001_add_model_health_tracking.sql`
- Data Access Layer: `src/db/model_health.py`

---

## Support

For questions or issues:
- Check the full integration guide for detailed examples
- Review the API endpoint documentation
- Contact the backend team for API changes

**Happy Coding!** 🚀
