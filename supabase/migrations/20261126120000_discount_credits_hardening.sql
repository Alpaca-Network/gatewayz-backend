-- Discount credits: settlement, cap timing and hold release.
--
-- What is current. Discount credits are a per-account balance granted only
-- from the Gatewayz vault (discount_grant, called by the vault mirror job).
-- Billing holds a request's discount before the request (discount_hold),
-- settles it afterwards (discount_apply), puts it back on a refund
-- (discount_reverse) and, from this migration, frees a hold when a request
-- ends without being billed (discount_release). Each account has a daily cap
-- (discount_set_daily_cap, discount_roll_day; 20261125120000). The header of
-- 20261011120000 still describes the delegated-staking programme that first
-- created these tables; that programme is retired, and its last functions
-- are dropped below. The tables keep their names.
--
-- What changes
--   1. discount_apply honours the request's own live hold: a request draws
--      least(trunc(cost x d, 6), balance - other holds,
--            greatest(own hold, cap - used today - other holds)).
--      A lowered cap never shrinks a hold already placed, so the pre-check
--      that admitted a request on the strength of its hold still holds.
--      Today's use may then end above the cap; that is recorded as it is.
--   2. A raised cap takes effect 24 hours after it was requested, not at the
--      next UTC midnight. New column next_daily_cap_at; next_daily_cap_from
--      is still written (the UTC date of next_daily_cap_at) for the API.
--      Rows written before this migration (next_daily_cap_at null) keep the
--      date rule.
--   3. discount_release (new): drops one account's hold for one request.
--   4. delegation_attribution_spent, delegation_reserve_attribution and
--      delegation_grant_attribution (retired programme; the last of them
--      could still call discount_grant) are dropped. No table is dropped.
--
-- discount_hold, discount_reverse, discount_grant and discount_adjust are
-- unchanged. Replaced functions keep their signatures, return types and
-- grants; discount_set_daily_cap's result gains next_daily_cap_at.
--
-- Rollback: re-create discount_roll_day, discount_apply and
-- discount_set_daily_cap from 20261125120000 and drop discount_release. The
-- new column can stay. The dropped delegation functions are not needed by
-- anything.

-- 1. When a pending raise comes into force (timestamptz, UTC instant).
alter table public.inference_discount_balances
  add column if not exists next_daily_cap_at timestamptz null;

comment on column public.inference_discount_balances.next_daily_cap_at is
  'When the pending raise (next_daily_cap_usd) comes into force: 24 hours after it was requested.';
comment on column public.inference_discount_balances.next_daily_cap_from is
  'UTC date of next_daily_cap_at (kept for the API). Rows written before 20261126120000 '
  'have only this column, and their raise applies from this date.';
comment on column public.inference_discount_balances.cap_used_usd is
  'Discount credits drawn on cap_day. Stale when cap_day is in the past: read it as 0. '
  'May exceed daily_cap_usd when a request settles a hold placed before the cap was lowered.';

-- 2. Start a new day on one balance row: reset today's use when the UTC date
--    has moved on, and bring a pending raise into force once
--    next_daily_cap_at has passed (or, for a row written before this
--    migration with no next_daily_cap_at, once next_daily_cap_from has
--    come). The CALLER must already hold the row lock. Returns the row as it
--    now stands (null when the account has no row).
create or replace function public.discount_roll_day(p_user_id bigint)
returns public.inference_discount_balances
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_today date := (now() at time zone 'utc')::date;
  v_row public.inference_discount_balances;
  v_due boolean;
begin
  select * into v_row from public.inference_discount_balances where user_id = p_user_id;
  if not found then
    return v_row;
  end if;

  v_due := coalesce(
    v_row.next_daily_cap_usd is not null and (
      v_row.next_daily_cap_at <= now()
      or (v_row.next_daily_cap_at is null
          and v_row.next_daily_cap_from is not null
          and v_row.next_daily_cap_from <= v_today)),
    false);
  if not v_due and v_row.cap_day >= v_today then
    return v_row;
  end if;

  update public.inference_discount_balances
  set daily_cap_usd = case when v_due then next_daily_cap_usd else daily_cap_usd end,
      next_daily_cap_usd = case when v_due then null else next_daily_cap_usd end,
      next_daily_cap_from = case when v_due then null else next_daily_cap_from end,
      next_daily_cap_at = case when v_due then null else next_daily_cap_at end,
      cap_used_usd = case when cap_day < v_today then 0 else cap_used_usd end,
      cap_day = greatest(cap_day, v_today)
  where user_id = p_user_id
  returning * into v_row;
  return v_row;
end;
$$;

