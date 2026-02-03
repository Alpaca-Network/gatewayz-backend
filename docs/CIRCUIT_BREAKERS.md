# Circuit Breaker Implementation Guide

## Overview

This document describes the circuit breaker pattern implementation for provider API calls in the Gatewayz backend. Circuit breakers prevent cascading failures by automatically stopping requests to failing providers and allowing them time to recover.

**Related Issues**: #1043, #1039

**Status**: ✅ Implemented (February 2026)

---

## What Problem Does This Solve?

### The Problem
When a provider API (like OpenRouter, Groq, etc.) experiences issues, continuing to send requests causes:
- **Cascading Failures**: Backend threads blocked waiting for timeouts
- **Resource Exhaustion**: Connection pool saturation
- **Poor User Experience**: 30-60s timeouts instead of fast failover
- **Wasted Resources**: CPU/network spent on doomed requests

### The Solution
Circuit breakers detect failing providers and automatically:
1. **Stop Sending Requests**: Reject requests immediately when provider is down
2. **Allow Recovery Time**: Wait before retrying (60s default)
3. **Test Recovery**: Gradually resume traffic to test if provider recovered
4. **Resume Normal Operation**: Automatically close circuit when provider healthy

---

## Architecture

### Circuit Breaker States

```
┌─────────────────────────────────────────────────────────────┐
│                    Circuit Breaker States                   │
└─────────────────────────────────────────────────────────────┘

       ┌──────────────┐
       │   CLOSED     │ ◄──────────────────────┐
       │ (Normal ops) │                        │
       └──────┬───────┘                        │
              │                                │
              │ 5 consecutive                  │ 2 successes
              │ failures OR                    │ in HALF_OPEN
              │ >50% failure                   │
              │ rate                           │
              ▼                                │
       ┌──────────────┐                        │
       │     OPEN     │                        │
       │ (Reject all) │                        │
       └──────┬───────┘                        │
              │                                │
              │ Wait 60s                       │
              │ timeout                        │
              ▼                                │
       ┌──────────────┐                        │
       │  HALF_OPEN   │ ───────────────────────┘
       │ (Testing)    │
       └──────┬───────┘
              │
              │ Any failure
              │ reopens
              ▼
       ┌──────────────┐
       │     OPEN     │
       │ (Back to     │
       │  rejecting)  │
       └──────────────┘
```

### State Descriptions

**CLOSED** (Normal Operation)
- All requests pass through to provider
- Tracks failure rate and consecutive failures
- Opens if thresholds exceeded

**OPEN** (Provider Failing)
- All requests rejected immediately with `CircuitBreakerError`
- No requests sent to provider
- Waits for timeout period (60s) before testing recovery

**HALF_OPEN** (Testing Recovery)
- Limited requests pass through to test if provider recovered
- Requires 2 consecutive successes to close circuit
- Any failure immediately reopens circuit

---

## Configuration

### Default Configuration

```python
# src/services/openrouter_client.py
OPENROUTER_CIRCUIT_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,           # Open after 5 consecutive failures
    success_threshold=2,            # Close after 2 consecutive successes
    timeout_seconds=60,             # Wait 60s before retrying
    failure_window_seconds=60,      # Measure failure rate over 60s
    failure_rate_threshold=0.5,     # Open if >50% failure rate
    min_requests_for_rate=10,       # Need 10+ requests to calc rate
)
```

### Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | 5 | Consecutive failures to open circuit |
| `success_threshold` | 2 | Consecutive successes to close circuit |
| `timeout_seconds` | 60 | Seconds to wait before testing recovery |
| `failure_window_seconds` | 60 | Time window for failure rate calculation |
| `failure_rate_threshold` | 0.5 | Failure rate (0.0-1.0) to open circuit |
| `min_requests_for_rate` | 10 | Minimum requests before calculating rate |

### Per-Provider Configuration

You can customize circuit breaker behavior per provider:

```python
from src.services.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker

# Example: More aggressive for critical provider
critical_config = CircuitBreakerConfig(
    failure_threshold=3,      # Open faster
    timeout_seconds=30,       # Retry sooner
)

breaker = get_circuit_breaker("groq", critical_config)
```

---

## Usage

### Basic Usage (Synchronous)

