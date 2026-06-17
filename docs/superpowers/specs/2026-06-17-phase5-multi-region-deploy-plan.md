# Phase 5 — Multi-Region Deploy Plan (Gatewayz One)

**Status:** rollout phase 1 (inventory + wire) **DONE in code** — no infra/traffic change yet. The selection core (`src/services/region_router.py`) is built + tested; it is now wired to runtime config via `src/services/region_service.py` and the serving region is surfaced as the `X-Gatewayz-Region` response header (`src/middleware/region_middleware.py`). Single region still serves all traffic until `MULTI_REGION_ENABLED=true` + real infra. Phases 2–5 below remain owner/infra-driven.

## Current state
- **Gateway (data plane):** stateless FastAPI app (Vercel `api/index.py` / Railway/Docker `start.sh`), single region.
- **`region_router.py` (pure, built):** `Region` dataclass + `is_region_eligible(region)`, `select_regions(regions, *, home)` → ordered eligible regions, `primary_region(...)`. No I/O — the decision core; not yet wired or fed real region inventory.
- **State today:** Supabase (single primary Postgres), Redis (rate limits / caches), Stripe/Resend external.

## Target: active-active, latency-routed
Two+ regions each running the full stateless gateway; users hit the nearest healthy region; control-plane config (registry, routing_policies — Phase 1 tables) is shared; the billing ledger (Phase 3) is the cross-region source of truth for money.

```
            ┌─ region A (gateway, Redis-A) ─┐
client ─ GeoDNS/anycast ─┤                              ├─ Supabase (primary + read replicas)
            └─ region B (gateway, Redis-B) ─┘            └─ providers (region_affinity from Phase 1)
```

## What gates multi-region (must resolve before active-active)
| Concern | Issue | Resolution |
|---------|-------|------------|
| **Credit balances** | Concurrent debits in two regions can oversell a balance (last-writer-wins) | Cut billing over to the Phase 3 ledger with atomic reserve→settle (single source of truth); until then, route a user's billing-affecting writes to one **home** region (`select_regions(home=...)`). |
| **Rate limits** | Per-region Redis = N× the intended limit | Global limit store (shared Redis / Upstash global) OR partition limits per region and divide budgets. |
| **DB writes** | Single Postgres primary = cross-region write latency | Keep writes to primary; add read replicas per region for catalog/registry reads (mostly-read path). |
| **Config drift** | Registry/policies must be identical everywhere | Already centralized in Supabase (Phase 1 tables) — regions read the same control plane. |
| **Provider affinity** | Some providers are region-locked | Use `providers.region_affinity` (Phase 1) to bias `select_regions`/failover per region. |

## Rollout phases (each independently reversible)
1. **Inventory + wire (no traffic change). ✅ DONE.** `region_service.py` feeds the config inventory (`GATEWAY_REGION` / `GATEWAY_REGIONS` / `GATEWAY_HOME_REGION`, gated by `MULTI_REGION_ENABLED`) into `region_router`; `RegionHeaderMiddleware` stamps `X-Gatewayz-Region` (+ `-Selected`) on every response; `region_status()` exposes the wiring. Single region still serves all traffic (flag off). Tested: `tests/services/test_region_service.py` (+ existing `region_router` selection tests).
2. **Passive second region.** Deploy the gateway to region B pointed at the same Supabase primary + its own Redis. Health-check only; no user traffic. Verify catalog/inference parity.
3. **Read-routed.** GeoDNS/anycast sends nearest-region traffic for **read/inference** paths; billing-affecting writes pinned to the home region (`select_regions(home=user_home)`). Watch latency + error budgets.
4. **Active-active billing.** Only after the Phase 3 ledger is the billing source of truth (atomic reserve/settle handles concurrent debits). Then drop the home-region pin; both regions accept billing writes.
5. **Failover drills.** Kill region A; confirm region B absorbs traffic within DNS TTL; confirm no double-charge / no lost rate-limit enforcement.

## Dependencies / sequencing
- **Phase 1 migration applied** → `providers.region_affinity`, control-plane tables exist.
- **Phase 3 ledger cut over** (see the shadow→reconcile→switch plan) → required before phase 4 (active-active billing). Until then, stay at rollout phase 3 with home-region write pinning.
- Global/partitioned rate-limit store decision (infra).

## Out of scope here (needs infra/owner)
GeoDNS/anycast provider choice, Redis global vs per-region, Supabase read-replica provisioning, region list + capacity. This doc is the sequencing + invariants; the infra changes are a separate, owner-driven step.
