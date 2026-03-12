# Gatewayz — Conceptual Model Unit Testing Plan

> **Purpose**: A theoretical, feature-by-feature unit test specification derived directly from the Conceptual Model.
> Every testable claim, invariant, threshold, and business rule from the conceptual model is mapped to one or more unit tests.
> These tests should run in CI without external dependencies (mock all I/O).
>
> **Generated**: 2026-03-09 | **Source**: `docs/CONCEPTUAL_MODEL.md`

---

## How to Read This Document

Each section maps to a conceptual model system area. Every test has:
- **ID**: `CM-{area}.{number}` (Conceptual Model reference)
- **What it tests**: The specific claim/invariant from the conceptual model
- **Unit under test**: The function/class/module to test
- **Assertions**: What the test must verify
- **Mocks needed**: External dependencies to stub

---

## 1. Authentication & API Key Security

> *Conceptual Model Claim*: "Keys encrypted at rest using AES-128 Fernet. HMAC-SHA256 key hashing enables fast lookup without decryption."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-1.1 | `test_api_key_encrypted_with_fernet` | `security.security.encrypt_api_key()` | Output is valid Fernet token, not plaintext. Decrypting with same key yields original. | None (pure crypto) |
| CM-1.2 | `test_api_key_decryption_roundtrip` | `security.security.decrypt_api_key()` | `decrypt(encrypt(key)) == key` for arbitrary keys. | None |
| CM-1.3 | `test_api_key_hmac_sha256_hashing` | `security.security.hash_api_key()` | Output is hex-encoded SHA-256 HMAC. Same input always produces same hash. Different inputs produce different hashes. | None |
| CM-1.4 | `test_hmac_lookup_without_decryption` | `db.api_keys.get_api_key_by_hash()` | Key lookup uses HMAC hash column, never decrypts stored keys during lookup. | Supabase client |
| CM-1.5 | `test_encrypted_key_not_plaintext_in_db` | `db.api_keys.create_api_key()` | The value written to DB `encrypted_key` column is not equal to the plaintext key. | Supabase client |
| CM-1.6 | `test_rbac_four_tiers_exist` | `security.deps` / roles module | System recognizes exactly 4 roles: `admin`, `team`, `dev`, `free`. Each has distinct permission sets. | None |
| CM-1.7 | `test_admin_role_has_all_permissions` | Role permissions lookup | Admin role includes all permissions that other roles have, plus admin-only ones. | None |
| CM-1.8 | `test_free_role_has_minimum_permissions` | Role permissions lookup | Free role has the most restricted permission set. | None |
| CM-1.9 | `test_ip_allowlist_blocks_non_listed_ip` | `security.deps.validate_api_key_security()` | When key has IP allowlist configured, requests from unlisted IPs are rejected. | Supabase client |
| CM-1.10 | `test_ip_allowlist_allows_listed_ip` | `security.deps.validate_api_key_security()` | When key has IP allowlist, requests from listed IPs are allowed. | Supabase client |
| CM-1.11 | `test_domain_restriction_blocks_wrong_domain` | `security.deps.validate_api_key_security()` | When key has domain restrictions, requests from wrong domain are rejected. | Supabase client |

---

## 2. Rate Limiting — Three-Layer Architecture

> *Conceptual Model Claim*: "Three-layer rate limiting: IP-level → API key-level → Anonymous. If Redis unavailable, in-memory fallback activates. Requests never blocked due to infrastructure failure."

### 2.1 Layer 1: IP-Level Rate Limiting

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-2.1.1 | `test_ip_rate_limit_under_threshold_allows` | `middleware.security_middleware` | Requests under IP RPM limit pass through. | Redis |
| CM-2.1.2 | `test_ip_rate_limit_over_threshold_blocks` | `middleware.security_middleware` | Requests exceeding IP RPM limit return 429. | Redis |
| CM-2.1.3 | `test_ip_rate_limit_applied_before_auth` | `middleware.security_middleware` | IP check executes before API key validation in middleware order. | Redis |
| CM-2.1.4 | `test_velocity_detection_triggers_on_anomalous_pattern` | `middleware.security_middleware` | Rapid burst of requests triggers velocity mode. | Redis |
| CM-2.1.5 | `test_authenticated_users_exempt_from_ip_limits` | `middleware.security_middleware` | Requests with valid API key bypass IP-level limits. | Redis, Supabase |

### 2.2 Layer 2: API Key-Level Rate Limiting

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-2.2.1 | `test_key_rate_limit_tracks_rpm` | `services.rate_limiting` | Redis INCR called with key `rate_limit:{api_key_id}:{minute}`, TTL 60s. | Redis |
| CM-2.2.2 | `test_key_rate_limit_enforces_plan_tier` | `services.rate_limiting` | Different plan tiers have different RPM limits. Dev < Team < Enterprise. | Redis, plan config |
| CM-2.2.3 | `test_key_rate_limit_tracks_tokens_per_day` | `services.rate_limiting` | Daily token usage tracked and enforced per key. | Redis |
| CM-2.2.4 | `test_key_rate_limit_tracks_tokens_per_month` | `services.rate_limiting` | Monthly token usage tracked and enforced per key. | Redis |
| CM-2.2.5 | `test_rate_limit_returns_proper_headers` | `services.rate_limiting` | Response includes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. | Redis |