```python
from src.services.circuit_breaker import get_circuit_breaker, CircuitBreakerError

def make_provider_request(model: str, messages: list):
    circuit_breaker = get_circuit_breaker("openrouter")

    try:
        result = circuit_breaker.call(
            provider_api_call,  # Your API call function
            model,
            messages
        )
        return result
    except CircuitBreakerError as e:
        # Circuit is open, provider unavailable
        logger.warning(f"Circuit open for openrouter: {e.message}")
        # Trigger failover to backup provider
        return try_backup_provider(model, messages)
    except Exception as e:
        # Other errors (circuit recorded the failure)
        logger.error(f"Provider request failed: {e}")
        raise
```

### Async Usage

```python
async def make_async_provider_request(model: str, messages: list):
    circuit_breaker = get_circuit_breaker("openrouter")

    try:
        result = await circuit_breaker.call_async(
            async_provider_api_call,
            model,
            messages
        )
        return result
    except CircuitBreakerError:
        # Handle open circuit
        return await try_async_backup_provider(model, messages)
```

### OpenRouter Integration Example

The OpenRouter client has circuit breakers integrated:

```python
# src/services/openrouter_client.py

def make_openrouter_request_openai(messages, model, **kwargs):
    """Make request with circuit breaker protection"""
    circuit_breaker = get_circuit_breaker("openrouter", OPENROUTER_CIRCUIT_CONFIG)

    try:
        response = circuit_breaker.call(
            _make_openrouter_request_openai_internal,
            messages,
            model,
            **kwargs
        )
        return response
    except CircuitBreakerError as e:
        logger.warning(f"OpenRouter circuit breaker OPEN: {e.message}")
        # Error captured for monitoring
        capture_provider_error(e, provider='openrouter', model=model)
        raise  # Triggers automatic failover
```

---

## Monitoring & Management

### Prometheus Metrics

Circuit breaker state is exposed via Prometheus metrics:

```promql
# State transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
circuit_breaker_state_transitions_total{provider="openrouter", from_state="closed", to_state="open"}

# Current state (1 = active state, 0 = inactive)
circuit_breaker_current_state{provider="openrouter", state="open"}

# Failures recorded by circuit breaker
circuit_breaker_failures_total{provider="openrouter", state="closed"}

# Successes recorded by circuit breaker
circuit_breaker_successes_total{provider="openrouter", state="half_open"}

# Requests rejected due to open circuit
circuit_breaker_rejected_requests_total{provider="openrouter"}
```

### Management API Endpoints

#### Get All Circuit Breaker States

```bash
GET /circuit-breakers
```

**Response:**
```json
{
  "circuit_breakers": {
    "openrouter": {
      "provider": "openrouter",
      "state": "closed",
      "failure_count": 0,
      "success_count": 15,
      "failure_rate": 0.0,
      "recent_requests": 15,
      "opened_at": null,
      "seconds_until_retry": 0
    },
    "groq": {
      "provider": "groq",
      "state": "open",
      "failure_count": 5,
      "success_count": 0,
      "failure_rate": 1.0,
      "recent_requests": 5,
      "opened_at": "2026-02-03T10:30:00Z",
      "seconds_until_retry": 45
    }
  },
  "total_count": 2,
  "open_count": 1,
  "half_open_count": 0,
  "closed_count": 1
}
```

#### Get Specific Provider State

```bash
GET /circuit-breakers/openrouter
```

#### Manually Reset Circuit Breaker

```bash
POST /circuit-breakers/openrouter/reset
```

Use when you know a provider has recovered and want to immediately resume traffic.

#### Reset All Circuit Breakers

```bash
POST /circuit-breakers/reset-all
```

**⚠️ Use with caution** - only reset when confident all providers have recovered.

---

## Grafana Dashboard

### Panel: Circuit Breaker States

```promql
# Visualize current circuit states
circuit_breaker_current_state{state="open"} * 1  # Open circuits
```

### Panel: Open Circuit Count

```promql
# Count providers with open circuits
sum(circuit_breaker_current_state{state="open"})
```

### Panel: Circuit Breaker Rejected Requests Rate

```promql
# Requests per second being rejected
rate(circuit_breaker_rejected_requests_total[5m])
```

### Panel: State Transition Rate

```promql
# How often circuits are opening/closing
rate(circuit_breaker_state_transitions_total[5m])
```

---

## Alerts

### Critical: Provider Circuit Open

