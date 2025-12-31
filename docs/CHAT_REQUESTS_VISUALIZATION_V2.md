# Chat Completion Requests Visualization - Frontend Design V2

## Overview

This document outlines the frontend design for visualizing chat completion request data with a **Provider → Model → Details** navigation flow, featuring latency vs token scatter plots with time-range filtering (similar to crypto charts).

## User Flow

```
┌─────────────────┐
│  All Providers  │ → Select Provider
└────────┬────────┘
         ↓
┌─────────────────┐
│  Provider's     │ → Select Model (only models with requests)
│  Models List    │
└────────┬────────┘
         ↓
┌─────────────────┐
│  Model Details  │ → View scatter plot with time filters
│  & Graph        │    (1Y, 6M, 1M, 1W, 1D)
└─────────────────┘
```

## Backend Endpoints

### 1. Get All Providers with Requests
```
GET /api/monitoring/chat-requests/providers
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "provider_id": 1,
      "name": "OpenRouter",
      "slug": "openrouter",
      "models_with_requests": 15,
      "total_requests": 2500
    },
    {
      "provider_id": 2,
      "name": "Anthropic",
      "slug": "anthropic",
      "models_with_requests": 3,
      "total_requests": 450
    }
  ],
  "metadata": {
    "total_providers": 2,
    "timestamp": "2025-12-29T..."
  }
}
```

### 2. Get Models for a Provider (with requests only)
```
GET /api/monitoring/chat-requests/models?provider_id=1
```

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "model_id": 1,
      "model_identifier": "google/gemini-2.0-flash",
      "model_name": "Google Gemini 2.0 Flash",
      "provider": {
        "id": 1,
        "name": "OpenRouter",
        "slug": "openrouter"
      },
      "stats": {
        "total_requests": 150,
        "total_input_tokens": 5000,
        "total_output_tokens": 25000,
        "total_tokens": 30000,
        "avg_processing_time_ms": 1520.5
      }
    }
  ],
  "metadata": {
    "total_models": 15,
    "timestamp": "2025-12-29T..."
  }
}
```

### 3. Get Requests for a Model with Time Filtering
```
GET /api/monitoring/chat-requests?model_id=1&start_date=2025-12-22&end_date=2025-12-29&limit=1000
```

**Query Parameters:**
- `model_id` (required): The model to fetch requests for
- `start_date` (optional): ISO format date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
- `end_date` (optional): ISO format date
- `limit` (default: 100, max: 1000): Max records

**Response:**
```json
{
  "success": true,
  "data": [
    {
      "id": "uuid",
      "request_id": "req-123",
      "model_id": 1,
      "input_tokens": 50,
      "output_tokens": 200,
      "total_tokens": 250,
      "processing_time_ms": 1500,
      "status": "completed",
      "created_at": "2025-12-29T10:30:00Z",
      "models": {
        "model_name": "Google Gemini 2.0 Flash",
        "providers": {
          "name": "OpenRouter"
        }
      }
    }
  ],
  "metadata": {
    "total_count": 150,
    "returned_count": 150,
    "filters": {
      "model_id": 1,
      "start_date": "2025-12-22",
      "end_date": "2025-12-29"
    }
  }
}
```

## Page 1: Providers List

### Layout
```
┌─────────────────────────────────────────────────────────────┐
│  📊 Chat Request Analytics                                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Select a Provider:                                          │
│                                                              │
│  ┌────────────────────────────────────────────┐            │
│  │  🔷 OpenRouter                              │  →         │
│  │  • 15 models with requests                  │            │
│  │  • 2,500 total requests                     │            │
│  └────────────────────────────────────────────┘            │
│                                                              │
│  ┌────────────────────────────────────────────┐            │
│  │  🔶 Anthropic                               │  →         │
│  │  • 3 models with requests                   │            │
│  │  • 450 total requests                       │            │
│  └────────────────────────────────────────────┘            │
│                                                              │
│  ┌────────────────────────────────────────────┐            │
│  │  🔸 Together AI                             │  →         │
│  │  • 8 models with requests                   │            │
│  │  • 1,250 total requests                     │            │
│  └────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### Implementation
```jsx
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

const ProvidersPage = () => {
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    fetchProviders();
  }, []);

  const fetchProviders = async () => {
    try {
      const res = await fetch('/api/monitoring/chat-requests/providers');
      const data = await res.json();
      setProviders(data.data);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch providers:', error);
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="p-6">Loading providers...</div>;
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <h1 className="text-3xl font-bold mb-6">📊 Chat Request Analytics</h1>

      <h2 className="text-xl font-semibold mb-4">Select a Provider:</h2>

      <div className="space-y-4">
        {providers.map(provider => (
          <div
            key={provider.provider_id}
            onClick={() => navigate(`/analytics/provider/${provider.provider_id}`)}
            className="border rounded-lg p-6 hover:shadow-lg cursor-pointer transition-all hover:border-blue-500"
          >
            <div className="flex justify-between items-center">
              <div>
                <h3 className="text-2xl font-semibold mb-2">
                  🔷 {provider.name}
                </h3>
                <div className="text-gray-600 space-y-1">
                  <div>• {provider.models_with_requests} models with requests</div>
                  <div>• {provider.total_requests.toLocaleString()} total requests</div>
                </div>
              </div>
              <div className="text-3xl text-gray-400">→</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ProvidersPage;
```

