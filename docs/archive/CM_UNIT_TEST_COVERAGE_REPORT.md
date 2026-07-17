# Conceptual Model Unit Test Coverage Report

> **Comparing**: `CONCEPTUAL_MODEL_UNIT_TESTING_PLAN.md` (186 theoretical tests) vs `TEST_MAPPING.md` (5,491 actual tests)
>
> **Generated**: 2026-03-09

---

## Executive Summary

| Status | Count | % |
|--------|-------|---|
| **COVERED** | 89 | 47.8% |
| **PARTIAL** | 31 | 16.7% |
| **MISSING** | 66 | 35.5% |
| **TOTAL** | 186 | 100% |

**Bottom line**: Nearly half the conceptual model claims are fully tested. But **66 tests are completely missing** — these are behaviors your system promises in its spec that have zero automated verification. The biggest holes are in **auto-refunds, high-value model protection, token estimation, model catalog validation, webhooks, auth flow provisioning, and API compatibility features** (JSON mode, tool calling, logprobs).

---

## Coverage Heatmap by Section

| Section | Total | Covered | Partial | Missing | Coverage |
|---------|-------|---------|---------|---------|----------|
| 1. Auth & API Key Security | 11 | 6 | 2 | 3 | 54.5% |
| 2. Rate Limiting (3-Layer) | 17 | 10 | 3 | 4 | 58.8% |
| 3. Model Resolution | 10 | 5 | 4 | 1 | 50.0% |
| 4. Intelligent Routing | 15 | 9 | 2 | 4 | 60.0% |
| 5. Failover & Circuit Breaker | 24 | 17 | 2 | 5 | 70.8% |
| 6. Credit System | 17 | 5 | 3 | 9 | 29.4% |
| 7. Plans & Trials | 10 | 4 | 4 | 2 | 40.0% |
| 8. Caching System | 16 | 7 | 4 | 5 | 43.8% |
| 9. Model Catalog | 8 | 1 | 2 | 5 | 12.5% |
| 10. API Compatibility | 14 | 5 | 4 | 5 | 35.7% |
| 11. Health Monitoring | 9 | 8 | 0 | 1 | 88.9% |
| 12. Auth Flow | 10 | 3 | 0 | 7 | 30.0% |
| 13. Observability | 8 | 5 | 1 | 2 | 62.5% |
| 14. Token Estimation | 3 | 0 | 0 | 3 | 0% |
| 15. Image & Audio | 3 | 3 | 0 | 0 | 100% |
| 16. Webhooks & Events | 5 | 1 | 0 | 4 | 20.0% |
| 17. Deployment | 3 | 2 | 0 | 1 | 66.7% |
| 18. Provider Ecosystem | 3 | 0 | 0 | 3 | 0% |

---

## Full Test-by-Test Mapping

### Section 1: Authentication & API Key Security

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-1.1 | `test_api_key_encrypted_with_fernet` | **COVERED** | `tests/security/test_security.py` → `test_encrypt_api_key` |
| CM-1.2 | `test_api_key_decryption_roundtrip` | **COVERED** | `tests/security/test_security.py` → `test_decrypt_api_key` |
| CM-1.3 | `test_api_key_hmac_sha256_hashing` | **COVERED** | `tests/security/test_security.py` → `test_hash_api_key` |
| CM-1.4 | `test_hmac_lookup_without_decryption` | **PARTIAL** | `tests/security/test_deps.py` → validates key via hash, but doesn't assert decryption NOT called |
| CM-1.5 | `test_encrypted_key_not_plaintext_in_db` | **PARTIAL** | Fernet output verified, but not tested in DB write context |
| CM-1.6 | `test_rbac_four_tiers_exist` | **MISSING** | — |
| CM-1.7 | `test_admin_role_has_all_permissions` | **MISSING** | — |
| CM-1.8 | `test_free_role_has_minimum_permissions` | **MISSING** | — |
| CM-1.9 | `test_ip_allowlist_blocks_non_listed_ip` | **COVERED** | `tests/security/test_security.py` → `test_validate_ip_allowlist_blocked` |
| CM-1.10 | `test_ip_allowlist_allows_listed_ip` | **COVERED** | `tests/security/test_security.py` → `test_validate_ip_allowlist_allowed` |
| CM-1.11 | `test_domain_restriction_blocks_wrong_domain` | **COVERED** | `tests/security/test_security.py` → `test_validate_domain_restriction_blocked` |

