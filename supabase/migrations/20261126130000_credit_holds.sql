-- Credit holds and unpaid balances.
--
-- 1. credit_holds (new) + credit_hold / credit_release (new). A request's
--    pre-check reserves its maximum paid cost under the users row lock, so
--    concurrent requests cannot each count the same balance: a hold is
--    refused when the balance less every other live hold of the account is
--    below the amount. Keyed by the billing request_id, idempotent on it.
--    Expired holds stop counting and are deleted on the next hold.
-- 2. atomic_deduct_credits (replaced, from 20260916120000_fix_atomic_deduct_
--    request_id.sql): unchanged except that charging a request deletes that
--    request's own hold. Holds never block a charge: they exist to stop
--    OTHER requests from counting the balance, not the request that placed
--    them.
-- 3. credit_debts (new) + charge_with_debt (new, derived from
--    atomic_deduct_credits): for a charge made after the response was
--    delivered. Under the users row lock and idempotent on request_id, it
--    charges min(cost, balance) (allowance first, then purchased credits,
--    as atomic_deduct_credits does), writes the usual credit_transactions
--    row for the charged part and records the rest as an unpaid balance.
-- 4. atomic_add_credits (replaced, from 20260705000001_add_atomic_add_
--    credits.sql): added credits pay the account's unpaid balances first,
--    oldest first, in the same transaction, with a ledger row describing
--    the settlement. With no unpaid balances (always, while the application
--    switch CREDIT_DEBT_ENABLED is off) it behaves exactly as before.
--
-- The application calls none of the new functions until CREDIT_HOLDS_ENABLED
-- / CREDIT_DEBT_ENABLED are switched on. Additive and idempotent (IF NOT
-- EXISTS, CREATE OR REPLACE with unchanged signatures).
--
-- Down (manual): drop function credit_hold, credit_release, charge_with_debt;
-- drop table credit_holds, credit_debts; re-apply atomic_deduct_credits from
-- 20260916120000 and atomic_add_credits from 20260705000001.

-- 1. Tables -------------------------------------------------------------------

