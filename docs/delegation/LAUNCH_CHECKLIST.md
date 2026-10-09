# Launch checklist: delegated staking (inference-as-yield v2)

Ordered from "counsel signed off" to "flags on". Do not skip ahead: the backend
refuses a positive rate for an asset whose vault/pool is unset (422
`asset_not_configured`), and an asset with no revenue yet pauses itself.

Owner: the engineer on call. Every on-chain step is in
[RUNBOOK_STAKEWISE_VAULT.md](RUNBOOK_STAKEWISE_VAULT.md) /
[RUNBOOK_CARDANO_POOL.md](RUNBOOK_CARDANO_POOL.md). Env var names are from
`src/config/config.py`; endpoints from `src/routes/delegation.py`.

API base: `https://api.gatewayz.ai`. Mutating admin calls need a **superadmin**
user's API key (`require_superadmin`); reads accept any admin key.

---

## 0. Gates

- [ ] Counsel has answered the questions in [COUNSEL_BRIEF.md](COUNSEL_BRIEF.md) in writing, including the user-facing copy and terms.
- [ ] Boss approved the launch rates, `DELEGATION_DAILY_CAP_CREDITS` and `DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS`.
- [ ] Ops alert recipient set (`OPS_ALERT_EMAIL`, else alerts fall back to superadmin emails).
- [ ] An employee volunteer has an ETH wallet and a Cardano wallet (key-hash `stake1u…` address, not a script `stake17…`) for the smoke test.

## 1. On-chain readiness

- [ ] **ETH:** vault verified per RUNBOOK_STAKEWISE_VAULT.md §7 (`feePercent() = 9900`, roles handed off) **and ≥ 1 validator active** with the Operator Service harvesting.
- [ ] **ADA:** pool `registered` in Koios `pool_info`, `live_pledge ≥ pledge`, active stake in place (RUNBOOK_CARDANO_POOL.md §1.4, §8).
- [ ] Either asset can launch alone; leave the other's env var unset.

## 2. Railway env vars (service `api`) — master flag still OFF

| Var | Launch value | Why |
|---|---|---|
| `DELEGATED_STAKING_ENABLED` | `false` (flip in step 5) | Master switch |
| `STAKEWISE_VAULT_ADDRESS` | `<VAULT_ADDRESS>` | Unset → ETH off |
| `STAKEWISE_VAULT_CHAIN_ID` | `1` | Frontend ETH tab requires chain 1 |
| `ALCHEMY_API_KEY` (or `ETHEREUM_RPC_URL`) | company key | Vault reads otherwise use public RPCs |
| `CARDANO_POOL_ID` | `<POOL_ID>` (`pool1…`) | Unset → ADA off |
| `KOIOS_API_KEY` | koios.rest Profile token (secret) | Higher limits than the public tier |
| `KOIOS_BASE_URL` | default `https://api.koios.rest/api/v1` | |
| `DELEGATION_ALLOW_CARDANO_TESTNET` | `false` | Never on in prod |
| `DELEGATION_INFERENCE_MARGIN` | `0.20` (confirm with finance) | m; a credit costs us (1 − m) |
| `DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ETH` | `0.0000684932` | §3 math |
| `DELEGATION_EXPECTED_DAILY_REVENUE_PER_USD_ADA` | `0.0000493151` | §3 math |
| `DELEGATION_DAILY_CAP_CREDITS` | `5` | Per account per day, all wallets + assets |
| `DELEGATION_GLOBAL_DAILY_BUDGET_CREDITS` | `50` | All accounts per day |
| `DELEGATION_MEASUREMENTS_PER_DAY` / `DELEGATION_MIN_MEASUREMENTS_PER_DAY` | `4` / `2` | Defaults |
| `DELEGATION_RECONCILIATION_TOLERANCE` | `0.05` | Default |
| `DELEGATION_RECONCILIATION_GRACE_USD` | formula in RUNBOOK_CARDANO_POOL.md §8 — **≤ 600** at the default budget | Covers the ~3-epoch ADA revenue lag and the ETH activation lag |
| `DELEGATION_*_CRON_*` | defaults (measure :10, reconcile 00:30, accrue 00:50 UTC) | |