-- 3. As in 20261125120000, plus: the request's own live hold is honoured.
--    v_own is read before expired holds are deleted and counts only an
--    unexpired hold of this account for this request; an expired hold gives
--    no protection.
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
  v_own numeric;
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

  v_balance := public.discount_roll_day(p_user_id);

  -- This request's own live hold, read before expired holds are deleted.
  select coalesce(sum(amount_usd), 0) into v_own from public.inference_discount_holds
  where request_id = p_request_id and user_id = p_user_id and expires_at > now();

  delete from public.inference_discount_holds
  where user_id = p_user_id and expires_at <= now();
  select coalesce(sum(amount_usd), 0) into v_others from public.inference_discount_holds
  where user_id = p_user_id and request_id <> p_request_id;
  -- A request whose key is held by another account draws nothing.
  if exists (select 1 from public.inference_discount_holds
             where request_id = p_request_id and user_id <> p_user_id) then
    return 0;
  end if;
  -- Settle only THIS account's hold: a request_id another account holds
  -- under is never released from here.
  delete from public.inference_discount_holds
  where request_id = p_request_id and user_id = p_user_id;

  -- The cap term never goes below the request's own hold: a cap lowered
  -- after the hold was placed does not shrink it. The balance term still
  -- applies, so the balance never goes negative.
  v_applied := least(
    trunc(p_cost * v_percent, 6),
    v_balance.balance_usd - v_others,
    greatest(v_own, v_balance.daily_cap_usd - v_balance.cap_used_usd - v_others));
  if v_applied <= 0 then
    return 0;
  end if;

  update public.inference_discount_balances
  set balance_usd = balance_usd - v_applied,
      lifetime_used_usd = lifetime_used_usd + v_applied,
      cap_used_usd = cap_used_usd + v_applied,
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

-- 4. Set the account's daily cap (USD, truncated to 6 dp; 0 stops discount
--    use). Lower or equal: applies at once and cancels any pending raise.
--    Higher: recorded and applied 24 hours after this call
--    (next_daily_cap_at), so a key used to raise the cap cannot also spend
--    the raise the same day. Creates the balance row if the account has none.
--    Returns {status: set | scheduled | invalid, daily_cap_usd?,
--    next_daily_cap_usd?, next_daily_cap_from?, next_daily_cap_at?}.
create or replace function public.discount_set_daily_cap(p_user_id bigint, p_cap numeric)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_cap numeric;
  v_status text;
  v_at timestamptz;
  v_row public.inference_discount_balances;
begin
  v_cap := trunc(p_cap, 6);
  if p_user_id is null or v_cap is null or v_cap < 0 or v_cap > 1000000 then
    return jsonb_build_object('status', 'invalid');
  end if;

  insert into public.inference_discount_balances (user_id) values (p_user_id)
  on conflict (user_id) do nothing;
  select * into v_row from public.inference_discount_balances
  where user_id = p_user_id for update;
  v_row := public.discount_roll_day(p_user_id);

  if v_cap <= v_row.daily_cap_usd then
    update public.inference_discount_balances
    set daily_cap_usd = v_cap, next_daily_cap_usd = null, next_daily_cap_from = null,
        next_daily_cap_at = null, updated_at = now()
    where user_id = p_user_id
    returning * into v_row;
    v_status := 'set';
  else
    v_at := now() + interval '24 hours';
    update public.inference_discount_balances
    set next_daily_cap_usd = v_cap,
        next_daily_cap_at = v_at,
        next_daily_cap_from = (v_at at time zone 'utc')::date,
        updated_at = now()
    where user_id = p_user_id
    returning * into v_row;
    v_status := 'scheduled';
  end if;

  return jsonb_build_object(
    'status', v_status,
    'daily_cap_usd', v_row.daily_cap_usd,
    'next_daily_cap_usd', v_row.next_daily_cap_usd,
    'next_daily_cap_from', v_row.next_daily_cap_from,
    'next_daily_cap_at', v_row.next_daily_cap_at);
end;
$$;

-- 5. Free one account's hold for one request that ended without being billed
--    (provider error, nothing to charge, refused after the pre-check).
--    Returns the amount the live hold reserved; 0 when there was none, it
--    had expired, or it belongs to another account (which is left alone).
--    Never changes a balance. Takes the balance row lock, like every other
--    function that touches this account's holds.
create or replace function public.discount_release(p_request_id uuid, p_user_id bigint)
returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_hold public.inference_discount_holds;
begin
  if p_request_id is null or p_user_id is null then
    return 0;
  end if;
  perform 1 from public.inference_discount_balances
  where user_id = p_user_id for update;

  delete from public.inference_discount_holds
  where request_id = p_request_id and user_id = p_user_id
  returning * into v_hold;
  if not found or v_hold.expires_at <= now() then
    return 0;
  end if;
  return v_hold.amount_usd;
end;
$$;

-- 6. The retired programme's attribution functions. Nothing in the
--    application calls them; delegation_grant_attribution could still add
--    discount credits through discount_grant.
drop function if exists public.delegation_grant_attribution(bigint, bigint, numeric);
drop function if exists public.delegation_reserve_attribution(
  text, text, text, date, bigint, numeric, numeric, numeric, numeric, uuid, numeric, numeric);
drop function if exists public.delegation_attribution_spent(bigint, date, text[], bigint);

-- 7. Lock down the new function: service_role only. (The replaced functions
--    keep the grants they already have.)
revoke all on function public.discount_release(uuid, bigint)
  from public, anon, authenticated;
grant execute on function public.discount_release(uuid, bigint) to service_role;
