# GPU Transparency Dashboard + Compute Marketplace — Design (Milestone 4, testnet stage)

**Issues:** gatewayz-backend #2261 (epic), #2262 registration, #2263 dashboard, #2264 feed, #2265 verified work, #2266 WAYZ payouts, #2267 onboarding.
**Depends on:** M1 (WAYZ token; 90M `providerRewardsPool` minted to an EOA at genesis), M2 (`user_wallets`, SIWE, `verify_wallet_signature`), M3 (`billing_ref`, threat model, identity firewall).
**Research:** session scratchpad `m4/research.md` (file:line cited, 2026-09-03).

## 1. What we are building (and the one decision that shapes it)

External GPU operators register nodes that serve open-weight models through an OpenAI-compatible server (vLLM first). Gatewayz routes traffic to them as a provider, records every unit of work under the request's `billing_ref`, verifies a sample, accrues WAYZ owed per verified 1k tokens, settles from the rewards pool, and publishes aggregate utilization publicly.

**Trust-boundary decision (binding; adds a party to the threat model).** A community GPU operator *is* the compute and sees prompt content by construction. Therefore, at testnet stage:
- Community routing is **opt-in per request only**: the client asks for model id `community/<model>` (e.g. `community/llama-3.1-8b-instruct`). Community nodes are **never** in an automatic failover chain and never chosen by auto-routing.
- Only **open-weight** models are offered; operators are **approved by an admin** before serving (#2262's whitelisting step).
- The threat model gets a "Community compute operator" party: identity is still stripped (G1 holds — the firewall and canary cover this provider path too), but content is visible to a non-contractual party; the model id prefix is the user's consent. Documented in the API and onboarding docs.

## 2. Data model (new migration `20260903200000_gpu_marketplace.sql`; RLS enabled, service-role only, except where noted)

```
gpu_providers        id PK · user_id FK users · payout_wallet_address text (lower-case, must be a row in user_wallets for user_id)
                     · display_name · contact_email (nullable) · status text CHECK IN ('pending','approved','suspended')
                     · region_default · created_at · approved_at · approved_by
gpu_nodes            id PK · provider_id FK · name · region text · gpu_model text · vram_gb int · bandwidth_mbps int
                     · endpoint_url text (https only) · endpoint_api_key_encrypted text (Fernet, existing keyring)
                     · models jsonb (list of {id, max_context, dtype})
                     · node_token_hash text UNIQUE (salted sha256 of gw_node_… bearer, same helper as api keys)
                     · status CHECK IN ('registered','active','degraded','offline','disabled')
                     · last_heartbeat_at · health_score numeric · outstanding_requests int default 0 · created_at
provider_work        id PK · billing_ref text UNIQUE · node_id FK · provider_id FK · model text
                     · prompt_hash text · response_hash text · prompt_tokens int · completion_tokens int · latency_ms int
                     · status CHECK IN ('completed','failed') · attested boolean default false · attestation_sig text
                     · verification CHECK IN ('pending','sampled','verified','failed','skipped') default 'pending'
                     · created_at        — NO prompt/response content, ever (threat model G3)
provider_payout_rates model_class text PK · wayz_per_1k_tokens numeric(78,0) (wei) · updated_at   — seeded: 'small'(≤13B), 'medium'(≤34B), 'large'(>34B)
provider_earnings    id PK · provider_id FK · work_id FK UNIQUE · amount_wei numeric(78,0) · status CHECK IN ('accrued','settled','void') · settlement_id FK nullable · created_at
provider_settlements id PK · provider_id FK · period_start · period_end · amount_wei numeric(78,0) · tx_hash · status CHECK IN ('pending','sent','failed') · error · created_at
gpu_utilization_hourly hour timestamptz · region · model · requests int · completion_tokens bigint · prompt_tokens bigint
                     · avg_latency_ms int · error_rate numeric · active_nodes int · PK (hour, region, model)   — public SELECT granted to anon (aggregate only)
```
Node display to the public uses `gpu_nodes.name`, region, gpu_model, status, uptime — never wallet, endpoint, or provider user.

## 3. Registration, nodes, heartbeat (W-A1) — #2262

All under `src/routes/gpu.py` (`("gpu", "GPU Marketplace")` in `main.py`). Envelope `{success, data}`; errors via `HTTPException` with snake_case `detail` (remember the app-wide handler masks 409/422 bodies — clients key on status).

- `POST /gpu/providers` (auth `get_user_id`) `{display_name, payout_wallet_address, contact_email?, region_default?}` → 201 provider (`status='pending'`). Wallet must be linked to the caller (`get_wallet(addr).user_id == user_id`) else 400 `wallet_not_linked`. One provider per user (409).
- `GET /gpu/providers/me` → provider + nodes + earnings summary.
- `POST /gpu/nodes` (auth; provider must be `approved`) `{name, region, gpu_model, vram_gb, bandwidth_mbps, endpoint_url, endpoint_api_key, models:[{id,max_context}]}` → 201 `{node, node_token}` — **token shown once**, `gw_node_<32 urlsafe>`; stored hashed. Endpoint must be https and must answer `GET {endpoint_url}/v1/models` with the declared ids within 5 s (probe at registration; 400 `endpoint_unreachable`/`models_mismatch`).
- `PATCH /gpu/nodes/{id}` (auth, owner) fields above; `DELETE` → `disabled`. `POST /gpu/nodes/{id}/rotate-token`.
- `POST /gpu/nodes/{id}/heartbeat` (auth: **node bearer token** via a new `get_node` dependency in `src/security/node_auth.py`; rate limit 6/min/node) `{load: {outstanding, gpu_util_pct?}, models:[…], version?, signature?}` — `signature` optional: wallet signature over `f"gatewayz-heartbeat:{node_id}:{ts}"`; when present and valid, `attested_heartbeat=true`. Updates `last_heartbeat_at`, `status` (`active`), `outstanding_requests`.
- Admin: `POST /gpu/admin/providers/{id}/approve|suspend` (`require_admin`), `GET /gpu/admin/providers`.
- Scheduled: node liveness sweep every 2 min — no heartbeat for 3 min → `degraded`, 10 min → `offline` (mirrors `scheduled_sync.py` patterns).
- DB module `src/db/gpu.py` (existing try/except → warning → safe-default convention); `select_nodes_for_model(model) -> list[dict]` returns `active` nodes of `approved` providers that list the model, ordered by `(outstanding_requests, health_score desc)`.

## 4. Routing integration (W-A2) — #2262/#2265 plumbing

- New provider slug `community`, adapter `src/services/providers/community_adapter.py`: for model `community/<m>`, pick the head of `select_nodes_for_model(m)` (503 `no_community_node_available` if empty), build a per-node `OpenAICompatAdapter` from a `ProviderConfig(slug=f"community:{node_id}", base_url=node.endpoint_url, …)` with the node's decrypted endpoint key (cache adapters per node id, invalidate on PATCH/rotate), forward through the **same** `scrub_upstream_kwargs` boundary (M3), increment/decrement `outstanding_requests` around the call, set the `provider` recorded on the request to `community`.
- Register `community` in `PROVIDER_ROUTING` (`src/handlers/provider_registry.py`) and mark it **excluded from failover chains and auto-routing** (`provider_failover.py`, `smart_router`) — add an explicit deny in `build_provider_failover_chain` and a test.
- Catalog: `community/*` model ids appear in `/v1/models` from the union of active nodes' `models` (via the existing catalog sync's `ensure_provider_exists` path or a small dedicated projection), tagged `source_gateway: "community"`, with a `GATEWAY_REGISTRY` entry (display only).
- After each community call (success or failure), write `provider_work` with `billing_ref = request.state.billing_ref`, hashes (`sha256` of the canonical JSON of `messages` and of the response text), token counts, latency, status. If the node returned header `X-Gatewayz-Attestation` (wallet signature over `f"{billing_ref}|{model}|{prompt_hash}|{response_hash}|{prompt_tokens}|{completion_tokens}"`), verify with `verify_wallet_signature(provider.payout_wallet_address, …)` → `attested=true`.
- Identity firewall: add `community` to the leak-canary provider list (it must pass like any other provider).