### 2.3 Layer 3: Anonymous Rate Limiting

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-2.3.1 | `test_anonymous_limits_stricter_than_authenticated` | `services.anonymous_rate_limiter` | Anonymous RPM < Authenticated RPM for same endpoint. | Redis |
| CM-2.3.2 | `test_anonymous_rate_limit_uses_ip_hash` | `services.anonymous_rate_limiter` | Redis key is `anon_rate:{ip_hash}:{minute}` with TTL 60s. | Redis |
| CM-2.3.3 | `test_anonymous_rate_limit_blocks_excess` | `services.anonymous_rate_limiter` | Returns 429 when anonymous limit exceeded. | Redis |

### 2.4 Graceful Degradation (CRITICAL INVARIANT)

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-2.4.1 | `test_redis_down_activates_fallback` | `services.rate_limiting_fallback` | When Redis connection fails, in-memory fallback activates without error. | Redis (raise ConnectionError) |
| CM-2.4.2 | `test_fallback_lru_cache_500_entries` | `services.rate_limiting_fallback` | Fallback cache holds max 500 entries, evicts LRU when full. | None |
| CM-2.4.3 | `test_fallback_ttl_15_minutes` | `services.rate_limiting_fallback` | Fallback entries expire after 15 minutes. | None (time mock) |
| CM-2.4.4 | `test_requests_never_blocked_by_infra_failure` | `services.rate_limiting` | When Redis AND fallback both fail, request is ALLOWED (fail-open). | Redis (raise), fallback (raise) |

---

## 3. Model Resolution Pipeline

> *Conceptual Model Claim*: "120+ aliases map shorthand names to canonical model IDs. Provider detection follows priority: explicit overrides → format-based rules → mapping tables → org-prefix fallbacks."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-3.1 | `test_alias_r1_resolves_to_deepseek` | `services.model_transformations` | `"r1"` → `"deepseek/deepseek-r1"` | None |
| CM-3.2 | `test_alias_gpt4o_resolves_to_openai` | `services.model_transformations` | `"gpt-4o"` → `"openai/gpt-4o"` | None |
| CM-3.3 | `test_at_least_120_aliases_defined` | `services.model_transformations` | Alias mapping dict has ≥ 120 entries. | None |
| CM-3.4 | `test_canonical_id_passes_through_unchanged` | `services.model_transformations` | `"openai/gpt-4o"` remains `"openai/gpt-4o"` (no double-resolution). | None |
| CM-3.5 | `test_provider_detection_explicit_override_highest_priority` | `services.providers` | When explicit provider override given, it takes precedence over all other detection methods. | None |
| CM-3.6 | `test_provider_detection_format_based_rules` | `services.providers` | `"accounts/fireworks/models/..."` detected as Fireworks provider. | None |
| CM-3.7 | `test_provider_detection_org_prefix_fallback` | `services.providers` | `"meta-llama/..."` falls back to appropriate provider via org prefix. | None |
| CM-3.8 | `test_model_id_transformation_fireworks_format` | `services.model_transformations` | Canonical ID transformed to Fireworks native format (`accounts/fireworks/models/...`). | None |
| CM-3.9 | `test_model_id_transformation_per_provider` | `services.model_transformations` | Each provider gets its own correctly formatted model ID from the same canonical ID. | None |
| CM-3.10 | `test_unknown_alias_returns_error_or_passthrough` | `services.model_transformations` | Unknown alias either raises error or passes through as-is (defined behavior, not undefined). | None |

---

## 4. Intelligent Routing

> *Conceptual Model Claim*: "General router with 4 modes (quality/cost/latency/balanced). Code router with 4 modes (auto/price/quality/agentic). ML-powered and benchmark-driven."

### 4.1 General Router

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-4.1.1 | `test_general_router_parses_quality_mode` | Router model detection | `"router:general:quality"` parsed as general router, quality mode. | None |
| CM-4.1.2 | `test_general_router_parses_cost_mode` | Router model detection | `"router:general:cost"` parsed correctly. | None |
| CM-4.1.3 | `test_general_router_parses_latency_mode` | Router model detection | `"router:general:latency"` parsed correctly. | None |
| CM-4.1.4 | `test_general_router_parses_balanced_mode` | Router model detection | `"router:general:balanced"` parsed correctly. | None |
| CM-4.1.5 | `test_general_router_quality_selects_high_benchmark_model` | Router selection logic | Quality mode selects models with highest benchmark scores. | Provider health, model registry |
| CM-4.1.6 | `test_general_router_cost_selects_cheapest_model` | Router selection logic | Cost mode selects lowest price/token model that meets minimum quality. | Pricing data |
| CM-4.1.7 | `test_general_router_latency_selects_fastest_model` | Router selection logic | Latency mode selects model with lowest P50 latency. | Latency data |
| CM-4.1.8 | `test_general_router_invalid_mode_rejected` | Router model detection | `"router:general:invalid"` returns error. | None |