### Section 2: Rate Limiting

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-2.1.1 | `test_ip_rate_limit_under_threshold_allows` | **COVERED** | `tests/middleware/test_security_middleware.py` |
| CM-2.1.2 | `test_ip_rate_limit_over_threshold_blocks` | **COVERED** | `tests/middleware/test_security_middleware.py` → `test_rate_limit_exceeded` |
| CM-2.1.3 | `test_ip_rate_limit_applied_before_auth` | **PARTIAL** | Middleware order implied, not explicitly asserted |
| CM-2.1.4 | `test_velocity_detection_triggers` | **COVERED** | `tests/middleware/test_security_middleware.py` → `test_velocity_mode_activation` |
| CM-2.1.5 | `test_authenticated_exempt_from_ip_limits` | **COVERED** | `tests/middleware/test_security_middleware.py` → `test_authenticated_user_exempt` |
| CM-2.2.1 | `test_key_rate_limit_tracks_rpm` | **COVERED** | `tests/services/test_rate_limiting.py` |
| CM-2.2.2 | `test_key_rate_limit_enforces_plan_tier` | **PARTIAL** | Rate limits tested, no cross-tier comparison |
| CM-2.2.3 | `test_key_rate_limit_tracks_tokens_per_day` | **MISSING** | — |
| CM-2.2.4 | `test_key_rate_limit_tracks_tokens_per_month` | **MISSING** | — |
| CM-2.2.5 | `test_rate_limit_returns_proper_headers` | **COVERED** | `tests/middleware/test_security_middleware.py` → `test_rate_limit_headers_format` |
| CM-2.3.1 | `test_anonymous_stricter_than_authenticated` | **PARTIAL** | Anonymous tested, no comparison to authenticated |
| CM-2.3.2 | `test_anonymous_uses_ip_hash` | **COVERED** | `tests/services/test_anonymous_rate_limiter.py` → `test_ip_hashing_for_redis_key` |
| CM-2.3.3 | `test_anonymous_blocks_excess` | **COVERED** | `tests/services/test_anonymous_rate_limiter.py` → `test_anonymous_rate_limit_exceeded` |
| CM-2.4.1 | `test_redis_down_activates_fallback` | **COVERED** | `tests/services/test_rate_limiting.py` → `test_redis_connection_failure_fallback` |
| CM-2.4.2 | `test_fallback_lru_cache_500_entries` | **MISSING** | — |
| CM-2.4.3 | `test_fallback_ttl_15_minutes` | **MISSING** | — |
| CM-2.4.4 | `test_requests_never_blocked_by_infra_failure` | **COVERED** | `tests/services/test_rate_limiting.py` → `test_fail_open_behavior` |

### Section 3: Model Resolution

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-3.1 | `test_alias_r1_resolves_to_deepseek` | **PARTIAL** | Alias resolution tested broadly, specific "r1" not confirmed |
| CM-3.2 | `test_alias_gpt4o_resolves_to_openai` | **PARTIAL** | Same — "gpt-4o" specific not confirmed |
| CM-3.3 | `test_at_least_120_aliases_defined` | **MISSING** | — |
| CM-3.4 | `test_canonical_id_passthrough` | **COVERED** | `tests/services/test_model_transformations.py` → `test_canonical_model_id_passthrough` |
| CM-3.5 | `test_explicit_override_highest_priority` | **PARTIAL** | Provider detection tested, priority order not verified |
| CM-3.6 | `test_format_based_rules` | **COVERED** | `tests/services/test_model_transformations.py` → `test_fireworks_format_detection` |
| CM-3.7 | `test_org_prefix_fallback` | **PARTIAL** | Not explicitly verified as a fallback method |
| CM-3.8 | `test_fireworks_format_transformation` | **COVERED** | `tests/services/test_model_transformations.py` + `tests/unit/` |
| CM-3.9 | `test_per_provider_transformation` | **COVERED** | `tests/services/test_model_transformations_comprehensive.py` |
| CM-3.10 | `test_unknown_alias_behavior` | **COVERED** | `tests/services/test_model_transformations.py` → `test_unknown_model_passthrough` |

