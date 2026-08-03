# `/v1/models` Endpoint — Full Audit Report

**Date:** 2026-02-18
**Scope:** 7 parallel audit agents covering all layers of `GET /v1/models?gateway=all&unique=true`
**Files Audited:** 25+ source files across middleware, routes, services, database, and config layers

---

## Executive Summary

The audit found **12 CRITICAL**, **22 HIGH**, **25 MEDIUM**, and **20 LOW** severity issues across all layers. The **root cause of the 504 timeout** is a combination of:

1. **N+1 pricing queries** — `enrich_model_with_pricing()` issues a separate Supabase HTTP call per model (potentially 10,000+ round-trips)
2. **Blocking synchronous I/O on the async event loop** — Multiple layers make sync DB/HTTP calls without `asyncio.to_thread()`
3. **Broken unique models cache** — `get_unique_models_catalog()` method doesn't exist on `ModelCatalogCache`, causing `AttributeError` silently returning `[]`
4. **No L1 stampede protection** — Cache expiry under load triggers thundering herd to DB
5. **Redundant work** — `gateway=all` warm cache still executes 24 unnecessary provider fetches

---

## CRITICAL Findings (12)

### Middleware Layer
| ID | File | Issue |
|----|------|-------|
| MW-C1 | `concurrency_middleware.py` | Direct `semaphore._value` manipulation — broken admission gate, race condition |
| MW-C2 | `security_middleware.py` | Synchronous blocking DB call (`get_user`) on event loop per request |
| MW-C3 | `security_middleware.py` | Synchronous blocking DB call (`is_ip_whitelisted`) on event loop per request |
| MW-C4 | `request_timeout_middleware.py` | Catches `TimeoutError` instead of `asyncio.TimeoutError` — broken on Python 3.10 |

### Route/Orchestration Layer
| ID | File | Issue |
|----|------|-------|
| RT-C1 | `catalog.py:912` | Cache hit returns raw `dict`, loses all HTTP headers (Cache-Control, ETag, Vary) |
| RT-C2 | `catalog.py:1267` | `get_cached_providers()` blocking I/O called directly on async event loop |
| RT-C3 | `catalog.py:1009-1148` | 24 `get_cached_models()` calls block event loop; `threading.RLock` acquired on loop thread |

### Caching Layer
| ID | File | Issue |
|----|------|-------|
| CA-C1 | `models.py:972` | `cache.get_unique_models_catalog()` does not exist — `AttributeError` silently returns `[]` for all `unique_models=true` requests |
| CA-C2 | `catalog_response_cache.py` | L1 has zero thundering herd / stampede protection — all concurrent requests hit DB on TTL expiry |
| CA-C3 | `models.py:259` | `_building_catalog_flag` global boolean — concurrent provider builds corrupt each other |

### Database Layer
| ID | File | Issue |
|----|------|-------|
| DB-C1 | `models_catalog_db.py` | All catalog DB functions are synchronous, called from async handlers without `asyncio.to_thread()` |

### Model Enhancement Layer
| ID | File | Issue |
|----|------|-------|
| ME-C1 | `models.py:2477` | `NameError: model_id` in `fetch_specific_model` — silently returns `None` for all lookups |

### Pricing Layer (Root Cause of 504)
| ID | File | Issue |
|----|------|-------|
| PR-C1 | `pricing_lookup.py:289` | **N+1 query pattern**: `enrich_model_with_pricing()` issues a separate Supabase HTTP call per model. For 10K models = 10K sequential round-trips = 200-500 seconds of blocking I/O |

---

## HIGH Findings (22)

### Middleware
| ID | Issue |
|----|-------|
| MW-H1 | Middleware registration order is backwards — Security is innermost, not outermost |
| MW-H2 | `/v1/models` not timeout-exempt but `/api/catalog` is — inconsistent |
| MW-H3 | `SecurityMiddleware` uses `BaseHTTPMiddleware` — buffers all responses, breaks streaming |
| MW-H4 | `ConcurrencyMiddleware._waiting` counter check-then-increment not atomic |
| MW-H5 | Fingerprint rate limit applies to authenticated API clients — no `is_authenticated` guard |
| MW-H6 | Unsanitized client-supplied `X-Request-ID` — log injection risk |

### Route/Orchestration
| ID | Issue |
|----|-------|
| RT-H1 | `gateway=all` warm cache still executes 24 useless provider fetches |
| RT-H2 | 5 gateways (helicone, openai, anthropic, clarifai, alibaba) missing from `provider_groups` |
| RT-H3 | Default gateway mismatch: `get_all_models` defaults `"openrouter"`, `get_models` defaults `"all"` |
| RT-H4 | ETag uses Python `hash()` — non-deterministic across processes, defeats conditional GET |

### Caching
| ID | Issue |
|----|-------|
| CA-H1 | Cache key ignores `provider` and `is_private` params — cross-user cache collisions, data leakage |
| CA-H2 | `get_redis_config()` singleton not thread-safe — connection pool leak at startup |
| CA-H3 | `cleanup_expired_keys()` uses blocking `KEYS *` command |

### Database
| ID | Issue |
|----|-------|
| DB-H1 | Stale schema: code reads 4 dropped columns (`pricing_prompt`, `architecture`) — sorting broken |
| DB-H2 | `get_all_unique_models_for_catalog()` uses primary client instead of read replica |
| DB-H3 | Two non-transactional queries create data consistency window |
| DB-H4 | `limit=None` silently truncates at Supabase default 1000 rows |