### 4.2 Code Router

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-4.2.1 | `test_code_router_parses_auto_mode` | Router model detection | `"router:code:auto"` parsed as code router, auto mode. | None |
| CM-4.2.2 | `test_code_router_parses_agentic_mode` | Router model detection | `"router:code:agentic"` parsed correctly. | None |
| CM-4.2.3 | `test_code_router_parses_price_mode` | Router model detection | `"router:code:price"` parsed correctly. | None |
| CM-4.2.4 | `test_code_router_parses_quality_mode` | Router model detection | `"router:code:quality"` parsed correctly. | None |
| CM-4.2.5 | `test_code_router_uses_swe_bench_scores` | Code router selection | Model selection factors in SWE-bench scores. | Benchmark data |
| CM-4.2.6 | `test_code_router_classifies_task_complexity` | Code router classifier | Given a simple vs complex prompt, classifier assigns different complexity tiers. | None |
| CM-4.2.7 | `test_code_router_matches_tier_to_model` | Code router selection | Higher complexity tier maps to higher-capability model. | Model registry |

---

## 5. Provider Failover

> *Conceptual Model Claim*: "14-provider failover chain. Triggers on 401/402/403/404/502/503/504. Does NOT trigger on 400/429. Circuit breaker after 5 consecutive failures. Auto-recovery after 5-minute cool-down."

### 5.1 Failover Chain

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-5.1.1 | `test_failover_chain_has_14_providers` | `services.provider_failover` | Default failover chain contains 14 providers. | None |
| CM-5.1.2 | `test_failover_chain_ordered_by_reliability` | `services.provider_failover` | Chain order reflects reliability ranking (most reliable first). | None |
| CM-5.1.3 | `test_failover_retries_on_502` | `services.provider_failover` | 502 from primary → request sent to next provider in chain. | Provider clients |
| CM-5.1.4 | `test_failover_retries_on_503` | `services.provider_failover` | 503 triggers failover to next provider. | Provider clients |
| CM-5.1.5 | `test_failover_retries_on_504` | `services.provider_failover` | 504 triggers failover to next provider. | Provider clients |
| CM-5.1.6 | `test_failover_retries_on_401` | `services.provider_failover` | 401 triggers failover (provider auth issue). | Provider clients |
| CM-5.1.7 | `test_failover_retries_on_402` | `services.provider_failover` | 402 triggers failover (provider out of credits). | Provider clients |
| CM-5.1.8 | `test_failover_retries_on_403` | `services.provider_failover` | 403 triggers failover. | Provider clients |
| CM-5.1.9 | `test_failover_retries_on_404` | `services.provider_failover` | 404 triggers failover (model not found on provider). | Provider clients |
| CM-5.1.10 | `test_failover_does_NOT_trigger_on_400` | `services.provider_failover` | 400 (user error) does NOT trigger failover. Error returned directly. | Provider clients |
| CM-5.1.11 | `test_failover_does_NOT_trigger_on_429` | `services.provider_failover` | 429 (rate limit) does NOT trigger failover. Retry with backoff instead. | Provider clients |
| CM-5.1.12 | `test_failover_transparent_to_caller` | `services.provider_failover` | When failover succeeds, caller gets successful response with no indication of internal retries (unless via metadata). | Provider clients |

### 5.2 Model-Aware Failover Rules

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-5.2.1 | `test_openai_models_failover_only_to_openai_or_openrouter` | `services.provider_failover` | `openai/gpt-4o` failover chain contains only OpenAI and OpenRouter. | None |
| CM-5.2.2 | `test_anthropic_models_failover_only_to_anthropic_or_openrouter` | `services.provider_failover` | `anthropic/claude-*` failover chain contains only Anthropic and OpenRouter. | None |
| CM-5.2.3 | `test_opensource_models_failover_across_all_providers` | `services.provider_failover` | Open-source models (e.g., `meta-llama/*`) can failover to any provider serving them. | None |

### 5.3 Circuit Breaker

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-5.3.1 | `test_circuit_breaker_starts_closed` | Circuit breaker module | New provider starts in CLOSED state. | Redis |
| CM-5.3.2 | `test_circuit_breaker_opens_after_5_failures` | Circuit breaker module | After 5 consecutive failures, state transitions from CLOSED → OPEN. | Redis |
| CM-5.3.3 | `test_circuit_breaker_4_failures_stays_closed` | Circuit breaker module | 4 consecutive failures keeps state CLOSED. | Redis |
| CM-5.3.4 | `test_circuit_breaker_open_blocks_requests` | Circuit breaker module | OPEN state skips provider in failover chain. | Redis |
| CM-5.3.5 | `test_circuit_breaker_recovery_after_5_minutes` | Circuit breaker module | After 5 minutes (300s) in OPEN, transitions to HALF_OPEN. | Redis, time mock |
| CM-5.3.6 | `test_circuit_breaker_half_open_success_closes` | Circuit breaker module | Successful request in HALF_OPEN → CLOSED. | Redis |
| CM-5.3.7 | `test_circuit_breaker_half_open_failure_reopens` | Circuit breaker module | Failed request in HALF_OPEN → OPEN (restarts timer). | Redis |
| CM-5.3.8 | `test_circuit_breaker_success_resets_failure_count` | Circuit breaker module | A success in CLOSED state resets consecutive failure counter to 0. | Redis |
| CM-5.3.9 | `test_circuit_breaker_independent_per_provider` | Circuit breaker module | Provider A's breaker state doesn't affect Provider B. | Redis |

