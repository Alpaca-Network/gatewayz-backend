# GPU Public Transparency Feed

**Status:** implemented (W-C, gatewayz-backend#2263 #2264). Depends on W-A1's
migration (`gpu_providers`, `gpu_nodes`, `provider_work`,
`gpu_utilization_hourly`) and W-A2's `community` routing adapter for real
data to flow in; until those land (or before any community traffic exists)
every endpoint here still returns valid, empty/zeroed responses rather than
erroring.

See `docs/api.md`'s "GPU Public Transparency Feed" section for the request/
response shapes. This document covers the design decisions and the one
deliberate approximation the field data relies on.

## Code

- `src/db/gpu_rollups.py` — all reads/writes; see its module docstring for
  the full rationale.
- `src/services/gpu/rollup.py` — the hourly `AsyncIOScheduler` job.
- `src/routes/gpu_public.py` — the four `GET /gpu/public/*` routes.
- `src/schemas/gpu_public.py` — the Pydantic models that drive both the live
  JSON responses and `GET /gpu/public/schema` / `public-feed.schema.json`.
- `tests/security/test_gpu_public_aggregate_only.py` — the leak-canary test.

## The aggregate-only guarantee

Spec §6 requires these endpoints never expose a wallet address, `user_id`,
`billing_ref`, endpoint URL, or node token. This is enforced two ways:

1. **Selection, not filtering.** `src/db/gpu_rollups.py`'s DB queries
   explicitly list the columns they select (`name,region,gpu_model,vram_gb,
   status,models,...`) — they never `select("*")` and then trim client-side.
   A future column added to `gpu_nodes` or `provider_work` (e.g. a new
   sensitive field) does not automatically leak; someone has to explicitly
   add it to a select list.
2. **A recursive sentinel scan.** `tests/security/
   test_gpu_public_aggregate_only.py` seeds mocked DB rows carrying a
   sentinel wallet, endpoint URL, provider user id, `billing_ref`, email,
   and node token hash — deliberately including every forbidden field a
   `select("*")` *would* have returned, wider than what the real select
   strings ask for — then recursively scans every public JSON response
   (keys and values) for those sentinels. This is the same shape as
   `tests/security/test_upstream_identity_firewall.py`'s canary for the M3
   anonymity threat model.

## `active_nodes`: a documented deviation from the literal spec text

Spec §6 says the hourly rollup's `active_nodes` column is "nodes with a
heartbeat in that hour." Taken literally, this can't be computed correctly:
`gpu_nodes.last_heartbeat_at` stores only the single most recent heartbeat,
not a history. The rollup job backfills 7 days of history on first run
(168 hourly buckets); if `active_nodes` were derived from
`last_heartbeat_at`, only the single most-recently-computed hour could ever
be non-zero — every backfilled hour would read `active_nodes: 0`, which
misrepresents the network's actual history worse than the rollup simply not
existing.

Instead, `active_nodes` here means **"nodes that completed at least one
`provider_work` row in that hour"** — computed in
`compute_hourly_aggregates()` as the count of distinct `node_id` values
seen. This has real historical fidelity for both the live and backfilled
paths, since `provider_work.created_at` is a real historical timestamp.
The tradeoff: a node that's up and heartbeating but idle (no requests that
hour) reads as inactive. For a *utilization* dashboard, this is arguably
the more honest number anyway — an idle-but-alive node doesn't move any of
the other metrics on the page either.

## `uptime_24h_pct`: a group-level proxy, not a per-node measurement

Per-node uptime has the same underlying data problem: there is no per-node
heartbeat history table, only the (hour, region, model) rollup. So
`get_public_nodes()` estimates a node's `uptime_24h_pct` as: the share of
the last 24 hourly rollup buckets in which **any** node serving the same
(region, model) pair recorded `active_nodes >= 1`.

Concretely: if node N is the only node in `us-east` serving
`llama-3.1-8b-instruct`, its uptime approximates its own hours-with-traffic
share (reasonably accurate). If there are five nodes in that (region,
model) pair, node N's reported uptime reflects whether *the group* served
traffic that hour, not whether N specifically did — an optimistic
approximation that will overstate an individual flaky node's uptime as the
group gets denser. This is called out explicitly here (and in the
Pydantic field description on `GpuPublicNode.uptime_24h_pct`) rather than
presented as an exact measurement. A future iteration could add a
per-node heartbeat history table if per-node accuracy becomes a product
requirement; out of scope for testnet stage.

## Utilization grouping semantics

`GET /gpu/public/utilization?group=region|model` re-aggregates the raw
(hour, region, model) rollup rows down to (hour, region) or (hour, model).
`requests`/`prompt_tokens`/`completion_tokens` sum across the collapsed
sub-groups; `avg_latency_ms`/`error_rate` are request-weighted averages.
`active_nodes` is taken as the **max**, not the sum, across the collapsed
sub-groups — a node serving two models in the same region and hour must
not be double-counted when grouping by region alone.

## Caching and rate limiting

- **Cache:** 30s TTL. Redis-backed when available
  (`src.config.redis_config`); falls back to a per-process in-memory dict
  when Redis is down. Every response carries `Cache-Control: public,
  max-age=30` regardless of which cache backend served it.
- **Rate limit:** 60 requests/min/IP, using `sliding_window_check` from
  `src.services.rate_limiting` — the same primitive
  `src.services.auth_rate_limiting`'s `AuthRateLimiter` (the IP limiter
  fronting `/auth/login`, `/auth/register`, etc.) is built on. These routes
  don't use `AuthRateLimitType` (a closed enum of auth-specific actions) or
  `create_endpoint_rate_limit` (API-key-keyed; these routes have no key),
  so they call `sliding_window_check` directly with their own
  `gpu_public:{ip}` key prefix.