### Pricing
| ID | Issue |
|----|-------|
| PR-H1 | `_get_cross_reference_pricing()` is O(N²) linear scan per model against OpenRouter catalog |
| PR-H2 | OpenRouter pricing format contradiction: `PER_TOKEN` vs `PER_1M_TOKENS` in different files |
| PR-H3 | `_pricing_cache` in `pricing_lookup.py` has no thread safety |

### Provider Assembly
| ID | Issue |
|----|-------|
| PV-H1 | `_provider_cache` read/write has no lock — thundering herd + torn writes |
| PV-H2 | Circuit breaker `get_all_status()` deadlocks via non-reentrant `threading.Lock()` |

### Model Enhancement
| ID | Issue |
|----|-------|
| ME-H1 | `enhance_model_with_provider_info` is O(N×M) — linear scan per model for provider lookup |
| ME-H2 | `detect_provider_from_model_id` rebuilds full dict + O(k) values scan per provider per request |

---

## Top 5 Fixes by Impact

### 1. Batch Pricing Query (Fixes PR-C1 — Root cause of 504)
Replace per-model DB queries with a single batch query:
```python
def get_all_pricing_batch() -> dict[str, dict]:
    result = client.table("models") \
        .select("model_name, model_pricing(...)") \
        .eq("is_active", True).execute()
    return {row["model_name"]: extract_pricing(row) for row in result.data}
```
**Impact:** Reduces catalog build from O(N) Supabase queries to O(1). Eliminates 504 timeout.

### 2. Fix `get_unique_models_catalog()` Method Name (Fixes CA-C1)
Add aliases to `ModelCatalogCache` or fix call sites in `models.py`:
```python
# In ModelCatalogCache class:
get_unique_models_catalog = get_unique_models
set_unique_models_catalog = set_unique_models
```
**Impact:** Restores `unique_models=true` functionality from silently broken to working.

### 3. Wrap All Blocking I/O in `asyncio.to_thread()` (Fixes RT-C2, RT-C3, DB-C1, MW-C2, MW-C3)
```python
# Before (blocking event loop):
providers = get_cached_providers()
models = get_cached_models("featherless")
user = get_user(api_key)

# After:
providers = await asyncio.to_thread(get_cached_providers)
models = await asyncio.to_thread(get_cached_models, "featherless")
user = await asyncio.to_thread(get_user, api_key)
```
**Impact:** Unblocks the async event loop, preventing all-request stalls during cache misses.

### 4. Add L1 Stampede Protection (Fixes CA-C2)
Use Redis `SET NX EX` lock pattern:
```python
lock_key = f"catalog:rebuild_lock:{gateway}:{param_hash}"
acquired = redis.set(lock_key, "1", nx=True, ex=60)
if not acquired:
    # Wait briefly, then re-check cache
    await asyncio.sleep(0.5)
    return await get_cached_catalog_response(gateway, params)
```
**Impact:** Prevents thundering herd on L1 cache expiry.

### 5. Skip Redundant Provider Fetches on Cache Hit (Fixes RT-H1)
```python
if not (gateway_value == "all" and all_models_list):
    # Only fetch individual providers when needed
    if gateway_value in ("onerouter", "all"):
        onerouter_models = get_cached_models("onerouter") or []
    # ... rest of providers
```
**Impact:** Eliminates 24 unnecessary Redis/DB calls on every cached `gateway=all` request.

---

## Fix Priority Matrix

```
                    HIGH IMPACT
                        │
    ┌───────────────────┼───────────────────┐
    │                   │                   │
    │  PR-C1 (batch)    │  CA-C1 (method)   │
    │  DB-C1 (async)    │  RT-H1 (skip)     │
    │  CA-C2 (stampede) │  CA-H1 (cache key) │
    │  MW-C2/C3 (async) │  RT-H2 (providers) │
    │                   │                   │
LOW ├───────────────────┼───────────────────┤ HIGH
EFFORT│                 │                   │ EFFORT
    │  RT-C1 (headers)  │  MW-H1 (order)    │
    │  RT-H4 (etag)     │  MW-H3 (ASGI)     │
    │  ME-C1 (NameError)│  PR-H1 (O(N²))   │
    │  PV-H2 (RLock)    │  PR-H2 (format)   │
    │                   │                   │
    └───────────────────┼───────────────────┘
                        │
                    LOW IMPACT
```

---

## Full Finding Counts by Layer

| Layer | CRITICAL | HIGH | MEDIUM | LOW | Total |
|-------|----------|------|--------|-----|-------|
| Middleware | 4 | 6 | 7 | 6 | 23 |
| Route/Orchestration | 3 | 4 | 5 | 2 | 14 |
| Caching (L1/L2) | 3 | 5 | 6 | 5 | 19 |
| Database | 1 | 5 | 7 | 5 | 18 |
| Pricing | 1 | 3 | 4 | 2 | 10 |
| Provider Assembly | 0 | 5 | 8 | 7 | 20 |
| Model Enhancement | 1 | 4 | 7 | 6 | 18 |
| **Total** | **13** | **32** | **44** | **33** | **122** |

---

## Appendix: Detailed Reports

Each layer's full findings with code references, evidence, and fix recommendations are available in the individual agent audit transcripts.