---

## 6. Credit System

> *Conceptual Model Claim*: "Cost = (prompt_tokens × prompt_price) + (completion_tokens × completion_price). Subscription allowance used first. Pre-flight credit check. Idempotent deduction. Auto-refund on provider errors."

### 6.1 Cost Calculation

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.1.1 | `test_cost_formula_prompt_plus_completion` | `services.pricing` | Cost = (prompt_tokens × prompt_price) + (completion_tokens × completion_price). | None |
| CM-6.1.2 | `test_cost_zero_tokens_zero_cost` | `services.pricing` | 0 tokens = $0.00 cost. | None |
| CM-6.1.3 | `test_cost_uses_model_specific_pricing` | `services.pricing_lookup` | Each model has its own prompt/completion price. GPT-4 ≠ Llama pricing. | Pricing config |
| CM-6.1.4 | `test_cost_calculation_precision` | `services.pricing` | Uses Decimal (not float) to avoid floating-point errors on small token prices. | None |

### 6.2 Credit Deduction Order

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.2.1 | `test_subscription_allowance_used_before_purchased` | `db.credit_transactions` | When user has both subscription allowance and purchased credits, subscription decreases first. | Supabase |
| CM-6.2.2 | `test_purchased_credits_used_after_allowance_exhausted` | `db.credit_transactions` | After subscription allowance hits 0, purchased credits are deducted. | Supabase |
| CM-6.2.3 | `test_purchased_credits_never_expire` | `db.credit_transactions` | Purchased credits have no expiration logic. | Supabase |
| CM-6.2.4 | `test_subscription_allowance_does_not_roll_over` | Subscription reset logic | Monthly allowance resets to full amount, unused portion is lost. | Supabase, time mock |

### 6.3 Pre-Flight Credit Check

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.3.1 | `test_preflight_check_insufficient_returns_402` | Chat route / credit check | User with 0 credits gets 402 before any provider call is made. | Supabase, provider client (should NOT be called) |
| CM-6.3.2 | `test_preflight_check_estimates_max_cost` | Credit pre-flight logic | Estimate based on max_tokens × completion_price + prompt_tokens × prompt_price. | Pricing config |
| CM-6.3.3 | `test_preflight_check_passes_when_sufficient` | Credit pre-flight logic | User with enough credits passes pre-flight. | Supabase |
| CM-6.3.4 | `test_no_provider_call_on_failed_preflight` | Chat route | Verify provider client is never invoked when credits insufficient. | Provider client mock (assert not called) |

### 6.4 Idempotent Deduction

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.4.1 | `test_same_request_id_deducted_once` | `db.credit_transactions` | Submitting same request_id twice results in exactly one deduction. | Supabase |
| CM-6.4.2 | `test_different_request_ids_deducted_separately` | `db.credit_transactions` | Two different request_ids each produce a deduction. | Supabase |

### 6.5 Auto-Refund

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.5.1 | `test_provider_5xx_triggers_auto_refund` | Credit refund logic | Provider returns 500/502/503 → credits refunded to user. | Provider client, Supabase |
| CM-6.5.2 | `test_provider_timeout_triggers_auto_refund` | Credit refund logic | Provider request times out → credits refunded. | Provider client, Supabase |
| CM-6.5.3 | `test_empty_stream_triggers_auto_refund` | Credit refund logic | Streaming response with 0 content → credits refunded. | Provider client, Supabase |
| CM-6.5.4 | `test_user_4xx_does_NOT_trigger_refund` | Credit refund logic | Provider returns 400 (user error) → NO refund. | Provider client, Supabase |

### 6.6 High-Value Model Protection

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-6.6.1 | `test_premium_model_blocked_if_pricing_is_default` | Pricing validation | GPT-4, Claude, Gemini, o1/o3/o4 models are blocked if pricing falls through to default rate. | Pricing config |
| CM-6.6.2 | `test_premium_model_allowed_with_explicit_pricing` | Pricing validation | Premium models with explicit pricing configured are allowed. | Pricing config |
| CM-6.6.3 | `test_non_premium_model_allowed_with_default_pricing` | Pricing validation | Non-premium open-source models allowed even with default pricing. | Pricing config |

---

## 7. Plans & Trials