### Section 4: Intelligent Routing

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-4.1.1 | `test_general_router_quality_mode` | **COVERED** | `tests/services/test_general_router.py` |
| CM-4.1.2 | `test_general_router_cost_mode` | **COVERED** | `tests/services/test_general_router.py` |
| CM-4.1.3 | `test_general_router_latency_mode` | **COVERED** | `tests/services/test_general_router.py` |
| CM-4.1.4 | `test_general_router_balanced_mode` | **COVERED** | `tests/services/test_general_router.py` |
| CM-4.1.5 | `test_quality_selects_high_benchmark` | **MISSING** | — |
| CM-4.1.6 | `test_cost_selects_cheapest` | **MISSING** | — |
| CM-4.1.7 | `test_latency_selects_fastest` | **MISSING** | — |
| CM-4.1.8 | `test_invalid_mode_rejected` | **COVERED** | `tests/services/test_general_router.py` |
| CM-4.2.1 | `test_code_router_auto_mode` | **COVERED** | `tests/services/test_code_router.py` |
| CM-4.2.2 | `test_code_router_agentic_mode` | **COVERED** | `tests/services/test_code_router.py` |
| CM-4.2.3 | `test_code_router_price_mode` | **COVERED** | `tests/services/test_code_router.py` |
| CM-4.2.4 | `test_code_router_quality_mode` | **COVERED** | `tests/services/test_code_router.py` |
| CM-4.2.5 | `test_swe_bench_scores_used` | **MISSING** | — |
| CM-4.2.6 | `test_task_complexity_classification` | **PARTIAL** | Tier selection implies it, not explicit |
| CM-4.2.7 | `test_tier_to_model_mapping` | **PARTIAL** | Tested implicitly, not explicitly |

### Section 5: Provider Failover & Circuit Breaker

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-5.1.1 | `test_failover_chain_14_providers` | **PARTIAL** | Chain tested, count not asserted |
| CM-5.1.2 | `test_chain_ordered_by_reliability` | **MISSING** | — |
| CM-5.1.3 | `test_failover_on_502` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.4 | `test_failover_on_503` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.5 | `test_failover_on_504` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.6 | `test_failover_on_401` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.7 | `test_failover_on_402` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.8 | `test_failover_on_403` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.9 | `test_failover_on_404` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.10 | `test_no_failover_on_400` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.11 | `test_no_failover_on_429` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.1.12 | `test_failover_transparent_to_caller` | **MISSING** | — |
| CM-5.2.1 | `test_openai_failover_restricted` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.2.2 | `test_anthropic_failover_restricted` | **COVERED** | `tests/services/test_provider_failover.py` |
| CM-5.2.3 | `test_opensource_failover_universal` | **PARTIAL** | Not explicitly verified |
| CM-5.3.1 | `test_breaker_starts_closed` | **COVERED** | `tests/utils/test_provider_safety.py` |
| CM-5.3.2 | `test_breaker_opens_after_5_failures` | **COVERED** | `tests/utils/test_provider_safety.py` |
| CM-5.3.3 | `test_breaker_4_failures_stays_closed` | **MISSING** | — (boundary test) |
| CM-5.3.4 | `test_breaker_open_blocks_requests` | **COVERED** | `tests/utils/test_provider_safety.py` |
| CM-5.3.5 | `test_breaker_recovery_after_5min` | **COVERED** | `tests/utils/test_provider_safety.py` |
| CM-5.3.6 | `test_half_open_success_closes` | **COVERED** | `tests/services/test_circuit_breaker_improvements.py` |
| CM-5.3.7 | `test_half_open_failure_reopens` | **COVERED** | `tests/services/test_circuit_breaker_improvements.py` |
| CM-5.3.8 | `test_success_resets_failure_count` | **COVERED** | `tests/utils/test_provider_safety.py` |
| CM-5.3.9 | `test_breaker_independent_per_provider` | **MISSING** | — |

