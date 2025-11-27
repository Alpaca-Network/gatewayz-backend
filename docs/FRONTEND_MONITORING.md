# Frontend Monitoring Dashboard Integration Guide

## Overview

This guide shows you how to integrate the Gatewayz monitoring API into your frontend dashboard.

**Two Options:**
1. **React/Next.js Dashboard** - Custom dashboard using monitoring API
2. **Grafana Cloud** - Production-grade monitoring (recommended for production)

---

## Option 1: React/Next.js Dashboard

### Architecture

```
Frontend (React/Next.js)
    ↓
Monitoring API (/api/monitoring/*)
    ↓
Redis + PostgreSQL
```

### API Endpoints Available

```typescript
// Base URL
const API_BASE = 'https://api.gatewayz.ai';

// Available endpoints
const ENDPOINTS = {
  // Provider Health
  providerHealth: '/api/monitoring/health',
  providerHealthDetail: (provider: string) => `/api/monitoring/health/${provider}`,

  // Statistics
  realtimeStats: '/api/monitoring/stats/realtime',
  hourlyStats: (provider: string, hours = 24) =>
    `/api/monitoring/stats/hourly/${provider}?hours=${hours}`,

  // Circuit Breakers
  circuitBreakers: '/api/monitoring/circuit-breakers',
  providerCircuitBreakers: (provider: string) =>
    `/api/monitoring/circuit-breakers/${provider}`,

  // Errors
  providerErrors: (provider: string, limit = 100) =>
    `/api/monitoring/errors/${provider}?limit=${limit}`,

  // Analytics
  providerComparison: '/api/monitoring/providers/comparison',
  anomalies: '/api/monitoring/anomalies',
  trialAnalytics: '/api/monitoring/trial-analytics',
  costAnalysis: (days = 7) => `/api/monitoring/cost-analysis?days=${days}`,
  errorRates: (hours = 24) => `/api/monitoring/error-rates?hours=${hours}`,

  // Latency
  latencyPercentiles: (provider: string, model: string) =>
    `/api/monitoring/latency/${provider}/${model}`,
  latencyTrends: (provider: string, hours = 24) =>
    `/api/monitoring/latency-trends/${provider}?hours=${hours}`,

  // Token Efficiency
  tokenEfficiency: (provider: string, model: string) =>
    `/api/monitoring/token-efficiency/${provider}/${model}`,
};
```

### 1. API Client Setup

```typescript
// lib/monitoring-api.ts

export interface ProviderHealth {
  provider: string;
  health_score: number;
  status: 'healthy' | 'degraded' | 'unhealthy';
  last_updated: string;
}

export interface RealtimeStats {
  timestamp: string;
  providers: Record<string, {
    total_requests: number;
    total_cost: number;
    health_score: number;
    hourly_breakdown: Record<string, any>;
  }>;
  total_requests: number;
  total_cost: number;
  avg_health_score: number;
}

export interface CircuitBreaker {
  provider: string;
  model: string;
  state: 'CLOSED' | 'OPEN' | 'HALF_OPEN';
  failure_count: number;
  is_available: boolean;
  last_updated: number;
}

export interface Anomaly {
  type: string;
  provider: string;
  hour: string;
  value: number;
  expected: number;
  severity: 'critical' | 'warning';
}

class MonitoringAPI {
  private baseURL: string;
  private apiKey?: string;

  constructor(baseURL: string, apiKey?: string) {
    this.baseURL = baseURL;
    this.apiKey = apiKey;
  }

  private async fetch<T>(endpoint: string): Promise<T> {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    if (this.apiKey) {
      headers['Authorization'] = `Bearer ${this.apiKey}`;
    }

    const response = await fetch(`${this.baseURL}${endpoint}`, { headers });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }

  // Provider Health
  async getProviderHealth(): Promise<ProviderHealth[]> {
    return this.fetch('/api/monitoring/health');
  }

  async getProviderHealthDetail(provider: string): Promise<ProviderHealth> {
    return this.fetch(`/api/monitoring/health/${provider}`);
  }

  // Real-time Statistics
  async getRealtimeStats(hours = 1): Promise<RealtimeStats> {
    return this.fetch(`/api/monitoring/stats/realtime?hours=${hours}`);
  }

  // Circuit Breakers
  async getCircuitBreakers(): Promise<CircuitBreaker[]> {
    return this.fetch('/api/monitoring/circuit-breakers');
  }

  // Anomalies
  async getAnomalies(): Promise<{
    timestamp: string;
    anomalies: Anomaly[];
    total_count: number;
    critical_count: number;
    warning_count: number;
  }> {
    return this.fetch('/api/monitoring/anomalies');
  }

  // Cost Analysis
  async getCostAnalysis(days = 7) {
    return this.fetch(`/api/monitoring/cost-analysis?days=${days}`);
  }

  // Provider Comparison
  async getProviderComparison() {
    return this.fetch('/api/monitoring/providers/comparison');
  }

  // Trial Analytics
  async getTrialAnalytics() {
    return this.fetch('/api/monitoring/trial-analytics');
  }
}

export const monitoringAPI = new MonitoringAPI(
  process.env.NEXT_PUBLIC_API_URL || 'https://api.gatewayz.ai',
  process.env.NEXT_PUBLIC_ADMIN_API_KEY // Optional: for authenticated access
);
```