> *Conceptual Model Claim*: "Trial: Free, 3 days, $5 credit cap, 1M tokens, 10K requests. Trial users access :free models after expiration. Purchased credits never expire."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-7.1 | `test_new_user_gets_5_dollar_credits` | User provisioning | New user's initial balance is $5.00. | Supabase |
| CM-7.2 | `test_new_user_gets_3_day_trial` | User provisioning | `trial_end` set to now + 3 days. | Supabase, time mock |
| CM-7.3 | `test_trial_1m_token_limit` | Trial validation | Trial user rejected after 1M tokens consumed. | Supabase |
| CM-7.4 | `test_trial_10k_request_limit` | Trial validation | Trial user rejected after 10K requests. | Supabase |
| CM-7.5 | `test_expired_trial_returns_402` | `services.trial_validation` | Expired trial user gets 402 on standard model. | Supabase, time mock |
| CM-7.6 | `test_expired_trial_can_access_free_models` | `services.trial_validation` | Expired trial user CAN access models with `:free` suffix. | Supabase, time mock |
| CM-7.7 | `test_active_trial_can_access_all_models` | `services.trial_validation` | Active trial user can access standard models. | Supabase |
| CM-7.8 | `test_plan_tiers_exist` | Plan configuration | System defines exactly 4 tiers: Trial, Dev, Team, Enterprise. | None |
| CM-7.9 | `test_team_has_higher_rate_limits_than_dev` | Plan configuration | Team tier RPM > Dev tier RPM. | None |
| CM-7.10 | `test_purchased_credits_survive_plan_change` | Plan/credit logic | Changing plan does not affect purchased credit balance. | Supabase |

---

## 8. Caching System

> *Conceptual Model Claim*: "Multi-layer: Semantic (cosine > 0.95) → Exact-match (20K entries, 60min TTL, LRU) → External (Butter). Cache degradation: every layer degrades gracefully. No cache failure ever blocks request."

### 8.1 Exact-Match Response Cache

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-8.1.1 | `test_exact_match_cache_hit` | Response cache | Same {messages + model + params} returns cached response. | None (in-memory) |
| CM-8.1.2 | `test_exact_match_cache_miss` | Response cache | Different params = cache miss. | None |
| CM-8.1.3 | `test_exact_match_cache_uses_sha256` | Response cache | Cache key computed as SHA-256 of {messages + model + params}. | None |
| CM-8.1.4 | `test_exact_match_cache_max_20k_entries` | Response cache | After 20,000 entries, oldest LRU entry is evicted. | None |
| CM-8.1.5 | `test_exact_match_cache_60min_ttl` | Response cache | Entries expire after 60 minutes. | Time mock |
| CM-8.1.6 | `test_exact_match_cache_lru_eviction` | Response cache | Least-recently-used entry evicted when full. | None |

### 8.2 Supporting Caches

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-8.2.1 | `test_auth_cache_ttl_5_to_10_minutes` | Auth cache | Cached user data expires within 5-10 minute window. | Time mock |
| CM-8.2.2 | `test_catalog_l1_cache_ttl_5_minutes` | Catalog cache | Full catalog response cached for 5 minutes. | Time mock |
| CM-8.2.3 | `test_catalog_l2_cache_ttl_15_to_30_minutes` | Catalog cache | Per-provider lists cached 15-30 minutes. | Time mock |
| CM-8.2.4 | `test_health_cache_ttl_6_minutes` | Health cache | Model health data expires after 6 minutes. | Time mock |
| CM-8.2.5 | `test_local_memory_cache_500_entries` | Local memory fallback | Max 500 entries in local memory cache. | None |
| CM-8.2.6 | `test_local_memory_cache_ttl_15_minutes` | Local memory fallback | Local memory entries expire after 15 minutes. | Time mock |

### 8.3 Cache Degradation (CRITICAL INVARIANT)

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-8.3.1 | `test_redis_down_falls_back_to_local_memory` | Cache layer | When Redis unavailable, local memory cache used. | Redis (raise ConnectionError) |
| CM-8.3.2 | `test_all_caches_miss_falls_through_to_db` | Cache layer | When all cache layers miss, request goes to database. | Redis (miss), local (miss) |
| CM-8.3.3 | `test_cache_failure_never_blocks_request` | Cache layer | Exception in any cache layer is caught; request proceeds. | Cache (raise Exception) |
| CM-8.3.4 | `test_db_query_cache_reduces_load_60_to_80_percent` | DB query cache | Repeated identical queries served from cache (measure cache hit ratio). | Supabase |

---

## 9. Model Catalog

> *Conceptual Model Claim*: "10,000+ models. Every model carries: id, name, provider_slug, context_length, modality, pricing, streaming support, function calling, vision, health_status, benchmarks, HuggingFace metrics. Models without pricing excluded."

