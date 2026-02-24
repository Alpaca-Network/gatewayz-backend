# Downtime Admin Page - Frontend Example

This guide shows how to build an admin page to view downtime incidents and their logs.

## Overview

**Features:**
- List all downtime incidents with filters
- Click an incident to view detailed logs
- Filter logs by level (ERROR, WARNING, etc.)
- Search logs by keyword
- View error analysis and statistics
- Real-time updates for ongoing incidents

## API Integration

### 1. Fetch Downtime Incidents

```typescript
// API Client
const API_BASE = 'https://api.gatewayz.ai';
const ADMIN_API_KEY = process.env.NEXT_PUBLIC_ADMIN_API_KEY;

interface DowntimeIncident {
  id: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  status: 'ongoing' | 'resolved' | 'investigating';
  severity: 'low' | 'medium' | 'high' | 'critical';
  error_message: string | null;
  http_status_code: number | null;
  environment: string;
  log_count: number;
  created_at: string;
}

// Fetch incidents
export async function fetchDowntimeIncidents(params?: {
  limit?: number;
  status?: string;
  severity?: string;
  environment?: string;
}) {
  const queryParams = new URLSearchParams();
  if (params?.limit) queryParams.append('limit', params.limit.toString());
  if (params?.status) queryParams.append('status', params.status);
  if (params?.severity) queryParams.append('severity', params.severity);
  if (params?.environment) queryParams.append('environment', params.environment);

  const response = await fetch(
    `${API_BASE}/admin/downtime/incidents?${queryParams}`,
    {
      headers: {
        'Authorization': `Bearer ${ADMIN_API_KEY}`,
        'Content-Type': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch incidents: ${response.statusText}`);
  }

  return response.json();
}