```yaml
- alert: ProviderCircuitOpen
  expr: circuit_breaker_current_state{state="open"} == 1
  for: 2m
  annotations:
    summary: "Circuit breaker open for {{ $labels.provider }}"
    description: "Provider {{ $labels.provider }} circuit has been open for 2+ minutes"
```

### Warning: High Circuit Transitions

```yaml
- alert: HighCircuitTransitions
  expr: rate(circuit_breaker_state_transitions_total[5m]) > 0.1
  for: 5m
  annotations:
    summary: "Frequent circuit breaker state changes"
    description: "Circuit breakers transitioning >6 times per minute"
```

### Warning: High Rejection Rate

```yaml
- alert: HighCircuitRejectionRate
  expr: rate(circuit_breaker_rejected_requests_total[5m]) > 10
  for: 5m
  annotations:
    summary: "High rate of circuit breaker rejections"
    description: "More than 10 requests/sec being rejected due to open circuits"
```

---

## Troubleshooting

### Issue: Circuit Constantly Opening and Closing

**Symptoms:**
- Circuit transitions between states frequently (every 1-2 minutes)
- High state transition rate in metrics

**Diagnosis:**
```bash
# Check transition rate
curl http://localhost:8000/metrics | grep circuit_breaker_state_transitions

# Check provider state
curl http://localhost:8000/circuit-breakers/openrouter
```

**Solutions:**
1. **Increase `timeout_seconds`**: Give provider more time to stabilize
2. **Increase `failure_threshold`**: Be more tolerant of transient errors
3. **Adjust `failure_rate_threshold`**: Require higher failure rate to open
4. **Investigate Provider**: Check if provider genuinely unstable

### Issue: Circuit Opens Too Slowly

**Symptoms:**
- Provider degraded but circuit still closed
- Users experiencing slow timeouts
- High connection pool usage

**Solutions:**
1. **Decrease `failure_threshold`**: Open faster (e.g., 3 instead of 5)
2. **Decrease `failure_rate_threshold`**: Open at lower failure rate
3. **Decrease `min_requests_for_rate`**: Calculate rate with fewer samples

### Issue: Circuit Not Opening Despite Provider Down

**Symptoms:**
- Provider returning errors
- Circuit remains CLOSED
- Metrics show failures but no state transitions

**Diagnosis:**
```python
# Check if circuit breaker is properly initialized
circuit_breaker = get_circuit_breaker("provider-name")
state = circuit_breaker.get_state()
print(f"State: {state['state']}, Failures: {state['failure_count']}")
```

**Common Causes:**
1. **Circuit Breaker Not Wrapped**: Ensure provider calls use `circuit_breaker.call()`
2. **Errors Not Propagating**: Check if errors are being caught before reaching circuit breaker
3. **Configuration Too Lenient**: Thresholds too high for failure rate

### Issue: Circuit Stays Open Too Long

**Symptoms:**
- Provider recovered but circuit still open
- Manual reset required frequently

**Solutions:**
1. **Decrease `timeout_seconds`**: Test recovery sooner (e.g., 30s)
2. **Decrease `success_threshold`**: Close faster after testing
3. **Check Provider Health**: Ensure provider truly stable before resetting

---

## Best Practices

### 1. Always Handle CircuitBreakerError

```python
try:
    result = circuit_breaker.call(api_function)
except CircuitBreakerError:
    # MUST handle this - indicates provider unavailable
    return failover_to_backup_provider()
```

### 2. Don't Catch Exceptions Inside Circuit Breaker

```python
# ❌ BAD - Circuit breaker can't detect failures
def bad_api_call():
    try:
        return provider.request()
    except Exception:
        return None  # Circuit breaker thinks this succeeded!

# ✅ GOOD - Let exceptions propagate
def good_api_call():
    return provider.request()  # Exceptions reach circuit breaker
```

### 3. Use Appropriate Timeouts

Circuit breaker timeout should be longer than provider API timeout:
```python
# Provider timeout: 30s
# Circuit breaker timeout: 60s
# This prevents reopening before provider has time to recover
```

### 4. Monitor State Transitions

High transition rates indicate instability:
```python
# Alert if transitioning more than 6 times per minute
rate(circuit_breaker_state_transitions_total[5m]) > 0.1
```

### 5. Test Failover Paths