Save → Railway redeploys. Then:

```bash
curl -s https://api.gatewayz.ai/delegation/status | jq .data
# enabled:false, eth.vault_address = <VAULT_ADDRESS>, chain_id 1, cardano.pool_id = <POOL_ID>, allowance_rates []
curl -s -H "Authorization: Bearer <ADMIN_API_KEY>" https://api.gatewayz.ai/admin/delegation/rates | jq .data
# rates[].configured true for each asset you set; suggested.eth ≈ 0.085616, suggested.ada ≈ 0.061643
```

## 3. Rate math (A = R / (1 − m))

`suggested = 1000 × R / (1 − m)` credits per $1 000 staked per day
(`suggested_rate`, `src/services/delegation/rewards.py`; rounded down to 6 dp).

| | ETH | ADA |
|---|---|---|
| Revenue to us per year (assumption) | ~2.5 % of stake | ~1.8 % of stake |
| Cross-check (opened 2026-10-08) | Lido stETH 7-day SMA APR **2.2456 %** (`eth-api.lido.fi/v1/protocol/steth/apr/sma`) — a large pool's rate after its own fee; our vault keeps 99 % of rewards | Koios `epoch_info` epochs 655–657: total rewards ≈ **2.10 %/yr** of active stake network-wide; we keep fixed cost + 99 % |
| R = rate / 365 | 0.025 / 365 = **0.0000684932** | 0.018 / 365 = **0.0000493151** |
| suggested, m = 0.20 | 1000 × 0.0000684932 / 0.8 = **0.085616** | 1000 × 0.0000493151 / 0.8 = **0.061643** |
| What a user sees | $10 000 staked → 0.856 credits/day → "99% off up to $25.68/month" | $10 000 delegated → 0.616 credits/day → $18.49/month |

Caveats: R is only earned once ETH sits in an **active** validator and once the
pool **mints blocks** (≈ 1 block/epoch per 1 M ADA). Below that, realised R is
lower and reconciliation will pause the asset — the grace buys time, it does not
fix a revenue shortfall.

## 4. Set the allowance rates

Use the `suggested` values returned by `GET /admin/delegation/rates` in step 2:

```bash
curl -s -X PUT https://api.gatewayz.ai/admin/delegation/rates \
  -H "Authorization: Bearer <SUPERADMIN_API_KEY>" -H "Content-Type: application/json" \
  -d '{
    "rates": [
      {"asset": "eth", "credits_per_1k_usd_per_day": "0.085616", "is_active": true,
       "note": "launch: suggested 1000*R/(1-m), R=2.5%/yr, m=0.20"},
      {"asset": "ada", "credits_per_1k_usd_per_day": "0.061643", "is_active": true,
       "note": "launch: suggested 1000*R/(1-m), R=1.8%/yr, m=0.20"}
    ]
  }' | jq .data.rates
```

Omit an asset you are not launching. Rates are never edited in place (old row
deactivated, new inserted) and every change is audited (`delegation.rates.update`).

## 5. Turn the backend on

- [ ] Railway `api`: `DELEGATED_STAKING_ENABLED=true` → redeploy.
- [ ] Run one sweep and check it recorded something:
  ```bash
  curl -s -X POST https://api.gatewayz.ai/admin/delegation/run \
    -H "Authorization: Bearer <SUPERADMIN_API_KEY>" -H "Content-Type: application/json" \
    -d '{"job":"measure"}' | jq .data
  curl -s -H "Authorization: Bearer <ADMIN_API_KEY>" https://api.gatewayz.ai/admin/status | jq .data.delegation
  # enabled:true; assets.eth/ada configured:true, paused:false; last_measurement_at recent
  curl -s https://api.gatewayz.ai/delegation/status | jq .data.eth.fee_percent   # "99.00"
  ```
  With no linked wallets yet, `measure` reports zero wallets — that is fine.

## 6. Turn the frontend on

