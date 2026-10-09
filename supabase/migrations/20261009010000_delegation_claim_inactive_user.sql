-- Migration: delegation_claim_accrual refuses a deactivated / missing payee
-- Created: 2026-10-09
--
-- Follow-up to 20261008230000_delegated_staking.sql (already applied): the
-- pay-time claim must never reserve credits for an account that does not
-- exist or is deactivated (users.is_active = false). Adds the 'inactive_user'
-- outcome; everything else in the function is unchanged. users.is_active is
-- read through to_jsonb so a schema without that column cannot break claims
-- (a missing flag means active, as everywhere else in the backend).
--
-- CREATE OR REPLACE with the same signature: idempotent, keeps the existing
-- grants, which are re-asserted below anyway. Version stamp 20261009010000 is
-- newer than every file in supabase/migrations and unused.

-- Claim a pending accrual for payment to p_user_id: re-checks the pause and
-- the account cap (counting claimed + paid rows only, i.e. money already
-- committed to this account) and flips pending -> claimed. A row that no
-- longer fits is voided. A row already claimed is returned as-is so a crashed
-- payment can be resumed (the ledger's UUID request_id keeps that idempotent).
-- Returns {status, accrual?} with status in claimed | paid | void | paused |
-- not_linked | inactive_user | not_found. The pause is fail-closed (a missing
-- controls row is paused) and the payee must be the wallet's CURRENT account.
create or replace function public.delegation_claim_accrual(
  p_accrual_id bigint, p_user_id bigint, p_account_cap numeric
) returns jsonb
language plpgsql
set search_path = public, pg_temp
as $$
declare
  v_row public.delegation_accruals;
  v_spent numeric;
begin
  select * into v_row from public.delegation_accruals where id = p_accrual_id;
  if not found then
    return jsonb_build_object('status', 'not_found');
  end if;
  perform pg_advisory_xact_lock(hashtext('delegation_accruals:' || v_row.reward_date::text));
  -- Re-read under the lock: a concurrent claim may have moved it.
  select * into v_row from public.delegation_accruals where id = p_accrual_id for update;

  if v_row.status in ('paid', 'void', 'claimed') then
    return jsonb_build_object('status', v_row.status, 'accrual', to_jsonb(v_row));
  end if;

  if not exists (select 1 from public.delegation_controls c
                 where c.asset = v_row.asset and c.accruals_paused = false) then
    return jsonb_build_object('status', 'paused');
  end if;

  -- Authorization, checked under the lock: the payee must be the account the
  -- wallet is linked to right now. Unlinked, moved or never linked -> no claim.
  if p_user_id is null or not exists (
    select 1 from public.user_wallets w
    where w.wallet_address = v_row.wallet_address and w.user_id = p_user_id
  ) then
    return jsonb_build_object('status', 'not_linked');
  end if;
  -- ...and an account that exists and is not deactivated. Read through
  -- to_jsonb so a schema without users.is_active cannot break the claim
  -- (a missing flag means active, as everywhere else in the backend).
  if not exists (
    select 1 from public.users u
    where u.id = p_user_id and coalesce((to_jsonb(u) ->> 'is_active')::boolean, true)
  ) then
    return jsonb_build_object('status', 'inactive_user');
  end if;

  v_spent := public.delegation_account_spent(
    p_user_id, v_row.reward_date, array['claimed', 'paid'], v_row.id);
  if v_row.credits > p_account_cap - v_spent then
    update public.delegation_accruals set status = 'void', updated_at = now()
    where id = v_row.id returning * into v_row;
    return jsonb_build_object('status', 'void', 'accrual', to_jsonb(v_row));
  end if;

  update public.delegation_accruals
  set status = 'claimed', paid_user_id = p_user_id, updated_at = now()
  where id = v_row.id returning * into v_row;
  return jsonb_build_object('status', 'claimed', 'accrual', to_jsonb(v_row));
end;
$$;

revoke all on function public.delegation_claim_accrual(bigint, bigint, numeric)
  from public, anon, authenticated;
grant execute on function public.delegation_claim_accrual(bigint, bigint, numeric)
  to service_role;