### 9.1 Model Metadata

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-9.1.1 | `test_every_model_has_required_fields` | Model catalog schema | Every model has: id, name, provider_slug, context_length, pricing. | Catalog data |
| CM-9.1.2 | `test_model_id_is_canonical_format` | Model catalog | IDs follow `{org}/{model-name}` format. | None |
| CM-9.1.3 | `test_pricing_field_never_null` | Model catalog | No model in catalog has null/zero pricing. | Catalog data |
| CM-9.1.4 | `test_modality_is_known_type` | Model catalog | Modality is one of: `text→text`, `text→image`, `image→text`, etc. | None |
| CM-9.1.5 | `test_context_length_is_positive_integer` | Model catalog | context_length > 0 for every model. | Catalog data |

### 9.2 Catalog Inclusion Rules

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-9.2.1 | `test_model_without_pricing_excluded` | Catalog builder | Model with no pricing data is NOT included in served catalog. | Catalog sync |
| CM-9.2.2 | `test_model_with_inactive_provider_excluded` | Catalog builder | Model from unregistered/unreachable provider excluded. | Catalog sync |
| CM-9.2.3 | `test_deduplicated_view_no_duplicate_ids` | Catalog dedup | Unique/deduplicated view has no duplicate model IDs. | Catalog data |
| CM-9.2.4 | `test_full_view_shows_all_providers` | Catalog full view | Full view shows same model from multiple providers. | Catalog data |

### 9.3 Catalog Sync & Resilience

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-9.3.1 | `test_catalog_served_from_cache_not_provider` | Catalog read path | User request reads from cache layers, never hits provider API. | Cache, provider API (assert not called) |
| CM-9.3.2 | `test_provider_api_down_serves_last_sync` | Catalog sync | If provider API fails during sync, last successful catalog data still served. | Provider API (raise), cache |
| CM-9.3.3 | `test_catalog_response_sub_10ms_on_cache_hit` | Catalog L1 cache | Response time < 10ms when served from L1 cache (benchmarkable). | Cache (populated) |

---

## 10. API Compatibility

> *Conceptual Model Claim*: "OpenAI drop-in replacement — change base URL, no code changes. Anthropic drop-in replacement. Supports streaming (SSE) and non-streaming."

### 10.1 OpenAI Compatibility

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-10.1.1 | `test_openai_response_format_has_choices` | Chat route response builder | Response contains `choices` array. | Provider client |
| CM-10.1.2 | `test_openai_response_format_has_usage` | Chat route response builder | Response contains `usage` object with `prompt_tokens`, `completion_tokens`, `total_tokens`. | Provider client |
| CM-10.1.3 | `test_openai_response_format_has_id` | Chat route response builder | Response has `id` field (e.g., `chatcmpl-...`). | Provider client |
| CM-10.1.4 | `test_openai_response_format_has_model` | Chat route response builder | Response has `model` field. | Provider client |
| CM-10.1.5 | `test_openai_streaming_sse_format` | Streaming normalizer | Stream events are `data: {json}\n\n` format. | Provider client |
| CM-10.1.6 | `test_openai_streaming_ends_with_done` | Streaming normalizer | Stream ends with `data: [DONE]\n\n`. | Provider client |
| CM-10.1.7 | `test_openai_json_mode_returns_valid_json` | Chat route | When `response_format: {"type": "json_object"}`, response content is valid JSON. | Provider client |
| CM-10.1.8 | `test_openai_tool_calling_response_format` | Chat route | When tools provided, response can contain `tool_calls` array. | Provider client |
| CM-10.1.9 | `test_openai_logprobs_included_when_requested` | Chat route | When `logprobs: true`, response includes `logprobs` field. | Provider client |

### 10.2 Anthropic Compatibility

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-10.2.1 | `test_anthropic_response_format_has_content` | Messages route response builder | Response contains `content` array. | Provider client |
| CM-10.2.2 | `test_anthropic_response_format_has_usage` | Messages route response builder | Response contains `usage` with `input_tokens`, `output_tokens`. | Provider client |
| CM-10.2.3 | `test_anthropic_streaming_event_format` | Streaming normalizer | Anthropic SSE events follow Anthropic's event type format. | Provider client |

### 10.3 Response Normalization

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-10.3.1 | `test_response_normalized_regardless_of_provider` | Response normalizer | Same model served by different providers produces consistent response format. | Multiple provider clients |
| CM-10.3.2 | `test_provider_specific_fields_stripped` | Response normalizer | Provider-specific metadata not leaked to user response. | Provider client |

---

## 11. Health Monitoring

