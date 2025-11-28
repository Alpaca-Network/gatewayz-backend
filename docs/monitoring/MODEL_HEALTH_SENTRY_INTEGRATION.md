# Model Health Sentry Integration

## Overview

This document describes the integration of Sentry error tracking with the model health monitoring system. When models fail health checks or become unavailable, errors are automatically captured and sent to Sentry for monitoring and alerting.

## Architecture

### Components

1. **Sentry Context Utilities** (`src/utils/sentry_context.py`)
   - New function: `capture_model_health_error()`
   - Specialized error capture for model health failures

2. **Model Health Monitor** (`src/services/model_health_monitor.py`)
   - Performs health checks on all models across providers
   - Captures errors to Sentry when health checks fail

3. **Model Availability Service** (`src/services/model_availability.py`)
   - Tracks model availability and circuit breaker states
   - Captures errors to Sentry when models become unavailable

## Error Capture Flow

```
Model Health Check
    ↓
Health Check Fails
    ↓
capture_model_health_error()
    ↓
Sentry Context Set:
  - model_id
  - provider
  - gateway
  - operation
  - status
  - response_time_ms
  - error details
    ↓
sentry_sdk.capture_exception()
    ↓
Sentry Dashboard
```

## Usage

### Automatic Error Capture

Errors are automatically captured in the following scenarios:

1. **Health Check Failures** - When `_check_model_health()` detects a failed health check
2. **Model Exceptions** - When exceptions occur during health checks
3. **Availability Changes** - When models transition from available to unavailable state
4. **Circuit Breaker Opens** - When circuit breaker opens due to repeated failures

### Error Context

Each captured error includes the following context:

```python
{
    'model_id': 'gpt-4',              # Model identifier
    'provider': 'openai',              # Provider name
    'gateway': 'openrouter',           # Gateway used
    'operation': 'health_check',       # Operation type
    'status': 'unhealthy',             # Health status
    'response_time_ms': 5000.0,        # Response time if available
    'error_count': 5,                  # Number of errors
    'success_rate': 0.75,              # Success rate
    'circuit_breaker_state': 'open',   # Circuit breaker state
    'error_message': 'Connection timeout', # Error details
    'last_failure': '2025-11-28T...'   # ISO timestamp
}
```

### Sentry Tags

Errors are tagged for easy filtering:

- `provider`: Provider name (e.g., 'openai', 'anthropic')
- `gateway`: Gateway name (e.g., 'openrouter', 'portkey')
- `model_id`: Model identifier (e.g., 'gpt-4', 'claude-3-opus')
- `operation`: Operation type (e.g., 'health_check', 'availability_check')

## Example Error Scenarios

### Scenario 1: Health Check Timeout

```python
# Model health check times out
model_id: 'gpt-3.5-turbo'
provider: 'openai'
gateway: 'openrouter'
error: 'Connection timeout'
status_code: 408

# Captured to Sentry with context:
{
    'model_id': 'gpt-3.5-turbo',
    'provider': 'openai',
    'gateway': 'openrouter',
    'operation': 'health_check',
    'status': 'unhealthy',
    'status_code': 408,
    'error_message': 'Connection timeout'
}
```

### Scenario 2: Model Becomes Unavailable

```python
# Model transitions from available to unavailable
model_id: 'claude-3-opus'
provider: 'anthropic'
gateway: 'openrouter'
error_count: 5
success_rate: 0.60

# Captured to Sentry with context:
{
    'model_id': 'claude-3-opus',
    'provider': 'anthropic',
    'gateway': 'openrouter',
    'operation': 'availability_check',
    'status': 'unavailable',
    'error_count': 5,
    'success_rate': 0.60,
    'circuit_breaker_state': 'open'
}
```

### Scenario 3: Network Exception

```python
# Exception occurs during health check
model_id: 'llama-3-70b'
provider: 'meta'
gateway: 'together'
exception: 'Network error: Unable to connect'

# Captured to Sentry with full exception:
Exception: Network error: Unable to connect
Context: {
    'model_id': 'llama-3-70b',
    'provider': 'meta',
    'gateway': 'together',
    'operation': 'health_check',
    'status': 'unhealthy'
}
```

## Sentry Dashboard Usage

### Filtering Errors

**All model health errors:**
```
context.model_health
```

**Errors for specific provider:**
```
tags[provider]:openai
```

**Errors for specific gateway:**
```
tags[gateway]:openrouter
```

**Errors for specific model:**
```
tags[model_id]:gpt-4
```

**Circuit breaker opened:**
```
context.model_health.circuit_breaker_state:open
```

### Common Queries

**High-priority models failing:**
```
tags[model_id]:(gpt-4 OR claude-3-opus OR gpt-4-turbo)
```

**Models with low success rates:**
```
context.model_health.success_rate:<0.8
```

**Recent health check failures:**
```
context.model_health.operation:health_check AND timestamp:>-1h
```

## Error Deduplication

To prevent alert fatigue, errors are only captured in specific scenarios:

1. **First Failure**: When a previously healthy model first fails
2. **Circuit Breaker Opens**: When the circuit breaker transitions to OPEN state
3. **Health Check Fails**: When individual health checks fail

Errors are **NOT** captured for:
- Healthy models (successful health checks)
- Models already known to be unhealthy (unless circuit breaker state changes)
- Repeated failures of the same model (after initial capture)

