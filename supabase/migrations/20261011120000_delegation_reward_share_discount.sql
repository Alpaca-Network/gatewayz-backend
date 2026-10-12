-- Migration: delegated staking v3 -- reward share as an inference discount
--   delegation_revenue.stake_total_native, delegation_reward_attributions,
--   inference_discount_balances, inference_discount_ledger,
--   inference_discount_holds, and the SQL functions that move discount
--   balance (discount_grant / discount_hold / discount_apply /
--   discount_adjust / discount_reverse) and reserve + grant attributions.
-- Created: 2026-10-11
--
-- v3 replaces the v2 admin-set allowance rate with a share of what the stake
-- actually earned us: for each revenue period (an ETH fee-recipient day, a
-- Cardano epoch) a linked wallet is attributed revenue x (its stake / the
-- vault's or pool's total stake), and DELEGATION_REWARD_SHARE of that is
-- granted to its account as an inference DISCOUNT balance. A billed request
-- then pays (1 - d) x list price in credits and d x list price comes out of
-- the discount balance until it runs out. See src/services/delegation/
-- attribution.py and src/services/delegation/discount.py.
--
-- Same posture as 20261008230000_delegated_staking.sql:
--   * service-role only -- RLS on, no anon/authenticated policy, table AND
--     owned sequence explicitly revoked; functions executable by
--     service_role only.
--   * Every balance change happens inside one SQL function holding the
--     user's balance row lock (SELECT ... FOR UPDATE), idempotent on a UUID
--     request_id (inference_discount_ledger.request_id is UNIQUE). Never
--     read-then-write from Python.
--   * Attributions are reserved 'pending' under a per-date advisory lock
--     (the same pattern as delegation_reserve_accrual) with the per-account
--     daily cap, the global daily budget and the period's recorded revenue
--     as ceilings, then granted in the same transaction that credits the
--     balance.
--   * Ships inert: nothing calls any of this until DELEGATED_STAKING_ENABLED
--     (and, for billing, DELEGATION_DISCOUNT_ENABLED) is set.
--
-- Additive and idempotent throughout (IF NOT EXISTS, CREATE OR REPLACE), so a
-- re-run is a no-op. Version stamp 20261011120000 is newer than every file in
-- supabase/migrations (latest on main: 20261009010000) and the open PRs'
-- migrations, and unused.

-- 1. The denominator of an attribution, recorded with the revenue it
--    divides: ETH = vault totalAssets - the fee recipient's assets at the
--    reading; ADA = the pool's active stake for the epoch. Null on rows
--    recorded before v3.
alter table public.delegation_revenue
  add column if not exists stake_total_native numeric(38,18) null;