## Page 2: Provider's Models List

### Layout
```
┌─────────────────────────────────────────────────────────────┐
│  ← Back to Providers                                         │
│                                                              │
│  OpenRouter - Models (15 models with requests)              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  ┌────────────────────────────────────────────┐            │
│  │  Google Gemini 2.0 Flash                    │  →         │
│  │  • 150 requests                             │            │
│  │  • Avg: 1,520ms                             │            │
│  │  • 30,000 tokens                            │            │
│  └────────────────────────────────────────────┘            │
│                                                              │
│  ┌────────────────────────────────────────────┐            │
│  │  GPT-4o Mini                                │  →         │
│  │  • 75 requests                              │            │
│  │  • Avg: 850ms                               │            │
│  │  • 15,000 tokens                            │            │
│  └────────────────────────────────────────────┘            │
│                                                              │
│  ┌────────────────────────────────────────────┐            │
│  │  Claude 3.5 Sonnet                          │  →         │
│  │  • 50 requests                              │            │
│  │  • Avg: 2,100ms                             │            │
│  │  • 12,500 tokens                            │            │
│  └────────────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────────┘
```

### Implementation
```jsx
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';

const ProviderModelsPage = () => {
  const { providerId } = useParams();
  const [provider, setProvider] = useState(null);
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    fetchModels();
  }, [providerId]);

  const fetchModels = async () => {
    try {
      const res = await fetch(`/api/monitoring/chat-requests/models?provider_id=${providerId}`);
      const data = await res.json();
      setModels(data.data);
      if (data.data.length > 0) {
        setProvider(data.data[0].provider);
      }
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch models:', error);
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="p-6">Loading models...</div>;
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <button
        onClick={() => navigate('/analytics')}
        className="mb-6 text-blue-600 hover:text-blue-800 flex items-center gap-2"
      >
        ← Back to Providers
      </button>

      <h1 className="text-3xl font-bold mb-2">
        {provider?.name || 'Provider'} - Models
      </h1>
      <p className="text-gray-600 mb-6">
        {models.length} models with requests
      </p>

      <div className="space-y-4">
        {models.map(model => (
          <div
            key={model.model_id}
            onClick={() => navigate(`/analytics/model/${model.model_id}`)}
            className="border rounded-lg p-6 hover:shadow-lg cursor-pointer transition-all hover:border-blue-500"
          >
            <div className="flex justify-between items-center">
              <div>
                <h3 className="text-xl font-semibold mb-2">{model.model_name}</h3>
                <div className="text-sm text-gray-600 space-y-1">
                  <div>• {model.stats.total_requests} requests</div>
                  <div>• Avg: {Math.round(model.stats.avg_processing_time_ms)}ms</div>
                  <div>• {model.stats.total_tokens.toLocaleString()} tokens</div>
                </div>
              </div>
              <div className="text-3xl text-gray-400">→</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ProviderModelsPage;
```

## Page 3: Model Details with Graph