## 5. Verified work + payouts (W-B) — #2265, #2266

- **Spot-check verifier** `src/services/gpu/spot_check.py`, scheduled every 10 min: sample `COMMUNITY_SPOTCHECK_RATE` (default 0.05; doubled for non-attested work) of `verification='pending'` completed rows from the last hour whose prompt can be replayed — we do **not** store prompts, so replay requires the prompt: keep a **short-lived Redis copy** (`gpu_spotcheck:{billing_ref}` → canonical messages, TTL 20 min) written only for rows selected at sampling time *before* the request (pre-sampling: decide at request time with probability p whether this request is a spot-check candidate; only then store the prompt copy). Verification = re-run the same prompt with `temperature=0`, `max_tokens=min(64, completion_tokens)` on (a) the same node and (b) a trusted provider serving the same open-weight model if configured (`COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER`), then compare: same-node determinism (prefix similarity ≥ 0.8 on the first 64 tokens, via difflib ratio on text), plausible token counts (±25% of claimed), non-empty. Pass → `verified`; fail → `failed`, `provider_earnings.status='void'`, node `health_score -= 20`, 3 fails in 24 h → node `disabled` + provider notified. Unsampled rows → `verified` after 24 h if the node's failure rate that day < 5%, else `skipped` (unpaid).
- **Earnings**: on `verification in ('verified')`, insert `provider_earnings(amount_wei = (prompt_tokens + completion_tokens) / 1000 * rate(model_class))`; model class from a small map of known model ids → class (config table `provider_payout_rates` + `provider_model_classes` or a jsonb on rates).
- **Settlement** `src/services/chain/wayz_rewards_client.py` (mirrors `wayz_token_faucet_client.py`, separate trust tier, key `WAYZ_REWARDS_POOL_PRIVATE_KEY` = the `providerRewardsPool` EOA) + scheduled daily job: per approved provider, sum `accrued` earnings ≥ `COMMUNITY_MIN_PAYOUT_WAYZ` (10) → ERC20 `transfer(payout_wallet, amount)`; create `provider_settlements(pending→sent|failed)`, mark earnings `settled`; per-run cap `COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ` (100_000) and a pool-balance check before sending. No-op with a warning when the key/token address is unset (today).
- Provider-facing: `GET /gpu/providers/me/earnings` (accrued/settled totals, last 50 work rows without hashes' prompt info, settlements with tx links to Snowtrace).

## 6. Public transparency + feed (W-C) — #2263, #2264

- Hourly rollup job → `gpu_utilization_hourly` from `provider_work` + node heartbeats (active_nodes = nodes with a heartbeat in that hour). Backfill last 7 days on first run.
- Public, no-auth, cached 30 s (Redis), rate-limited 60/min/IP, under `/gpu/public/`:
  - `GET /gpu/public/summary` → `{active_nodes, approved_providers, regions:[{region, nodes}], models:[{id, nodes}], last_hour:{requests, tokens, avg_latency_ms, error_rate}, updated_at}`
  - `GET /gpu/public/nodes` → `[{name, region, gpu_model, vram_gb, status, uptime_24h_pct, models:[ids]}]` (no wallet/endpoint/provider identity)
  - `GET /gpu/public/utilization?window=24h|7d&group=region|model` → hourly series from the rollup
  - `GET /gpu/public/schema` → JSON Schema for the three payloads (also committed at `docs/gpu/public-feed.schema.json`)
- "Real-time" feed v1 = polling of the above with `Cache-Control: public, max-age=30` (no SSE/websocket infra exists; revisit if needed).
- Aggregate-only guarantee test: responses contain no wallet address, no `user_id`, no `billing_ref`, no endpoint URL (sentinel test like the identity canary).

## 7. Frontend (W-D) — #2263 (+ provider portal for #2262/#2266)

- `/gpu` (public; precedent `src/app/model-health`): summary cards, utilization chart (recharts, hourly, toggle region/model), nodes table, model→GPU mapping. Data via react-query against the public endpoints (through the Next `/api/*` proxy pattern used by `/model-health`, or direct backend — follow the dominant pattern).
- `/gpu/provider` (auth): register (wallet picker from `GET /auth/wallets`), status badge (pending/approved), nodes list, add node (token shown once with copy + "you won't see this again"), rotate/disable, earnings + settlements with Snowtrace links. Hidden behind `NEXT_PUBLIC_GPU_MARKETPLACE=true` until backend ships.

## 8. Onboarding (W-E) — #2267

`docs/gpu/PROVIDER_ONBOARDING.md`: requirements (Linux, NVIDIA ≥ 24 GB VRAM for `small`, public https endpoint, uptime expectations), vLLM launch line with `--served-model-name`, register → wait for approval → add node → run the agent, payouts (rates table, WAYZ, Fuji testnet, min payout, settlement cadence, Snowtrace), verification rules (spot-checks, penalties), the trust disclosure (you will see prompt content; opt-in traffic only; no identity), support. `scripts/gpu_node_agent.py`: heartbeat loop (bearer token; optional wallet signature via `eth_account` from a local key file), health self-check of the local vLLM, and an optional reverse-proxy mode that adds `X-Gatewayz-Attestation` to responses (documented as recommended, not required at testnet). `docs/api.md` section "GPU marketplace" + threat-model addendum (§1).

## 9. Config
`COMMUNITY_ROUTING_ENABLED` (false), `COMMUNITY_SPOTCHECK_RATE` (0.05), `COMMUNITY_SPOTCHECK_REFERENCE_PROVIDER` (unset), `WAYZ_REWARDS_POOL_PRIVATE_KEY`, `COMMUNITY_MIN_PAYOUT_WAYZ` (10), `COMMUNITY_MAX_PAYOUT_PER_RUN_WAYZ` (100000), `COMMUNITY_SETTLEMENT_INTERVAL_HOURS` (24), `GPU_NODE_OFFLINE_AFTER_SECONDS` (600). Frontend: `NEXT_PUBLIC_GPU_MARKETPLACE`.

## 10. Testing rules
Real `eth_account` keys for attestation/heartbeat signature tests; TestClient for every route; the identity leak-canary must include `community`; aggregate-only sentinel test on public endpoints; settlement client tested with a real `HexBytes` tx hash and `Web3` contract call shape (the M1 lessons); no network in tests (intercept httpx for node probes).

## 11. Workstreams
| WS | Scope | Issues |
|---|---|---|
| W-A1 | migration, `src/db/gpu.py`, provider/node/heartbeat/admin routes, node auth, liveness sweep | #2262 |
| W-A2 | `community` adapter + routing exclusion + catalog + `provider_work` recording + attestation header + canary | #2262 #2265 |
| W-B | spot-check verifier, earnings, rates, settlement client + job, earnings endpoint | #2265 #2266 |
| W-C | hourly rollup, public endpoints, schema, aggregate-only test | #2263 #2264 |
| W-D | frontend `/gpu` + `/gpu/provider` | #2263 #2262 |
| W-E | onboarding docs, node agent script, threat-model addendum, api docs | #2267 |
Merge order: A1 → A2, B, C (parallel, each rebases on A1) → D, E.
