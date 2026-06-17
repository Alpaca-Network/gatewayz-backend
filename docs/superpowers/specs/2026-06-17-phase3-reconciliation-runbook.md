# Phase 3 — Credit-Ledger Reconciliation Runbook (Gatewayz One)

**Item 2 of the shadow → reconcile → cutover chain.** Once the shadow ledger is
accruing (item 1: `CREDIT_LEDGER_SHADOW_ENABLED=true` in the deploy env), this is
how we prove the ledger agrees with live billing *before* cutting billing over to
it.

## Pieces
- **`src/services/billing/ledger_reconciliation.py`** — pure reconciliation math
  (unit-tested in `tests/services/test_ledger_reconciliation.py`). No I/O.
- **`scripts/reconcile_credit_ledger.py`** — I/O + rendering shell. Pulls
  `credit_ledger` + `usage_records` over a window and reports drift. Exit 0 = ok,
  1 = drift/unbalanced (so it can gate a CI/cron check).

## Running it
Run against the **same project the gateway deploys against** (production → `ynleroehyrmaafkgjgmr`).
On Railway the service-role key + URL are already in the deploy env, so:

```bash
railway run -- python scripts/reconcile_credit_ledger.py --since 7d
railway run -- python scripts/reconcile_credit_ledger.py --since 2026-06-17T00:00:00Z --json
```

Scope `--since` to **after** shadow was enabled — older windows have usage but no
ledger rows (drift = full usage total, expected and meaningless pre-accrual).

## What it checks
1. **Internal integrity** — every `ref` balances (Σdebit == Σcredit). Unbalanced
   refs ⇒ a partial/corrupt write; reported by ref.
2. **Ledger vs. live drift** — ledger REVENUE credit total vs. `usage_records.cost`,
   per user and overall, within `--tolerance` (default ±$0.01).

Like-for-like population: the shadow path skips admin tier + sub-$0.000001 charges,
so the live side applies the same exclusions (`--min-cost`; admins resolved from
`users.tier == 'admin'`). The REVENUE total is the authoritative figure — the
allowance/purchased debit *split* can drift under concurrency (see the cutover
TODO in `credit_handler`); reconciliation checks the REVENUE side, which stays
correct.

## Cutover gate (decision criteria)
Cut billing over to the ledger only after, over a representative window where
shadow was continuously on:
- `report.ok` is true (zero unbalanced refs **and** total drift within tolerance), and
- no individual user is persistently over tolerance, and
- `ledger_ref_count` tracks the count of billable requests (no large coverage gap).

Until then the ledger stays a shadow; live `subscription_allowance` /
`purchased_credits` remain authoritative.