Ensure your application handles CircuitBreakerError gracefully:
```python
# Integration test
def test_circuit_open_triggers_failover():
    # Force circuit open
    circuit_breaker._transition_to(CircuitState.OPEN)

    # Verify failover works
    result = make_request()
    assert result["provider"] == "backup_provider"
```

---

## Integration with Existing Features

### Provider Failover

Circuit breakers work seamlessly with existing failover logic:
1. Circuit opens when provider fails
2. `CircuitBreakerError` raised
3. Failover service catches error and tries backup provider
4. Circuit tests recovery after timeout

### Rate Limiting

Circuit breakers complement rate limiting:
- **Rate Limiting**: Prevents overload from valid requests
- **Circuit Breakers**: Prevents cascading failures from invalid/failing requests

### Health Monitoring

Circuit breaker state is included in health checks:
```python
# GET /health endpoint includes circuit states
{
  "database": "healthy",
  "redis": "healthy",
  "circuit_breakers": {
    "openrouter": "closed",
    "groq": "open"
  }
}
```

---

## Testing

### Unit Tests

```python
def test_circuit_opens_after_threshold():
    circuit = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))

    # Trigger failures
    for _ in range(3):
        with pytest.raises(Exception):
            circuit.call(failing_function)

    # Circuit should now be open
    with pytest.raises(CircuitBreakerError):
        circuit.call(working_function)
```

### Integration Tests

```python
async def test_circuit_breaker_with_real_provider():
    # Make requests that should fail
    for _ in range(5):
        try:
            await make_openrouter_request("invalid-model", [])
        except Exception:
            pass

    # Circuit should be open
    breaker = get_circuit_breaker("openrouter")
    assert breaker.get_state()["state"] == "open"
```

### Load Tests

```bash
# Generate load to test circuit behavior
ab -n 1000 -c 50 http://localhost:8000/v1/chat/completions \
   -H "Authorization: Bearer test-key" \
   -H "Content-Type: application/json" \
   -T application/json \
   -p request_body.json
```

---

## Performance Impact

### Overhead

Circuit breaker adds minimal overhead:
- **Per Request**: ~0.1-0.5ms (state check + metrics update)
- **Memory**: ~1KB per provider circuit breaker
- **Redis**: Optional state persistence (graceful degradation if unavailable)

### Benefits

- **Prevent Timeouts**: 30-60s timeout → 1ms fast rejection
- **Reduce Load**: Stop sending requests to failing providers
- **Faster Failover**: Immediate instead of after timeout
- **Resource Protection**: Prevent thread/connection pool exhaustion

---

## Distributed Deployment

Circuit breaker state is stored in Redis for distributed deployments:

```python
# State automatically synced across instances via Redis
# If Redis unavailable, falls back to local in-memory state

# Instance A opens circuit
breaker_a.record_failure()  # Opens circuit, writes to Redis

# Instance B sees open circuit immediately
breaker_b.check_should_attempt()  # Reads state from Redis, rejects request
```

---

## Roadmap

### Phase 2 Enhancements

- [ ] Per-model circuit breakers (not just per-provider)
- [ ] Adaptive thresholds based on historical data
- [ ] Circuit breaker for database queries
- [ ] Circuit breaker for Redis operations
- [ ] Custom failure classification (don't count 4xx as failures)
- [ ] Gradual traffic ramp-up in HALF_OPEN state

---

## References

### Code Files

- **Circuit Breaker Core**: `src/services/circuit_breaker.py` (580 lines)
- **Prometheus Metrics**: `src/services/prometheus_metrics.py` (+40 lines)
- **Status API**: `src/routes/circuit_breaker_status.py` (200 lines)
- **OpenRouter Integration**: `src/services/openrouter_client.py` (modified)

### Related Documentation

- [Deployment Guide](./DEPLOYMENT_GUIDE_PHASE1.md)
- [Architecture Overview](./architecture.md)
- [Provider Failover](./PROVIDER_FAILOVER.md)

### External Resources

- [Martin Fowler - CircuitBreaker Pattern](https://martinfowler.com/bliki/CircuitBreaker.html)
- [AWS - Circuit Breaker Pattern](https://aws.amazon.com/builders-library/avoiding-overload-in-distributed-systems-by-putting-the-smaller-service-in-control/)
- [Netflix Hystrix Documentation](https://github.com/Netflix/Hystrix/wiki)

---

**Last Updated**: February 3, 2026
**Version**: 1.0
**Related Issues**: #1043, #1039