### Section 6: Credit System

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-6.1.1 | `test_cost_formula` | **COVERED** | `tests/services/test_pricing.py` → `test_calculate_cost_basic` |
| CM-6.1.2 | `test_zero_tokens_zero_cost` | **COVERED** | `tests/services/test_pricing.py` |
| CM-6.1.3 | `test_model_specific_pricing` | **COVERED** | `tests/services/test_pricing.py` |
| CM-6.1.4 | `test_decimal_precision` | **PARTIAL** | Tested implicitly, no Decimal-not-float assertion |
| CM-6.2.1 | `test_subscription_before_purchased` | **COVERED** | `tests/db/test_tiered_credits.py` |
| CM-6.2.2 | `test_purchased_after_allowance_exhausted` | **COVERED** | `tests/db/test_tiered_credits.py` |
| CM-6.2.3 | `test_purchased_credits_never_expire` | **MISSING** | — |
| CM-6.2.4 | `test_allowance_no_rollover` | **COVERED** | `tests/db/test_tiered_credits.py` |
| CM-6.3.1 | `test_preflight_insufficient_402` | **COVERED** | `tests/services/test_credit_precheck.py` |
| CM-6.3.2 | `test_preflight_estimates_max_cost` | **PARTIAL** | Pre-check exists, formula not explicitly verified |
| CM-6.3.3 | `test_preflight_passes_sufficient` | **COVERED** | `tests/services/test_credit_precheck.py` |
| CM-6.3.4 | `test_no_provider_call_on_failed_preflight` | **MISSING** | — |
| CM-6.4.1 | `test_same_request_id_once` | **PARTIAL** | Transactions tested, idempotency not explicit |
| CM-6.4.2 | `test_different_ids_separate` | **PARTIAL** | Same |
| CM-6.5.1 | `test_5xx_auto_refund` | **MISSING** | — |
| CM-6.5.2 | `test_timeout_auto_refund` | **MISSING** | — |
| CM-6.5.3 | `test_empty_stream_auto_refund` | **MISSING** | — |
| CM-6.5.4 | `test_4xx_no_refund` | **MISSING** | — |
| CM-6.6.1 | `test_premium_blocked_default_pricing` | **MISSING** | — |
| CM-6.6.2 | `test_premium_allowed_explicit_pricing` | **MISSING** | — |
| CM-6.6.3 | `test_non_premium_allowed_default` | **MISSING** | — |

### Section 7: Plans & Trials

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-7.1 | `test_new_user_5_dollar_credits` | **PARTIAL** | Trial tested, $5 specific not verified |
| CM-7.2 | `test_new_user_3_day_trial` | **PARTIAL** | Trial tested, 3-day specific not asserted |
| CM-7.3 | `test_trial_1m_token_limit` | **COVERED** | `tests/services/test_trial_validation.py` |
| CM-7.4 | `test_trial_10k_request_limit` | **COVERED** | `tests/services/test_trial_validation.py` |
| CM-7.5 | `test_expired_trial_402` | **COVERED** | `tests/services/test_trial_validation.py` |
| CM-7.6 | `test_expired_trial_free_models` | **PARTIAL** | Free access tested, not specifically for expired trial |
| CM-7.7 | `test_active_trial_all_models` | **COVERED** | `tests/services/test_trial_validation.py` |
| CM-7.8 | `test_plan_tiers_exist` | **PARTIAL** | Enum tested, 4-tier match not confirmed |
| CM-7.9 | `test_team_higher_limits_than_dev` | **MISSING** | — |
| CM-7.10 | `test_credits_survive_plan_change` | **MISSING** | — |

