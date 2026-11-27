-- Card auto top-up: a saved card, charged off-session when the paid balance
-- cannot cover a request (src/services/billing/card_auto_topup.py,
-- docs/discount-credits/card-auto-topup.md).
--
-- 1. users: the saved card (Stripe payment method id and a display summary:
--    brand, last 4, expiry; never a card number) and the account's auto
--    top-up choice (on/off, amount, daily limit) and pause state. Dedicated
--    columns, not users.settings: settings is written as a whole by
--    PUT /user/profile, so nothing that allows a charge may live there.
--    stripe_customer_id already exists (20251030000000).
-- 2. auto_topup_attempts (new): one row per charge attempt. At most one
--    attempt per account is pending at a time (partial unique index).
-- 3. auto_topup_begin / auto_topup_finish (new): start an attempt under the
--    users row lock after checking the account's choice, the saved card, the
--    pause, the cooldown and the daily limit; record its outcome and pause
--    the account when the card was declined or needs the customer.
--
-- Credits for a successful charge are added by the existing
-- atomic_add_credits (idempotent on the charge's payment intent), so the
-- synchronous path and the payment_intent.succeeded webhook cannot both
-- credit it.
--
-- The application calls none of this while CARD_AUTO_TOPUP_ENABLED is off.
-- Additive and idempotent (IF NOT EXISTS, CREATE OR REPLACE).
--
-- Down (manual): drop function auto_topup_begin, auto_topup_finish; drop
-- table auto_topup_attempts; alter table users drop column
-- stripe_default_payment_method_id, stripe_card_summary, auto_topup_on,
-- auto_topup_amount_cents, auto_topup_daily_limit_cents,
-- auto_topup_paused_reason, auto_topup_paused_at.

-- 1. users ------------------------------------------------------------------

alter table public.users
  add column if not exists stripe_default_payment_method_id text,
  add column if not exists stripe_card_summary jsonb,
  add column if not exists auto_topup_on boolean not null default false,
  add column if not exists auto_topup_amount_cents integer
    check (auto_topup_amount_cents is null or auto_topup_amount_cents between 50 and 10000000),
  add column if not exists auto_topup_daily_limit_cents integer
    check (auto_topup_daily_limit_cents is null
           or auto_topup_daily_limit_cents between 0 and 100000000),
  add column if not exists auto_topup_paused_reason text,
  add column if not exists auto_topup_paused_at timestamptz;

comment on column public.users.stripe_default_payment_method_id is
  'Stripe payment method saved for auto top-up (Checkout in setup mode). Never a card number.';
comment on column public.users.stripe_card_summary is
  'Display summary of the saved card: {brand, last4, exp_month, exp_year}.';
comment on column public.users.auto_topup_on is
  'The account chose card auto top-up. Separate from the legacy users.settings.auto_topup_* keys, which never charged and are ignored.';
comment on column public.users.auto_topup_paused_reason is
  'Why auto top-up stopped (declined, requires_action, card_unusable). Cleared when a card is saved or auto top-up is switched on again.';

create index if not exists idx_users_stripe_default_payment_method_id
  on public.users (stripe_default_payment_method_id)
  where stripe_default_payment_method_id is not null;

-- 2. auto_topup_attempts ----------------------------------------------------

create table if not exists public.auto_topup_attempts (
  id uuid primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  amount_cents integer not null check (amount_cents > 0),
  status text not null default 'pending'
    check (status in ('pending', 'succeeded', 'requires_action', 'declined', 'failed', 'unknown')),
  day date not null default ((now() at time zone 'utc')::date),
  payment_id bigint,
  stripe_payment_method_id text,
  stripe_payment_intent_id text unique,
  failure_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_auto_topup_attempts_one_pending
  on public.auto_topup_attempts (user_id) where status = 'pending';
create index if not exists idx_auto_topup_attempts_user_day
  on public.auto_topup_attempts (user_id, day);

comment on table public.auto_topup_attempts is
  'Off-session card charges made by auto top-up. pending/succeeded/unknown count against the daily limit; unknown means Stripe may have charged (the webhook settles it).';

-- 3. Functions ---------------------------------------------------------------

-- Start a charge attempt. Under the users row lock (the same lock
-- atomic_add_credits and atomic_deduct_credits take), so concurrent
-- requests of one account start at most one attempt. Returns
-- {status: started, attempt_id, customer_id, payment_method_id,
--  charged_today_cents} or {status: refused, reason}:
--   no_account, disabled (the account has not switched it on), no_card,
--   paused, in_flight (another attempt is pending), cooldown (a recent
--   attempt failed for a non-card reason), daily_limit.
-- A repeat with the same p_attempt_id returns that attempt's current state.
create or replace function public.auto_topup_begin(
  p_user_id bigint,
  p_attempt_id uuid,
  p_amount_cents integer,
  p_daily_limit_cents integer,
  p_cooldown_seconds integer default 600
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_user record;
  v_existing public.auto_topup_attempts;
  v_today date := (now() at time zone 'utc')::date;
  v_charged bigint;
begin
  if p_user_id is null or p_attempt_id is null or coalesce(p_amount_cents, 0) <= 0 then
    return jsonb_build_object('status', 'refused', 'reason', 'invalid');
  end if;

  select id, stripe_customer_id, stripe_default_payment_method_id, auto_topup_on,
         auto_topup_paused_reason
  into v_user
  from public.users
  where id = p_user_id
  for update;
  if not found then
    return jsonb_build_object('status', 'refused', 'reason', 'no_account');
  end if;

  select * into v_existing from public.auto_topup_attempts where id = p_attempt_id;
  if found then
    if v_existing.user_id <> p_user_id then
      return jsonb_build_object('status', 'refused', 'reason', 'invalid');
    end if;
    return jsonb_build_object(
      'status', case when v_existing.status = 'pending' then 'started' else v_existing.status end,
      'attempt_id', v_existing.id,
      'customer_id', v_user.stripe_customer_id,
      'payment_method_id', v_existing.stripe_payment_method_id
    );
  end if;

  if not coalesce(v_user.auto_topup_on, false) then
    return jsonb_build_object('status', 'refused', 'reason', 'disabled');
  end if;
  if v_user.stripe_customer_id is null or v_user.stripe_default_payment_method_id is null then
    return jsonb_build_object('status', 'refused', 'reason', 'no_card');
  end if;
  if v_user.auto_topup_paused_reason is not null then
    return jsonb_build_object('status', 'refused', 'reason', 'paused');
  end if;

  -- An attempt left pending by a crashed worker: Stripe may have charged it.
  -- It keeps counting against the daily limit as unknown; the webhook settles it.
  update public.auto_topup_attempts
  set status = 'unknown', updated_at = now()
  where user_id = p_user_id and status = 'pending'
    and created_at < now() - interval '10 minutes';

  if exists (
    select 1 from public.auto_topup_attempts where user_id = p_user_id and status = 'pending'
  ) then
    return jsonb_build_object('status', 'refused', 'reason', 'in_flight');
  end if;

  if exists (
    select 1 from public.auto_topup_attempts
    where user_id = p_user_id and status in ('failed', 'unknown')
      and updated_at > now() - make_interval(secs => greatest(coalesce(p_cooldown_seconds, 600), 0))
  ) then
    return jsonb_build_object('status', 'refused', 'reason', 'cooldown');
  end if;

  select coalesce(sum(amount_cents), 0) into v_charged
  from public.auto_topup_attempts
  where user_id = p_user_id and day = v_today
    and status in ('pending', 'succeeded', 'unknown');
  if v_charged + p_amount_cents > greatest(coalesce(p_daily_limit_cents, 0), 0) then
    return jsonb_build_object(
      'status', 'refused', 'reason', 'daily_limit', 'charged_today_cents', v_charged
    );
  end if;

  insert into public.auto_topup_attempts (
    id, user_id, amount_cents, status, day, stripe_payment_method_id
  ) values (
    p_attempt_id, p_user_id, p_amount_cents, 'pending', v_today,
    v_user.stripe_default_payment_method_id
  );

  return jsonb_build_object(
    'status', 'started',
    'attempt_id', p_attempt_id,
    'customer_id', v_user.stripe_customer_id,
    'payment_method_id', v_user.stripe_default_payment_method_id,
    'charged_today_cents', v_charged
  );
end;
$$;

-- Record an attempt's outcome. p_status is succeeded, requires_action,
-- declined, failed or unknown. A final outcome (succeeded, requires_action,
-- declined, failed) is never changed afterwards, except that a webhook
-- reporting succeeded wins over failed/unknown (Stripe is the truth about
-- money). With p_pause the account's auto top-up is paused with p_status as
-- the reason, unless a different card was saved since the attempt began.
-- Returns {status, changed} (changed = this call moved the attempt).
create or replace function public.auto_topup_finish(
  p_attempt_id uuid,
  p_status text,
  p_payment_intent_id text default null,
  p_payment_id bigint default null,
  p_failure_code text default null,
  p_pause boolean default false
) returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_attempt public.auto_topup_attempts;
  v_changed boolean := false;
begin
  if p_status not in ('succeeded', 'requires_action', 'declined', 'failed', 'unknown') then
    return jsonb_build_object('status', 'invalid', 'changed', false);
  end if;

  select * into v_attempt from public.auto_topup_attempts where id = p_attempt_id for update;
  if not found then
    return jsonb_build_object('status', 'not_found', 'changed', false);
  end if;

  if v_attempt.status in ('pending', 'unknown')
     or (p_status = 'succeeded' and v_attempt.status = 'failed') then
    if v_attempt.status <> p_status then
      update public.auto_topup_attempts
      set status = p_status,
          stripe_payment_intent_id = coalesce(p_payment_intent_id, stripe_payment_intent_id),
          payment_id = coalesce(p_payment_id, payment_id),
          failure_code = coalesce(p_failure_code, failure_code),
          updated_at = now()
      where id = p_attempt_id;
      v_changed := true;
    end if;
  elsif p_payment_intent_id is not null and v_attempt.stripe_payment_intent_id is null then
    update public.auto_topup_attempts
    set stripe_payment_intent_id = p_payment_intent_id, updated_at = now()
    where id = p_attempt_id;
  end if;

  if v_changed and p_pause then
    update public.users
    set auto_topup_paused_reason = p_status, auto_topup_paused_at = now()
    where id = v_attempt.user_id
      and stripe_default_payment_method_id is not distinct from v_attempt.stripe_payment_method_id;
  end if;

  return jsonb_build_object(
    'status', case when v_changed then p_status else v_attempt.status end,
    'changed', v_changed,
    'user_id', v_attempt.user_id,
    'amount_cents', v_attempt.amount_cents
  );
end;
$$;

-- 4. Grants and row-level security: service_role only. -----------------------

revoke all on function public.auto_topup_begin(bigint, uuid, integer, integer, integer)
  from public, anon, authenticated;
revoke all on function public.auto_topup_finish(uuid, text, text, bigint, text, boolean)
  from public, anon, authenticated;
grant execute on function public.auto_topup_begin(bigint, uuid, integer, integer, integer)
  to service_role;
grant execute on function public.auto_topup_finish(uuid, text, text, bigint, text, boolean)
  to service_role;

alter table public.auto_topup_attempts enable row level security;
revoke all on public.auto_topup_attempts from public, anon, authenticated;
grant all on public.auto_topup_attempts to service_role;