### 2. React Hooks

```typescript
// hooks/useMonitoring.ts

import { useQuery } from '@tanstack/react-query';
import { monitoringAPI } from '@/lib/monitoring-api';

export function useProviderHealth() {
  return useQuery({
    queryKey: ['provider-health'],
    queryFn: () => monitoringAPI.getProviderHealth(),
    refetchInterval: 30000, // Refresh every 30 seconds
  });
}

export function useRealtimeStats(hours = 1) {
  return useQuery({
    queryKey: ['realtime-stats', hours],
    queryFn: () => monitoringAPI.getRealtimeStats(hours),
    refetchInterval: 10000, // Refresh every 10 seconds
  });
}

export function useCircuitBreakers() {
  return useQuery({
    queryKey: ['circuit-breakers'],
    queryFn: () => monitoringAPI.getCircuitBreakers(),
    refetchInterval: 15000, // Refresh every 15 seconds
  });
}

export function useAnomalies() {
  return useQuery({
    queryKey: ['anomalies'],
    queryFn: () => monitoringAPI.getAnomalies(),
    refetchInterval: 60000, // Refresh every minute
  });
}

export function useCostAnalysis(days = 7) {
  return useQuery({
    queryKey: ['cost-analysis', days],
    queryFn: () => monitoringAPI.getCostAnalysis(days),
    refetchInterval: 300000, // Refresh every 5 minutes
  });
}

export function useProviderComparison() {
  return useQuery({
    queryKey: ['provider-comparison'],
    queryFn: () => monitoringAPI.getProviderComparison(),
    refetchInterval: 30000,
  });
}

export function useTrialAnalytics() {
  return useQuery({
    queryKey: ['trial-analytics'],
    queryFn: () => monitoringAPI.getTrialAnalytics(),
    refetchInterval: 300000, // Refresh every 5 minutes
  });
}
```

### 3. Dashboard Components

#### Provider Health Cards

```tsx
// components/ProviderHealthCards.tsx

import { useProviderHealth } from '@/hooks/useMonitoring';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

export function ProviderHealthCards() {
  const { data: providers, isLoading } = useProviderHealth();

  if (isLoading) return <div>Loading...</div>;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {providers?.map((provider) => (
        <Card key={provider.provider}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">
              {provider.provider}
            </CardTitle>
            <Badge variant={
              provider.status === 'healthy' ? 'default' :
              provider.status === 'degraded' ? 'warning' : 'destructive'
            }>
              {provider.status}
            </Badge>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {provider.health_score.toFixed(1)}
            </div>
            <p className="text-xs text-muted-foreground">
              Health Score (0-100)
            </p>
            <div className="mt-2">
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div
                  className={`h-2 rounded-full ${
                    provider.health_score >= 80 ? 'bg-green-500' :
                    provider.health_score >= 50 ? 'bg-yellow-500' : 'bg-red-500'
                  }`}
                  style={{ width: `${provider.health_score}%` }}
                />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
```

#### Real-time Stats Dashboard