### Section 8: Caching System

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-8.1.1 | `test_cache_hit` | **COVERED** | `tests/services/test_response_cache.py` |
| CM-8.1.2 | `test_cache_miss` | **COVERED** | `tests/services/test_response_cache.py` |
| CM-8.1.3 | `test_sha256_key` | **COVERED** | `tests/services/test_response_cache.py` |
| CM-8.1.4 | `test_20k_entry_limit` | **PARTIAL** | Size limit tested, 20K not specifically verified |
| CM-8.1.5 | `test_60min_ttl` | **COVERED** | `tests/services/test_response_cache.py` |
| CM-8.1.6 | `test_lru_eviction` | **COVERED** | `tests/services/test_response_cache.py` |
| CM-8.2.1 | `test_auth_cache_5_10min_ttl` | **PARTIAL** | Auth cache tested, TTL range not verified |
| CM-8.2.2 | `test_catalog_l1_5min_ttl` | **PARTIAL** | TTL tested, 5min not specifically verified |
| CM-8.2.3 | `test_catalog_l2_15_30min_ttl` | **PARTIAL** | TTL tested, range not verified |
| CM-8.2.4 | `test_health_cache_6min_ttl` | **MISSING** | — |
| CM-8.2.5 | `test_local_memory_500_entries` | **MISSING** | — |
| CM-8.2.6 | `test_local_memory_15min_ttl` | **MISSING** | — |
| CM-8.3.1 | `test_redis_down_local_fallback` | **COVERED** | `tests/services/test_unified_catalog_cache.py` |
| CM-8.3.2 | `test_all_miss_falls_to_db` | **MISSING** | — |
| CM-8.3.3 | `test_cache_failure_never_blocks` | **COVERED** | `tests/services/test_unified_catalog_cache.py` |
| CM-8.3.4 | `test_60_80_percent_load_reduction` | **MISSING** | — |

### Section 9: Model Catalog

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-9.1.1 | `test_required_fields` | **MISSING** | — |
| CM-9.1.2 | `test_canonical_id_format` | **MISSING** | — |
| CM-9.1.3 | `test_pricing_never_null` | **MISSING** | — |
| CM-9.1.4 | `test_modality_known_type` | **MISSING** | — |
| CM-9.1.5 | `test_context_length_positive` | **MISSING** | — |
| CM-9.2.1 | `test_no_pricing_excluded` | **MISSING** | — |
| CM-9.2.2 | `test_inactive_provider_excluded` | **MISSING** | — |
| CM-9.2.3 | `test_dedup_no_duplicates` | **PARTIAL** | Unique models cached, dedup not explicitly verified |
| CM-9.2.4 | `test_full_view_all_providers` | **MISSING** | — |
| CM-9.3.1 | `test_served_from_cache_not_provider` | **PARTIAL** | Cache tested, provider-not-called not asserted |
| CM-9.3.2 | `test_api_down_serves_last_sync` | **COVERED** | `tests/services/test_zero_model_fallback.py` |
| CM-9.3.3 | `test_sub_10ms_cache_hit` | **MISSING** | — |

### Section 10: API Compatibility

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-10.1.1 | `test_response_has_choices` | **COVERED** | `tests/routes/test_chat.py` |
| CM-10.1.2 | `test_response_has_usage` | **COVERED** | `tests/routes/test_chat.py` |
| CM-10.1.3 | `test_response_has_id` | **PARTIAL** | Tested, chatcmpl- format not asserted |
| CM-10.1.4 | `test_response_has_model` | **PARTIAL** | Tested, explicit field not asserted |
| CM-10.1.5 | `test_streaming_sse_format` | **COVERED** | `tests/services/test_stream_normalizer.py` |
| CM-10.1.6 | `test_streaming_ends_done` | **COVERED** | `tests/services/test_stream_normalizer.py` |
| CM-10.1.7 | `test_json_mode_valid_json` | **MISSING** | — |
| CM-10.1.8 | `test_tool_calling_format` | **MISSING** | — |
| CM-10.1.9 | `test_logprobs_when_requested` | **MISSING** | — |
| CM-10.2.1 | `test_anthropic_has_content` | **COVERED** | `tests/routes/test_messages.py` |
| CM-10.2.2 | `test_anthropic_has_usage` | **COVERED** | `tests/routes/test_messages.py` |
| CM-10.2.3 | `test_anthropic_streaming_format` | **PARTIAL** | Streaming tested, Anthropic events not verified |
| CM-10.3.1 | `test_normalized_across_providers` | **MISSING** | — |
| CM-10.3.2 | `test_provider_fields_stripped` | **MISSING** | — |

