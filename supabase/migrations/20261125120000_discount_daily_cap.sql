-- Discount credits: a daily cap per account.
--
-- An account may draw at most `daily_cap_usd` of discount credits per UTC
-- calendar day (default US$50). The account owner sets the cap. When the cap
-- is reached the request is not refused: discount_apply simply draws less (or
-- nothing) and billing charges the rest to the paid balance as usual.
--
-- What changes
--   1. inference_discount_balances gains the cap, today's use, and a pending
--      raise.
--   2. discount_roll_day (new, internal): starts a new day on a balance row.
--   3. discount_apply, discount_hold, discount_reverse: same signatures and
--      return values as before, now limited by what is left of today's cap.
--      Everything else in them is unchanged from 20261011120000.
--   4. discount_set_daily_cap (new): lowering applies at once; RAISING applies
--      from the next UTC day, so a leaked API key cannot lift the cap and
--      drain the balance the same day.
--
-- Safe to apply before the code that uses it: existing callers see the same
-- functions, and the default cap (50) is far above current use.
-- Rollback: re-create the three functions from 20261011120000 and drop the
-- two new functions; the new columns can stay (nothing else reads them).

-- 1. Columns. cap_used_usd counts discount drawn on cap_day only; it is reset
--    by discount_roll_day the first time the row is touched on a later day.
alter table public.inference_discount_balances
  add column if not exists daily_cap_usd numeric(38,6) not null default 50
    check (daily_cap_usd >= 0),
  add column if not exists cap_day date not null
    default ((now() at time zone 'utc')::date),
  add column if not exists cap_used_usd numeric(38,6) not null default 0
    check (cap_used_usd >= 0),
  add column if not exists next_daily_cap_usd numeric(38,6) null
    check (next_daily_cap_usd >= 0),
  add column if not exists next_daily_cap_from date null;

comment on column public.inference_discount_balances.daily_cap_usd is
  'Most discount credits (USD) the account may draw per UTC day. Set by the account owner.';
comment on column public.inference_discount_balances.cap_used_usd is
  'Discount credits drawn on cap_day. Stale when cap_day is in the past: read it as 0.';
comment on column public.inference_discount_balances.next_daily_cap_usd is
  'A raised cap waiting to take effect on next_daily_cap_from (UTC date).';

-- 2. Start a new day on one balance row: reset today's use when the UTC date
--    has moved on, and bring a pending raise into force once its date has
--    come. The CALLER must already hold the row lock. Returns the row as it
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
begin
  update public.inference_discount_balances
  set daily_cap_usd = case
        when next_daily_cap_from is not null and next_daily_cap_from <= v_today
        then next_daily_cap_usd else daily_cap_usd end,
      next_daily_cap_usd = case
        when next_daily_cap_from is not null and next_daily_cap_from <= v_today
        then null else next_daily_cap_usd end,
      next_daily_cap_from = case
        when next_daily_cap_from is not null and next_daily_cap_from <= v_today
        then null else next_daily_cap_from end,
      cap_used_usd = case when cap_day < v_today then 0 else cap_used_usd end,
      cap_day = greatest(cap_day, v_today)
  where user_id = p_user_id
    and (cap_day < v_today
         or (next_daily_cap_from is not null and next_daily_cap_from <= v_today))
  returning * into v_row;
  if not found then
    select * into v_row from public.inference_discount_balances where user_id = p_user_id;
  end if;
  return v_row;
end;
$$;

-- 3a. As in 20261011120000, plus: applied is also limited by what is left of
--     today's cap after other requests' live holds.
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

  v_balance := public.discount_roll_day(p_user_id);

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

  v_applied := least(
    trunc(p_cost * v_percent, 6),
    v_balance.balance_usd - v_others,
    v_balance.daily_cap_usd - v_balance.cap_used_usd - v_others);
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

-- 3b. As in 20261011120000, plus: a hold never reserves more than what is
--     left of today's cap, so the pre-check does not count discount the cap
--     will refuse.
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
  v_balance := public.discount_roll_day(p_user_id);
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
  v_amount := least(
    trunc(p_estimate * v_percent, 6),
    v_balance.balance_usd - v_held,
    v_balance.daily_cap_usd - v_balance.cap_used_usd - v_held);
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

-- 3c. As in 20261011120000, plus: reversing a use made today frees that much
--     of today's cap.
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
  v_balance := public.discount_roll_day(v_use.user_id);
  v_ref := 'reverse:' || p_request_id::text;
  if exists (select 1 from public.inference_discount_ledger
             where kind = 'reverse' and ref = v_ref) then
    return 0;
  end if;

  v_amount := -v_use.amount_usd;
  update public.inference_discount_balances
  set balance_usd = balance_usd + v_amount,
      lifetime_used_usd = greatest(lifetime_used_usd - v_amount, 0),
      -- A use made today gives its part of today's cap back; an older one
      -- does not, so a late refund never raises today's allowance.
      cap_used_usd = case
        when (v_use.created_at at time zone 'utc')::date = v_balance.cap_day
        then greatest(cap_used_usd - v_amount, 0)
        else cap_used_usd end,
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

-- 4. Set the account's daily cap (USD, truncated to 6 dp; 0 stops discount
--    use). Lower or equal: applies at once and cancels any pending raise.
--    Higher: recorded and applied from the next UTC day. Creates the balance
--    row if the account has none. Returns {status: set | scheduled | invalid,
--    daily_cap_usd?, next_daily_cap_usd?, next_daily_cap_from?}.
create or replace function public.discount_set_daily_cap(p_user_id bigint, p_cap numeric)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_cap numeric;
  v_status text;
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
        updated_at = now()
    where user_id = p_user_id
    returning * into v_row;
    v_status := 'set';
  else
    update public.inference_discount_balances
    set next_daily_cap_usd = v_cap,
        next_daily_cap_from = (now() at time zone 'utc')::date + 1,
        updated_at = now()
    where user_id = p_user_id
    returning * into v_row;
    v_status := 'scheduled';
  end if;

  return jsonb_build_object(
    'status', v_status,
    'daily_cap_usd', v_row.daily_cap_usd,
    'next_daily_cap_usd', v_row.next_daily_cap_usd,
    'next_daily_cap_from', v_row.next_daily_cap_from);
end;
$$;

-- 5. Lock down the new functions: service_role only. (The three replaced
--    functions keep the grants they already have.)
revoke all on function public.discount_roll_day(bigint)
  from public, anon, authenticated;
revoke all on function public.discount_set_daily_cap(bigint, numeric)
  from public, anon, authenticated;
grant execute on function public.discount_roll_day(bigint) to service_role;
grant execute on function public.discount_set_daily_cap(bigint, numeric) to service_role;