> *Conceptual Model Claim*: "Tiered checks: Critical (5min), Popular (30min), Standard (2-4hr). Passive health from every request. Circuit breaker states: CLOSED/OPEN/HALF_OPEN. Health endpoints always return 200."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-11.1 | `test_health_check_critical_tier_5min_interval` | Health monitor config | Top 5% models by usage scheduled every 5 minutes. | Model usage data |
| CM-11.2 | `test_health_check_popular_tier_30min_interval` | Health monitor config | Next 20% models scheduled every 30 minutes. | Model usage data |
| CM-11.3 | `test_health_check_standard_tier_2_to_4hr_interval` | Health monitor config | Remaining 75% models scheduled every 2-4 hours. | Model usage data |
| CM-11.4 | `test_passive_health_captures_from_inference` | Health capture | Every real inference request contributes health data (success/failure/latency). | Inference pipeline |
| CM-11.5 | `test_health_endpoint_always_returns_200` | Health route | `/health` returns 200 even when subsystems are down. Degradation in body, not status code. | Supabase (down), Redis (down) |
| CM-11.6 | `test_health_response_contains_version` | Health route | Health response includes API version string. | None |
| CM-11.7 | `test_health_response_contains_status` | Health route | Health response includes overall status (operational/degraded). | None |
| CM-11.8 | `test_health_response_contains_timestamp` | Health route | Health response includes timestamp. | None |
| CM-11.9 | `test_incident_severity_levels` | Incident management | System supports: Critical, High, Medium, Low severity levels. | None |

---

## 12. Authentication Flow

> *Conceptual Model Claim*: "New users: $5 credits, 3-day trial, 'basic' tier. Login rate limit: 10/15min per IP. Register rate limit: 3/hour per IP. Auth info priority: email > Google OAuth > phone > GitHub."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-12.1 | `test_login_rate_limit_10_per_15min` | Auth rate limiter | 11th login attempt from same IP within 15 minutes returns 429. | In-memory limiter |
| CM-12.2 | `test_register_rate_limit_3_per_hour` | Auth rate limiter | 4th registration attempt from same IP within 1 hour returns 429. | In-memory limiter |
| CM-12.3 | `test_new_user_provisioned_with_basic_tier` | User provisioning | New user's tier is `"basic"`. | Supabase |
| CM-12.4 | `test_new_user_gets_auto_created_api_key` | User provisioning | New user gets primary API key created automatically. | Supabase |
| CM-12.5 | `test_api_key_format_gw_env_prefix` | API key creation | Generated keys follow `gw_{env}_*` format. | None |
| CM-12.6 | `test_auth_info_priority_email_first` | Auth info extraction | When user has email + Google + phone, email is selected. | Privy user data |
| CM-12.7 | `test_auth_info_priority_google_over_phone` | Auth info extraction | When user has Google + phone (no email), Google selected. | Privy user data |
| CM-12.8 | `test_partner_code_triggers_extended_trial` | Partner trial service | Partner code like `REDBEARD` gives extended trial instead of standard 3-day. | Partner config |
| CM-12.9 | `test_referral_code_stored_on_new_user` | User provisioning | Referral code saved to `users.referred_by_code`. | Supabase |
| CM-12.10 | `test_temp_email_detection_blocks_registration` | Email verification | Temporary/disposable email domains are detected and blocked. | Email validation service |

---

## 13. Observability

> *Conceptual Model Claim*: "Prometheus metrics, OpenTelemetry traces, Sentry error tracking, Arize/Braintrust AI monitoring, Pyroscope profiling."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-13.1 | `test_prometheus_inference_request_counter` | Prometheus metrics | `model_inference_requests` counter incremented on each inference call. | Prometheus registry |
| CM-13.2 | `test_prometheus_inference_duration_histogram` | Prometheus metrics | `model_inference_duration` histogram records request duration. | Prometheus registry |
| CM-13.3 | `test_prometheus_tokens_used_counter` | Prometheus metrics | `tokens_used` counter incremented by actual token count. | Prometheus registry |
| CM-13.4 | `test_prometheus_credits_used_counter` | Prometheus metrics | `credits_used` counter incremented by deducted amount. | Prometheus registry |
| CM-13.5 | `test_prometheus_ttfc_histogram` | Prometheus metrics | Time-to-first-chunk histogram recorded for streaming requests. | Prometheus registry |
| CM-13.6 | `test_sentry_captures_exceptions` | Sentry integration | Unhandled exceptions are captured by Sentry. | Sentry SDK |
| CM-13.7 | `test_opentelemetry_trace_created_per_request` | OTel middleware | Each HTTP request creates a trace span. | OTel tracer |
| CM-13.8 | `test_audit_log_on_security_violation` | Audit logging | Unauthorized admin access triggers `audit_logger.log_security_violation`. | Audit logger |

---

## 14. Token Estimation

> *Conceptual Model Claim*: "When providers don't return usage data, estimates at ~1 token per 4 characters."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-14.1 | `test_token_estimation_fallback_1_per_4_chars` | Token estimator | 400 characters → ~100 estimated tokens. | None |
| CM-14.2 | `test_token_estimation_used_when_provider_omits_usage` | Token estimator | When provider response has no `usage` field, fallback estimation used. | Provider client (no usage in response) |
| CM-14.3 | `test_real_usage_preferred_over_estimation` | Token usage logic | When provider returns `usage`, that is used instead of estimation. | Provider client (with usage) |

---

## 15. Image & Audio