### Layout
```
┌─────────────────────────────────────────────────────────────┐
│  ← Back to Models                                            │
│                                                              │
│  Google Gemini 2.0 Flash (OpenRouter)                       │
│  150 requests • Avg: 1,520ms • 30,000 tokens                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Time Range: [1D] [1W] [1M] [6M] [1Y] [All Time]           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  📈 Latency vs Total Tokens (Last 7 Days)                   │
│                                                              │
│     3000ms ┤                              •                 │
│     2500ms ┤                        •  •                    │
│     2000ms ┤              •    •  •   •  •                  │
│     1500ms ┤        • •  • • •  • •  •  •  •               │
│     1000ms ┤    • • • • • • • • • • • • • •  •             │
│      500ms ┤  • • • • • • • • • • • • • • • • •            │
│         0ms└──────────────────────────────────────────       │
│              0   100  200  300  400  500  600 tokens        │
│                                                              │
│  Each dot = 1 request                                        │
│  Color: 🟢 Fast (<1s) 🟡 Medium (1-2s) 🔴 Slow (>2s)       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Summary Statistics (Last 7 Days):                          │
│  ┌──────────────┬──────────────┬──────────────┐           │
│  │ Total        │ Success Rate │ Avg Latency  │           │
│  │ 150 requests │ 98.6%        │ 1,520ms      │           │
│  └──────────────┴──────────────┴──────────────┘           │
│  ┌──────────────┬──────────────┬──────────────┐           │
│  │ Avg Tokens   │ Min Latency  │ Max Latency  │           │
│  │ 200          │ 450ms        │ 2,850ms      │           │
│  └──────────────┴──────────────┴──────────────┘           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  Recent Requests (Last 10):                                 │
│  ┌──────────┬────────┬─────────┬──────────┐               │
│  │ Time     │ Tokens │ Latency │ Status   │               │
│  ├──────────┼────────┼─────────┼──────────┤               │
│  │ 10:30:15 │ 250    │ 1,520ms │ ✅ OK     │               │
│  │ 10:28:42 │ 180    │ 1,350ms │ ✅ OK     │               │
│  │ 10:25:10 │ 420    │ 2,100ms │ ✅ OK     │               │
│  │ ...      │ ...    │ ...     │ ...      │               │
│  └──────────┴────────┴─────────┴──────────┘               │
└─────────────────────────────────────────────────────────────┘
```