create table if not exists public.credit_holds (
  request_id uuid primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  amount_usd numeric(38,8) not null check (amount_usd > 0),
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists idx_credit_holds_user_expires
  on public.credit_holds (user_id, expires_at);

comment on table public.credit_holds is
  'Paid credits reserved by a request''s pre-check until it is charged, released, or the hold expires.';

create table if not exists public.credit_debts (
  id bigserial primary key,
  user_id bigint not null references public.users(id) on delete cascade,
  request_id uuid not null unique,
  amount_usd numeric(38,8) not null check (amount_usd > 0),
  settled_usd numeric(38,8) not null default 0
    check (settled_usd >= 0 and settled_usd <= amount_usd),
  created_at timestamptz not null default now(),
  settled_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_credit_debts_user_unsettled
  on public.credit_debts (user_id, created_at, id) where settled_at is null;

comment on table public.credit_debts is
  'Usage delivered but not covered by the balance at billing time. Paid from the next credits added (atomic_add_credits), oldest first.';

-- 2. credit_hold / credit_release ---------------------------------------------

-- Reserve p_amount of paid credits for one request for p_ttl_seconds
-- (60..3600). Returns the amount held, or 0 when refused: the balance less the
-- account's other live holds is below p_amount, the account does not exist,
-- or the request was already charged. A repeat for the same request returns
-- the existing hold for the same account and 0 for another account.
create or replace function public.credit_hold(
  p_user_id bigint, p_amount numeric, p_request_id uuid, p_ttl_seconds integer default 900
) returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowance numeric;
  v_purchased numeric;
  v_hold public.credit_holds;
  v_others numeric;
  v_amount numeric;
begin
  if p_request_id is null or p_user_id is null or coalesce(p_amount, 0) <= 0 then
    return 0;
  end if;

  -- The same row lock atomic_deduct_credits and atomic_add_credits take.
  select subscription_allowance, purchased_credits
  into v_allowance, v_purchased
  from public.users
  where id = p_user_id
  for update;
  if not found then
    return 0;
  end if;

  delete from public.credit_holds
  where user_id = p_user_id and expires_at <= now();

  select * into v_hold from public.credit_holds where request_id = p_request_id;
  if found then
    return case when v_hold.user_id = p_user_id then v_hold.amount_usd else 0 end;
  end if;
  -- A request already charged holds nothing more.
  if exists (select 1 from public.credit_transactions where request_id = p_request_id) then
    return 0;
  end if;

  -- Rounded up to the ledger unit, so a hold never covers less than asked.
  v_amount := ceil(p_amount * 100000000) / 100000000;
  select coalesce(sum(amount_usd), 0) into v_others
  from public.credit_holds
  where user_id = p_user_id;
  if coalesce(v_allowance, 0) + coalesce(v_purchased, 0) - v_others < v_amount then
    return 0;
  end if;

  insert into public.credit_holds (request_id, user_id, amount_usd, expires_at)
  values (
    p_request_id, p_user_id, v_amount,
    now() + make_interval(secs => least(greatest(coalesce(p_ttl_seconds, 900), 60), 3600))
  );
  return v_amount;
end;
$$;

-- Drop one account's hold for one request that ended without being charged.
-- Returns the amount the live hold reserved; 0 when there was none, it had
-- expired, or it belongs to another account (which is left alone). Never
-- changes a balance.
create or replace function public.credit_release(p_request_id uuid, p_user_id bigint)
returns numeric
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_hold public.credit_holds;
begin
  if p_request_id is null or p_user_id is null then
    return 0;
  end if;
  perform 1 from public.users where id = p_user_id for update;

  delete from public.credit_holds
  where request_id = p_request_id and user_id = p_user_id
  returning * into v_hold;
  if not found or v_hold.expires_at <= now() then
    return 0;
  end if;
  return v_hold.amount_usd;
end;
$$;

-- 3. atomic_deduct_credits: as in 20260916120000, plus the request's own hold
--    is deleted when the request is charged. -------------------------------

CREATE OR REPLACE FUNCTION atomic_deduct_credits(
    p_user_id          BIGINT,
    p_tokens_amount    NUMERIC,
    p_from_allowance   NUMERIC,
    p_from_purchased   NUMERIC,
    p_transaction_type VARCHAR(50),
    p_description      TEXT,
    p_metadata         JSONB DEFAULT '{}'::JSONB,
    p_request_id       UUID DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
AS $$
DECLARE
    v_allowance_before  NUMERIC;
    v_purchased_before  NUMERIC;
    v_allowance_after   NUMERIC;
    v_purchased_after   NUMERIC;
    v_balance_before    NUMERIC;
    v_balance_after     NUMERIC;
    v_transaction_id    BIGINT;
    v_updated_rows      INTEGER;
BEGIN
    -- ======================================================================
    -- VALIDATION
    -- ======================================================================
    IF p_tokens_amount <= 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'tokens_amount must be positive',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    IF p_from_allowance < 0 OR p_from_purchased < 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'from_allowance and from_purchased must be non-negative',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- Verify the breakdown adds up to the total (epsilon: see 20260916120000).
    IF ABS((p_from_allowance + p_from_purchased) - p_tokens_amount) > 0.000001 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'from_allowance + from_purchased must equal tokens_amount',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 1: lock the user row, then read current balances.
    -- ======================================================================
    SELECT subscription_allowance, purchased_credits
    INTO v_allowance_before, v_purchased_before
    FROM users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'user_not_found',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    v_allowance_before := COALESCE(v_allowance_before, 0);
    v_purchased_before := COALESCE(v_purchased_before, 0);
    v_balance_before := v_allowance_before + v_purchased_before;

    -- Holds (credit_holds) are deliberately not counted here: the charge for
    -- a request is never blocked by holds, its own or other requests'.
    IF v_balance_before < p_tokens_amount THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_credits',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    IF v_allowance_before < p_from_allowance THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_allowance',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    IF v_purchased_before < p_from_purchased THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'insufficient_purchased_credits',
            'transaction_id', NULL,
            'new_allowance', v_allowance_before,
            'new_purchased', v_purchased_before,
            'new_balance', v_balance_before
        );
    END IF;

    v_allowance_after := v_allowance_before - p_from_allowance;
    v_purchased_after := v_purchased_before - p_from_purchased;
    v_balance_after := v_allowance_after + v_purchased_after;

    UPDATE users
    SET subscription_allowance = v_allowance_after,
        purchased_credits = v_purchased_after,
        updated_at = NOW()
    WHERE id = p_user_id;

    GET DIAGNOSTICS v_updated_rows = ROW_COUNT;

    IF v_updated_rows = 0 THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'update_failed_unexpectedly',
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 2: INSERT credit_transactions (same transaction as the UPDATE above)
    -- ======================================================================
    INSERT INTO credit_transactions (
        user_id,
        amount,
        transaction_type,
        description,
        balance_before,
        balance_after,
        request_id,
        metadata,
        created_at
    ) VALUES (
        p_user_id,
        -p_tokens_amount,
        p_transaction_type,
        p_description,
        v_balance_before,
        v_balance_after,
        p_request_id,
        COALESCE(p_metadata, '{}'::JSONB) || jsonb_build_object(
            'from_allowance', p_from_allowance,
            'from_purchased', p_from_purchased,
            'allowance_before', v_allowance_before,
            'allowance_after', v_allowance_after,
            'purchased_before', v_purchased_before,
            'purchased_after', v_purchased_after,
            'atomic_procedure', true
        ) || CASE
            WHEN p_request_id IS NOT NULL
            THEN jsonb_build_object('request_id', p_request_id::TEXT)
            ELSE '{}'::JSONB
        END,
        NOW()
    )
    RETURNING id INTO v_transaction_id;

    -- ======================================================================
    -- STEP 3 (new): the request is charged, so its own hold is settled.
    -- ======================================================================
    IF p_request_id IS NOT NULL THEN
        DELETE FROM public.credit_holds
        WHERE request_id = p_request_id AND user_id = p_user_id;
    END IF;

    RETURN jsonb_build_object(
        'success', true,
        'transaction_id', v_transaction_id,
        'new_allowance', v_allowance_after,
        'new_purchased', v_purchased_after,
        'new_balance', v_balance_after,
        'error', NULL
    );

EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM,
            'transaction_id', NULL,
            'new_allowance', NULL,
            'new_purchased', NULL,
            'new_balance', NULL
        );
END;
$$;

-- 4. charge_with_debt -----------------------------------------------------------

-- Charge a request whose response was already delivered. Charges
-- min(p_amount, balance), allowance first and then purchased credits, writes
-- the credit_transactions row for the charged part (with the request_id) and
-- records the remainder in credit_debts. Idempotent on p_request_id: a repeat
-- charges nothing more and reports what the first call did. Deletes the
-- request's own hold.
create or replace function public.charge_with_debt(
  p_user_id bigint,
  p_amount numeric,
  p_transaction_type varchar(50),
  p_description text,
  p_metadata jsonb default '{}'::jsonb,
  p_request_id uuid default null
) returns jsonb
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowance_before numeric;
  v_purchased_before numeric;
  v_balance_before numeric;
  v_balance_after numeric;
  v_charged numeric;
  v_from_allowance numeric;
  v_from_purchased numeric;
  v_debt numeric;
  v_transaction_id bigint;
  v_existing_debt numeric;
begin
  if p_request_id is null then
    return jsonb_build_object('success', false, 'error', 'request_id is required');
  end if;
  if coalesce(p_amount, 0) <= 0 then
    return jsonb_build_object('success', false, 'error', 'amount must be positive');
  end if;

  select subscription_allowance, purchased_credits
  into v_allowance_before, v_purchased_before
  from public.users
  where id = p_user_id
  for update;
  if not found then
    return jsonb_build_object('success', false, 'error', 'user_not_found');
  end if;

  -- Idempotency, under the lock: this request was already charged.
  select id into v_transaction_id
  from public.credit_transactions where request_id = p_request_id limit 1;
  select amount_usd into v_existing_debt
  from public.credit_debts where request_id = p_request_id;
  if v_transaction_id is not null or v_existing_debt is not null then
    return jsonb_build_object(
      'success', true, 'idempotent', true,
      'transaction_id', v_transaction_id,
      'debt_usd', coalesce(v_existing_debt, 0),
      'error', null);
  end if;

  v_allowance_before := coalesce(v_allowance_before, 0);
  v_purchased_before := coalesce(v_purchased_before, 0);
  v_balance_before := v_allowance_before + v_purchased_before;

  v_charged := least(p_amount, greatest(v_balance_before, 0));
  v_from_allowance := least(greatest(v_allowance_before, 0), v_charged);
  v_from_purchased := v_charged - v_from_allowance;
  v_debt := p_amount - v_charged;
  v_balance_after := v_balance_before - v_charged;

  if v_charged > 0 then
    update public.users
    set subscription_allowance = v_allowance_before - v_from_allowance,
        purchased_credits = v_purchased_before - v_from_purchased,
        updated_at = now()
    where id = p_user_id;

    insert into public.credit_transactions (
      user_id, amount, transaction_type, description,
      balance_before, balance_after, request_id, metadata, created_at
    ) values (
      p_user_id, -v_charged, p_transaction_type, p_description,
      v_balance_before, v_balance_after, p_request_id,
      coalesce(p_metadata, '{}'::jsonb) || jsonb_build_object(
        'from_allowance', v_from_allowance,
        'from_purchased', v_from_purchased,
        'allowance_before', v_allowance_before,
        'allowance_after', v_allowance_before - v_from_allowance,
        'purchased_before', v_purchased_before,
        'purchased_after', v_purchased_before - v_from_purchased,
        'cost_usd_total', p_amount,
        'debt_usd', v_debt,
        'atomic_procedure', true,
        'request_id', p_request_id::text
      ),
      now()
    )
    returning id into v_transaction_id;
  end if;

  if v_debt > 0 then
    insert into public.credit_debts (user_id, request_id, amount_usd, metadata)
    values (
      p_user_id, p_request_id, v_debt,
      coalesce(p_metadata, '{}'::jsonb) || jsonb_build_object(
        'transaction_type', p_transaction_type,
        'description', p_description,
        'cost_usd_total', p_amount,
        'charged_usd', v_charged)
    );
  end if;

  delete from public.credit_holds
  where request_id = p_request_id and user_id = p_user_id;

  return jsonb_build_object(
    'success', true, 'idempotent', false,
    'transaction_id', v_transaction_id,
    'charged_usd', v_charged,
    'debt_usd', v_debt,
    'new_balance', v_balance_after,
    'error', null);

exception
  when others then
    return jsonb_build_object('success', false, 'error', sqlerrm);
end;
$$;

-- 5. atomic_add_credits: as in 20260705000001, plus added credits pay the
--    account's unpaid balances first (STEP 5). -----------------------------

CREATE OR REPLACE FUNCTION atomic_add_credits(
    p_user_id          BIGINT,
    p_credits          NUMERIC,
    p_transaction_type VARCHAR(50),
    p_description      TEXT,
    p_target           VARCHAR(20) DEFAULT 'purchased',
    p_payment_id       BIGINT DEFAULT NULL,
    p_metadata         JSONB DEFAULT '{}'::JSONB,
    p_request_id       UUID DEFAULT NULL,
    p_created_by       TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
AS $$
DECLARE
    v_allowance_before  NUMERIC;
    v_purchased_before  NUMERIC;
    v_allowance_after   NUMERIC;
    v_purchased_after   NUMERIC;
    v_balance_before    NUMERIC;
    v_balance_after     NUMERIC;
    v_transaction_id    BIGINT;
    v_existing_id       BIGINT;
    v_debt              RECORD;
    v_left              NUMERIC;
    v_pay               NUMERIC;
    v_settled           NUMERIC := 0;
    v_settled_ids       BIGINT[] := ARRAY[]::BIGINT[];
BEGIN
    -- ======================================================================
    -- VALIDATION
    -- ======================================================================
    IF p_credits <= 0 THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', 'credits must be positive',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    IF p_target NOT IN ('purchased', 'allowance') THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false,
            'error', 'target must be purchased or allowance',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    -- ======================================================================
    -- STEP 1: Lock the user row to serialize concurrent grants/deductions.
    -- ======================================================================
    SELECT subscription_allowance, purchased_credits
    INTO v_allowance_before, v_purchased_before
    FROM users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', 'user_not_found',
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
    END IF;

    v_allowance_before := COALESCE(v_allowance_before, 0);
    v_purchased_before := COALESCE(v_purchased_before, 0);
    v_balance_before := v_allowance_before + v_purchased_before;

    -- ======================================================================
    -- STEP 2: Idempotency check (while holding the user lock).
    -- ======================================================================
    IF p_request_id IS NOT NULL THEN
        SELECT id INTO v_existing_id
        FROM credit_transactions
        WHERE request_id = p_request_id
        LIMIT 1;

        IF FOUND THEN
            RETURN jsonb_build_object(
                'success', true, 'idempotent', true,
                'transaction_id', v_existing_id,
                'new_purchased', v_purchased_before,
                'new_allowance', v_allowance_before,
                'new_balance', v_balance_before,
                'error', NULL
            );
        END IF;
    END IF;

    -- ======================================================================
    -- STEP 3: Apply the grant to the requested balance.
    -- ======================================================================
    IF p_target = 'allowance' THEN
        v_allowance_after := v_allowance_before + p_credits;
        v_purchased_after := v_purchased_before;
    ELSE
        v_purchased_after := v_purchased_before + p_credits;
        v_allowance_after := v_allowance_before;
    END IF;
    v_balance_after := v_allowance_after + v_purchased_after;

    UPDATE users
    SET subscription_allowance = v_allowance_after,
        purchased_credits = v_purchased_after,
        updated_at = NOW()
    WHERE id = p_user_id;

    -- ======================================================================
    -- STEP 4: Ledger insert (same transaction).
    -- ======================================================================
    INSERT INTO credit_transactions (
        user_id, amount, transaction_type, description,
        balance_before, balance_after, payment_id, request_id, metadata, created_at
    ) VALUES (
        p_user_id, p_credits, p_transaction_type, p_description,
        v_balance_before, v_balance_after, p_payment_id, p_request_id,
        COALESCE(p_metadata, '{}'::JSONB) || jsonb_build_object(
            'target', p_target,
            'allowance_before', v_allowance_before,
            'allowance_after', v_allowance_after,
            'purchased_before', v_purchased_before,
            'purchased_after', v_purchased_after,
            'atomic_procedure', true
        ) || CASE
            WHEN p_created_by IS NOT NULL
            THEN jsonb_build_object('created_by', p_created_by)
            ELSE '{}'::JSONB
        END,
        NOW()
    )
    RETURNING id INTO v_transaction_id;

    -- ======================================================================
    -- STEP 5 (new): unpaid balances (credit_debts) are paid from these
    -- credits first, oldest first, taken from the balance just credited.
    -- ======================================================================
    v_left := p_credits;
    FOR v_debt IN
        SELECT id, amount_usd, settled_usd
        FROM public.credit_debts
        WHERE user_id = p_user_id AND settled_at IS NULL
        ORDER BY created_at, id
        FOR UPDATE
    LOOP
        EXIT WHEN v_left <= 0;
        v_pay := LEAST(v_left, v_debt.amount_usd - v_debt.settled_usd);
        CONTINUE WHEN v_pay <= 0;
        UPDATE public.credit_debts
        SET settled_usd = settled_usd + v_pay,
            settled_at = CASE
                WHEN settled_usd + v_pay >= amount_usd THEN NOW() ELSE NULL END
        WHERE id = v_debt.id;
        v_left := v_left - v_pay;
        v_settled := v_settled + v_pay;
        v_settled_ids := v_settled_ids || v_debt.id;
    END LOOP;

    IF v_settled > 0 THEN
        IF p_target = 'allowance' THEN
            v_allowance_after := v_allowance_after - v_settled;
        ELSE
            v_purchased_after := v_purchased_after - v_settled;
        END IF;

        UPDATE users
        SET subscription_allowance = v_allowance_after,
            purchased_credits = v_purchased_after,
            updated_at = NOW()
        WHERE id = p_user_id;

        INSERT INTO credit_transactions (
            user_id, amount, transaction_type, description,
            balance_before, balance_after, metadata, created_at
        ) VALUES (
            p_user_id, -v_settled, 'debt_settlement',
            'Unpaid balance from earlier usage, paid from added credits',
            v_balance_after, v_balance_after - v_settled,
            jsonb_build_object(
                'target', p_target,
                'credit_debt_ids', to_jsonb(v_settled_ids),
                'source_transaction_id', v_transaction_id,
                'atomic_procedure', true
            ),
            NOW()
        );
        v_balance_after := v_balance_after - v_settled;
    END IF;

    RETURN jsonb_build_object(
        'success', true, 'idempotent', false,
        'transaction_id', v_transaction_id,
        'new_purchased', v_purchased_after,
        'new_allowance', v_allowance_after,
        'new_balance', v_balance_after,
        'error', NULL
    ) || CASE
        WHEN v_settled > 0 THEN jsonb_build_object('debt_settled_usd', v_settled)
        ELSE '{}'::JSONB
    END;

EXCEPTION
    -- A concurrent grant with the same request_id that committed between the
    -- idempotency SELECT and the INSERT: an idempotent no-op.
    WHEN unique_violation THEN
        SELECT id INTO v_existing_id
        FROM credit_transactions
        WHERE request_id = p_request_id
        LIMIT 1;
        RETURN jsonb_build_object(
            'success', true, 'idempotent', true,
            'transaction_id', v_existing_id,
            'new_purchased', v_purchased_before,
            'new_allowance', v_allowance_before,
            'new_balance', v_balance_before,
            'error', NULL
        );
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false, 'idempotent', false, 'error', SQLERRM,
            'transaction_id', NULL, 'new_purchased', NULL,
            'new_allowance', NULL, 'new_balance', NULL
        );
END;
$$;

-- 6. Grants and row-level security: service_role only. ------------------------

revoke all on function public.credit_hold(bigint, numeric, uuid, integer)
  from public, anon, authenticated;
revoke all on function public.credit_release(uuid, bigint)
  from public, anon, authenticated;
revoke all on function public.charge_with_debt(bigint, numeric, varchar, text, jsonb, uuid)
  from public, anon, authenticated;
revoke all on function public.atomic_deduct_credits(
  bigint, numeric, numeric, numeric, varchar, text, jsonb, uuid)
  from public, anon, authenticated;
revoke all on function public.atomic_add_credits(
  bigint, numeric, varchar, text, varchar, bigint, jsonb, uuid, text)
  from public, anon, authenticated;

grant execute on function public.credit_hold(bigint, numeric, uuid, integer) to service_role;
grant execute on function public.credit_release(uuid, bigint) to service_role;
grant execute on function public.charge_with_debt(bigint, numeric, varchar, text, jsonb, uuid)
  to service_role;
grant execute on function public.atomic_deduct_credits(
  bigint, numeric, numeric, numeric, varchar, text, jsonb, uuid)
  to service_role;
grant execute on function public.atomic_add_credits(
  bigint, numeric, varchar, text, varchar, bigint, jsonb, uuid, text)
  to service_role;

alter table public.credit_holds enable row level security;
alter table public.credit_debts enable row level security;

revoke all on public.credit_holds from public, anon, authenticated;
revoke all on public.credit_debts from public, anon, authenticated;
revoke all on sequence public.credit_debts_id_seq from public, anon, authenticated;

grant all on public.credit_holds to service_role;
grant all on public.credit_debts to service_role;
grant all on sequence public.credit_debts_id_seq to service_role;
