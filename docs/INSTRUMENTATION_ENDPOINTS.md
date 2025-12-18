# Instrumentation Endpoints - Loki & Tempo Integration

This document describes the instrumentation endpoints for monitoring Loki logging and Tempo tracing integration.

## Overview

The instrumentation endpoints provide visibility into:
- **Loki**: Log aggregation and structured logging
- **Tempo**: Distributed tracing and trace correlation
- **Trace Context**: Current trace and span IDs for debugging

## Base URL

All instrumentation endpoints are available at:
```
/api/instrumentation
```

## Endpoints

### 1. Health Check (Public)

**GET** `/api/instrumentation/health`

Get overall instrumentation health status.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-12-16T18:30:00.000000",
  "loki": {
    "enabled": true,
    "url": "http://loki:3100/loki/api/v1/push",
    "service_name": "gatewayz-api",
    "environment": "production"
  },
  "tempo": {
    "enabled": true,
    "endpoint": "http://tempo:4318",
    "service_name": "gatewayz-api",
    "environment": "production"
  }
}
```

### 2. Trace Context (Public)

**GET** `/api/instrumentation/trace-context`

Get current trace and span IDs for correlation.

**Response:**
```json
{
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

**Use Case**: Include these IDs in error reports or support tickets for tracing specific requests.

### 3. Loki Status (Admin)

**GET** `/api/instrumentation/loki/status`

Get Loki logging configuration and status.

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "enabled": true,
  "push_url": "http://loki:3100/loki/api/v1/push",
  "query_url": "http://loki:3100/loki/api/v1/query_range",
  "service_name": "gatewayz-api",
  "environment": "production",
  "tags": {
    "app": "gatewayz-api",
    "environment": "production",
    "service": "gatewayz-api"
  },
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

### 4. Tempo Status (Admin)

**GET** `/api/instrumentation/tempo/status`

Get Tempo tracing configuration and status.

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "enabled": true,
  "otlp_http_endpoint": "http://tempo:4318",
  "otlp_grpc_endpoint": "http://tempo:4317",
  "service_name": "gatewayz-api",
  "environment": "production",
  "resource_attributes": {
    "service.name": "gatewayz-api",
    "service.version": "2.0.3",
    "deployment.environment": "production"
  },
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

### 5. Complete Configuration (Admin)

**GET** `/api/instrumentation/config`

Get complete instrumentation configuration.

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "service": {
    "name": "gatewayz-api",
    "version": "2.0.3",
    "environment": "production"
  },
  "loki": {
    "enabled": true,
    "push_url": "http://loki:3100/loki/api/v1/push",
    "query_url": "http://loki:3100/loki/api/v1/query_range",
    "labels": {
      "app": "gatewayz-api",
      "environment": "production",
      "service": "gatewayz-api"
    }
  },
  "tempo": {
    "enabled": true,
    "otlp_http_endpoint": "http://tempo:4318",
    "otlp_grpc_endpoint": "http://tempo:4317"
  },
  "environment_variables": {
    "LOKI_ENABLED": true,
    "LOKI_PUSH_URL": "***",
    "LOKI_QUERY_URL": "***",
    "TEMPO_ENABLED": true,
    "TEMPO_OTLP_HTTP_ENDPOINT": "***",
    "TEMPO_OTLP_GRPC_ENDPOINT": "***",
    "OTEL_SERVICE_NAME": "gatewayz-api",
    "APP_ENV": "production"
  },
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

### 6. Test Trace (Admin)

**POST** `/api/instrumentation/test-trace`

Generate a test trace for verification.

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "status": "success",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "message": "Test trace generated successfully. Check Tempo for trace details.",
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

**Next Steps**:
1. Open Grafana at `http://localhost:3000`
2. Navigate to Tempo datasource
3. Search for the trace_id returned above
4. Verify the trace appears with proper span hierarchy

### 7. Test Log (Admin)

**POST** `/api/instrumentation/test-log`

Generate a test log for verification.

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "status": "success",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "message": "Test log generated successfully. Check Loki for log details.",
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

**Next Steps**:
1. Open Grafana at `http://localhost:3000`
2. Navigate to Loki datasource
3. Query: `{service="gatewayz-api", test="true"}`
4. Verify the log appears with trace_id correlation

### 8. Environment Variables (Admin)

**GET** `/api/instrumentation/environment-variables`

Get instrumentation-related environment variables (sensitive values masked).

**Authentication**: Requires `Authorization: Bearer <ADMIN_API_KEY>`

**Response:**
```json
{
  "loki": {
    "LOKI_ENABLED": "true",
    "LOKI_URL": "***",
    "LOKI_PUSH_URL": "***",
    "LOKI_QUERY_URL": "***"
  },
  "tempo": {
    "TEMPO_ENABLED": "true",
    "TEMPO_URL": "***",
    "TEMPO_OTLP_HTTP_ENDPOINT": "***",
    "TEMPO_OTLP_GRPC_ENDPOINT": "***"
  },
  "service": {
    "SERVICE_NAME": "gatewayz-api",
    "SERVICE_VERSION": "1.0.0",
    "ENVIRONMENT": "production",
    "OTEL_SERVICE_NAME": "gatewayz-api"
  },
  "timestamp": "2025-12-16T18:30:00.000000"
}
```

## Usage Examples

### Check Instrumentation Health

```bash
curl http://localhost:8000/api/instrumentation/health
```

### Get Current Trace Context

```bash
curl http://localhost:8000/api/instrumentation/trace-context
```

### Test Loki Integration (Admin)

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  http://localhost:8000/api/instrumentation/test-log
```

### Test Tempo Integration (Admin)

```bash
curl -X POST \
  -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  http://localhost:8000/api/instrumentation/test-trace
```

### Get Full Configuration (Admin)

```bash
curl -H "Authorization: Bearer YOUR_ADMIN_KEY" \
  http://localhost:8000/api/instrumentation/config
```

## Environment Variables

### Required for Loki

```bash
LOKI_ENABLED=true
LOKI_PUSH_URL=http://loki:3100/loki/api/v1/push
LOKI_QUERY_URL=http://loki:3100/loki/api/v1/query_range
```

### Required for Tempo

```bash
TEMPO_ENABLED=true
TEMPO_OTLP_HTTP_ENDPOINT=http://tempo:4318
TEMPO_OTLP_GRPC_ENDPOINT=http://tempo:4317
```

### Service Identification

```bash
OTEL_SERVICE_NAME=gatewayz-api
SERVICE_NAME=gatewayz-api
SERVICE_VERSION=1.0.0
ENVIRONMENT=production
APP_ENV=production
```

## Trace ID Correlation

All logs and traces are automatically correlated by trace ID. When you:

1. Make a request to the API
2. The request gets a unique trace ID
3. All logs generated during that request include the trace ID
4. All spans generated during that request include the trace ID

This allows you to:
- Click from a log entry to see all related traces
- Click from a trace to see all related logs
- Correlate errors across services

## Troubleshooting

### Loki Not Receiving Logs

1. Check Loki endpoint is reachable:
   ```bash
   curl http://loki:3100/ready
   ```

2. Verify LOKI_ENABLED is true:
   ```bash
   curl -H "Authorization: Bearer KEY" \
     http://localhost:8000/api/instrumentation/loki/status
   ```

3. Check application logs for errors:
   ```bash
   docker logs <api-container>
   ```

### Tempo Not Receiving Traces

1. Check Tempo endpoint is reachable:
   ```bash
   curl http://tempo:3200/ready
   ```

2. Verify TEMPO_ENABLED is true:
   ```bash
   curl -H "Authorization: Bearer KEY" \
     http://localhost:8000/api/instrumentation/tempo/status
   ```

3. Generate a test trace:
   ```bash
   curl -X POST \
     -H "Authorization: Bearer KEY" \
     http://localhost:8000/api/instrumentation/test-trace
   ```

### Logs and Traces Not Correlating

1. Verify trace ID is in logs:
   ```bash
   curl -H "Authorization: Bearer KEY" \
     http://localhost:8000/api/instrumentation/test-log
   ```

2. Check Grafana datasource correlation settings
3. Verify derived fields are configured in Loki datasource

## Related Documentation

- [Health Caching Optimization](./HEALTH_CACHING_OPTIMIZATION.md)
- [OpenTelemetry Configuration](../src/config/opentelemetry_config.py)
- [Logging Configuration](../src/config/logging_config.py)
- [Grafana Loki Documentation](https://grafana.com/docs/loki/latest/)
- [Grafana Tempo Documentation](https://grafana.com/docs/tempo/latest/)