```tsx
// components/RealtimeStatsDashboard.tsx

import { useRealtimeStats } from '@/hooks/useMonitoring';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export function RealtimeStatsDashboard() {
  const { data: stats, isLoading } = useRealtimeStats(1);

  if (isLoading) return <div>Loading...</div>;

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            Total Requests
          </CardTitle>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            className="h-4 w-4 text-muted-foreground"
          >
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{stats?.total_requests.toLocaleString()}</div>
          <p className="text-xs text-muted-foreground">
            Last hour
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            Total Cost
          </CardTitle>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            className="h-4 w-4 text-muted-foreground"
          >
            <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
          </svg>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            ${stats?.total_cost.toFixed(2)}
          </div>
          <p className="text-xs text-muted-foreground">
            Last hour
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            Avg Health Score
          </CardTitle>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            className="h-4 w-4 text-muted-foreground"
          >
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {stats?.avg_health_score.toFixed(1)}
          </div>
          <p className="text-xs text-muted-foreground">
            All providers
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium">
            Active Providers
          </CardTitle>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            className="h-4 w-4 text-muted-foreground"
          >
            <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {Object.keys(stats?.providers || {}).length}
          </div>
          <p className="text-xs text-muted-foreground">
            Currently responding
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
```

#### Circuit Breaker Status

```tsx
// components/CircuitBreakerStatus.tsx

import { useCircuitBreakers } from '@/hooks/useMonitoring';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

export function CircuitBreakerStatus() {
  const { data: breakers, isLoading } = useCircuitBreakers();

  if (isLoading) return <div>Loading...</div>;

  // Filter to only show open or recently failed breakers
  const activeBreakers = breakers?.filter(
    b => b.state !== 'CLOSED' || b.failure_count > 0
  );

  if (!activeBreakers || activeBreakers.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        All circuit breakers are healthy ✓
      </div>
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Provider</TableHead>
          <TableHead>Model</TableHead>
          <TableHead>State</TableHead>
          <TableHead>Failures</TableHead>
          <TableHead>Available</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {activeBreakers.map((breaker) => (
          <TableRow key={`${breaker.provider}-${breaker.model}`}>
            <TableCell className="font-medium">{breaker.provider}</TableCell>
            <TableCell>{breaker.model}</TableCell>
            <TableCell>
              <Badge variant={
                breaker.state === 'CLOSED' ? 'default' :
                breaker.state === 'HALF_OPEN' ? 'warning' : 'destructive'
              }>
                {breaker.state}
              </Badge>
            </TableCell>
            <TableCell>{breaker.failure_count}</TableCell>
            <TableCell>
              {breaker.is_available ? '✓ Yes' : '✗ No'}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

#### Anomaly Alerts

```tsx
// components/AnomalyAlerts.tsx

import { useAnomalies } from '@/hooks/useMonitoring';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, AlertTriangle } from 'lucide-react';