## Configuration

### Environment Variables

Sentry configuration is managed via environment variables:

```bash
SENTRY_ENABLED=true
SENTRY_DSN=https://your-sentry-dsn@sentry.io/project-id
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.2
```

### Sampling

Model health errors use the default sampling configuration:
- Development: 100% (all errors captured)
- Production: Adaptive sampling based on endpoint

## Testing

### Unit Tests

Tests are located in `tests/services/test_model_health_monitor.py`:

- `test_sentry_capture_on_model_failure`: Verifies error capture on health check failure
- `test_sentry_capture_on_exception`: Verifies error capture on exceptions
- `test_sentry_not_captured_on_success`: Verifies no capture for healthy models

### Running Tests

```bash
# Run all model health monitor tests
pytest tests/services/test_model_health_monitor.py -v

# Run only Sentry-related tests
pytest tests/services/test_model_health_monitor.py::TestSentryErrorCapture -v
```

### Manual Testing

To manually test error capture:

1. Set `SENTRY_ENABLED=true` and configure `SENTRY_DSN`
2. Start the application
3. Trigger a model health check for a non-existent or failing model
4. Check Sentry dashboard for the captured error

## Monitoring & Alerts

### Recommended Alerts

1. **Critical Models Down**
   - Condition: High-priority models (gpt-4, claude-3-opus) become unavailable
   - Action: Immediate notification via PagerDuty

2. **Multiple Models Failing**
   - Condition: >10 unique models fail health checks in 5 minutes
   - Action: Slack notification to ops channel

3. **Provider Outage**
   - Condition: All models from a provider fail health checks
   - Action: Escalate to on-call engineer

4. **Circuit Breakers Opening**
   - Condition: >5 circuit breakers open in 10 minutes
   - Action: Warning notification

### Metrics to Track

- **Model failure rate**: Percentage of models failing health checks
- **Provider reliability**: Success rate by provider
- **Gateway performance**: Response times by gateway
- **Circuit breaker activity**: Number of opens/closes over time

## Troubleshooting

### Error Not Appearing in Sentry

1. **Verify Sentry is enabled**:
   ```bash
   echo $SENTRY_ENABLED  # Should be 'true'
   echo $SENTRY_DSN      # Should be set
   ```

2. **Check logs**: Look for "Sentry initialized" message on startup

3. **Verify model is failing**: Check health monitor logs for failed health checks

### Too Many Errors

If receiving too many model health errors:

1. **Adjust sampling rate**: Lower `SENTRY_TRACES_SAMPLE_RATE`
2. **Filter specific models**: Use Sentry's ignore rules for known flaky models
3. **Increase health check interval**: Modify `check_interval` in ModelHealthMonitor

### Missing Context

If errors lack context:

1. **Verify imports**: Ensure `capture_model_health_error` is imported
2. **Check parameters**: Verify all required parameters are passed
3. **Review logs**: Check for warnings about context setting

## Integration with Existing Systems

### Health Check Endpoints

The `/health` and `/api/monitoring/health` endpoints expose model health status:

```json
{
  "status": "healthy",
  "models": {
    "total": 100,
    "healthy": 95,
    "degraded": 3,
    "unhealthy": 2
  }
}
```

### Circuit Breaker Integration

Model availability service uses circuit breakers that integrate with Sentry:
- **CLOSED**: Normal operation, no errors
- **OPEN**: Failures detected, error captured to Sentry
- **HALF_OPEN**: Recovery testing, errors captured if test fails

### Analytics Integration

Model health errors can be correlated with:
- **PostHog events**: User-facing errors
- **Prometheus metrics**: System performance
- **Statsig flags**: Feature rollout status

## Best Practices

1. **Monitor Critical Models First**: Focus alerts on business-critical models
2. **Set Appropriate Thresholds**: Avoid alert fatigue with reasonable error thresholds
3. **Regular Review**: Weekly review of model health trends in Sentry
4. **Document Incidents**: Use Sentry issues to document and track model outages
5. **Correlate with Provider Status**: Cross-reference with provider status pages

## Future Enhancements

Potential improvements to the system:

1. **Severity Levels**: Classify errors by severity (warning, error, critical)
2. **Auto-Recovery Tracking**: Track when models auto-recover
3. **Historical Trends**: Store long-term model health history
4. **Predictive Alerts**: ML-based prediction of upcoming failures
5. **Provider SLA Tracking**: Track provider uptime against SLAs

## References

- [Sentry Error Capture Expansion Guide](./SENTRY_ERROR_CAPTURE_EXPANSION.md)
- [Model Health Monitor Service](../../src/services/model_health_monitor.py)
- [Model Availability Service](../../src/services/model_availability.py)
- [Sentry Context Utilities](../../src/utils/sentry_context.py)

## Summary

The model health Sentry integration provides:

✓ **Automatic error capture** for model failures
✓ **Rich context** for debugging and root cause analysis
✓ **Intelligent deduplication** to prevent alert fatigue
✓ **Provider and gateway tracking** for accountability
✓ **Integration with circuit breakers** for reliability
✓ **Comprehensive testing** for production readiness

This enables proactive monitoring of model availability and rapid response to provider outages or model failures.
