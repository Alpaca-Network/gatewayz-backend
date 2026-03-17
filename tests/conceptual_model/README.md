# Conceptual Model Test Suite

## What is this?

This directory contains **186 unit tests** that verify whether the codebase matches the claims made in the [Conceptual Model](https://github.com/Alpaca-Network/gatewayz-backend/wiki/Conceptual-Model) -- the specification for what Gatewayz should do.

Every testable claim in the Conceptual Model (e.g., "keys encrypted at rest using AES-128 Fernet" or "subscription allowance consumed before purchased credits") has a corresponding test here. If the test passes, the code matches the spec. If it fails, there's a gap.

## How it connects

```
Wiki: Conceptual Model             "What the system SHOULD do"
  |                                  (56 features, 10 layers)
  v
Wiki: CM Unit Testing Plan         "186 tests that SHOULD exist"
  |                                  (one per testable claim)
  v
tests/conceptual_model/            "The actual test code"    <-- YOU ARE HERE
  |                                  (18 files, 186 tests)
  v
Wiki: CM Unit Test Coverage Report "Which ones pass/fail?"
  |                                  (47.8% covered, 66 gaps)
  v
Wiki: Delta Report                 "What to fix for stable release"
                                     (P0/P1/P2 priorities)
```

## File layout

Each file maps to a section of the Conceptual Model:

| File | Section | Tests | What it covers |
|------|---------|-------|---------------|
| `test_cm01_auth_api_key_security.py` | 1 | 18 | Fernet encryption, HMAC hashing, RBAC, IP allowlists |
| `test_cm02_rate_limiting.py` | 2 | 17 | 3-layer rate limiting, Redis fallback, velocity detection |
| `test_cm03_model_resolution.py` | 3 | 10 | Alias resolution, canonical IDs, provider detection |
| `test_cm04_intelligent_routing.py` | 4 | 15 | Code Router, General Router, quality priors |
| `test_cm05_provider_failover.py` | 5 | 24 | Failover chains, circuit breakers, model-aware rules |
| `test_cm06_credit_system.py` | 6 | 18 | Cost calculation, deduction order, pre-flight, refunds |
| `test_cm07_plans_trials.py` | 7 | 10 | Trial creation, limits, conversion, plan tiers |
| `test_cm08_caching.py` | 8 | 16 | Redis cache, in-memory fallback, TTL, invalidation |
| `test_cm09_model_catalog.py` | 9 | 8 | Sync, dedup, search, metadata, pricing validation |
| `test_cm10_api_compatibility.py` | 10 | 14 | OpenAI format, Anthropic format, streaming, tools |
| `test_cm11_health_monitoring.py` | 11 | 9 | Tiered checks, passive capture, incidents |
| `test_cm12_auth_flow.py` | 12 | 10 | Login, signup, trial provisioning, OAuth |
| `test_cm13_observability.py` | 13 | 8 | Prometheus, OpenTelemetry, Sentry, request IDs |
| `test_cm14_token_estimation.py` | 14 | 3 | tiktoken accuracy, fallback heuristic |
| `test_cm15_image_audio.py` | 15 | 3 | Image generation, audio transcription |
| `test_cm16_webhooks_events.py` | 16 | 5 | Webhook delivery, HMAC signing, retry |
| `test_cm17_deployment.py` | 17 | 3 | Health endpoint, graceful shutdown |
| `test_cm18_provider_ecosystem.py` | 18 | 3 | Multi-provider, 30+ integrations |

## Test markers

Every test class or function is tagged with one of two markers:

- **`@pytest.mark.cm_verified`** -- The code matches the Conceptual Model claim. This test **should pass**.
- **`@pytest.mark.cm_gap`** -- The code does NOT match the claim yet. This test documents the gap and is **expected to fail** until the feature is implemented.

Currently: **164 verified**, **3 gaps** (trial duration, webhook HMAC signing).

## How to run

```bash
# All conceptual model tests
pytest tests/conceptual_model/ -v

# Just one section
pytest tests/conceptual_model/test_cm06_credit_system.py -v

# Only verified tests (should all pass)
pytest tests/conceptual_model/ -m cm_verified -v

# Only gap tests (expected to fail -- these are the deltas)
pytest tests/conceptual_model/ -m cm_gap -v

# With parallel execution
pytest tests/conceptual_model/ -n auto -v
```

## How to read a test

Each test follows this pattern:

```python
@pytest.mark.cm_verified
class TestCostCalculation:
    """CM-6.1: Cost = (prompt_tokens * prompt_price) + (completion_tokens * completion_price)."""

    def test_cost_formula_prompt_plus_completion(self):
        """CM-6.1.1: Cost follows the formula prompt*price + completion*price."""
        # ... test code ...
```

- The **class docstring** references the Conceptual Model section (CM-6.1)
- The **method docstring** references the specific claim (CM-6.1.1)
- These IDs match the [CM Unit Testing Plan](https://github.com/Alpaca-Network/gatewayz-backend/wiki/Conceptual-Model-Unit-Testing-Plan) in the wiki

## How to add a new test

1. Find the claim in the [Conceptual Model](https://github.com/Alpaca-Network/gatewayz-backend/wiki/Conceptual-Model)
2. Check the [CM Unit Testing Plan](https://github.com/Alpaca-Network/gatewayz-backend/wiki/Conceptual-Model-Unit-Testing-Plan) for the test ID (e.g., CM-6.3.1)
3. Add the test to the matching `test_cmXX_*.py` file
4. Mark it `@pytest.mark.cm_verified` if the code already works, or `@pytest.mark.cm_gap` if not
5. Use fixtures from `conftest.py` (mock_supabase, mock_redis, frozen_time, etc.)

## CI

The `conceptual-model-tests.yml` workflow runs these tests on every PR and posts a sticky comment with per-section pass/fail counts. See any PR for an example.

## Fixtures (conftest.py)

| Fixture | What it does |
|---------|-------------|
| `mock_supabase` | Mock Supabase client with fluent chain API |
| `mock_redis` | Mock Redis client with common operations |
| `mock_redis_unavailable` | Simulates Redis returning None |
| `mock_redis_error` | Simulates Redis raising ConnectionError |
| `mock_provider_response` | Factory for mock HTTP responses |
| `frozen_time` | Controllable time.time() and datetime.now() |
| `fernet_key` | Valid Fernet encryption key |
| `sample_messages` | Standard OpenAI-format messages |
| `sample_model_catalog_entry` | Model catalog entry with all fields |

All external I/O is mocked. These tests never hit real databases, Redis, or APIs.