-- 2. One attribution per (wallet, asset, revenue period). Written 'pending'
--    before any balance moves; 'granted' in the same transaction that credits
--    the discount balance; 'void' when it no longer fits the account cap at
--    grant time. reward_date is the date the cap and budget are counted on
--    (the revenue row's revenue_date).
create table if not exists public.delegation_reward_attributions (
  id bigserial primary key,
  wallet_address text not null,
  asset text not null check (asset in ('eth', 'ada')),
  period_key text not null,
  reward_date date not null,
  user_id bigint null,                      -- decision-time account; the payee once granted
  stake_native numeric(38,18) not null check (stake_native >= 0),
  stake_total_native numeric(38,18) not null check (stake_total_native > 0),
  revenue_native numeric(38,18) not null,
  revenue_usd numeric(38,18) not null,
  attributed_usd numeric(38,18) not null check (attributed_usd >= 0),
  reward_share numeric(9,6) not null check (reward_share >= 0 and reward_share <= 1),
  discount_usd numeric(38,6) not null check (discount_usd >= 0),
  status text not null default 'pending' check (status in ('pending', 'granted', 'void')),
  request_id uuid not null,
  created_at timestamptz not null default now(),
  granted_at timestamptz null,
  updated_at timestamptz not null default now(),
  unique (wallet_address, asset, period_key)
);

create unique index if not exists uq_dra_request_id
  on public.delegation_reward_attributions (request_id);
create index if not exists idx_dra_reward_date
  on public.delegation_reward_attributions (reward_date);
create index if not exists idx_dra_user
  on public.delegation_reward_attributions (user_id, created_at desc);
create index if not exists idx_dra_asset_period
  on public.delegation_reward_attributions (asset, period_key);
create index if not exists idx_dra_pending
  on public.delegation_reward_attributions (wallet_address) where status = 'pending';

-- 3. One discount balance per account. 1 unit = $1 of inference list price.
create table if not exists public.inference_discount_balances (
  user_id bigint primary key references public.users(id) on delete cascade,
  balance_usd numeric(38,6) not null default 0 check (balance_usd >= 0),
  lifetime_granted_usd numeric(38,6) not null default 0 check (lifetime_granted_usd >= 0),
  lifetime_used_usd numeric(38,6) not null default 0 check (lifetime_used_usd >= 0),
  last_grant_at timestamptz null,
  updated_at timestamptz not null default now()
);

create index if not exists idx_idb_positive
  on public.inference_discount_balances (balance_usd) where balance_usd > 0;

-- 4. Every balance movement. amount_usd is signed (grant/reverse +, use -,
--    adjust either). request_id is the idempotency key: UNIQUE, so a grant,
--    use or adjust is applied at most once. A reversal carries no request_id;
--    it is keyed by ref = 'reverse:<request_id of the use>' instead, UNIQUE
--    among reversals.
create table if not exists public.inference_discount_ledger (
  id bigserial primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  kind text not null check (kind in ('grant', 'use', 'adjust', 'reverse')),
  amount_usd numeric(38,6) not null,
  balance_after numeric(38,6) not null check (balance_after >= 0),
  request_id uuid null unique,
  ref text null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_idl_user_created
  on public.inference_discount_ledger (user_id, created_at desc);
create unique index if not exists uq_idl_reverse_ref
  on public.inference_discount_ledger (ref) where kind = 'reverse';

-- 4b. Pre-request HOLDS on the discount balance. A request's credit
--    pre-check reserves the discount it will draw (trunc(estimate x d, 6),
--    capped at what is not already held) under the balance row lock, so N
--    concurrent pre-checks can never each count the same balance: the sum of
--    live holds never exceeds the balance. discount_apply settles a request
--    against its own hold (and deletes it -- the unused part is released);
--    a hold that is never settled (the request failed) expires and stops
--    counting. Keyed by the billing request_id.
create table if not exists public.inference_discount_holds (
  request_id uuid primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  amount_usd numeric(38,6) not null check (amount_usd > 0),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists idx_idh_user_expires
  on public.inference_discount_holds (user_id, expires_at);

comment on table public.inference_discount_holds is
  'Discount reserved by a request''s credit pre-check until it is billed (or the hold expires). Live holds never exceed the balance.';

comment on table public.delegation_reward_attributions is
  'One row per (wallet, asset, revenue period): the wallet''s pro-rata share of revenue that reached Gatewayz, and the discount granted from it. Pending before any balance moves.';
comment on table public.inference_discount_balances is
  'Per-account inference discount balance (1 unit = $1 of list price). Changed only by discount_grant / discount_apply / discount_adjust / discount_reverse.';
comment on table public.inference_discount_ledger is
  'Every inference discount balance movement; request_id UNIQUE (idempotency).';

-- 5. Balance functions. All take the account's balance row lock first and
--    check idempotency under it, so a concurrent duplicate waits for the
--    first and then sees its ledger row.

-- Credit p_amount (truncated to 6 dp) to the account. Idempotent on
-- p_request_id. Returns {status: granted | exists | invalid, amount_usd?,
-- balance_after?}.
create or replace function public.discount_grant(
  p_user_id bigint, p_amount numeric, p_request_id uuid, p_ref text,
  p_metadata jsonb default '{}'::jsonb
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_amount numeric;
  v_existing public.inference_discount_ledger;
  v_balance public.inference_discount_balances;
begin
  v_amount := trunc(coalesce(p_amount, 0), 6);
  if p_request_id is null or p_user_id is null or v_amount <= 0 then
    return jsonb_build_object('status', 'invalid');
  end if;

  insert into public.inference_discount_balances (user_id) values (p_user_id)
  on conflict (user_id) do nothing;
  select * into v_balance from public.inference_discount_balances
  where user_id = p_user_id for update;

  select * into v_existing from public.inference_discount_ledger where request_id = p_request_id;
  if found then
    return jsonb_build_object(
      'status', 'exists', 'amount_usd', v_existing.amount_usd,
      'balance_after', v_existing.balance_after);
  end if;

  update public.inference_discount_balances
  set balance_usd = balance_usd + v_amount,
      lifetime_granted_usd = lifetime_granted_usd + v_amount,
      last_grant_at = now(),
      updated_at = now()
  where user_id = p_user_id
  returning * into v_balance;

  insert into public.inference_discount_ledger (
    user_id, kind, amount_usd, balance_after, request_id, ref, metadata
  ) values (
    p_user_id, 'grant', v_amount, v_balance.balance_usd, p_request_id, p_ref,
    coalesce(p_metadata, '{}'::jsonb)
  );

  return jsonb_build_object(
    'status', 'granted', 'amount_usd', v_amount, 'balance_after', v_balance.balance_usd);
end;
$$;

-- Draw the discount for one billed request: applied = min(trunc(p_cost x d,
-- 6), balance), with d capped at 0.99 so every request still pays (and
-- records) a positive credit charge. Idempotent on p_request_id, BOUND to the
-- account and the cost: a repeat by the same account for the same cost
-- returns the amount the first call applied and never applies twice; the same
-- request_id with any other account or cost -- or a request whose discount was
-- reversed -- returns 0 (full price), so one key can never fund two requests.
-- Returns 0 when there is nothing to apply (no balance row, zero balance,
-- zero cost / percent) and then writes nothing. Draws only from what other
-- requests' live holds leave free -- its own hold included -- and settles
-- (deletes) its own hold, releasing the unused part.
create or replace function public.discount_apply(
  p_user_id bigint, p_cost numeric, p_discount_percent numeric, p_request_id uuid,
  p_metadata jsonb default '{}'::jsonb
) returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_percent numeric;
  v_applied numeric;
  v_others numeric;
  v_existing public.inference_discount_ledger;
  v_balance public.inference_discount_balances;
begin
  if p_request_id is null or p_user_id is null or coalesce(p_cost, 0) <= 0 then
    return 0;
  end if;
  v_percent := least(greatest(coalesce(p_discount_percent, 0), 0), 0.99);
  if v_percent = 0 then
    return 0;
  end if;

  select * into v_balance from public.inference_discount_balances
  where user_id = p_user_id for update;
  if not found then
    return 0;
  end if;

  select * into v_existing from public.inference_discount_ledger where request_id = p_request_id;
  if found then
    if v_existing.kind = 'use' and v_existing.user_id = p_user_id
       and v_existing.metadata ->> 'cost' is not null
       and (v_existing.metadata ->> 'cost')::numeric = p_cost and not exists (
      select 1 from public.inference_discount_ledger r
      where r.kind = 'reverse' and r.ref = 'reverse:' || p_request_id::text
    ) then
      return -v_existing.amount_usd;
    end if;
    return 0;
  end if;

  delete from public.inference_discount_holds
  where user_id = p_user_id and expires_at <= now();
  select coalesce(sum(amount_usd), 0) into v_others from public.inference_discount_holds
  where user_id = p_user_id and request_id <> p_request_id;
  delete from public.inference_discount_holds where request_id = p_request_id;

  v_applied := least(trunc(p_cost * v_percent, 6), v_balance.balance_usd - v_others);
  if v_applied <= 0 then
    return 0;
  end if;

  update public.inference_discount_balances
  set balance_usd = balance_usd - v_applied,
      lifetime_used_usd = lifetime_used_usd + v_applied,
      updated_at = now()
  where user_id = p_user_id
  returning * into v_balance;

  insert into public.inference_discount_ledger (
    user_id, kind, amount_usd, balance_after, request_id, ref, metadata
  ) values (
    p_user_id, 'use', -v_applied, v_balance.balance_usd, p_request_id, 'inference',
    coalesce(p_metadata, '{}'::jsonb)
      || jsonb_build_object('cost', p_cost, 'discount_percent', v_percent)
  );
  return v_applied;
end;
$$;

-- Reserve the discount a request will draw, at pre-check time:
-- held = min(trunc(p_estimate x d, 6), balance - every other live hold), d
-- capped at 0.99, for p_ttl_seconds (60..3600). Idempotent on p_request_id:
-- a repeat returns the live hold. Expired holds of the account are deleted
-- first. Returns 0 (and holds nothing) when nothing is available.
create or replace function public.discount_hold(
  p_user_id bigint, p_estimate numeric, p_discount_percent numeric, p_request_id uuid,
  p_ttl_seconds integer default 900
) returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_percent numeric;
  v_balance public.inference_discount_balances;
  v_hold public.inference_discount_holds;
  v_held numeric;
  v_amount numeric;
begin
  if p_request_id is null or p_user_id is null or coalesce(p_estimate, 0) <= 0 then
    return 0;
  end if;
  v_percent := least(greatest(coalesce(p_discount_percent, 0), 0), 0.99);
  if v_percent = 0 then
    return 0;
  end if;

  select * into v_balance from public.inference_discount_balances
  where user_id = p_user_id for update;
  if not found then
    return 0;
  end if;
  delete from public.inference_discount_holds
  where user_id = p_user_id and expires_at <= now();

  select * into v_hold from public.inference_discount_holds where request_id = p_request_id;
  if found then
    return case when v_hold.user_id = p_user_id then v_hold.amount_usd else 0 end;
  end if;
  -- A request already billed holds nothing more.
  if exists (select 1 from public.inference_discount_ledger where request_id = p_request_id) then
    return 0;
  end if;

  select coalesce(sum(amount_usd), 0) into v_held from public.inference_discount_holds
  where user_id = p_user_id;
  v_amount := least(trunc(p_estimate * v_percent, 6), v_balance.balance_usd - v_held);
  if v_amount <= 0 then
    return 0;
  end if;
  insert into public.inference_discount_holds (request_id, user_id, amount_usd, expires_at)
  values (
    p_request_id, p_user_id, v_amount,
    now() + make_interval(secs => least(greatest(coalesce(p_ttl_seconds, 900), 60), 3600))
  );
  return v_amount;
end;
$$;

-- Put back the discount a request drew (the request was refused or
-- refunded). Idempotent: a second reversal of the same request returns 0.
-- Returns the amount restored.
create or replace function public.discount_reverse(p_request_id uuid)
returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_use public.inference_discount_ledger;
  v_balance public.inference_discount_balances;
  v_amount numeric;
  v_ref text;
begin
  if p_request_id is null then
    return 0;
  end if;
  select * into v_use from public.inference_discount_ledger
  where request_id = p_request_id and kind = 'use';
  if not found then
    return 0;
  end if;

  select * into v_balance from public.inference_discount_balances
  where user_id = v_use.user_id for update;
  v_ref := 'reverse:' || p_request_id::text;
  if exists (select 1 from public.inference_discount_ledger
             where kind = 'reverse' and ref = v_ref) then
    return 0;
  end if;

  v_amount := -v_use.amount_usd;
  update public.inference_discount_balances
  set balance_usd = balance_usd + v_amount,
      lifetime_used_usd = greatest(lifetime_used_usd - v_amount, 0),
      updated_at = now()
  where user_id = v_use.user_id
  returning * into v_balance;

  insert into public.inference_discount_ledger (
    user_id, kind, amount_usd, balance_after, request_id, ref, metadata
  ) values (
    v_use.user_id, 'reverse', v_amount, v_balance.balance_usd, null, v_ref,
    jsonb_build_object('reversed_ledger_id', v_use.id)
  );
  return v_amount;
end;
$$;

-- An admin correction, signed. Never takes the balance below 0 (refused,
-- not clamped), requires a reason, idempotent on p_request_id. Lifetime
-- totals are not changed -- they count grants and use only; the ledger
-- explains the difference. Returns {status: adjusted | exists | invalid |
-- user_not_found | insufficient_balance, amount_usd?, balance_after?}.
create or replace function public.discount_adjust(
  p_user_id bigint, p_amount numeric, p_request_id uuid, p_reason text,
  p_admin_user_id bigint default null
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_amount numeric;
  v_existing public.inference_discount_ledger;
  v_balance public.inference_discount_balances;
begin
  v_amount := trunc(coalesce(p_amount, 0), 6);
  if p_request_id is null or p_user_id is null or v_amount = 0
     or coalesce(btrim(p_reason), '') = '' then
    return jsonb_build_object('status', 'invalid');
  end if;
  if not exists (select 1 from public.users u where u.id = p_user_id) then
    return jsonb_build_object('status', 'user_not_found');
  end if;

  insert into public.inference_discount_balances (user_id) values (p_user_id)
  on conflict (user_id) do nothing;
  select * into v_balance from public.inference_discount_balances
  where user_id = p_user_id for update;

  select * into v_existing from public.inference_discount_ledger where request_id = p_request_id;
  if found then
    return jsonb_build_object(
      'status', 'exists', 'amount_usd', v_existing.amount_usd,
      'balance_after', v_existing.balance_after);
  end if;

  -- A correction may not take back discount that in-flight requests hold.
  if v_balance.balance_usd + v_amount < (
    select coalesce(sum(h.amount_usd), 0) from public.inference_discount_holds h
    where h.user_id = p_user_id and h.expires_at > now()
  ) then
    return jsonb_build_object('status', 'insufficient_balance', 'balance_after', v_balance.balance_usd);
  end if;

  update public.inference_discount_balances
  set balance_usd = balance_usd + v_amount, updated_at = now()
  where user_id = p_user_id
  returning * into v_balance;

  insert into public.inference_discount_ledger (
    user_id, kind, amount_usd, balance_after, request_id, ref, metadata
  ) values (
    p_user_id, 'adjust', v_amount, v_balance.balance_usd, p_request_id, left(p_reason, 500),
    jsonb_build_object('admin_user_id', p_admin_user_id, 'reason', left(p_reason, 500))
  );
  return jsonb_build_object(
    'status', 'adjusted', 'amount_usd', v_amount, 'balance_after', v_balance.balance_usd);
end;
$$;

-- 6. Attribution reserve + grant, under the reward date's advisory lock.
--
--    An account's spend for a date is every live attribution (pending,
--    granted) attributed to it: by its user_id or by a wallet it has linked
--    now -- a wallet that moved between accounts counts against both,
--    conservative, exactly like delegation_account_spent.

create or replace function public.delegation_attribution_spent(
  p_user_id bigint, p_reward_date date, p_statuses text[],
  p_exclude_id bigint default null
) returns numeric
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select coalesce(sum(a.discount_usd), 0)
  from public.delegation_reward_attributions a
  where a.reward_date = p_reward_date
    and a.status = any(p_statuses)
    and (p_exclude_id is null or a.id <> p_exclude_id)
    and (
      a.user_id = p_user_id
      or a.wallet_address in (
        select w.wallet_address from public.user_wallets w where w.user_id = p_user_id
      )
    );
$$;

-- Reserve (insert pending) one wallet-asset-period attribution. The
-- attributed amount is capped so the period's attributions never exceed the
-- revenue recorded for it; the discount is trunc(attributed x share, 6),
-- capped at the account's remaining daily headroom; refused when the global
-- daily budget would be exceeded or the asset is paused (fail closed).
-- Returns {status, attribution?, capped?} with status in created | exists |
-- paused | no_revenue | revenue_exhausted | over_cap | budget_exhausted |
-- invalid.
create or replace function public.delegation_reserve_attribution(
  p_wallet_address text, p_asset text, p_period_key text, p_reward_date date,
  p_user_id bigint, p_stake_native numeric, p_stake_total_native numeric,
  p_attributed_usd numeric, p_reward_share numeric, p_request_id uuid,
  p_account_cap numeric, p_global_budget numeric
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_existing public.delegation_reward_attributions;
  v_row public.delegation_reward_attributions;
  v_revenue public.delegation_revenue;
  v_share numeric;
  v_already numeric;
  v_attributed numeric;
  v_discount numeric;
  v_spent numeric := 0;
  v_grant numeric;
  v_committed numeric;
begin
  perform pg_advisory_xact_lock(hashtext('delegation_attributions:' || p_reward_date::text));

  select * into v_existing from public.delegation_reward_attributions
  where wallet_address = lower(p_wallet_address) and asset = p_asset
    and period_key = p_period_key;
  if found then
    return jsonb_build_object('status', 'exists', 'attribution', to_jsonb(v_existing));
  end if;

  if p_request_id is null or coalesce(p_stake_total_native, 0) <= 0
     or coalesce(p_stake_native, 0) <= 0 or coalesce(p_attributed_usd, 0) <= 0 then
    return jsonb_build_object('status', 'invalid');
  end if;

  if not exists (select 1 from public.delegation_controls c
                 where c.asset = p_asset and c.accruals_paused = false) then
    return jsonb_build_object('status', 'paused');
  end if;

  select * into v_revenue from public.delegation_revenue
  where asset = p_asset and period_key = p_period_key;
  if not found or v_revenue.revenue_usd <= 0 then
    return jsonb_build_object('status', 'no_revenue');
  end if;

  -- Never attribute more than the period earned, across every wallet. The
  -- period's attributions are all on the same reward_date, so this lock
  -- serializes them.
  select coalesce(sum(attributed_usd), 0) into v_already
  from public.delegation_reward_attributions
  where asset = p_asset and period_key = p_period_key and status in ('pending', 'granted');
  v_attributed := least(p_attributed_usd, v_revenue.revenue_usd - v_already);
  if v_attributed <= 0 then
    return jsonb_build_object('status', 'revenue_exhausted');
  end if;

  v_share := least(greatest(coalesce(p_reward_share, 0), 0), 1);
  v_discount := trunc(v_attributed * v_share, 6);

  if p_user_id is not null then
    v_spent := public.delegation_attribution_spent(
      p_user_id, p_reward_date, array['pending', 'granted']);
  end if;
  v_grant := trunc(least(v_discount, p_account_cap - v_spent), 6);
  if v_grant <= 0 then
    return jsonb_build_object('status', 'over_cap');
  end if;

  select coalesce(sum(discount_usd), 0) into v_committed
  from public.delegation_reward_attributions
  where reward_date = p_reward_date and status in ('pending', 'granted');
  if v_committed + v_grant > p_global_budget then
    return jsonb_build_object('status', 'budget_exhausted');
  end if;

  insert into public.delegation_reward_attributions (
    wallet_address, asset, period_key, reward_date, user_id, stake_native,
    stake_total_native, revenue_native, revenue_usd, attributed_usd, reward_share,
    discount_usd, status, request_id
  ) values (
    lower(p_wallet_address), p_asset, p_period_key, p_reward_date, p_user_id, p_stake_native,
    p_stake_total_native, v_revenue.revenue_native, v_revenue.revenue_usd, v_attributed,
    v_share, v_grant, 'pending', p_request_id
  ) returning * into v_row;

  return jsonb_build_object(
    'status', 'created', 'attribution', to_jsonb(v_row),
    'capped', v_grant < v_discount or v_attributed < p_attributed_usd);
end;
$$;

-- Grant a pending attribution to p_user_id, atomically: re-checks the pause
-- (fail closed), that the wallet is linked to p_user_id right now, that the
-- account exists and is not deactivated, and the account cap (granted rows
-- only); then credits the discount balance (discount_grant, idempotent on the
-- attribution's request_id) and marks the row granted -- one transaction, so
-- there is no half-paid state. Returns {status, attribution?, grant?} with
-- status in granted | already | void | paused | not_linked | inactive_user |
-- not_found | grant_failed.
create or replace function public.delegation_grant_attribution(
  p_attribution_id bigint, p_user_id bigint, p_account_cap numeric
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_row public.delegation_reward_attributions;
  v_spent numeric;
  v_grant jsonb;
begin
  select * into v_row from public.delegation_reward_attributions where id = p_attribution_id;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  perform pg_advisory_xact_lock(hashtext('delegation_attributions:' || v_row.reward_date::text));
  select * into v_row from public.delegation_reward_attributions
  where id = p_attribution_id for update;

  if v_row.status = 'granted' then
    return jsonb_build_object('status', 'already', 'attribution', to_jsonb(v_row));
  end if;
  if v_row.status = 'void' then
    return jsonb_build_object('status', 'void', 'attribution', to_jsonb(v_row));
  end if;

  if not exists (select 1 from public.delegation_controls c
                 where c.asset = v_row.asset and c.accruals_paused = false) then
    return jsonb_build_object('status', 'paused');
  end if;

  if p_user_id is null or not exists (
    select 1 from public.user_wallets w
    where w.wallet_address = v_row.wallet_address and w.user_id = p_user_id
  ) then
    return jsonb_build_object('status', 'not_linked');
  end if;
  if not exists (
    select 1 from public.users u
    where u.id = p_user_id and coalesce((to_jsonb(u) ->> 'is_active')::boolean, true)
  ) then
    return jsonb_build_object('status', 'inactive_user');
  end if;

  v_spent := public.delegation_attribution_spent(
    p_user_id, v_row.reward_date, array['granted'], v_row.id);
  if v_row.discount_usd > p_account_cap - v_spent then
    update public.delegation_reward_attributions set status = 'void', updated_at = now()
    where id = v_row.id returning * into v_row;
    return jsonb_build_object('status', 'void', 'attribution', to_jsonb(v_row));
  end if;

  v_grant := public.discount_grant(
    p_user_id, v_row.discount_usd, v_row.request_id,
    'delegation_share:' || v_row.asset || ':' || v_row.period_key,
    jsonb_build_object(
      'attribution_id', v_row.id, 'wallet', v_row.wallet_address, 'asset', v_row.asset,
      'period_key', v_row.period_key, 'attributed_usd', v_row.attributed_usd,
      'reward_share', v_row.reward_share));
  if coalesce(v_grant ->> 'status', '') not in ('granted', 'exists') then
    return jsonb_build_object('status', 'grant_failed', 'grant', v_grant);
  end if;

  update public.delegation_reward_attributions
  set status = 'granted', user_id = p_user_id, granted_at = now(), updated_at = now()
  where id = v_row.id returning * into v_row;
  return jsonb_build_object('status', 'granted', 'attribution', to_jsonb(v_row), 'grant', v_grant);
end;
$$;

-- 7. Lock down. Tables and sequences: service_role only. Functions: revoke
--    the PUBLIC default, execute for service_role only.
revoke all on function public.discount_grant(bigint, numeric, uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.discount_apply(bigint, numeric, numeric, uuid, jsonb)
  from public, anon, authenticated;
revoke all on function public.discount_reverse(uuid)
  from public, anon, authenticated;
revoke all on function public.discount_hold(bigint, numeric, numeric, uuid, integer)
  from public, anon, authenticated;
revoke all on function public.discount_adjust(bigint, numeric, uuid, text, bigint)
  from public, anon, authenticated;
revoke all on function public.delegation_attribution_spent(bigint, date, text[], bigint)
  from public, anon, authenticated;
revoke all on function public.delegation_reserve_attribution(
  text, text, text, date, bigint, numeric, numeric, numeric, numeric, uuid, numeric, numeric)
  from public, anon, authenticated;
revoke all on function public.delegation_grant_attribution(bigint, bigint, numeric)
  from public, anon, authenticated;

grant execute on function public.discount_grant(bigint, numeric, uuid, text, jsonb)
  to service_role;
grant execute on function public.discount_apply(bigint, numeric, numeric, uuid, jsonb)
  to service_role;
grant execute on function public.discount_reverse(uuid) to service_role;
grant execute on function public.discount_hold(bigint, numeric, numeric, uuid, integer)
  to service_role;
grant execute on function public.discount_adjust(bigint, numeric, uuid, text, bigint)
  to service_role;
grant execute on function public.delegation_attribution_spent(bigint, date, text[], bigint)
  to service_role;
grant execute on function public.delegation_reserve_attribution(
  text, text, text, date, bigint, numeric, numeric, numeric, numeric, uuid, numeric, numeric)
  to service_role;
grant execute on function public.delegation_grant_attribution(bigint, bigint, numeric)
  to service_role;

alter table public.delegation_reward_attributions enable row level security;
alter table public.inference_discount_balances enable row level security;
alter table public.inference_discount_ledger enable row level security;
alter table public.inference_discount_holds enable row level security;

revoke all on public.delegation_reward_attributions from anon, authenticated;
revoke all on public.inference_discount_balances from anon, authenticated;
revoke all on public.inference_discount_ledger from anon, authenticated;
revoke all on public.inference_discount_holds from anon, authenticated;
revoke all on sequence public.delegation_reward_attributions_id_seq from anon, authenticated;
revoke all on sequence public.inference_discount_ledger_id_seq from anon, authenticated;

grant all on public.delegation_reward_attributions to service_role;
grant all on public.inference_discount_balances to service_role;
grant all on public.inference_discount_ledger to service_role;
grant all on public.inference_discount_holds to service_role;
grant all on sequence public.delegation_reward_attributions_id_seq to service_role;
grant all on sequence public.inference_discount_ledger_id_seq to service_role;