> *Conceptual Model Claim*: "Image generation via /v1/images/generations. Audio transcription via /v1/audio/transcriptions."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-15.1 | `test_image_generation_deducts_credits` | Image route | Successful image generation deducts credits from user balance. | Provider client, Supabase |
| CM-15.2 | `test_image_generation_insufficient_credits_402` | Image route | User with 0 credits gets 402. | Supabase |
| CM-15.3 | `test_audio_transcription_returns_text` | Audio route | Transcription response contains text field. | Provider client |

---

## 16. Webhooks & Events

> *Conceptual Model Claim*: "Webhook events: credits.low, credits.depleted, credits.added, model.degraded, rate_limit.approaching, batch.completed. HMAC-SHA256 signed. Retry with exponential backoff."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-16.1 | `test_webhook_payload_hmac_signed` | Webhook delivery | Outgoing webhook payload includes HMAC-SHA256 signature header. | HTTP client |
| CM-16.2 | `test_webhook_credits_low_event_triggered` | Credit monitoring | When balance drops below threshold, `credits.low` event fires. | Supabase |
| CM-16.3 | `test_webhook_credits_depleted_event_triggered` | Credit monitoring | When balance hits 0, `credits.depleted` event fires. | Supabase |
| CM-16.4 | `test_webhook_retry_exponential_backoff` | Webhook delivery | Failed delivery retried with increasing delay. | HTTP client (raise on first attempts) |
| CM-16.5 | `test_stripe_webhook_always_returns_200` | Stripe webhook route | `/api/stripe/webhook` returns 200 even on processing errors. | Stripe |

---

## 17. Deployment & Entry Points

> *Conceptual Model Claim*: "Vercel serverless, Railway/Docker container, and self-hosted deployment targets."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-17.1 | `test_create_app_returns_fastapi_instance` | `main.create_app()` | Returns a FastAPI application instance. | All config |
| CM-17.2 | `test_vercel_entry_point_imports_app` | `api/index.py` | Vercel entry point successfully imports the app. | All config |
| CM-17.3 | `test_app_includes_all_route_groups` | `main.create_app()` | App has routes for: chat, auth, users, admin, payments, health, catalog, etc. | All config |

---

## 18. Provider Ecosystem

> *Conceptual Model Claim*: "30+ providers. 10,000+ models."

| ID | Test Name | Unit Under Test | Assertions | Mocks |
|----|-----------|-----------------|------------|-------|
| CM-18.1 | `test_at_least_30_providers_registered` | Provider registry | Provider count ≥ 30. | None |
| CM-18.2 | `test_each_provider_has_client_module` | Provider clients | Each registered provider has a corresponding `*_client.py` module. | None |
| CM-18.3 | `test_provider_client_implements_required_interface` | Provider clients | Each client implements `send_request()` or equivalent. | None |

---

## Summary Statistics

| Section | Test Count |
|---------|-----------|
| 1. Authentication & API Key Security | 11 |
| 2. Rate Limiting (3-Layer) | 17 |
| 3. Model Resolution Pipeline | 10 |
| 4. Intelligent Routing | 15 |
| 5. Provider Failover & Circuit Breaker | 24 |
| 6. Credit System | 17 |
| 7. Plans & Trials | 10 |
| 8. Caching System | 16 |
| 9. Model Catalog | 8 |
| 10. API Compatibility | 14 |
| 11. Health Monitoring | 9 |
| 12. Authentication Flow | 10 |
| 13. Observability | 8 |
| 14. Token Estimation | 3 |
| 15. Image & Audio | 3 |
| 16. Webhooks & Events | 5 |
| 17. Deployment & Entry Points | 3 |
| 18. Provider Ecosystem | 3 |
| **TOTAL** | **186** |

---

## Testing Principles

1. **Every test is a unit test** — mock all external I/O (Supabase, Redis, provider APIs, Stripe, Sentry)
2. **Each test verifies ONE conceptual model claim** — if the test name says "5 failures opens breaker," it only tests that
3. **No network calls** — all provider/database interactions are mocked
4. **Deterministic** — time-dependent tests use `unittest.mock.patch` on `time.time()` or `datetime.now()`
5. **Fast** — entire suite should run in < 60 seconds
6. **CI-friendly** — no environment variables required (all secrets mocked)
7. **Failure = Conceptual Model Violation** — if a test fails, the system is not conforming to its own specification

---

## Priority Order for Implementation

1. **P0 — Revenue Protection**: Section 6 (Credits), Section 7 (Trials), Section 5 (Failover)
2. **P0 — Core Functionality**: Section 10 (API Compat), Section 3 (Model Resolution)
3. **P1 — Reliability**: Section 2 (Rate Limiting), Section 5.3 (Circuit Breakers), Section 8.3 (Cache Degradation)
4. **P1 — Security**: Section 1 (Auth & Keys), Section 12 (Auth Flow)
5. **P2 — Intelligence**: Section 4 (Routing), Section 11 (Health Monitoring)
6. **P2 — Observability**: Section 13 (Metrics), Section 14 (Token Estimation)
7. **P3 — Supporting**: Sections 15-18 (Image/Audio, Webhooks, Deployment, Providers)