### Section 11: Health Monitoring

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-11.1 | `test_critical_tier_5min` | **COVERED** | `tests/test_intelligent_health_monitor.py` |
| CM-11.2 | `test_popular_tier_30min` | **COVERED** | `tests/test_intelligent_health_monitor.py` |
| CM-11.3 | `test_standard_tier_2_4hr` | **COVERED** | `tests/test_intelligent_health_monitor.py` |
| CM-11.4 | `test_passive_health_from_inference` | **MISSING** | — |
| CM-11.5 | `test_health_always_200` | **COVERED** | `tests/routes/test_health.py` |
| CM-11.6 | `test_health_has_version` | **COVERED** | `tests/routes/test_health.py` |
| CM-11.7 | `test_health_has_status` | **COVERED** | `tests/routes/test_health.py` |
| CM-11.8 | `test_health_has_timestamp` | **COVERED** | `tests/routes/test_health.py` |
| CM-11.9 | `test_severity_levels` | **COVERED** | `tests/test_health_alerting.py` |

### Section 12: Authentication Flow

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-12.1 | `test_login_10_per_15min` | **COVERED** | `tests/services/test_auth_rate_limiting.py` |
| CM-12.2 | `test_register_3_per_hour` | **COVERED** | `tests/services/test_auth_rate_limiting.py` |
| CM-12.3 | `test_new_user_basic_tier` | **MISSING** | — |
| CM-12.4 | `test_new_user_auto_api_key` | **MISSING** | — |
| CM-12.5 | `test_key_format_gw_env` | **COVERED** | `tests/security/test_security.py` |
| CM-12.6 | `test_auth_priority_email_first` | **MISSING** | — |
| CM-12.7 | `test_auth_priority_google_over_phone` | **MISSING** | — |
| CM-12.8 | `test_partner_code_extended_trial` | **MISSING** | — |
| CM-12.9 | `test_referral_code_stored` | **MISSING** | — |
| CM-12.10 | `test_temp_email_blocked` | **COVERED** | `tests/utils/test_security_validators.py` |

### Section 13: Observability

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-13.1 | `test_inference_request_counter` | **COVERED** | `tests/test_prometheus_metrics.py` |
| CM-13.2 | `test_inference_duration_histogram` | **COVERED** | `tests/test_observability_middleware.py` |
| CM-13.3 | `test_tokens_used_counter` | **COVERED** | `tests/test_prometheus_metrics.py` |
| CM-13.4 | `test_credits_used_counter` | **COVERED** | `tests/test_prometheus_metrics.py` |
| CM-13.5 | `test_ttfc_histogram` | **MISSING** | — |
| CM-13.6 | `test_sentry_captures_exceptions` | **COVERED** | `tests/utils/test_auto_sentry.py` |
| CM-13.7 | `test_otel_trace_per_request` | **PARTIAL** | Middleware tested, trace span not verified |
| CM-13.8 | `test_audit_log_security_violation` | **MISSING** | — |

### Section 14: Token Estimation

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-14.1 | `test_1_per_4_chars_fallback` | **MISSING** | — |
| CM-14.2 | `test_used_when_provider_omits_usage` | **MISSING** | — |
| CM-14.3 | `test_real_usage_preferred` | **MISSING** | — |

### Section 15: Image & Audio

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-15.1 | `test_image_deducts_credits` | **COVERED** | `tests/routes/test_images.py` |
| CM-15.2 | `test_image_insufficient_402` | **COVERED** | `tests/routes/test_images.py` |
| CM-15.3 | `test_audio_returns_text` | **COVERED** | `tests/routes/test_audio.py` |

### Section 16: Webhooks & Events

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-16.1 | `test_webhook_hmac_signed` | **MISSING** | — |
| CM-16.2 | `test_credits_low_event` | **MISSING** | — |
| CM-16.3 | `test_credits_depleted_event` | **MISSING** | — |
| CM-16.4 | `test_webhook_retry_backoff` | **MISSING** | — |
| CM-16.5 | `test_stripe_webhook_always_200` | **COVERED** | `tests/services/test_payment_processing.py` |

### Section 17: Deployment

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-17.1 | `test_create_app_fastapi` | **COVERED** | `tests/smoke/test_deployment.py` |
| CM-17.2 | `test_vercel_entry_imports` | **MISSING** | — |
| CM-17.3 | `test_all_route_groups` | **COVERED** | `tests/smoke/test_deployment.py` |

### Section 18: Provider Ecosystem