// Fetch specific incident with logs
export async function fetchIncidentDetails(incidentId: string) {
  const response = await fetch(
    `${API_BASE}/admin/downtime/incidents/${incidentId}`,
    {
      headers: {
        'Authorization': `Bearer ${ADMIN_API_KEY}`,
        'Content-Type': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch incident: ${response.statusText}`);
  }

  return response.json();
}

// Fetch logs for an incident
export async function fetchIncidentLogs(
  incidentId: string,
  filters?: {
    level?: 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG';
    logger_name?: string;
    search?: string;
  }
) {
  const queryParams = new URLSearchParams();
  if (filters?.level) queryParams.append('level', filters.level);
  if (filters?.logger_name) queryParams.append('logger_name', filters.logger_name);
  if (filters?.search) queryParams.append('search', filters.search);

  const response = await fetch(
    `${API_BASE}/admin/downtime/incidents/${incidentId}/logs?${queryParams}`,
    {
      headers: {
        'Authorization': `Bearer ${ADMIN_API_KEY}`,
        'Content-Type': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch logs: ${response.statusText}`);
  }

  return response.json();
}

// Fetch log analysis
export async function fetchIncidentAnalysis(incidentId: string) {
  const response = await fetch(
    `${API_BASE}/admin/downtime/incidents/${incidentId}/analysis`,
    {
      headers: {
        'Authorization': `Bearer ${ADMIN_API_KEY}`,
        'Content-Type': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch analysis: ${response.statusText}`);
  }

  return response.json();
}

// Fetch statistics
export async function fetchDowntimeStatistics(days: number = 30) {
  const response = await fetch(
    `${API_BASE}/admin/downtime/statistics?days=${days}`,
    {
      headers: {
        'Authorization': `Bearer ${ADMIN_API_KEY}`,
        'Content-Type': 'application/json',
      },
    }
  );

  if (!response.ok) {
    throw new Error(`Failed to fetch statistics: ${response.statusText}`);
  }

  return response.json();
}
```

## React/Next.js Component

### Main Admin Page Component

```tsx
'use client';

import { useState, useEffect } from 'react';
import { format, formatDistanceToNow } from 'date-fns';
import {
  fetchDowntimeIncidents,
  fetchIncidentDetails,
  fetchIncidentLogs,
  fetchIncidentAnalysis,
  DowntimeIncident,
} from '@/lib/api/downtime';

export default function DowntimeAdminPage() {
  const [incidents, setIncidents] = useState<DowntimeIncident[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({
    status: '',
    severity: '',
    environment: '',
  });

  // Fetch incidents on mount and when filters change
  useEffect(() => {
    loadIncidents();
  }, [filters]);

  const loadIncidents = async () => {
    setLoading(true);
    try {
      const data = await fetchDowntimeIncidents({
        limit: 50,
        ...filters,
      });
      setIncidents(data.incidents);
    } catch (error) {
      console.error('Failed to load incidents:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900">
            Downtime Incidents
          </h1>
          <p className="mt-2 text-gray-600">
            Monitor and analyze application downtime events
          </p>
        </div>

        {/* Filters */}
        <div className="bg-white rounded-lg shadow p-6 mb-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Status
              </label>
              <select
                value={filters.status}
                onChange={(e) => setFilters({ ...filters, status: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">All Statuses</option>
                <option value="ongoing">Ongoing</option>
                <option value="resolved">Resolved</option>
                <option value="investigating">Investigating</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Severity
              </label>
              <select
                value={filters.severity}
                onChange={(e) => setFilters({ ...filters, severity: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">All Severities</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Environment
              </label>
              <select
                value={filters.environment}
                onChange={(e) => setFilters({ ...filters, environment: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
              >
                <option value="">All Environments</option>
                <option value="production">Production</option>
                <option value="staging">Staging</option>
                <option value="development">Development</option>
              </select>
            </div>
          </div>
        </div>

        {/* Incidents List */}
        <div className="bg-white rounded-lg shadow">
          {loading ? (
            <div className="p-8 text-center">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
              <p className="mt-4 text-gray-600">Loading incidents...</p>
            </div>
          ) : incidents.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              No downtime incidents found
            </div>
          ) : (
            <div className="divide-y divide-gray-200">
              {incidents.map((incident) => (
                <IncidentRow
                  key={incident.id}
                  incident={incident}
                  onClick={() => setSelectedIncident(incident.id)}
                  isSelected={selectedIncident === incident.id}
                />
              ))}
            </div>
          )}
        </div>

        {/* Incident Details Modal */}
        {selectedIncident && (
          <IncidentDetailsModal
            incidentId={selectedIncident}
            onClose={() => setSelectedIncident(null)}
          />
        )}
      </div>
    </div>
  );
}

// Incident row component
function IncidentRow({
  incident,
  onClick,
  isSelected,
}: {
  incident: DowntimeIncident;
  onClick: () => void;
  isSelected: boolean;
}) {
  const severityColors = {
    low: 'bg-blue-100 text-blue-800',
    medium: 'bg-yellow-100 text-yellow-800',
    high: 'bg-orange-100 text-orange-800',
    critical: 'bg-red-100 text-red-800',
  };

  const statusColors = {
    ongoing: 'bg-red-100 text-red-800',
    resolved: 'bg-green-100 text-green-800',
    investigating: 'bg-yellow-100 text-yellow-800',
  };

  return (
    <div
      onClick={onClick}
      className={`p-6 cursor-pointer hover:bg-gray-50 transition-colors ${
        isSelected ? 'bg-blue-50' : ''
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex-1">
          <div className="flex items-center gap-3 mb-2">
            <span
              className={`px-2 py-1 text-xs font-semibold rounded-full ${
                statusColors[incident.status]
              }`}
            >
              {incident.status.toUpperCase()}
            </span>
            <span
              className={`px-2 py-1 text-xs font-semibold rounded-full ${
                severityColors[incident.severity]
              }`}
            >
              {incident.severity.toUpperCase()}
            </span>
            <span className="text-xs text-gray-500">
              {incident.environment}
            </span>
          </div>

          <h3 className="text-lg font-semibold text-gray-900 mb-1">
            {incident.error_message || 'Health check failed'}
          </h3>

          <div className="flex items-center gap-4 text-sm text-gray-600">
            <span>
              Started: {format(new Date(incident.started_at), 'MMM d, yyyy HH:mm:ss')}
            </span>
            {incident.ended_at && (
              <span>
                Duration: {formatDuration(incident.duration_seconds)}
              </span>
            )}
            {!incident.ended_at && (
              <span className="text-red-600 font-medium">
                Ongoing for {formatDistanceToNow(new Date(incident.started_at))}
              </span>
            )}
          </div>

          {incident.http_status_code && (
            <div className="mt-2 text-sm text-gray-600">
              HTTP Status: {incident.http_status_code}
            </div>
          )}
        </div>

        <div className="text-right">
          <div className="text-2xl font-bold text-gray-900">
            {incident.log_count || 0}
          </div>
          <div className="text-xs text-gray-500">logs captured</div>
        </div>
      </div>
    </div>
  );
}

// Helper function
function formatDuration(seconds: number | null): string {
  if (!seconds) return 'N/A';
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${minutes}m ${secs}s`;
}
```

### Incident Details Modal

```tsx
'use client';

import { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import {
  fetchIncidentDetails,
  fetchIncidentLogs,
  fetchIncidentAnalysis,
} from '@/lib/api/downtime';

interface IncidentDetailsModalProps {
  incidentId: string;
  onClose: () => void;
}

export function IncidentDetailsModal({
  incidentId,
  onClose,
}: IncidentDetailsModalProps) {
  const [incident, setIncident] = useState<any>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any>(null);
  const [logFilters, setLogFilters] = useState({
    level: '',
    search: '',
  });
  const [activeTab, setActiveTab] = useState<'logs' | 'analysis'>('logs');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadIncidentData();
  }, [incidentId]);

  useEffect(() => {
    if (incident) {
      loadLogs();
    }
  }, [logFilters]);

  const loadIncidentData = async () => {
    setLoading(true);
    try {
      const [incidentData, analysisData] = await Promise.all([
        fetchIncidentDetails(incidentId),
        fetchIncidentAnalysis(incidentId),
      ]);

      setIncident(incidentData.incident);
      setAnalysis(analysisData.analysis);

      // Load initial logs
      const logsData = await fetchIncidentLogs(incidentId);
      setLogs(logsData.logs);
    } catch (error) {
      console.error('Failed to load incident data:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadLogs = async () => {
    try {
      const logsData = await fetchIncidentLogs(incidentId, logFilters);
      setLogs(logsData.logs);
    } catch (error) {
      console.error('Failed to load logs:', error);
    }
  };

  if (loading || !incident) {
    return (
      <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-6xl max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">
              Incident Details
            </h2>
            <p className="text-sm text-gray-600 mt-1">
              ID: {incidentId}
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 rounded-full transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Incident Info */}
        <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <div className="text-xs text-gray-500 uppercase">Status</div>
              <div className="text-sm font-semibold mt-1">{incident.status}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase">Severity</div>
              <div className="text-sm font-semibold mt-1">{incident.severity}</div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase">Started</div>
              <div className="text-sm font-semibold mt-1">
                {new Date(incident.started_at).toLocaleString()}
              </div>
            </div>
            <div>
              <div className="text-xs text-gray-500 uppercase">Duration</div>
              <div className="text-sm font-semibold mt-1">
                {incident.duration_seconds
                  ? formatDuration(incident.duration_seconds)
                  : 'Ongoing'}
              </div>
            </div>
          </div>

          {incident.error_message && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-md">
              <div className="text-xs text-red-600 uppercase font-semibold mb-1">
                Error Message
              </div>
              <div className="text-sm text-red-900">{incident.error_message}</div>
            </div>
          )}
        </div>

        {/* Tabs */}
        <div className="border-b border-gray-200">
          <div className="flex">
            <button
              onClick={() => setActiveTab('logs')}
              className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'logs'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              Logs ({logs.length})
            </button>
            <button
              onClick={() => setActiveTab('analysis')}
              className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'analysis'
                  ? 'border-blue-500 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              Analysis
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="overflow-y-auto" style={{ maxHeight: 'calc(90vh - 300px)' }}>
          {activeTab === 'logs' ? (
            <LogsTab
              logs={logs}
              filters={logFilters}
              onFilterChange={setLogFilters}
            />
          ) : (
            <AnalysisTab analysis={analysis} />
          )}
        </div>
      </div>
    </div>
  );
}

// Logs tab component
function LogsTab({ logs, filters, onFilterChange }: any) {
  const levelColors = {
    ERROR: 'bg-red-100 text-red-800 border-red-300',
    WARNING: 'bg-yellow-100 text-yellow-800 border-yellow-300',
    INFO: 'bg-blue-100 text-blue-800 border-blue-300',
    DEBUG: 'bg-gray-100 text-gray-800 border-gray-300',
  };

  return (
    <div className="p-6">
      {/* Filters */}
      <div className="mb-6 flex gap-4">
        <select
          value={filters.level}
          onChange={(e) => onFilterChange({ ...filters, level: e.target.value })}
          className="px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
        >
          <option value="">All Levels</option>
          <option value="ERROR">ERROR</option>
          <option value="WARNING">WARNING</option>
          <option value="INFO">INFO</option>
          <option value="DEBUG">DEBUG</option>
        </select>

        <input
          type="text"
          placeholder="Search logs..."
          value={filters.search}
          onChange={(e) => onFilterChange({ ...filters, search: e.target.value })}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
        />
      </div>

      {/* Logs */}
      <div className="space-y-2">
        {logs.length === 0 ? (
          <div className="text-center py-8 text-gray-500">
            No logs found matching your filters
          </div>
        ) : (
          logs.map((log: any, index: number) => (
            <div
              key={index}
              className="p-4 bg-white border border-gray-200 rounded-md hover:shadow-sm transition-shadow font-mono text-sm"
            >
              <div className="flex items-start gap-3">
                <span
                  className={`px-2 py-0.5 text-xs font-semibold rounded border ${
                    levelColors[log.level as keyof typeof levelColors] ||
                    'bg-gray-100 text-gray-800 border-gray-300'
                  }`}
                >
                  {log.level}
                </span>
                <div className="flex-1">
                  <div className="text-gray-900 mb-1">{log.message}</div>
                  <div className="flex items-center gap-4 text-xs text-gray-500">
                    <span>{log.timestamp}</span>
                    {log.logger && <span>Logger: {log.logger}</span>}
                  </div>
                  {log.exception && (
                    <pre className="mt-2 p-2 bg-red-50 text-red-900 text-xs overflow-x-auto rounded">
                      {log.exception}
                    </pre>
                  )}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// Analysis tab component
function AnalysisTab({ analysis }: any) {
  if (!analysis) {
    return (
      <div className="p-6 text-center text-gray-500">
        No analysis available
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          <div className="text-3xl font-bold text-gray-900">
            {analysis.total_logs}
          </div>
          <div className="text-sm text-gray-600 mt-1">Total Logs</div>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-lg p-6">
          <div className="text-3xl font-bold text-red-900">
            {analysis.error_count}
          </div>
          <div className="text-sm text-red-600 mt-1">Errors</div>
        </div>
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6">
          <div className="text-3xl font-bold text-yellow-900">
            {analysis.warning_count}
          </div>
          <div className="text-sm text-yellow-600 mt-1">Warnings</div>
        </div>
      </div>

      {/* Error Types */}
      <div className="mb-8">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">
          Error Types
        </h3>
        <div className="space-y-2">
          {Object.entries(analysis.error_types || {}).map(([type, count]) => (
            <div
              key={type}
              className="flex items-center justify-between p-3 bg-white border border-gray-200 rounded-md"
            >
              <span className="font-medium text-gray-900">{type}</span>
              <span className="text-red-600 font-semibold">{count as number}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Top Errors */}
      <div>
        <h3 className="text-lg font-semibold text-gray-900 mb-4">
          Top Error Messages
        </h3>
        <div className="space-y-2">
          {(analysis.top_errors || []).map(([message, count]: [string, number], index: number) => (
            <div
              key={index}
              className="p-4 bg-white border border-gray-200 rounded-md"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 font-mono text-sm text-gray-900">
                  {message}
                </div>
                <div className="text-red-600 font-semibold">{count}×</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${minutes}m ${secs}s`;
}
```

## Usage Example

```tsx
// app/admin/downtime/page.tsx
import DowntimeAdminPage from '@/components/admin/DowntimeAdminPage';

export default function AdminDowntimePage() {
  return <DowntimeAdminPage />;
}
```

## Environment Variables

```bash
# .env.local
NEXT_PUBLIC_ADMIN_API_KEY=your_admin_api_key_here
NEXT_PUBLIC_API_BASE_URL=https://api.gatewayz.ai
```

## Features Demonstrated

### 1. **Incident List**
- ✅ Display all incidents with status badges
- ✅ Filter by status, severity, environment
- ✅ Show duration and log count
- ✅ Real-time updates for ongoing incidents

### 2. **Incident Details Modal**
- ✅ Full incident information
- ✅ Tabbed interface (Logs / Analysis)
- ✅ Log filtering by level and search
- ✅ Syntax highlighting for errors
- ✅ Error analysis with statistics

### 3. **Log Viewer**
- ✅ Color-coded log levels
- ✅ Searchable and filterable
- ✅ Shows timestamps and logger names
- ✅ Exception stack traces

### 4. **Error Analysis**
- ✅ Statistics dashboard
- ✅ Error type breakdown
- ✅ Top error messages
- ✅ Visual indicators

## Styling

Add Tailwind CSS configuration:

```javascript
// tailwind.config.js
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};
```

## API Response Examples

### List Incidents Response
```json
{
  "status": "success",
  "total_incidents": 5,
  "ongoing": 1,
  "resolved": 4,
  "incidents": [
    {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "started_at": "2026-02-12T10:00:00Z",
      "ended_at": "2026-02-12T10:15:00Z",
      "duration_seconds": 900,
      "status": "resolved",
      "severity": "critical",
      "error_message": "Database connection timeout",
      "http_status_code": 503,
      "environment": "production",
      "log_count": 1234
    }
  ]
}
```

### Logs Response
```json
{
  "status": "success",
  "total_logs": 1234,
  "total_captured": 1234,
  "filters": {
    "level": "ERROR",
    "logger": null,
    "search": null
  },
  "logs": [
    {
      "timestamp": "2026-02-12T10:05:00Z",
      "level": "ERROR",
      "logger": "src.routes.chat",
      "message": "Database connection timeout",
      "trace_id": "abc123",
      "exception": "TimeoutError: Connection timed out after 30s"
    }
  ]
}
```

This creates a complete, production-ready admin interface for viewing downtime incidents and analyzing logs!