export function AnomalyAlerts() {
  const { data: anomalyData, isLoading } = useAnomalies();

  if (isLoading) return <div>Loading...</div>;

  if (!anomalyData || anomalyData.total_count === 0) {
    return (
      <Alert>
        <AlertTitle>No Anomalies Detected</AlertTitle>
        <AlertDescription>
          All systems operating normally.
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="space-y-4">
      {anomalyData.anomalies.map((anomaly, idx) => (
        <Alert
          key={idx}
          variant={anomaly.severity === 'critical' ? 'destructive' : 'default'}
        >
          {anomaly.severity === 'critical' ? (
            <AlertCircle className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          <AlertTitle>
            {anomaly.type.replace('_', ' ').toUpperCase()} - {anomaly.provider}
          </AlertTitle>
          <AlertDescription>
            Detected at {anomaly.hour}: {anomaly.type === 'cost_spike' ? '$' : ''}
            {anomaly.value.toFixed(2)} (expected: {anomaly.expected.toFixed(2)})
          </AlertDescription>
        </Alert>
      ))}
    </div>
  );
}
```

#### Complete Monitoring Dashboard Page

```tsx
// app/dashboard/monitoring/page.tsx

import { ProviderHealthCards } from '@/components/ProviderHealthCards';
import { RealtimeStatsDashboard } from '@/components/RealtimeStatsDashboard';
import { CircuitBreakerStatus } from '@/components/CircuitBreakerStatus';
import { AnomalyAlerts } from '@/components/AnomalyAlerts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export default function MonitoringDashboard() {
  return (
    <div className="flex-1 space-y-4 p-8 pt-6">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Monitoring Dashboard</h2>
        <div className="flex items-center space-x-2">
          <span className="text-sm text-muted-foreground">
            Auto-refresh: Every 30s
          </span>
        </div>
      </div>

      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="health">Provider Health</TabsTrigger>
          <TabsTrigger value="circuit-breakers">Circuit Breakers</TabsTrigger>
          <TabsTrigger value="anomalies">Anomalies</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <RealtimeStatsDashboard />

          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-7">
            <Card className="col-span-4">
              <CardHeader>
                <CardTitle>Recent Anomalies</CardTitle>
                <CardDescription>
                  Detected cost spikes, latency issues, and error patterns
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AnomalyAlerts />
              </CardContent>
            </Card>

            <Card className="col-span-3">
              <CardHeader>
                <CardTitle>Active Issues</CardTitle>
                <CardDescription>
                  Circuit breakers and failing providers
                </CardDescription>
              </CardHeader>
              <CardContent>
                <CircuitBreakerStatus />
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="health" className="space-y-4">
          <ProviderHealthCards />
        </TabsContent>

        <TabsContent value="circuit-breakers" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Circuit Breaker Status</CardTitle>
              <CardDescription>
                Monitor provider failures and automatic failover
              </CardDescription>
            </CardHeader>
            <CardContent>
              <CircuitBreakerStatus />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="anomalies" className="space-y-4">
          <AnomalyAlerts />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

### 4. Installation

```bash
# Install dependencies
npm install @tanstack/react-query

# If using shadcn/ui
npx shadcn-ui@latest init
npx shadcn-ui@latest add card badge alert table tabs
```

### 5. Setup React Query Provider

```tsx
// app/providers.tsx

'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';

export function Providers({ children }: { children: React.Node }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30000, // 30 seconds
        refetchOnWindowFocus: false,
      },
    },
  }));

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
```

```tsx
// app/layout.tsx

import { Providers } from './providers';

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
```

---

## Option 2: Grafana Cloud (Recommended for Production)

### Why Grafana Cloud?

- ✅ Production-grade dashboards
- ✅ Advanced alerting
- ✅ No frontend code needed
- ✅ Free tier available
- ✅ Mobile app support

### Quick Setup

#### 1. Create Grafana Cloud Account
https://grafana.com/auth/sign-up

#### 2. Add Environment Variables

```bash
# .env
GRAFANA_CLOUD_ENABLED=true
GRAFANA_PROMETHEUS_REMOTE_WRITE_URL=https://prometheus-prod-xx-prod-us-central-x.grafana.net/api/prom/push
GRAFANA_PROMETHEUS_USERNAME=123456
GRAFANA_PROMETHEUS_API_KEY=glc_your-api-key
```

#### 3. Restart Application

Metrics will automatically flow to Grafana Cloud.

#### 4. Create Dashboards

Import pre-built dashboards or create custom ones using the monitoring API data.

#### 5. Set Up Alerts

Upload `prometheus-alerts.yml` to Grafana Cloud for automated alerting.

---

## Comparison

| Feature | React Dashboard | Grafana Cloud |
|---------|----------------|---------------|
| **Cost** | Free (self-hosted) | Free tier / $50/month |
| **Setup Time** | 2-4 hours | 15 minutes |
| **Customization** | Full control | Limited to Grafana |
| **Mobile Support** | Need to build | Built-in app |
| **Alerting** | Need to build | Advanced built-in |
| **Historical Data** | Need to build | Built-in |
| **Recommended For** | Custom branding | Production monitoring |

---

## Recommended Approach

### Development
Use **React Dashboard** for quick visibility in your admin panel.

### Production
Use **Grafana Cloud** for serious monitoring + alerts.

### Hybrid (Best)
- **Grafana Cloud**: For operations team, alerting, deep analysis
- **React Dashboard**: For customer-facing metrics, quick overview in admin panel

---

## Next Steps

1. **Choose your approach** (React, Grafana, or both)
2. **Set up monitoring API calls** (if using React)
3. **Create first dashboard** (start with Provider Health)
4. **Set up alerts** (critical: provider down, high error rate)
5. **Monitor and iterate**

---

## Need Help?

- **API Documentation**: `/docs/MONITORING.md`
- **Example Dashboards**: Coming soon
- **Support**: support@gatewayz.ai