- [ ] Vercel → project **gatewayz-frontend** → Settings → Environment Variables → Production: `NEXT_PUBLIC_DELEGATED_STAKING_ENABLED=true`.
- [ ] **Redeploy** production (the value is inlined at build time; anything but the exact string `true` is off — `src/lib/delegation/flags.ts`).
- [ ] https://beta.gatewayz.ai/rewards shows "Stake for inference" with an ETH and/or ADA tab (a tab renders only when its target is configured).

## 7. Smoke test (employee, real money, small amounts)

1. [ ] **Link:** ETH wallet via SIWE; Cardano wallet via CIP-30 `signData` (`/auth/wallet/cardano/nonce` → `/auth/wallet/cardano/link`). Both appear in `GET /delegation/rewards → linked_wallets`.
2. [ ] **Deposit:** stake 0.05 ETH into the vault from `/rewards` **[SIGN]**; delegate the Cardano wallet to `<POOL_ID>` **[SIGN]** (adds the 2 ADA key deposit if unregistered). Check the ETH tx on Etherscan and the ADA delegation in Koios `account_info`.
3. [ ] **Measurement row:** `POST /admin/delegation/run {"job":"measure"}`; `GET /delegation/rewards → positions` shows the ETH position now; the ADA position appears once the delegation is active (~2 epochs).
4. [ ] **Accrual (next UTC day, after 00:50):** `history[]` has a row for yesterday with `status: "paid"` (needs ≥ 2 measurements that day); the user's credit ledger has a `delegation_reward` transaction.
5. [ ] **Reconciliation (after 00:30):** `GET /admin/delegation/reconciliation` → `eth.status` / `ada.status` `ok`; first ETH revenue row is a `baseline`; `recent_revenue` grows after harvests.
6. [ ] **Exit:** request an exit for part of the ETH from `/rewards`, then claim once processed (public vaults have a 15-hour claim delay — `PUBLIC_VAULT_EXITED_ASSETS_CLAIM_DELAY`, `v3-core/script/Network.sol`).

## 8. Rollback

User stake never moves in a rollback — it stays in the user's wallet, the vault
or delegated to the pool; tell affected users they can exit/redelegate at any time.

| Severity | Action | Effect |
|---|---|---|
| One asset misbehaving | `PUT /admin/delegation/rates` with `{"rates":[{"asset":"eth","credits_per_1k_usd_per_day":"0","is_active":false}]}` | That asset earns nothing new; the other keeps running |
| Overspend | automatic: reconciliation sets `delegation_controls.accruals_paused` and emails ops | No new accruals or pending payouts for that asset. **There is no manual pause endpoint** — use the rate deactivation above. Resume only after fixing the cause: `POST /admin/delegation/resume {"asset":"eth"}` (superadmin, audited; re-pauses if overspend persists) |
| Whole feature | Railway: `DELEGATED_STAKING_ENABLED=false` | Every job records `skipped: disabled`; `/delegation/status` → `enabled:false`; no rates published |
| Hide the UI | Vercel: `NEXT_PUBLIC_DELEGATED_STAKING_ENABLED=false` + redeploy | Section disappears from `/rewards` |

## 9. Monitoring

- [ ] Ops emails / Sentry ERRORs (12 h dedupe): `delegation_overspent_{asset}`, `delegation_revenue_read_failed_{asset}`, `delegation_measured_nothing_{asset}` (`src/services/delegation/alerts.py`).
- [ ] `GET /admin/status → data.delegation`: `enabled`, `assets.{eth,ada}.{configured,paused,paused_reason}`, `last_measurement_at` (stale > 8 h = problem), `jobs` (last run of `delegation_measurements`, `delegation_accruals`, `delegation_reconciliation`).
- [ ] Weekly: `GET /admin/delegation/reconciliation` → `coverage` (revenue / cost) per asset ≥ 1; lower the rate if it trends below.
- [ ] Infra: StakeWise operator metrics (`sw_operator_*`), validator effectiveness, KES expiry, pledge, relay health — see the two runbooks.
- [ ] After ~6 epochs of steady ADA revenue, lower `DELEGATION_RECONCILIATION_GRACE_USD`.
