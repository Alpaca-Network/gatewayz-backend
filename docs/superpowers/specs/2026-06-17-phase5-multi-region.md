# Gatewayz One — Phase 5: Multi-Region Active-Active (Topology & Config)

**Date:** 2026-06-17
**Status:** STAGED design — not deployed. Phase 5 is gated (spec §11); single-region
hardened is a valid pause point. This documents the topology and the config/code
seams so the infra can be stood up for review, not as an applied change.

The in-process decision function for region failover already exists and is unit-tested:
`src/services/region_router.py` (`select_regions` / `primary_region`). The items below
are the infrastructure that function plugs into — most are ops, not application code.

---

## 1. Topology (recap of spec §4)

```
            geo-DNS / anycast (nearest healthy region)
        ┌─────────────── REGION A (active) ───────────────┐   ┌──── REGION B (active) ────┐
        │ stateless data plane: gateway→router→dispatch→ctx │   │ same (replica)            │
        │ regional projection (Redis): registry, price,     │◀─push─▶ regional projection   │
        │   health, balance, context                        │   │                           │
        └──────────────────────┬────────────────────────────┘   └───────────────────────────┘
                               │ async events (usage, ledger, health)
                               ▼
                CONTROL PLANE (region-primary): registry/sync, pricing,
                health aggregator, billing ledger, admin · Postgres primary + read replicas
```

**Invariant:** the hot path only *reads* the in-region projection and *writes asynchronously*.
No synchronous control-plane/primary-DB call on the request path → a full control-plane
outage degrades to "no sync/admin," not "no inference." Region failover and provider
failover are independent layers.

---

## 2. Config knobs (to add, env-driven via `src/config/config.py`)

| Env var | Purpose | Default |
|---|---|---|
| `REGION_NAME` | this instance's region id (e.g. `us-east`) | required in multi-region |
| `REGION_HOME_HEADER` | header/edge attribute carrying the client's geo-home region | `X-Gatewayz-Region` |
| `REGION_PEERS` | comma-separated peer region names for failover | `""` (single region) |
| `PROJECTION_REDIS_URL__<REGION>` | per-region Redis projection endpoint | — |
| `PG_READ_REPLICA_URL` | regional read replica for control-plane reads | falls back to primary |
| `REGION_FAILOVER_ENABLED` | master switch for cross-region failover | `false` |

Single-region today = `REGION_PEERS` empty + `REGION_FAILOVER_ENABLED=false`; `region_router`
then returns just the home region. No behavior change until these are set.

---

## 3. Infrastructure (ops — not application code)

1. **Geo-DNS / anycast** — route clients to the nearest region (e.g. Cloudflare/Route53 latency
   routing). Health-check each region's `/health`; pull an unhealthy region from rotation.
2. **Regional Redis projection** — each region runs its own Redis holding the derived,
   disposable projection (registry, price_table, health_snapshot, balance_cache, context_cache).
   The control plane pushes updates to every region's Redis; regions never write the projection.
3. **Postgres primary + read replicas** — control plane writes the primary (single source of
   truth); each region reads a local replica. Eventual consistency is acceptable because the hot
   path uses the Redis projection, not the DB, for routing/pricing/balance.
4. **Balance under multi-region** — the optimistic reserve (Phase 3) tolerates eventual balance
   consistency: the margin floor + a per-region reserve buffer absorb the cross-region race, and
   the control-plane ledger reconciliation is exact. (Math lives in `src/services/credit_ledger.py`.)

---

## 4. Rollout (gated)

1. Stand up Region B as a read-only replica + projection consumer; mirror traffic (shadow), no user impact.
2. Enable `REGION_FAILOVER_ENABLED` for internal keys only; verify region failover via `region_router`.
3. Add Region B to geo-DNS rotation for a small traffic slice; watch latency/error SLOs.
4. Full active-active once reconciliation drift and cross-region latency are within target.

**Pause point:** if economics don't justify a second region, single-region hardened (Phases 0–4)
is a complete, profitable system; this phase can wait.