### Implementation
```jsx
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';

const TIME_RANGES = {
  '1D': { label: '1 Day', days: 1 },
  '1W': { label: '1 Week', days: 7 },
  '1M': { label: '1 Month', days: 30 },
  '6M': { label: '6 Months', days: 180 },
  '1Y': { label: '1 Year', days: 365 },
  'ALL': { label: 'All Time', days: null }
};

const ModelDetailsPage = () => {
  const { modelId } = useParams();
  const [model, setModel] = useState(null);
  const [requests, setRequests] = useState([]);
  const [timeRange, setTimeRange] = useState('1W');
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    fetchModelAndRequests();
  }, [modelId, timeRange]);

  const fetchModelAndRequests = async () => {
    try {
      // Fetch model info
      const modelRes = await fetch('/api/monitoring/chat-requests/models');
      const modelData = await modelRes.json();
      const foundModel = modelData.data.find(m => m.model_id === parseInt(modelId));
      setModel(foundModel);

      // Calculate date range
      const endDate = new Date();
      const startDate = new Date();
      const range = TIME_RANGES[timeRange];

      if (range.days) {
        startDate.setDate(startDate.getDate() - range.days);
      } else {
        // All time - set to very old date
        startDate.setFullYear(2000);
      }

      // Fetch requests with time filter
      const params = new URLSearchParams({
        model_id: modelId,
        start_date: startDate.toISOString(),
        end_date: endDate.toISOString(),
        limit: '1000'
      });

      const requestsRes = await fetch(`/api/monitoring/chat-requests?${params}`);
      const requestsData = await requestsRes.json();
      setRequests(requestsData.data);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch data:', error);
      setLoading(false);
    }
  };

  if (loading || !model) {
    return <div className="p-6">Loading...</div>;
  }

  // Prepare scatter plot data
  const scatterData = requests.map(req => ({
    tokens: req.total_tokens,
    latency: req.processing_time_ms,
    status: req.status,
    // Color based on latency
    fill: req.processing_time_ms < 1000 ? '#22c55e' : // green - fast
          req.processing_time_ms < 2000 ? '#eab308' : // yellow - medium
          '#ef4444' // red - slow
  }));

  // Calculate stats for current time range
  const stats = calculateStats(requests);

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <button
        onClick={() => navigate(`/analytics/provider/${model.provider.id}`)}
        className="mb-6 text-blue-600 hover:text-blue-800 flex items-center gap-2"
      >
        ← Back to Models
      </button>

      <div className="mb-6">
        <h1 className="text-3xl font-bold mb-2">{model.model_name}</h1>
        <p className="text-gray-600">
          {model.provider.name} • {stats.total} requests •
          Avg: {Math.round(stats.avgLatency)}ms •
          {stats.totalTokens.toLocaleString()} tokens
        </p>
      </div>

      {/* Time Range Selector */}
      <div className="mb-6 flex gap-2">
        <span className="font-semibold mr-2">Time Range:</span>
        {Object.entries(TIME_RANGES).map(([key, { label }]) => (
          <button
            key={key}
            onClick={() => setTimeRange(key)}
            className={`px-4 py-2 rounded ${
              timeRange === key
                ? 'bg-blue-600 text-white'
                : 'bg-gray-200 hover:bg-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Scatter Plot */}
      <div className="bg-white border rounded-lg p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">
          📈 Latency vs Total Tokens ({TIME_RANGES[timeRange].label})
        </h2>
        <ResponsiveContainer width="100%" height={400}>
          <ScatterChart margin={{ top: 20, right: 20, bottom: 60, left: 60 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="tokens"
              name="Total Tokens"
              label={{ value: 'Total Tokens', position: 'bottom', offset: 40 }}
            />
            <YAxis
              type="number"
              dataKey="latency"
              name="Latency (ms)"
              label={{ value: 'Latency (ms)', angle: -90, position: 'insideLeft', offset: 10 }}
            />
            <Tooltip
              cursor={{ strokeDasharray: '3 3' }}
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  return (
                    <div className="bg-white border p-2 rounded shadow">
                      <p>Tokens: {payload[0].value}</p>
                      <p>Latency: {payload[1].value}ms</p>
                    </div>
                  );
                }
                return null;
              }}
            />
            <Scatter
              name="Requests"
              data={scatterData}
              fill="#3b82f6"
            />
          </ScatterChart>
        </ResponsiveContainer>
        <div className="mt-4 text-sm text-gray-600 flex gap-4">
          <span>Each dot = 1 request</span>
          <span>🟢 Fast (&lt;1s)</span>
          <span>🟡 Medium (1-2s)</span>
          <span>🔴 Slow (&gt;2s)</span>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="bg-white border rounded-lg p-6 mb-6">
        <h2 className="text-xl font-semibold mb-4">
          Summary Statistics ({TIME_RANGES[timeRange].label})
        </h2>
        <div className="grid grid-cols-3 gap-4">
          <StatCard label="Total Requests" value={stats.total} />
          <StatCard label="Success Rate" value={`${stats.successRate}%`} />
          <StatCard label="Avg Latency" value={`${Math.round(stats.avgLatency)}ms`} />
          <StatCard label="Avg Tokens" value={Math.round(stats.avgTokens)} />
          <StatCard label="Min Latency" value={`${stats.minLatency}ms`} />
          <StatCard label="Max Latency" value={`${stats.maxLatency}ms`} />
        </div>
      </div>

      {/* Recent Requests Table */}
      <div className="bg-white border rounded-lg p-6">
        <h2 className="text-xl font-semibold mb-4">Recent Requests (Last 10)</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full">
            <thead className="bg-gray-100">
              <tr>
                <th className="px-4 py-2 text-left">Time</th>
                <th className="px-4 py-2 text-right">Tokens</th>
                <th className="px-4 py-2 text-right">Latency</th>
                <th className="px-4 py-2 text-center">Status</th>
              </tr>
            </thead>
            <tbody>
              {requests.slice(0, 10).map((req, idx) => (
                <tr key={idx} className="border-t hover:bg-gray-50">
                  <td className="px-4 py-2">
                    {new Date(req.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right">{req.total_tokens}</td>
                  <td className="px-4 py-2 text-right">{req.processing_time_ms}ms</td>
                  <td className="px-4 py-2 text-center">
                    {req.status === 'completed' ? '✅ OK' : '❌ Failed'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

// Helper: Calculate statistics
const calculateStats = (requests) => {
  if (requests.length === 0) {
    return {
      total: 0,
      successRate: 0,
      avgLatency: 0,
      avgTokens: 0,
      minLatency: 0,
      maxLatency: 0,
      totalTokens: 0
    };
  }

  const completed = requests.filter(r => r.status === 'completed');
  const latencies = requests.map(r => r.processing_time_ms);
  const tokens = requests.map(r => r.total_tokens);

  return {
    total: requests.length,
    successRate: ((completed.length / requests.length) * 100).toFixed(1),
    avgLatency: latencies.reduce((a, b) => a + b, 0) / latencies.length,
    avgTokens: tokens.reduce((a, b) => a + b, 0) / tokens.length,
    minLatency: Math.min(...latencies),
    maxLatency: Math.max(...latencies),
    totalTokens: tokens.reduce((a, b) => a + b, 0)
  };
};

// StatCard Component
const StatCard = ({ label, value }) => (
  <div className="bg-gray-50 p-4 rounded-lg">
    <div className="text-sm text-gray-600 mb-1">{label}</div>
    <div className="text-2xl font-bold">{value}</div>
  </div>
);

export default ModelDetailsPage;
```

## Routing Setup

```jsx
// App.jsx or Router.jsx
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import ProvidersPage from './pages/ProvidersPage';
import ProviderModelsPage from './pages/ProviderModelsPage';
import ModelDetailsPage from './pages/ModelDetailsPage';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/analytics" element={<ProvidersPage />} />
        <Route path="/analytics/provider/:providerId" element={<ProviderModelsPage />} />
        <Route path="/analytics/model/:modelId" element={<ModelDetailsPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
```

## Time Range Helper

```javascript
// utils/timeRanges.js
export const calculateDateRange = (rangeKey) => {
  const endDate = new Date();
  const startDate = new Date();

  switch (rangeKey) {
    case '1D':
      startDate.setDate(startDate.getDate() - 1);
      break;
    case '1W':
      startDate.setDate(startDate.getDate() - 7);
      break;
    case '1M':
      startDate.setMonth(startDate.getMonth() - 1);
      break;
    case '6M':
      startDate.setMonth(startDate.getMonth() - 6);
      break;
    case '1Y':
      startDate.setFullYear(startDate.getFullYear() - 1);
      break;
    case 'ALL':
      startDate.setFullYear(2000); // Very old date for all time
      break;
    default:
      startDate.setDate(startDate.getDate() - 7); // Default to 1 week
  }

  return {
    start: startDate.toISOString(),
    end: endDate.toISOString()
  };
};
```

## Features Summary

✅ **3-Level Navigation**: Providers → Models → Details
✅ **Only Models with Requests**: Filters out models with 0 requests
✅ **Scatter Plot**: Latency vs Total Tokens
✅ **Time Range Filters**: 1D, 1W, 1M, 6M, 1Y, All Time (crypto-style)
✅ **Color-Coded Performance**: Green (fast), Yellow (medium), Red (slow)
✅ **Summary Statistics**: Total requests, success rate, avg latency, etc.
✅ **Recent Requests Table**: Last 10 requests with details
✅ **Responsive Design**: Works on mobile and desktop

## Next Steps

1. Install dependencies:
   ```bash
   npm install recharts react-router-dom
   ```

2. Set up routing in your React app

3. Create the three pages (ProvidersPage, ProviderModelsPage, ModelDetailsPage)

4. Style with TailwindCSS or your preferred CSS framework

5. Test with real data from your backend

6. Optional enhancements:
   - Export to CSV/JSON
   - Real-time updates (WebSocket)
   - Advanced filters (status, token range)
   - Correlation analysis
   - Download graphs as images