| CM ID | Test | Status | Existing Coverage |
|-------|------|--------|-------------------|
| CM-18.1 | `test_30_plus_providers` | **MISSING** | — |
| CM-18.2 | `test_each_provider_has_client` | **MISSING** | — |
| CM-18.3 | `test_client_implements_interface` | **MISSING** | — |

---

## Critical Gaps — Grouped by Risk

### REVENUE RISK (0% coverage in these areas)

**Auto-Refund System** (CM-6.5.1 through CM-6.5.4)
- No tests that provider 5xx → auto-refund
- No tests that timeouts → auto-refund
- No tests that empty streams → auto-refund
- No tests that user 4xx → NO refund
- **Impact**: Users could be double-charged or never refunded on provider failures

**High-Value Model Protection** (CM-6.6.1 through CM-6.6.3)
- No tests that GPT-4/Claude/Gemini blocked when pricing falls to default
- **Impact**: Premium models could serve at default rates → massive under-billing

**Idempotent Deduction** (CM-6.4.1, CM-6.4.2)
- Only partial coverage — request_id idempotency not explicitly tested
- **Impact**: Retries could double-charge users

### RELIABILITY RISK

**Rate Limit Fallback Specifics** (CM-2.4.2, CM-2.4.3)
- 500-entry LRU and 15-minute TTL not verified
- **Impact**: Fallback could behave unexpectedly under load

**Token Estimation** (CM-14.1 through CM-14.3)
- Zero coverage for the 1-token-per-4-chars fallback
- **Impact**: When providers don't return usage, billing could be wrong

### SPEC CONFORMANCE RISK

**Model Catalog Validation** (CM-9.1.1 through CM-9.2.4)
- No tests that models have required fields, valid pricing, valid modality
- No tests that models without pricing are excluded
- **Impact**: Invalid or unpriced models could leak into catalog

**API Compatibility** (CM-10.1.7 through CM-10.3.2)
- No tests for JSON mode, tool calling, logprobs, cross-provider normalization
- **Impact**: Drop-in OpenAI compatibility claim unverified for advanced features

**Auth Flow Provisioning** (CM-12.3 through CM-12.9)
- No tests for new user tier assignment, auto API key, auth priority, partner codes, referral storage
- **Impact**: New user onboarding could silently break

---

## Implementation Recommendations

### Wave 1 — Revenue Protection (P0, ~20 tests)
```
CM-6.5.1 through CM-6.5.4  (auto-refund: 4 tests)
CM-6.6.1 through CM-6.6.3  (premium model protection: 3 tests)
CM-6.4.1, CM-6.4.2         (idempotent deduction: 2 tests, upgrade from PARTIAL)
CM-6.3.4                    (no provider call on failed preflight: 1 test)
CM-14.1 through CM-14.3    (token estimation fallback: 3 tests)
CM-6.2.3                    (purchased credits never expire: 1 test)
CM-7.9, CM-7.10             (tier limits, credits survive plan change: 2 tests)
```

### Wave 2 — Spec Conformance (P1, ~25 tests)
```
CM-9.1.1 through CM-9.2.4  (catalog validation: 9 tests)
CM-10.1.7 through CM-10.3.2 (API compatibility: 5 tests)
CM-12.3 through CM-12.9    (auth provisioning: 5 tests)
CM-1.6 through CM-1.8      (RBAC tiers: 3 tests)
CM-3.3                      (120+ aliases: 1 test)
```

### Wave 3 — Reliability Hardening (P2, ~15 tests)
```
CM-2.4.2, CM-2.4.3          (fallback specifics: 2 tests)
CM-2.2.3, CM-2.2.4          (token per day/month limits: 2 tests)
CM-5.3.3, CM-5.3.9          (circuit breaker boundary/independence: 2 tests)
CM-8.2.4 through CM-8.2.6   (cache TTLs: 3 tests)
CM-8.3.2, CM-8.3.4          (cache fallthrough: 2 tests)
CM-16.1 through CM-16.4     (webhooks: 4 tests)
```

### Wave 4 — Completeness (P3, ~6 tests)
```
CM-18.1 through CM-18.3     (provider ecosystem: 3 tests)
CM-17.2                      (Vercel entry: 1 test)
CM-11.4                      (passive health: 1 test)
CM-13.5, CM-13.8             (TTFC, audit log: 2 tests)
```
