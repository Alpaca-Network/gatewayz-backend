-- Migration: community-GPU provider payouts move from WAYZ to native ETH
-- on Base (product decision 2026-09-22: WAYZ is not going public for now,
-- so a WAYZ payout has no market value to a provider).
--
-- Earnings are now DENOMINATED IN USD (integer micro-dollars, 1 USD =
-- 1,000,000 micros) at accrual time and converted to ETH wei at the
-- Chainlink ETH/USD spot price only at settlement time -- providers get a
-- stable USD-denominated rate, the treasury's cost is predictable, and
-- the ETH price used for every payout is recorded on the settlement row.
-- See src/services/gpu/settlement.py and docs/gpu/VERIFICATION_AND_PAYOUTS.md.
--
-- Additive only: every old WAYZ/wei column is kept (made nullable where it
-- was NOT NULL) so historical rows are untouched. Rows accrued before this
-- migration have amount_usd_micros IS NULL and are never picked up by the
-- ETH settlement path (see src/db/gpu_payouts.py::mark_earnings_settling).
--
-- Idempotent: safe to reapply.

-- ---------------------------------------------------------------------------
-- provider_payout_rates: USD-per-1k-tokens (micros)
-- ---------------------------------------------------------------------------
alter table public.provider_payout_rates
    add column if not exists usd_micros_per_1k_tokens bigint null
    check (usd_micros_per_1k_tokens is null or usd_micros_per_1k_tokens >= 0);
alter table public.provider_payout_rates alter column wayz_per_1k_tokens drop not null;

-- PLACEHOLDER rates -- product has not set real numbers yet. Tune by hand:
--   small (<=13B params)  -- $0.02 / 1k tokens
--   medium (<=34B params) -- $0.05 / 1k tokens
--   large (>34B params)   -- $0.10 / 1k tokens
-- (before the per-provider provider_payout_tiers multiplier, which still
-- applies unchanged). Only fills rows that have no USD rate yet, so a
-- hand-tuned value is never overwritten by a reapply.
insert into public.provider_payout_rates (model_class, usd_micros_per_1k_tokens)
values ('small', 20000), ('medium', 50000), ('large', 100000)
on conflict (model_class) do update
    set usd_micros_per_1k_tokens = excluded.usd_micros_per_1k_tokens,
        updated_at = now()
    where public.provider_payout_rates.usd_micros_per_1k_tokens is null;

comment on column public.provider_payout_rates.usd_micros_per_1k_tokens is
  'USD micro-dollars paid per 1k verified tokens (1 USD = 1e6). PLACEHOLDER values seeded 2026-09-22. Paid out in ETH on Base at settlement-time spot price.';

-- ---------------------------------------------------------------------------
-- provider_earnings: USD amount
-- ---------------------------------------------------------------------------
alter table public.provider_earnings
    add column if not exists amount_usd_micros bigint null
    check (amount_usd_micros is null or amount_usd_micros >= 0);
alter table public.provider_earnings alter column amount_wei drop not null;

create index if not exists idx_provider_earnings_usd_accrued
    on public.provider_earnings (provider_id)
    where status = 'accrued' and amount_usd_micros is not null;

comment on column public.provider_earnings.amount_usd_micros is
  'USD micro-dollars earned (1 USD = 1e6). NULL for legacy WAYZ-denominated rows (amount_wei), which the ETH settlement job never pays.';

-- ---------------------------------------------------------------------------
-- provider_settlements: asset/chain, USD amount, price used
-- ---------------------------------------------------------------------------
alter table public.provider_settlements
    add column if not exists asset text not null default 'WAYZ';
alter table public.provider_settlements
    add column if not exists chain text not null default 'avalanche-fuji';
alter table public.provider_settlements
    add column if not exists amount_usd_micros bigint null;
-- ETH/USD price used for the conversion, as the Chainlink answer scaled by
-- its own decimals (e.g. 8) -- stored as a decimal string of USD per ETH.
alter table public.provider_settlements
    add column if not exists eth_usd_price numeric(38, 8) null;
alter table public.provider_settlements
    add column if not exists price_updated_at timestamptz null;
-- Recorded BEFORE broadcast (hash derived from the locally signed tx), so
-- a crash or RPC error mid-send can never lose track of a transfer that
-- may still land. 'pending' + tx_hash == broadcast, awaiting a receipt;
-- 'sent' only after a receipt with status 1. Reconcile resolves by
-- receipt, or by tx_nonce once the pool's mined nonce has passed it.
alter table public.provider_settlements
    add column if not exists tx_nonce bigint null;
alter table public.provider_settlements
    add column if not exists gas_limit bigint null;

comment on column public.provider_settlements.amount_wei is
  'Amount transferred in the settlement asset''s smallest unit (wei for both WAYZ and ETH).';
comment on column public.provider_settlements.asset is
  'Settlement asset: ETH (default for new settlements since 2026-09-22) or WAYZ (legacy testnet rows).';

-- ---------------------------------------------------------------------------
-- Emission mode (REWARDS_MODE='emission'): the provider pool is now a USD
-- amount per day (PROVIDER_EMISSION_USD_PER_DAY), allocated by the same
-- 7-day score. The WAYZ split (emission_epochs.*_wei) still drives stakers'
-- inference credits unchanged.
-- ---------------------------------------------------------------------------
alter table public.provider_scores
    add column if not exists allocation_usd_micros bigint null;
alter table public.emission_epochs
    add column if not exists providers_usd_micros bigint null;

comment on column public.provider_scores.allocation_usd_micros is
  'USD micro-dollars allocated to this provider for the epoch (paid in ETH on Base at settlement). allocation_wei is 0 for USD-mode epochs.';
comment on column public.emission_epochs.providers_usd_micros is
  'USD micro-dollars actually allocated to providers this epoch (after floor dust). NULL for legacy WAYZ-paid epochs.';
